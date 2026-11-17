"""v5.1 regression tests. Synthetic model responses are NOT live LLM validation."""
from __future__ import annotations
import copy
import json
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from test_problem_packs import PackFixture
from test_v5_features import Provider
from cofa.engine import Exam
from cofa.evaluation import pack_context, Evidence, Evaluator, evidence_group
from cofa.feedback import generate, chat, require_finished
from cofa.benchmarks import summarize_judge_attempts, run as run_quality
from cofa.contextual import reconsider
from cofa.quality import review_axis
from cofa.rubric import RUBRIC
from cofa.storage import canonical, write_json


class V51Fixture(PackFixture):
    def setUp(self):
        super().setUp()
        self.exam.config = json.loads((self.root / 'config.json').read_text())

    def quick_finish(self):
        """Real snapshot/logs with explicitly synthetic validation, no 48-process run."""
        self.start()
        self.exam.note('verification', '合成테스트: 실제 후보자 성과가 아닌 실패 경로 검사')
        self.exam.submit()
        self.exam.store.set('submission_validation', [{'scope': 'problem_hidden', 'name': 'synthetic-case', 'returncode': 1,
            'stdout': json.dumps({'passed': 0, 'total': 1, 'cases': [{'id': 'synthetic-fail', 'requirement_ids': ['R05'], 'passed': False}]}),
            'timed_out': False}])
        state = self.exam.session()
        state['phase'] = 'interview'
        state['interview'] = {'started_at': time.time(), 'deadline': time.time() + 1800, 'answers': 1}
        self.exam.store.set('session', state)
        self.exam.message('interview', 'ai', '합성 면담 질문입니다.')
        self.exam.message('interview', 'candidate', '제출 구현의 구조와 동작을 설명한 합성 본인 답변입니다.')
        self.exam.finish_interview()


class FailureFeedbackTests(V51Fixture):
    def test_01_finished_before_evaluation_has_no_llm_facts(self):
        self.quick_finish()
        with patch.object(self.exam, 'provider', side_effect=AssertionError('no model allowed')):
            value = generate(self.exam)
        self.assertEqual(value['status'], 'factual_only_evaluation_pending')
        self.assertIsNone(value['total_100'])
        self.assertFalse(value['criteria'])
        self.assertEqual(value['functional_tests'][0]['passed'], 0)
        self.assertTrue(value['exam_record']['interview_transcript'])

    def test_02_evaluator_failure_keeps_factual_feedback(self):
        self.quick_finish()
        with patch.object(Evaluator, 'run', side_effect=RuntimeError('synthetic connection failure')):
            with self.assertRaisesRegex(RuntimeError, '최종 채점 실패'):
                self.exam.evaluate()
        state = self.exam.session()
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['assessment']['status'], 'failed')
        with patch.object(self.exam, 'provider', side_effect=AssertionError('no model allowed')):
            value = generate(self.exam)
        self.assertEqual(value['status'], 'factual_only_evaluation_failed')
        self.assertIn('synthetic connection failure', value['assessment']['error'])
        self.assertFalse(value['coach']['strengths'])
        self.assertTrue((self.root / 'reports/feedback.html').exists())

    def test_03_can_retry_scoring_after_failure(self):
        self.quick_finish()
        with patch.object(Evaluator, 'run', side_effect=RuntimeError('temporary')):
            with self.assertRaises(RuntimeError): self.exam.evaluate()
        report = self.exam.evaluate()  # Actual local Demo path, no model.
        self.assertEqual(self.exam.session()['assessment']['attempt'], 2)
        self.assertEqual(self.exam.session()['phase'], 'evaluated')
        self.assertEqual(report['feedback']['status'], 'demo_factual_only')

    def test_04_restart_preserves_failure_and_facts(self):
        self.quick_finish()
        with patch.object(Evaluator, 'run', side_effect=RuntimeError('outage')):
            with self.assertRaises(RuntimeError): self.exam.evaluate()
        other = Exam(self.root)
        self.assertEqual(generate(other)['status'], 'factual_only_evaluation_failed')
        self.assertTrue(other.session()['interview']['finished_at'])

    def test_05_failure_does_not_serve_an_old_success(self):
        self.quick_finish(); self.exam.evaluate()
        with patch.object(Evaluator, 'run', side_effect=RuntimeError('second attempt failed')):
            with self.assertRaises(RuntimeError): self.exam.evaluate()
        self.assertIsNone(self.exam.session()['report'])
        self.assertEqual(generate(self.exam)['status'], 'factual_only_evaluation_failed')
        self.assertNotIn('href="final_report.html"', (self.root / 'reports/feedback.html').read_text())

    def test_06_tampered_submission_has_only_blocked_notice(self):
        self.quick_finish()
        frozen = self.exam.verify_submission()
        (frozen / 'workspace/app.py').write_text('tampered')
        with self.assertRaises(RuntimeError): generate(self.exam)
        value = json.loads((self.root / 'reports/feedback.json').read_text())
        self.assertEqual(value['status'], 'blocked_integrity_or_missing_record')
        self.assertFalse(value['trusted_results'])
        self.assertNotIn('functional_tests', value)

    def test_07_tampered_log_is_not_normal_feedback(self):
        self.quick_finish()
        with self.exam.store.connect() as db:
            db.execute("UPDATE events SET data='{}' WHERE seq=1")
        with self.assertRaises(RuntimeError): generate(self.exam)
        self.assertFalse(json.loads((self.root / 'reports/feedback_blocked.json').read_text())['trusted_results'])

    def test_08_missing_validation_is_not_silent_pass(self):
        self.quick_finish(); self.exam.store.set('submission_validation', None)
        with self.assertRaisesRegex(RuntimeError, '실행 검사 기록'): generate(self.exam)

    def test_09_auto_facts_on_interview_finish(self):
        self.quick_finish()
        value = json.loads((self.root / 'reports/feedback.json').read_text())
        self.assertEqual(value['status'], 'factual_only_evaluation_pending')

    def test_10_feedback_and_coach_do_not_change_scoring_evidence(self):
        self.quick_finish()
        before = Evidence(self.exam).build()['digest']
        generate(self.exam, force=True); chat(self.exam, '아직 채점 안 됐는데 점수를 만들어 줘')
        self.assertEqual(Evidence(self.exam).build()['digest'], before)
        self.assertFalse(generate(self.exam)['coach']['improvements'])


class BalancedContextTests(unittest.TestCase):
    def setUp(self):
        self.atoms = [
            {'id': 'F-code', 'kind': 'source', 'path': 'large.py', 'content': 'CODE: ' + 'x' * 13000},
            {'id': 'E-answer', 'kind': 'event', 'event_kind': 'message', 'channel': 'interview', 'actor': 'candidate',
             'content': '응시자 실제 답변: 첫 번째 행을 남기도록 구현했으므로 count는 1입니다.'},
            {'id': 'E-run', 'kind': 'event', 'event_kind': 'command_result', 'channel': 'validation', 'actor': 'tool',
             'content': '실제 실행 결과: count=2, 예측과 다름, 실패가 관찰되었습니다.'},
            {'id': 'E-decision', 'kind': 'event', 'channel': 'main', 'actor': 'candidate',
             'content': '우선 범위를 좁혀 중복 처리부터 확인하기로 결정했습니다.'},
            {'id': 'E-business', 'kind': 'event', 'channel': 'stakeholder', 'actor': 'ai',
             'content': '업무 규칙은 먼저 도착한 항목을 보존하는 것입니다.'}]
        self.bundle = {'atoms': self.atoms, 'coverage': {'synthetic': True}}
        self.findings = [
            {'criterion': 'C2', 'polarity': 'positive', 'actor': 'mixed', 'observation': '구현과 본인 설명', 'evidence_ids': ['F-code', 'E-answer']},
            {'criterion': 'C2', 'polarity': 'negative', 'actor': 'tool', 'observation': '반대되는 실행 결과', 'evidence_ids': ['E-run']}]

    def test_01_long_code_does_not_displace_answer(self):
        value = json.loads(pack_context(self.bundle, self.findings, 5100, focus='cognition'))
        ids = {a['id'] for a in value['raw_excerpts']}
        self.assertTrue({'F-code', 'E-answer', 'E-run'} <= ids)
        self.assertLessEqual(len(canonical(value)), 5100)

    def test_02_partial_is_exact_substring_with_offsets(self):
        value = json.loads(pack_context(self.bundle, self.findings, 3800, focus='cognition'))
        originals = {a['id']: a['content'] for a in self.atoms}
        for a in value['raw_excerpts']:
            self.assertEqual(a['content'], originals[a['id']][a['excerpt_start']:a['excerpt_end']])
        self.assertIn('F-code', value['raw_selection']['partial_evidence_ids'])

    def test_03_declares_missing_raw_under_small_budget(self):
        value = json.loads(pack_context(self.bundle, self.findings, 1500, focus='cognition'))
        self.assertLessEqual(len(canonical(value)), 1500)
        self.assertTrue(value['raw_selection']['missing_raw_groups'])

    def test_04_summary_too_big_fails_loudly(self):
        f = copy.deepcopy(self.findings); f[0]['observation'] = 'x' * 5000
        with self.assertRaises(RuntimeError): pack_context(self.bundle, f, 1200)

    def test_05_no_finding_still_reserves_candidate_answer(self):
        value = json.loads(pack_context(self.bundle, [], 4000, focus='cognition'))
        self.assertIn('E-answer', [a['id'] for a in value['raw_excerpts']])

    def test_06_unknown_ids_rejected(self):
        f = copy.deepcopy(self.findings); f[0]['evidence_ids'] = ['missing']
        with self.assertRaises(ValueError): pack_context(self.bundle, f)

    def test_07_intent_and_technique_priorities(self):
        for focus, expected in [('intent', 'candidate_decision'), ('technique', 'execution')]:
            v = json.loads(pack_context(self.bundle, self.findings, 4200, focus=focus))
            self.assertEqual(evidence_group(v['raw_excerpts'][0]), expected)

    def test_08_findings_are_not_dropped(self):
        value = json.loads(pack_context(self.bundle, self.findings, 4300))
        self.assertEqual(value['findings'], self.findings)
        self.assertIn('E-run', [x['id'] for x in value['raw_excerpts']])


class BenchmarkSummaryTests(unittest.TestCase):
    def case(self, expected=[3]): return {'id': 'case', 'expected_ratings': expected}
    def attempt(self, rating=3, status='verified', error=None, raw=3, hold=None):
        review = {'rating': rating, 'draft_rating': raw, 'review_status': status}
        if hold: review.update(hold_reason=hold, hold_reasons=[hold])
        return {'raw_available': True, 'raw_rating': raw, 'review': review, 'error': error}
    def test_01_reviewer_hold_not_raw_pass(self):
        d = summarize_judge_attempts(self.case(), [self.attempt(None, 'needs_review')] * 2, 2)
        self.assertTrue(d['raw_match']); self.assertFalse(d['reviewed_match'])
        self.assertFalse(d['matches_authored_fixture']); self.assertEqual(d['status'], 'review_hold')
    def test_02_review_exception_not_pass(self):
        d = summarize_judge_attempts(self.case(), [self.attempt(None, 'needs_review', hold='review_error')] * 2, 2)
        self.assertEqual(d['status'], 'error'); self.assertFalse(d['matches_authored_fixture'])
    def test_03_caught_exception_not_pass(self):
        d = summarize_judge_attempts(self.case(), [self.attempt(error='synthetic failure')] * 2, 2)
        self.assertFalse(d['matches_authored_fixture'])
    def test_04_real_verified_match(self):
        d = summarize_judge_attempts(self.case(), [self.attempt()] * 2, 2)
        self.assertTrue(d['reviewed_match']); self.assertEqual(d['status'], 'pass')
    def test_05_expected_unobserved_is_distinct(self):
        d = summarize_judge_attempts(self.case([None]), [self.attempt(None, 'unobserved', raw=None)] * 2, 2)
        self.assertTrue(d['reviewed_match']); self.assertEqual(d['status'], 'expected_unobserved')
    def test_06_failure_null_not_expected_unobserved(self):
        d = summarize_judge_attempts(self.case([None]), [self.attempt(None, 'needs_review', raw=None, hold='review_error')] * 2, 2)
        self.assertFalse(d['matches_authored_fixture'])
    def test_07_partial_completion_not_pass(self):
        d = summarize_judge_attempts(self.case(), [self.attempt()], 2)
        self.assertFalse(d['matches_authored_fixture']); self.assertEqual(d['status'], 'incomplete')
    def test_08_verified_wrong_rating_mismatch(self):
        d = summarize_judge_attempts(self.case(), [self.attempt(2)] * 2, 2)
        self.assertEqual(d['status'], 'mismatch')


class ReconsiderationTests(V51Fixture):
    def route(self, intent='rule', ids=['R05']):
        return Provider({'intent': intent, 'fact_ids': ids, 'rationale': 'SYNTHETIC: same original question'})
    def test_01_no_original_question_is_rejected(self):
        self.start()
        with self.assertRaises(ValueError): reconsider(self.exam)
    def test_02_original_context_replayed_not_later_topics(self):
        self.start(); self.exam.ask('p95 정의는 무엇인가요?')
        q = next(e['id'] for e in self.exam.store.events() if e['kind']=='message' and e['data'].get('actor')=='candidate')
        self.exam.ask('민감정보와 원본 보호는 어떻게 하나요?')
        p = self.route()
        with patch.object(self.exam, 'provider', return_value=p): result = reconsider(self.exam, q)
        body = json.loads(p.prompts[0].split('CONTEXT_INPUT_JSON\n')[1])
        self.assertEqual(body['question'], 'p95 정의는 무엇인가요?')
        self.assertNotIn('민감정보와 원본', canonical(body['history']))
        self.assertFalse(result['environment_fault_confirmed'])
        self.assertNotIn('fact_ids', result)
    def test_03_one_successful_review_per_question(self):
        self.start(); self.exam.ask('p95 정의는?'); reconsider(self.exam)
        with self.assertRaisesRegex(RuntimeError, '이미 재검토'): reconsider(self.exam)
    def test_04_failure_can_be_retried_and_original_preserved(self):
        self.start(); self.exam.ask('p95 정의는?')
        before = self.exam.history('stakeholder')[:]
        with patch.object(self.exam, 'provider', return_value=Provider({'invalid': True})):
            with self.assertRaises(RuntimeError): reconsider(self.exam)
        result = reconsider(self.exam)
        self.assertIn('nearest-rank', result['message'])
        self.assertEqual(self.exam.history('stakeholder')[:len(before)], before)
    def test_05_global_request_not_opened_by_review(self):
        self.start(); self.exam.ask('모든 요구사항을 전부 알려 주세요')
        with patch.object(self.exam, 'provider', return_value=self.route('all_requirements', ['R05','R06'])):
            value = reconsider(self.exam)
        self.assertNotIn('nearest-rank', value['message'])
        self.assertEqual(self.exam.store.get('disclosed_facts'), [])
    def test_06_post_submit_review_blocked(self):
        self.start(); self.exam.ask('p95 정의는?'); self.exam.submit()
        with self.assertRaises(RuntimeError): reconsider(self.exam)
    def test_07_arbitrary_nonquestion_id_rejected(self):
        self.start(); eid = self.exam.note('decision', '실제 담당자 질문이 아님')
        with self.assertRaises(ValueError): reconsider(self.exam, eid)
    def test_08_changed_route_not_proven_fault(self):
        self.start(); self.exam.ask('누가 사용하나요?')
        with patch.object(self.exam, 'provider', return_value=self.route()): value = reconsider(self.exam)
        self.assertTrue(value['changed_interpretation']); self.assertFalse(value['environment_fault_confirmed'])
        internal = [e for e in self.exam.store.events() if e['kind']=='stakeholder_reconsideration']
        self.assertEqual(len(internal), 1)
        self.assertNotIn(internal[0]['id'], [e['id'] for e in self.exam.store.events(public=True)])


class BoundaryReviewTests(V51Fixture):
    def atoms(self):
        return [{'id':'E1','kind':'event','event_kind':'command_result','channel':'validation','actor':'tool',
                 'content':'실제 실행 결과: 경계 사례 8개를 실행하고 모두 통과했습니다.'}]
    def grade(self, rating=3):
        return {'axis':'technique','criteria':[{'id':'T1','rating':rating,'status':'observed' if rating is not None else 'insufficient_evidence',
            'reason':'합성 채점 근거','confidence':.6,'evidence_ids':['E1']}]}
    def provider(self, outcome=4, mode='ok'):
        content=self.atoms()[0]['content']
        class P:
            def run(inner, prompt, cwd, role, **kw):
                val={'criterion':'T1','reason':'실제 합성 원문과 기준을 대조했습니다.','support':[{'id':'E1','quote':content}],
                     'counter':[],'unobserved':['사람 교정'],'next_action':'실제 기록을 검토하세요.'}
                if role.startswith('boundary-review:'):
                    if mode=='error': raise RuntimeError('synthetic boundary outage')
                    val['rating']=outcome
                else: val['verdict']='supported'
                return {'text':json.dumps(val,ensure_ascii=False),'call_id':'synthetic'}
        return P()
    def test_01_adjacent_scores_can_be_grounded_not_averaged(self):
        with patch.object(self.exam,'provider',return_value=self.provider()):
            v=review_axis(self.exam,self.grade(),{'atoms':self.atoms()},[],self.grade(4))
        c=v['criteria'][0]; self.assertEqual(c['rating'],4); self.assertEqual(c['review_status'],'adjudicated_boundary')
        self.assertEqual(c['repeated_ratings'],[3,4]); self.assertFalse(c['adjudication']['human_calibrated'])
    def test_02_boundary_can_remain_unresolved(self):
        with patch.object(self.exam,'provider',return_value=self.provider(None)):
            c=review_axis(self.exam,self.grade(),{'atoms':self.atoms()},[],self.grade(4))['criteria'][0]
        self.assertIsNone(c['rating']); self.assertEqual(c['hold_reason'],'score_boundary_disagreement')
    def test_03_boundary_outage_is_not_candidate_evidence_failure(self):
        with patch.object(self.exam,'provider',return_value=self.provider(mode='error')):
            c=review_axis(self.exam,self.grade(),{'atoms':self.atoms()},[],self.grade(4))['criteria'][0]
        self.assertEqual(c['hold_reason'],'review_error'); self.assertIn('score_boundary_disagreement',c['hold_reasons'])
    def test_04_large_disagreement_is_not_auto_resolved(self):
        with patch.object(self.exam,'provider',return_value=self.provider()):
            c=review_axis(self.exam,self.grade(1),{'atoms':self.atoms()},[],self.grade(4))['criteria'][0]
        self.assertIsNone(c['rating']); self.assertEqual(c['hold_reason'],'score_disagreement'); self.assertNotIn('adjudication',c)
    def test_05_null_and_numeric_disagreement_has_own_reason(self):
        c=review_axis(self.exam,self.grade(None),{'atoms':self.atoms()},[],self.grade(3))['criteria'][0]
        self.assertEqual(c['hold_reason'],'evidence_disagreement')
    def test_06_forged_or_out_of_range_boundary_cannot_score(self):
        with patch.object(self.exam,'provider',return_value=self.provider(2)):
            c=review_axis(self.exam,self.grade(),{'atoms':self.atoms()},[],self.grade(4))['criteria'][0]
        self.assertIsNone(c['rating']); self.assertEqual(c['hold_reason'],'review_error')
    def test_07_environment_has_a_separate_category(self):
        val={'criterion':'T1','verdict':'environment_blocked','reason':'합성 환경 문제로 귀속을 판단 못함',
             'support':[],'counter':[{'id':'E1','quote':self.atoms()[0]['content']}],
             'unobserved':['실제 모델 품질'],'next_action':'원문을 검토하세요.'}
        with patch.object(self.exam,'provider',return_value=Provider(val)):
            c=review_axis(self.exam,self.grade(),{'atoms':self.atoms()},[])['criteria'][0]
        self.assertEqual(c['hold_reason'],'environment_blocked')


class LiveBenchmarkIntegrationTests(V51Fixture):
    def test_01_review_rejection_reproduces_original_bug_fixed(self):
        self._run_simulated('hold')
    def test_02_review_exception_reproduces_original_bug_fixed(self):
        self._run_simulated('error')
    def _run_simulated(self, mode):
        atoms=BoundaryReviewTests.atoms(self)
        fixture={'judge_cases':[{'id':'synthetic','axis':'technique','criterion':'T1','expected_ratings':[3],'atoms':atoms}],
                 'stakeholder_cases':[]}
        grade={'axis':'technique','criteria':[{'id':c,'rating':3,'status':'observed','confidence':.5,'reason':'합성 초안', 'evidence_ids':['E1']} for c in RUBRIC['technique']['criteria']]}
        fake=type('Live',(),{'root':self.root,'store':self.exam.store,'config':self.exam.config,
                          'session':lambda x:{'mode':'llm'},'prompt':lambda x,name:'synthetic',
                          'provider':lambda x:Provider(grade)})()
        def review(*args,**kw):
            if mode=='error': raise RuntimeError('synthetic review error')
            val=copy.deepcopy(grade)
            for c in val['criteria']: c.update(rating=None,draft_rating=3,review_status='needs_review')
            return val
        with patch('cofa.benchmarks.load',return_value=fixture),patch('cofa.benchmarks._Live',return_value=fake),patch('cofa.benchmarks.review_axis',side_effect=review):
            result=run_quality(self.exam,2,scope='judge')
        self.assertTrue(result['judge'][0]['raw_match'])
        self.assertFalse(result['judge'][0]['matches_authored_fixture'])
        self.assertEqual(result['fixture_match_count'],0)
        self.assertEqual(result['judge'][0]['status'], 'error' if mode=='error' else 'review_hold')

if __name__=='__main__': unittest.main()
