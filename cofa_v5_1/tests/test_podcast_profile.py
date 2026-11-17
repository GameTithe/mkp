"""Offline invariants for the v4.1 interview-derived training profile.
These tests do not claim to evaluate a real model's semantic judgement.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cofa.engine import Exam
from cofa.evaluation import Evidence, Evaluator
from cofa.metrics import normalize_usage, role_group, usage_report
from cofa.preparation import import_preparation, list_preparation
from cofa.stakeholder import apply_disclosure, disclosure_policy, render_choice
from cofa.storage import Store
from test_problem_packs import PackFixture


def choice(action='answer', ids=()):
    return {'action': action, 'fact_ids': list(ids), 'clarify_ids': []}


class DisclosureProfileTests(PackFixture):
    def test_01_default_progressive_and_limit(self):
        self.assertEqual(disclosure_policy({'facts': []})['mode'], 'progressive')
        self.assertEqual(disclosure_policy(self.problem.truth())['max_new_facts_per_turn'], 3)

    def test_02_summary_never_reveals_unasked_facts(self):
        truth = self.problem.truth()
        actual = apply_disclosure(choice('summary'), truth, ['R05'])
        self.assertEqual(actual['fact_ids'], ['R05'])
        self.assertNotIn('[R06]', render_choice(actual, truth))

    def test_03_repeated_global_request_does_not_unlock_rest(self):
        self.start()
        for _ in range(4):
            result = self.exam.ask('모든 요구사항을 알려주세요')
            self.assertNotIn('[R05]', result)
        self.assertEqual(self.exam.store.get('disclosed_facts'), ['B01'])

    def test_04_server_caps_router_overdisclosure(self):
        truth = self.problem.truth()
        routed = choice(ids=[f['id'] for f in truth['facts']])
        actual = apply_disclosure(routed, truth, [])
        self.assertEqual(len(actual['fact_ids']), 3)
        self.assertTrue(actual['deferred'])

    def test_05_previously_known_facts_not_counted_as_new(self):
        truth = self.problem.truth()
        ids = [f['id'] for f in truth['facts']]
        actual = apply_disclosure(choice(ids=ids[:6]), truth, ids[:4])
        self.assertEqual(actual['fact_ids'], ids[:6])
        self.assertFalse(actual['deferred'])

    def test_06_each_fact_reachable_without_secret_prerequisite(self):
        truth = self.problem.truth()
        for fact in truth['facts']:
            actual = apply_disclosure(choice(ids=[fact['id']]), truth, [])
            self.assertEqual(actual['fact_ids'], [fact['id']])
            self.assertIn(fact['answer'], render_choice(actual, truth))

    def test_07_explicit_open_learning_mode_available(self):
        truth = copy.deepcopy(self.problem.truth())
        truth['disclosure']['mode'] = 'open'
        actual = apply_disclosure(choice('all_requirements'), truth, [])
        self.assertEqual(len(actual['fact_ids']), len(truth['facts']))
        self.assertIn('개방 학습 모드', render_choice(actual, truth))

    def test_08_invalid_policy_is_rejected(self):
        for settings in ({'mode': 'all'}, {'max_new_facts_per_turn': 0},
                         {'max_new_facts_per_turn': True}, {'max_new_facts_per_turn': 11}):
            with self.assertRaises(ValueError):
                disclosure_policy({'disclosure': settings})

    def test_09_summary_integration_only_known(self):
        self.start(); self.exam.ask('p95 정의는?')
        result = self.exam.ask('지금까지 확인한 조건을 정리해 주세요')
        self.assertIn('[R05]', result); self.assertNotIn('[R06]', result)
        self.assertEqual(self.exam.store.get('disclosed_facts'), ['R05'])

    def test_10_llm_router_large_response_still_capped(self):
        truth = self.problem.truth()
        class Provider:
            def run(self, *a, **kw):
                return {'text': json.dumps(choice(ids=[f['id'] for f in truth['facts']])), 'call_id': 'too-many'}
        self.start()
        with patch.object(self.exam, 'provider', return_value=Provider()):
            self.exam.ask('업무 조건 여러 개를 확인합니다.')
        self.assertEqual(len(self.exam.store.get('disclosed_facts')), 3)

    def test_11_router_receives_known_context_not_answer_code(self):
        self.start(); self.exam.ask('p95 정의는?'); self.exam.ask('그 기준은?')
        prompt = [e['data']['prompt'] for e in self.exam.store.events()
                  if e['kind'] == 'llm_request' and e['channel'] == 'stakeholder'][-1]
        body = json.loads(prompt.split('ROUTER_INPUT_JSON\n')[1])
        self.assertEqual(body['known_fact_ids'], ['R05'])
        self.assertEqual(body['disclosure']['mode'], 'progressive')
        self.assertNotIn('framescope.py', prompt)

    def test_12_summary_empty_does_not_manufacture_requirement(self):
        self.start(); result = self.exam.ask('지금까지 확인한 조건을 요약해 주세요')
        self.assertIn('아직 함께 확인한 조건이 없습니다', result)
        self.assertEqual(self.exam.store.get('disclosed_facts', []), [])


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.store = Store(Path(self.tmp.name))
    def tearDown(self): self.tmp.cleanup()
    def call(self, name, role='main', usage=None, stream=False, final=True, demo=False):
        self.store.event('llm_request', role, 'internal', call_id=name, demo=demo)
        if stream:
            self.store.event('codex_event', role, 'internal', call_id=name,
                             event={'type': 'turn.completed', 'usage': usage})
        if final:
            self.store.event('llm_complete', role, 'internal', call_id=name, usage=usage,
                             returncode=0, timed_out=False, demo=demo)
    def test_01_stream_and_final_summary_not_double_counted(self):
        self.call('one', usage={'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 70}, stream=True)
        dev=usage_report(self.store)['groups']['development']
        self.assertEqual(dev['known_total_tokens'],120)
        self.assertEqual(dev['known_cached_input_tokens'],70)
        self.assertEqual(dev['calls'],1)
    def test_02_roles_separate_candidate_and_exam_overhead(self):
        for i, role in enumerate(('main','worker:tester','auditor','stakeholder','interviewer','evidence_mapper','judge:intent','doctor')):
            self.call(str(i),role,{'input_tokens':10,'output_tokens':2})
        groups=usage_report(self.store)['groups']
        self.assertEqual(groups['development']['known_total_tokens'],36)
        self.assertEqual(groups['assessment']['known_total_tokens'],48)
        self.assertEqual(groups['setup']['known_total_tokens'],12)
        self.store.event('submission')
        self.call('automatic-audit','auditor',{'input_tokens':10,'output_tokens':2})
        groups=usage_report(self.store)['groups']
        self.assertEqual(groups['development']['known_total_tokens'],36)
        self.assertEqual(groups['assessment']['known_total_tokens'],60)
    def test_03_missing_usage_is_unknown_not_zero(self):
        self.call('one')
        report=usage_report(self.store)
        self.assertIsNone(report['groups']['development']['known_total_tokens'])
        self.assertFalse(report['groups']['development']['usage_complete'])
        self.assertIsNone(report['cost_amount']); self.assertIsNone(report['ability_score'])
    def test_04_malformed_counts_not_accepted(self):
        for incoming in (True,-1,'123',1.5,None):
            self.assertIsNone(normalize_usage({'input_tokens':incoming,'output_tokens':2}))
        self.assertIsNone(normalize_usage({'input_tokens':10,'output_tokens':2,'cached_input_tokens':11}))
    def test_05_partial_stream_reports_known_sum_and_missing_turn(self):
        self.call('one',usage={'input_tokens':10,'output_tokens':2},stream=True)
        self.store.event('codex_event','main','internal',call_id='one',event={'type':'turn.completed','usage':None})
        dev=usage_report(self.store)['groups']['development']
        self.assertEqual(dev['known_total_tokens'],12)
        self.assertEqual(dev['unmeasured_turns'],1);self.assertFalse(dev['usage_complete'])
    def test_06_demo_is_not_real_usage(self):
        self.call('one',usage={'input_tokens':10,'output_tokens':2},demo=True)
        dev=usage_report(self.store)['groups']['development']
        self.assertIsNone(dev['known_total_tokens']);self.assertEqual(dev['demo_calls'],1)
    def test_07_preflight_before_session_excluded(self):
        self.call('doctor','doctor',{'input_tokens':100,'output_tokens':20})
        self.store.event('session_start')
        self.call('main',usage={'input_tokens':10,'output_tokens':2})
        report=usage_report(self.store)
        self.assertEqual(len(report['calls']),1);self.assertEqual(report['groups']['setup']['calls'],0)
    def test_08_nested_cache_is_subset(self):
        result=normalize_usage({'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':40}})
        self.assertEqual(result['total_tokens'],110);self.assertEqual(result['cached_input_tokens'],40)
    def test_09_multiple_turns_accumulate(self):
        self.call('one',usage={'input_tokens':10,'output_tokens':2},stream=True)
        self.store.event('codex_event','main','internal',call_id='one',event={'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':4}})
        self.assertEqual(usage_report(self.store)['groups']['development']['known_total_tokens'],36)
    def test_10_unfinished_call_not_falsely_successful(self):
        self.call('one',final=False)
        self.assertEqual(usage_report(self.store)['groups']['development']['unfinished_calls'],1)


class PreparationAndEvidenceTests(PackFixture):
    def kit(self):
        source=Path(self.tmp.name)/'kit';source.mkdir(exist_ok=True)
        (source/'rules.md').write_text('# Verify\nRun small boundary tests.',encoding='utf-8')
        return source
    def import_kit(self,source):
        return import_preparation(self.exam,str(source),'my-kit','본인 작성','경계 검사 지침')
    def test_01_text_copied_and_hash_recorded(self):
        src=self.kit(); result=self.import_kit(src)
        self.assertFalse(result['auto_executed'])
        self.assertEqual(len(result['files']),1)
        self.assertEqual((self.exam.workspace/'prepared/my-kit/rules.md').read_bytes(),(src/'rules.md').read_bytes())
        self.assertEqual(list_preparation(self.exam)['kits'][0]['origin'],'본인 작성')
    def test_02_nontext_scripts_not_executed_or_copied(self):
        src=self.kit();(src/'run.py').write_text('raise Exception("do not execute")')
        result=self.import_kit(src)
        self.assertEqual(result['ignored_nontext_files'],['run.py'])
        self.assertFalse((self.exam.workspace/'prepared/my-kit/run.py').exists())
    def test_03_after_start_import_blocked(self):
        src=self.kit();self.start()
        with self.assertRaises(RuntimeError):self.import_kit(src)
    def test_04_existing_kit_not_overwritten(self):
        src=self.kit();self.import_kit(src)
        with self.assertRaises(ValueError):self.import_kit(src)
    def test_05_symlink_rejected(self):
        src=self.kit()
        try:(src/'link.md').symlink_to(src/'rules.md')
        except OSError:self.skipTest('symlink unavailable')
        with self.assertRaises(ValueError):self.import_kit(src)
        (src/'link.md').unlink()
        outside=Path(self.tmp.name)/'outside';outside.mkdir()
        (self.exam.workspace/'prepared').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):self.import_kit(src)
        self.assertEqual(list(outside.iterdir()),[])
    def test_06_obvious_tokens_and_env_files_rejected(self):
        src=self.kit();(src/'rules.md').write_text('sk-'+('X'*30))
        with self.assertRaises(ValueError):self.import_kit(src)
        (src/'rules.md').write_text('clean');(src/'.env').write_text('SOME_KEY=not-secret')
        with self.assertRaises(ValueError):self.import_kit(src)
    def test_07_start_freezes_declared_preparation(self):
        self.import_kit(self.kit());self.start()
        state=self.exam.session()
        self.assertEqual(state['preparation']['kits'][0]['label'],'my-kit')
        self.assertIn('prepared/my-kit/rules.md',state['initial_workspace_manifest'])
    def test_08_current_exam_folder_not_importable(self):
        with self.assertRaises(ValueError):self.import_kit(self.exam.workspace)
    def test_09_empty_origin_or_purpose_rejected(self):
        src=self.kit()
        for origin,purpose in (('', 'x'),('x','')):
            with self.assertRaises(ValueError):import_preparation(self.exam,str(src),'my-kit',origin,purpose)
    def test_10_telemetry_alone_does_not_score(self):
        bundle={'atoms':[{'id':'USAGE:development:p1','kind':'telemetry','actor':'tool'}]}
        obj={'axis':'technique','criteria':[{'id':'T'+str(i),'rating':4,'status':'observed','reason':'few tokens',
                 'evidence_ids':['USAGE:development:p1'],'confidence':1} for i in range(1,4)]}
        out=Evaluator(self.exam).validate_axis(obj,'technique',bundle)
        self.assertTrue(all(c['rating'] is None for c in out['criteria']))
    def test_11_long_resource_evidence_is_chunked_without_loss(self):
        self.start();self.exam.submit()
        for i in range(90):
            self.exam.store.event('llm_complete','main','internal',call_id='long-call-'+str(i),returncode=0,
                                  usage={'input_tokens':10,'output_tokens':2})
        bundle=Evidence(self.exam).build()
        parts=[a for a in bundle['atoms'] if a['kind']=='telemetry']
        self.assertGreater(len(parts),1);self.assertTrue(all(len(a['content'])<=7000 for a in parts))
        usage=json.loads(''.join(a['content'] for a in parts))
        self.assertEqual(len(usage['calls']),90)
    def test_12_demo_resources_present_and_no_cost_guessed(self):
        self.start();self.exam.chat('계획만 설명')
        report=self.exam.usage()
        self.assertEqual(report['groups']['development']['demo_calls'],1)
        self.assertEqual(report['calls'][0]['state'],'completed')
        self.assertIsNone(report['cost_amount'])


if __name__=='__main__': unittest.main()
