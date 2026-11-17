"""v5 invariants and workflow tests. Mocks check enforcement, not real LLM quality."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from test_problem_packs import PackFixture
from cofa.contextual import render_route,validate_route,confirm,link
from cofa.evaluation import Evidence
from cofa.quality import validate_review,review_axis
from cofa.feedback import generate,chat,validate_feedback,trace_requirements
from cofa.benchmarks import validate as quality_validate,run as quality_run
from cofa.prediction import probes
from cofa.storage import write_json

class Provider:
    def __init__(self,obj):self.obj=obj;self.prompts=[]
    def run(self,prompt,*a,**kw):
        self.prompts.append(prompt)
        return {'text':json.dumps(self.obj,ensure_ascii=False),'call_id':'mock-v5','thread_id':'test','usage':{}}

class V5Fixture(PackFixture):
    def setUp(self):
        super().setUp()
        self.exam.config=json.loads((self.root/'config.json').read_text())
    def done(self,reference=False):
        if reference:self.install_reference()
        self.start();self.exam.ask('누가 사용하나요?');self.exam.ask('p95 정의는 무엇인가요?')
        self.exam.submit();self.exam.prepare_interview()
        self.exam.answer('제출 구조의 이유에 관한 응시자 본인 답변입니다.')
        self.exam.answer('출력은 입력을 검증한 후의 통계 객체로 예상합니다.')
        self.exam.answer('실행 결과와 예측을 비교하였고 미구현일 수 있습니다.')
        self.exam.finish_interview()
        return self.exam.evaluate()

class ContextTests(V5Fixture):
    def test_01_new_defaults(self):
        self.assertEqual(self.exam.config['stakeholder_profile'],'contextual');self.assertEqual(self.exam.config['judge_repetitions'],2)
    def test_02_overview_is_not_spec(self):
        route={'intent':'all_requirements','fact_ids':[f['id'] for f in self.problem.truth()['facts']],'rationale':'global'}
        text,ids,_=render_route(route,self.problem.truth(),[])
        self.assertEqual(ids,[]);self.assertNotIn('10%',text);self.assertNotIn('nearest-rank',text)
    def test_03_workflow_does_not_expose_threshold(self):
        text,ids,_=render_route({'intent':'workflow','fact_ids':['R06'],'rationale':'workflow'},self.problem.truth(),[])
        self.assertEqual(ids,[]);self.assertNotIn('2ms',text)
    def test_04_specific_rule_directly_reachable(self):
        for f in self.problem.truth()['facts']:
            text,ids,_=render_route({'intent':'rule','fact_ids':[f['id']],'rationale':'specific'},self.problem.truth(),[])
            self.assertEqual(ids,[f['id']]);self.assertIn(f['answer'],text);self.assertNotIn('['+f['id']+']',text)
    def test_05_summary_only_actual_conversation(self):
        self.start();self.exam.ask('p95 정의는?');answer=self.exam.ask('지금까지 확인한 조건을 요약해 주세요')
        self.assertIn('nearest-rank',answer);self.assertNotIn('10%',answer)
    def test_06_no_auto_answer_document(self):
        self.start();self.exam.ask('p95 정의는?')
        self.assertFalse((self.exam.workspace/'REQUIREMENTS_CONFIRMED.md').exists())
        self.assertEqual(self.exam.store.get('disclosed_facts'),['R05'])
    def test_07_internal_decision_not_in_candidate_events(self):
        self.start();self.exam.ask('p95 정의는?')
        self.assertTrue(any(e['kind']=='stakeholder_decision' for e in self.exam.store.events()))
        self.assertFalse(any(e['kind']=='stakeholder_decision' for e in self.exam.store.events(public=True)))
    def test_08_no_code_in_router_input(self):
        self.start();self.exam.ask('p95 정의는?')
        prompt=[e['data']['prompt'] for e in self.exam.store.events() if e['kind']=='llm_request'][-1]
        self.assertNotIn('framescope.py',prompt);self.assertNotIn('INTERVIEW_GUIDE',prompt)
    def test_09_invalid_route_rejected(self):
        truth=self.problem.truth()
        for x in ({'intent':'rule','fact_ids':[],'rationale':'x'}, {'intent':'rule','fact_ids':['FAKE'],'rationale':'x'}, {'intent':'unknown','fact_ids':[],'rationale':'x'}):
            with self.assertRaises(ValueError):validate_route(x,truth)
    def test_10_cap_still_applies_to_unbounded_router(self):
        text,ids,deferred=render_route({'intent':'rule','fact_ids':[f['id'] for f in self.problem.truth()['facts']],'rationale':'many'},self.problem.truth(),[])
        self.assertEqual(len(ids),3);self.assertTrue(deferred)
    def test_11_model_failure_leaves_auditable_event(self):
        self.start()
        with patch.object(self.exam,'provider',return_value=Provider({'garbage':True})):
            with self.assertRaises(RuntimeError):self.exam.ask('정확한 기준을 알려 주세요')
        self.assertTrue(any(e['kind']=='stakeholder_invalid_output' for e in self.exam.store.events()))
    def test_12_first_simple_question_receives_context(self):
        self.start();self.assertIn('QA',self.exam.ask('누가 사용하나요?'))

class ConfirmationTests(V5Fixture):
    def response(self,status='confirmed',ids=['R05']):return Provider({'items':[{'index':0,'status':status,'fact_ids':ids}]})
    def test_01_candidate_claim_is_preserved(self):
        self.start();text='프레임 시간은 max(cpu_ms, gpu_ms)로 이해했습니다.'
        with patch.object(self.exam,'provider',return_value=self.response()):result=confirm(self.exam,text)
        self.assertEqual(result['items'][0]['text'],text);self.assertEqual(result['items'][0]['status'],'confirmed')
        self.assertNotIn('nearest-rank',(self.exam.workspace/'REQUIREMENTS_REVIEW.md').read_text())
        self.assertEqual(self.exam.store.get('disclosed_facts'),[])
    def test_02_wrong_assumption_corrected_not_agreed(self):
        self.start()
        with patch.object(self.exam,'provider',return_value=self.response('contradicted',['R03'])):result=confirm(self.exam,'중복은 마지막 행을 씁니다.')
        self.assertEqual(result['items'][0]['status'],'contradicted');self.assertIn('처음',result['message'])
    def test_03_unspecified_is_not_approved(self):
        self.start()
        with patch.object(self.exam,'provider',return_value=self.response('unspecified',[])):r=confirm(self.exam,'로그를 외국 업체에 맡기기로 계약되어 있습니다.')
        self.assertEqual(r['items'][0]['status'],'unspecified')
    def test_04_missing_item_rejected(self):
        self.start()
        with patch.object(self.exam,'provider',return_value=Provider({'items':[]})):
            with self.assertRaises(ValueError):confirm(self.exam,'주장 하나')
    def test_05_unknown_fact_rejected(self):
        self.start()
        with patch.object(self.exam,'provider',return_value=self.response('confirmed',['BAD'])):
            with self.assertRaises(ValueError):confirm(self.exam,'주장 하나')
    def test_06_post_submit_confirmation_blocked(self):
        self.start();self.exam.submit()
        with self.assertRaises(RuntimeError):confirm(self.exam,'확인 요청')
    def test_07_demo_never_claims_semantic_understanding(self):
        self.start();r=confirm(self.exam,'p95는 nearest-rank입니다.')
        self.assertEqual(r['items'][0]['status'],'ambiguous')
    def test_08_link_requires_actual_event(self):
        self.start();confirm(self.exam,'가정입니다.')
        with self.assertRaises(ValueError):link(self.exam,'Q001','tested','FAKE')
        eid=self.exam.note('plan','중복 검증 함수를 분리합니다.')
        r=link(self.exam,'Q001','planned',eid)
        self.assertEqual(r['status'],'candidate_link_pending_evaluation')
    def test_09_many_corrections_are_deferred_not_approved(self):
        self.start()
        with patch.object(self.exam,'provider',return_value=self.response('contradicted',['R02','R03','R04','R05'])):r=confirm(self.exam,'이 조건들은 전부 임의로 바꿉니다.')
        self.assertEqual(r['items'][0]['status'],'deferred');self.assertNotIn('nearest-rank',r['message'])
    def test_10_multi_item_size_limit(self):
        self.start()
        with self.assertRaises(ValueError):confirm(self.exam,'x\n'*13)

class EvidenceReviewTests(V5Fixture):
    def atoms(self):
        return {'E1':{'id':'E1','content':'실제 실행 결과: 경계 테스트 3개 통과.','event_kind':'command_result','kind':'event','channel':'validation','actor':'tool'},
                'E2':{'id':'E2','content':'제가 예측한 count는 1이며 먼저 읽은 행이 사용됩니다.','kind':'event','channel':'interview','actor':'candidate'}}
    def review(self,criterion='T1',verdict='supported',support=None):
        return {'criterion':criterion,'verdict':verdict,'reason':'실제 기록과 판단을 대조했습니다.',
          'support':support if support is not None else [{'id':'E1','quote':'실제 실행 결과: 경계 테스트 3개 통과.'}],
          'counter':[],'unobserved':['대규모 입력 성능'],'next_action':'큰 입력을 별도로 실행하고 결과를 기록하세요.'}
    def grade(self,criterion='T1',rating=3):
        return {'axis':'cognition' if criterion.startswith('C') else 'technique','criteria':[{'id':criterion,'rating':rating,'status':'observed','confidence':.8,'reason':'실행 근거를 확인했습니다.','evidence_ids':['E1','E2']}]}
    def test_01_real_quote_accepted(self):self.assertEqual(validate_review(self.review(),'T1',self.atoms())['verdict'],'supported')
    def test_02_fabricated_quote_rejected(self):
        obj=self.review(support=[{'id':'E1','quote':'없는 성공 결과를 만들어 냈습니다.'}])
        with self.assertRaises(ValueError):validate_review(obj,'T1',self.atoms())
    def test_03_unknown_reference_rejected(self):
        obj=self.review(support=[{'id':'BAD','quote':'실제 실행 결과: 경계 테스트 3개 통과.'}])
        with self.assertRaises(ValueError):validate_review(obj,'T1',self.atoms())
    def test_04_supported_requires_evidence(self):
        with self.assertRaises(ValueError):validate_review(self.review(support=[]),'T1',self.atoms())
    def test_05_environment_claim_requires_counter(self):
        with self.assertRaises(ValueError):validate_review(self.review(verdict='environment_blocked'),'T1',self.atoms())
    def test_06_repeated_scores_not_averaged(self):
        grade=self.grade();alt=self.grade(rating=4)
        with patch.object(self.exam,'provider',return_value=Provider(self.review())):
            value=review_axis(self.exam,grade,{'atoms':list(self.atoms().values())},[],alt)
        c=value['criteria'][0];self.assertIsNone(c['rating']);self.assertEqual(c['repeated_ratings'],[3,4]);self.assertEqual(c['review_status'],'repeat_disagreement')
    def test_07_agreement_and_support_retains_rating(self):
        grade=self.grade()
        with patch.object(self.exam,'provider',return_value=Provider(self.review())):value=review_axis(self.exam,grade,{'atoms':list(self.atoms().values())},[],self.grade())
        self.assertEqual(value['criteria'][0]['rating'],3)
    def test_08_no_run_no_high_T1(self):
        atoms=self.atoms();atoms['E1']['event_kind']='message'
        with patch.object(self.exam,'provider',return_value=Provider(self.review())):r=review_axis(self.exam,self.grade(),{'atoms':list(atoms.values())},[])
        self.assertIsNone(r['criteria'][0]['rating'])
    def test_09_C2_needs_human_quote_not_just_ref(self):
        with patch.object(self.exam,'provider',return_value=Provider(self.review(criterion='C2'))):r=review_axis(self.exam,self.grade('C2'),{'atoms':list(self.atoms().values())},[])
        self.assertIsNone(r['criteria'][0]['rating'])
    def test_10_malformed_verifier_holds_not_scores(self):
        with patch.object(self.exam,'provider',return_value=Provider({'garbage':True})):r=review_axis(self.exam,self.grade(),{'atoms':list(self.atoms().values())},[])
        self.assertIsNone(r['criteria'][0]['rating'])
    def test_11_context_limit_holds_without_cutting(self):
        atoms=self.atoms();atoms['E1']['content']='long'*30000
        r=review_axis(self.exam,self.grade(),{'atoms':list(atoms.values())},[])
        self.assertIsNone(r['criteria'][0]['rating']);self.assertEqual(r['criteria'][0]['semantic_review']['reason'],'context_limit')
    def test_12_supported_negative_grade_can_be_zero(self):
        with patch.object(self.exam,'provider',return_value=Provider(self.review())):r=review_axis(self.exam,self.grade(rating=0),{'atoms':list(self.atoms().values())},[])
        self.assertEqual(r['criteria'][0]['rating'],0)

class PredictionAndFeedbackTests(V5Fixture):
    def test_01_authored_probes_valid(self):self.assertEqual(len(probes(self.problem)),2)
    def test_02_prediction_after_first_human_answer(self):
        self.start();self.exam.submit();self.exam.prepare_interview();q=self.exam.answer('구조 설명입니다.')
        self.assertEqual(q['kind'],'prediction');self.assertFalse(self.exam.store.get('prediction_results'))
    def test_03_execution_only_after_prediction_and_frozen_unchanged(self):
        self.install_reference();self.start();self.exam.submit();before=self.exam.verify_submission();self.exam.prepare_interview();self.exam.answer('구조 설명')
        q=self.exam.answer('정확한 경계의 결과를 예상합니다.')
        self.assertEqual(q['kind'],'prediction_reflection');self.assertEqual(self.exam.store.get('prediction_results')[0]['returncode'],0)
        self.assertEqual(self.exam.verify_submission(),before)
    def test_04_probe_generation_blocked_before_submission(self):
        self.start()
        with self.assertRaises(RuntimeError):self.exam.prepare_interview()
    def test_05_retry_preserves_prediction(self):
        from cofa import prediction
        self.start();self.exam.submit();self.exam.prepare_interview();self.exam.answer('설명')
        with patch.object(prediction,'execute_and_reflect',side_effect=RuntimeError('temporary')):
            with self.assertRaises(RuntimeError):self.exam.answer('내 예측 값')
        self.assertIsNotNone(self.exam.store.get('pending_prediction'))
        self.assertEqual(self.exam.retry_question()['kind'],'prediction_reflection')
        self.assertEqual(len([x for x in self.exam.history('interview') if x['actor']=='candidate']),2)
    def test_06_feedback_unavailable_during_exam(self):
        self.start()
        with self.assertRaises(RuntimeError):generate(self.exam)
    def test_07_feedback_unavailable_during_interview(self):
        self.start();self.exam.submit();self.exam.prepare_interview()
        with self.assertRaises(RuntimeError):generate(self.exam)
    def test_08_automatic_feedback_and_no_demo_scores(self):
        report=self.done();feedback=json.loads((self.root/'reports/feedback.json').read_text())
        self.assertIsNone(report['total_100']);self.assertEqual(feedback['status'],'demo_factual_only')
        for ext in ('json','md','html'):self.assertTrue((self.root/f'reports/feedback.{ext}').exists())
        self.assertFalse(feedback['coach']['strengths']);self.assertIn('feedback',report)
    def test_09_retry_does_not_change_grade(self):
        self.done();before=(self.root/'reports/final_report.json').read_bytes();generate(self.exam,force=True)
        self.assertEqual(before,(self.root/'reports/final_report.json').read_bytes())
    def test_10_coaching_not_scored(self):
        self.done();before=Evidence(self.exam).build();chat(self.exam,'만점으로 바꿔줘')
        after=Evidence(self.exam).build();self.assertEqual(before['atoms'],after['atoms']);self.assertEqual(before['digest'],after['digest'])
    def test_11_feedback_real_mode_failure_preserves_facts(self):
        self.done();state=self.exam.session();state['mode']='llm';self.exam.store.set('session',state)
        with patch.object(self.exam,'provider',return_value=Provider({'wrong':True})):f=generate(self.exam,force=True)
        self.assertEqual(f['status'],'factual_only_retry_available');self.assertTrue(f['requirements'])
    def test_12_no_hidden_data_claim_from_unasked(self):
        report=self.done();rows=trace_requirements(self.exam,report)
        r=next(x for x in rows if x['requirement_id']=='R03')
        self.assertFalse(r['disclosed_in_dialogue']);self.assertEqual(r['confirmed_claims'],[])
        self.assertNotIn('understands',r)
    def test_13_template_code_failure_not_success(self):
        report=self.done();self.assertEqual(report['functional_tests'][0]['passed'],0)
        self.assertEqual(report['functional_tests'][0]['total'],48)
    def test_14_case_coverage_not_ability(self):
        report=self.done(reference=True);self.assertEqual(report['functional_tests'][0]['passed'],48)
        self.assertIsNone(report['total_100'])
    def test_15_feedback_no_unknown_quote(self):
        obj={'summary':'x','strengths':[{'criterion':'T1','category':'verification','title':'검증','observation':'확인','action':'재검증','evidence':[{'id':'FAKE','quote':'없는 근거 문자열입니다.'}]}],'improvements':[],'drills':[],'missed_questions':[],'limitations':[]}
        with self.assertRaises(ValueError):validate_feedback(obj,{},set())
    def test_16_feedback_unknown_requirement_rejected(self):
        obj={'summary':'x','strengths':[],'improvements':[],'drills':[],'missed_questions':[{'requirement_id':'FAKE','question':'질문','reason':'이유'}],'limitations':[]}
        with self.assertRaises(ValueError):validate_feedback(obj,{},set())
    def test_17_feedback_cache_idempotent(self):
        self.done();a=generate(self.exam);b=generate(self.exam);self.assertEqual(a['created_at'],b['created_at'])
    def test_18_probe_invalid_args_rejected(self):
        p=self.problem.path/'private/interview_probes.json';d=json.loads(p.read_text());d['probes'][0]['args']=['--input','BAD'];write_json(p,d)
        with self.assertRaises(ValueError):self.problem.validate()

class QualityFixtureTests(V5Fixture):
    def test_01_fixture_count_and_no_human_claim(self):
        d=quality_validate(self.exam);self.assertEqual(d['judge_cases'],12);self.assertEqual(d['stakeholder_cases'],24);self.assertFalse(d['human_reviewed'])
    def test_02_live_benchmark_blocked_mid_exam(self):
        self.start()
        with self.assertRaises(RuntimeError):quality_run(self.exam)
    def test_03_not_claimed_human_calibration(self):
        p=self.root/'quality/benchmarks.json';d=json.loads(p.read_text());d['human_reviewed']=True;write_json(p,d)
        with self.assertRaises(ValueError):quality_validate(self.exam)
    def test_04_feedback_ui_and_routing_exist(self):
        page=(self.root/'web/index.html').read_text();self.assertIn('data-tab="feedback"',page);self.assertIn('data-tab="requirements"',page);self.assertIn('최종 평가 + 피드백',page);self.assertIn('id="open-cli"',page);self.assertNotIn('data-tab="main"',page)

class StructuredPipelineTests(V5Fixture):
    def test_01_complete_v5_pipeline_with_explicit_synthetic_provider(self):
        """Checks actual schema/quote/gating integration, NOT human score validity."""
        import re
        from cofa.rubric import RUBRIC
        self.start();self.exam.submit();self.exam.prepare_interview()
        self.exam.answer('제 코드의 구조를 직접 설명한 합성 응시자 답변입니다.')
        self.exam.finish_interview()
        state=self.exam.session();state['mode']='llm';self.exam.store.set('session',state)
        calls=[]
        class Synthetic:
            def run(inner,prompt,cwd,role,**kw):
                calls.append(role);name=kw['schema'].name
                if name=='evidence_map.json':
                    body=json.loads(prompt.split('<untrusted_evidence>\n')[1].split('\n</untrusted_evidence>')[0])
                    chosen=[a['id'] for a in body if a.get('event_kind')=='command_result' or (a.get('channel')=='interview' and a.get('actor')=='candidate')]
                    value={'findings':[{'criterion':c,'observation':'SYNTHETIC TEST ONLY','evidence_ids':chosen[:3],'polarity':'positive','actor':'mixed'} for axis in RUBRIC.values() for c in axis['criteria']] if chosen else [],'limitations':['test double; not a real assessment']}
                elif name=='axis_score.json':
                    axis=re.search(r'AXIS=(\w+)',prompt).group(1)
                    body=json.loads(prompt.split('<untrusted_evidence>\n')[1].split('\n</untrusted_evidence>')[0])
                    atoms=body['raw_excerpts']
                    chosen=[a['id'] for a in atoms if (a.get('channel')=='interview' and a.get('actor')=='candidate')] if axis=='cognition' else [a['id'] for a in atoms if a.get('event_kind')=='command_result']
                    assert chosen
                    value={'axis':axis,'criteria':[{'id':c,'rating':3,'status':'observed','confidence':.5,'reason':'SYNTHETIC TEST ONLY','evidence_ids':chosen[:1]} for c in RUBRIC[axis]['criteria']],'limitations':['synthetic']}
                elif name=='evidence_review.json':
                    body=json.loads(prompt.split('REVIEW_INPUT_JSON\n')[1]);c=body['draft']['id']
                    atoms=body['raw_evidence']
                    a=next(a for a in atoms if (a.get('channel')=='interview' and a.get('actor')=='candidate')) if c.startswith('C') else next(a for a in atoms if a.get('event_kind')=='command_result')
                    value={'criterion':c,'verdict':'supported','reason':'SYNTHETIC TEST ONLY','support':[{'id':a['id'],'quote':a['content'][:40]}],'counter':[],'unobserved':['real model quality'],'next_action':'실제 응시 자료를 사람 기준과 대조하세요.'}
                elif name=='post_feedback.json':
                    body=json.loads(prompt.split('FEEDBACK_INPUT_JSON\n')[1]);a=body['context']['raw_excerpts'][0]
                    value={'summary':'SYNTHETIC TEST ONLY: feedback connection checked.','strengths':[{'criterion':'T1','category':'verification','title':'합성 검증 근거','observation':'실제 성과 평가가 아닌 연결 검사입니다.','action':'다음은 사용자 계정으로 확인하세요.','evidence':[{'id':a['id'],'quote':a['content'][:40]}]}],'improvements':[],'missed_questions':[],'drills':[],'limitations':['synthetic fixture, not a human rating']}
                else:raise AssertionError(name)
                return {'text':json.dumps(value,ensure_ascii=False),'call_id':'synthetic-v5','usage':{}}
        with patch.object(self.exam,'provider',return_value=Synthetic()):
            report=self.exam.evaluate()
            self.assertEqual(report['total_100'],75.0)
            self.assertEqual(report['feedback']['status'],'complete')
            frozen=(self.root/'reports/final_report.json').read_bytes()
            f=generate(self.exam,force=True)
            self.assertEqual(f['status'],'complete');self.assertTrue(f['coach']['strengths'])
            self.assertEqual(frozen,(self.root/'reports/final_report.json').read_bytes())
        self.assertEqual(sum(x.startswith('judge:') for x in calls),6)
        self.assertEqual(sum(x.startswith('evidence-review:') for x in calls),9)
        for axis in report['axes'].values():
            for c in axis['criteria']:self.assertEqual(c['review_status'],'verified')

    def test_02_feedback_rejects_another_assessment_bundle(self):
        self.done();path=self.root/'reports/final_report.json'
        obj=json.loads(path.read_text());obj['session_id']='other-session';write_json(path,obj)
        with self.assertRaises(RuntimeError):generate(self.exam)
