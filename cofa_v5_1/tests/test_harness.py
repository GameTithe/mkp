from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from cofa.codex import Codex, ProviderError
from cofa.engine import Exam
from cofa.evaluation import Evidence, Evaluator, validate_refs, parse_json, redact
from cofa.fs import copy_workspace, inventory, merge_workspace, safe_path
from cofa.storage import Store, write_json
from cofa.rubric import RUBRIC

SOURCE = Path(__file__).resolve().parents[1]
FAKE = SOURCE / 'tests' / 'fake_codex.py'


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'exam'
        shutil.copytree(SOURCE, self.root, ignore=shutil.ignore_patterns(
            'tests', 'logs', 'analysis', 'reports', 'submissions', 'runtime', 'agent_runs', '__pycache__', '*.pyc'))
        self.exam = Exam(self.root)
        # Legacy compatibility protocol; v5 defaults are covered in test_v5_features.py.
        self.exam.config.update(stakeholder_profile='legacy',semantic_evidence_review=False,judge_repetitions=1,interview_prediction=False,auto_feedback=False)

    def tearDown(self):
        self.temp.cleanup()

    def start_demo(self):
        self.exam.start('demo')

    def start_fake(self):
        self.exam.config['codex_command'] = [sys.executable, str(FAKE)]
        self.exam.config['call_timeout_seconds'] = 3
        self.exam.start('llm')

    def complete_demo(self):
        self.start_demo()
        self.exam.ask('사용자와 입력 오류 처리 조건은?')
        self.exam.chat('계획만 검토하라')
        self.exam.submit()
        self.exam.prepare_interview()
        self.exam.answer('내가 제출한 구현은 아직 없으므로 기능 검증은 실패한 상태입니다.')
        self.exam.finish_interview()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name); self.store = Store(self.root)
    def tearDown(self): self.tmp.cleanup()

    def test_01_concurrent_events_are_complete(self):
        def record(n):
            for i in range(30): self.store.event('sample', 'test', worker=n, number=i)
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(record, range(4)))
        self.assertEqual(len(self.store.events()), 120)
        self.assertTrue(self.store.verify()['ok'])

    def test_02_hash_chain_detects_modification(self):
        self.store.event('a', text='original')
        with sqlite3.connect(self.store.path) as db: db.execute("UPDATE events SET data='{}'")
        db.close()
        self.assertFalse(self.store.verify()['ok'])

    def test_03_full_unicode_export(self):
        text = '한국어 🧪\n' * 4000
        self.store.event('message', text=text)
        exported = json.loads(self.store.export().read_text(encoding='utf-8'))
        self.assertEqual(exported['data']['text'], text)

    def test_04_internal_event_visibility(self):
        self.store.event('private', visibility='internal', text='private')
        self.store.event('public', text='public')
        self.assertEqual(len(self.store.events(public=True)), 1)
        self.assertEqual(len(self.store.events()), 2)

    def test_05_operation_lock_rejects_concurrent_mutator(self):
        with self.store.operation():
            with self.assertRaises(RuntimeError):
                with Store(self.root).operation(): pass


class LifecycleTests(Fixture):
    def test_01_exact_default_limits(self):
        self.start_demo(); s = self.exam.session()
        self.assertEqual(s['deadline'] - s['started_at'], 7200)
        self.assertEqual(s['config']['interview_minutes'], 30)

    def test_02_start_does_not_reset_existing_session(self):
        self.start_demo(); original = self.exam.session()
        with self.assertRaises(RuntimeError): self.exam.start('demo')
        self.assertEqual(self.exam.session(), original)

    def test_03_deadline_survives_restart_and_blocks_work(self):
        self.start_demo(); s = self.exam.session(); s['deadline'] = time.time() - 2; self.exam.store.set('session', s)
        restarted = Exam(self.root)
        self.assertTrue(restarted.status()['development_expired'])
        with self.assertRaises(RuntimeError): restarted.chat('late')
        self.assertFalse(restarted.submit()['within_time'])

    def test_04_seed_is_not_accepted_as_solution(self):
        self.start_demo(); result = self.exam.check()
        self.assertFalse(result[0]['passed'])
        self.assertEqual(result[1]['status'], 'not_configured')

    def test_05_submission_uses_copy_not_live_workspace(self):
        self.start_demo(); p = self.exam.workspace / 'sample.txt'; p.write_text('before')
        self.exam.submit(); p.write_text('after')
        frozen = self.exam.verify_submission()
        self.assertEqual((frozen / 'workspace/sample.txt').read_text(), 'before')

    def test_06_changed_frozen_source_blocks_evaluation(self):
        self.start_demo(); self.exam.submit(); frozen = self.exam.verify_submission()
        (frozen / 'workspace/README.md').write_text('tamper')
        with self.assertRaises(RuntimeError): self.exam.verify_submission()

    def test_07_changed_manifest_blocks_evaluation(self):
        self.start_demo(); self.exam.submit(); frozen = self.exam.verify_submission()
        (frozen / 'submission.json').write_text('{}')
        with self.assertRaises(RuntimeError): self.exam.verify_submission()

    def test_08_post_submit_development_is_blocked(self):
        self.start_demo(); self.exam.submit()
        for callback in (lambda: self.exam.chat('a'), lambda: self.exam.ask('a'),
                         lambda: self.exam.save_file('x.py', 'a'), lambda: self.exam.run(['echo', 'x'])):
            with self.assertRaises(RuntimeError): callback()

    def test_09_interview_is_1800_seconds_and_restart_keeps_it(self):
        self.start_demo(); self.exam.submit(); q = self.exam.prepare_interview()
        self.assertTrue(q['evidence_ids'])
        qa = self.exam.session()['interview']
        self.assertEqual(qa['deadline'] - qa['started_at'], 1800)
        with self.assertRaises(RuntimeError): self.exam.prepare_interview()
        self.assertEqual(Exam(self.root).session()['interview']['deadline'], qa['deadline'])

    def test_10_late_answer_is_not_in_scored_transcript(self):
        self.start_demo(); self.exam.submit(); self.exam.prepare_interview()
        s = self.exam.session(); s['interview']['deadline'] = time.time() - 1; self.exam.store.set('session', s)
        with self.assertRaises(RuntimeError): self.exam.answer('LATE ANSWER')
        self.assertEqual(self.exam.session()['phase'], 'completed')
        self.assertFalse(any(x.get('text') == 'LATE ANSWER' for x in self.exam.history('interview')))

    def test_11_demo_end_to_end_has_no_fabricated_score(self):
        self.complete_demo(); result = self.exam.evaluate()
        self.assertEqual(result['score_status'], 'demo_no_score')
        self.assertIsNone(result['total_100'])
        self.assertTrue(result['coverage']['all_eligible_atoms_mapped'])
        self.assertTrue((self.root / 'reports/final_report.html').is_file())
        self.assertTrue(self.exam.store.verify()['ok'])

    def test_12_main_and_stakeholder_conversations_are_separate(self):
        self.start_demo(); self.exam.chat('main-unique'); self.exam.ask('stakeholder-unique')
        self.assertEqual(self.exam.history('main')[0]['text'], 'main-unique')
        self.assertEqual(self.exam.history('stakeholder')[0]['text'], 'stakeholder-unique')

    def test_13_empty_candidate_tests_not_marked_passed(self):
        self.start_demo(); (self.exam.workspace / 'tests').mkdir()
        self.assertEqual(self.exam.check()[1]['status'], 'not_configured')

    def test_14_run_records_full_stdout_stderr_exit(self):
        self.start_demo()
        result = self.exam.run([sys.executable, '-c', 'import sys; print("한글"*12000); print("err",file=sys.stderr); sys.exit(4)'])
        self.assertEqual(result['returncode'], 4); self.assertFalse(result['passed'])
        self.assertGreater(len(result['stdout']), 20000); self.assertIn('err', result['stderr'])
        self.assertEqual(self.exam.store.events()[-2]['data']['stdout'], result['stdout'])

    def test_15_interview_failure_does_not_lose_answer(self):
        from unittest.mock import patch
        self.start_demo(); self.exam.submit(); self.exam.prepare_interview()
        with patch.object(self.exam, '_question', side_effect=RuntimeError('intentional')):
            with self.assertRaises(RuntimeError): self.exam.answer('durable-answer')
        self.assertEqual(self.exam.session()['interview']['answers'], 1)
        self.assertIsNone(self.exam.store.get('current_question'))
        self.assertTrue(any(x['text'] == 'durable-answer' for x in self.exam.history('interview')))
        with self.assertRaises(RuntimeError): self.exam.answer('should-not-be-accepted-without-question')
        self.assertTrue(self.exam.retry_question()['question'])


class ProtocolTests(Fixture):
    def test_01_captures_unknown_events_and_full_tool_output(self):
        self.start_fake(); self.exam.chat('test')
        raw = [e['data']['event'] for e in self.exam.store.events() if e['kind'] == 'codex_event']
        self.assertTrue(any(x['type'] == 'future.event' for x in raw))
        tool = next(x['item'] for x in raw if x.get('item', {}).get('aggregated_output'))
        self.assertEqual(tool['aggregated_output'].count('테스트 도구 출력'), 2000)
        self.assertEqual(self.exam.store.get('thread:main'), 'fake-session-0001')
        self.assertTrue(list((self.root / 'logs/calls').glob('*/prompt.txt')))

    def test_02_resume_targets_specific_thread(self):
        self.start_fake(); self.exam.chat('first'); self.exam.chat('second')
        requests = [e for e in self.exam.store.events() if e['kind'] == 'llm_request']
        self.assertIn('resume', requests[-1]['data']['command'])
        self.assertEqual(requests[-1]['data']['thread'], 'fake-session-0001')
        self.assertEqual(requests[-1]['data']['command'][-1], '-')

    def test_03_nonzero_codex_exit_is_failure(self):
        self.start_fake()
        with self.assertRaises(ProviderError): self.exam.chat('FAKE_EXIT_FAIL')
        self.assertTrue(list((self.root / 'logs/calls').glob('*/stderr.txt')))

    def test_04_timeout_kills_process_and_records_failure(self):
        self.start_fake(); self.exam.config['call_timeout_seconds'] = .2
        start = time.monotonic()
        with self.assertRaises(ProviderError): self.exam.chat('FAKE_SLEEP')
        self.assertLess(time.monotonic() - start, 4)
        e = [e for e in self.exam.store.events() if e['kind'] == 'llm_complete'][-1]
        self.assertTrue(e['data']['timed_out'])

    def test_05_empty_final_response_is_not_success(self):
        self.start_fake()
        with self.assertRaises(ProviderError): self.exam.chat('FAKE_NO_FINAL')

    def test_06_parallel_agent_workspaces_are_separate(self):
        self.start_demo()
        result = self.exam.parallel([{'role': 'planner', 'task': 'plan'}, {'role': 'tester', 'task': 'test'}])
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['id'], result[1]['id'])
        for r in result: self.assertTrue((self.root / 'agent_runs' / r['id'] / 'workspace').is_dir())

    def test_07_selected_model_is_snapshotted_and_forwarded(self):
        self.exam.config['codex_command'] = [sys.executable, str(FAKE)]
        with self.assertRaises(ValueError):
            self.exam.start('llm', 'bad model')
        self.exam.start('llm', 'test-model')
        self.assertEqual(self.exam.status()['model'], 'test-model')
        self.assertEqual(Exam(self.root).set_model('next-model')['model'], 'next-model')
        self.exam.provider().run('model check', self.exam.workspace, 'main')
        request = [e for e in self.exam.store.events() if e['kind'] == 'llm_request'][-1]
        self.assertEqual(request['data']['command'][request['data']['command'].index('--model') + 1], 'next-model')

    def test_08_external_cli_reuses_recorded_chat(self):
        if os.name != 'nt': self.skipTest('Windows console launcher')
        from exam import open_chat_cli
        from unittest.mock import patch
        self.start_fake()
        with patch('exam.subprocess.Popen') as launch:
            launch.return_value.pid = 321
            launch.return_value.poll.return_value = 0
            launch.return_value.returncode = 0
            result = open_chat_cli(self.exam)
        self.assertEqual(result['pid'], 321)
        self.assertEqual(result['client'], 'codex-tui')
        self.assertIn(str(self.exam.workspace), launch.call_args.args[0])
        self.assertNotIn(str(self.root / 'exam.py'), launch.call_args.args[0])
        self.assertTrue(any(e['kind'] == 'cli_open' for e in self.exam.store.events()))

    def test_09_tui_rollout_is_synced_to_exam_log(self):
        from exam import _sync_codex_tui
        self.start_fake()
        rollout = self.root / 'tui.jsonl'
        rows = [
            {'type':'session_meta','payload':{'id':'tui-thread','cwd':str(self.exam.workspace)}},
            {'type':'event_msg','payload':{'type':'item_completed','turn_id':'turn-1','item':{'type':'UserMessage','content':[{'type':'text','text':'TUI USER'}]}}},
            {'type':'event_msg','payload':{'type':'item_completed','turn_id':'turn-1','item':{'type':'AgentMessage','phase':'final_answer','content':[{'type':'Text','text':'TUI ANSWER'}]}}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':'turn-1'}}]
        rollout.write_text(''.join(json.dumps(x)+'\n' for x in rows), encoding='utf-8')
        class Done:
            pid=123;returncode=0
            @staticmethod
            def poll(): return 0
        before,_=inventory(self.exam.workspace)
        _sync_codex_tui(self.exam,Done(),rollout,0,set(),before)
        self.assertEqual([x['text'] for x in self.exam.history('main')],['TUI USER','TUI ANSWER'])
        self.assertEqual(self.exam.store.get('thread:main'),'tui-thread')


class FilesystemTests(Fixture):
    def test_01_secret_files_are_not_submitted(self):
        self.start_demo(); (self.exam.workspace / '.env').write_text('SECRET=demo')
        result = self.exam.submit()
        self.assertIn('.env', result['excluded'])
        self.assertNotIn('.env', result['manifest'])

    def test_02_path_traversal_and_secret_editor_rejected(self):
        self.start_demo()
        for path in ('../private/stakeholder.json', '.env', '/tmp/outside'):
            with self.assertRaises(ValueError): self.exam.save_file(path, 'bad')

    def test_03_merge_detects_conflict_without_overwrite(self):
        self.start_demo(); p = self.exam.workspace / 'item.txt'; p.write_text('base')
        worker = self.exam.agent('implementer', 'test worker')
        branch = self.root / 'agent_runs' / worker['id'] / 'workspace/item.txt'
        branch.write_text('branch'); p.write_text('live')
        with self.assertRaises(RuntimeError): self.exam.merge(worker['id'])
        self.assertEqual(p.read_text(), 'live')

    def test_04_merge_accepts_nonconflicting_addition(self):
        self.start_demo(); worker = self.exam.agent('implementer', 'test worker')
        (self.root / 'agent_runs' / worker['id'] / 'workspace/new.txt').write_text('new')
        self.exam.merge(worker['id'])
        self.assertEqual((self.exam.workspace / 'new.txt').read_text(), 'new')

    def test_05_readonly_worker_cannot_be_merged(self):
        self.start_demo(); worker = self.exam.agent('auditor', 'audit')
        with self.assertRaises(RuntimeError): self.exam.merge(worker['id'])

    def test_06_symlink_escape_rejected(self):
        outside = self.root / 'outside'; outside.mkdir()
        try: (self.exam.workspace / 'link').symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest('symlink unavailable')
        with self.assertRaises(ValueError): safe_path(self.exam.workspace, 'link/x.txt')
        files, exclusions = inventory(self.exam.workspace)
        self.assertIn('link/', exclusions)


class EvaluationTests(Fixture):
    def test_01_all_long_transcript_chunks_including_tail_are_mapped(self):
        self.start_demo()
        text = 'BEGIN|' + 'long evidence\n' * 10000 + '|UNIQUE_TAIL'
        self.exam.message('main', 'candidate', text)
        self.exam.submit(); evidence = Evidence(self.exam); bundle = evidence.build()
        chunks = [a['content'] for a in bundle['atoms'] if a.get('channel') == 'main']
        self.assertIn('UNIQUE_TAIL', ''.join(chunks))
        evidence.map_all(bundle)
        coverage = json.loads((self.root / 'analysis/coverage.json').read_text())
        self.assertEqual(coverage['mapped_atoms'], coverage['total_atoms'])
        self.assertGreater(coverage['map_chunks'], 4)

    def test_02_unknown_evidence_id_is_rejected(self):
        with self.assertRaises(ValueError): validate_refs(['INVENTED'], {'E1'}, allow_empty=False)

    def test_03_malformed_json_is_rejected(self):
        with self.assertRaises(Exception): parse_json('I think 100 points')

    def test_04_rating_out_of_range_is_rejected(self):
        evaluator = Evaluator(self.exam)
        obj = {'axis': 'technique', 'criteria': [{'id': 'T'+str(i), 'rating': 99, 'status': 'observed',
                'reason': 'x', 'evidence_ids': ['E1'], 'confidence': .8} for i in range(1,4)]}
        with self.assertRaises(ValueError): evaluator.validate_axis(obj, 'technique', {'atoms': [{'id': 'E1'}]})

    def test_05_cognition_needs_candidate_answer_not_ai_claim(self):
        obj = {'axis': 'cognition', 'criteria': [{'id': 'C'+str(i), 'rating': 3, 'status': 'observed',
               'reason': 'x', 'evidence_ids': ['E1'], 'confidence': .8} for i in range(1,4)]}
        result = Evaluator(self.exam).validate_axis(obj, 'cognition', {'atoms': [{'id': 'E1', 'channel': 'interview', 'actor': 'ai'}]})
        self.assertTrue(all(x['rating'] is None for x in result['criteria']))

    def test_06_masking_preserves_context(self):
        result, n = redact('example sk-' + 'A'*30 + ' suffix')
        self.assertEqual(n, 1); self.assertIn('suffix', result); self.assertIn('REDACTED', result)

    def test_07_eval_requires_finished_interview(self):
        self.start_demo(); self.exam.submit()
        with self.assertRaises(RuntimeError): self.exam.evaluate()

    def test_08_binary_is_declared_not_silently_inspected(self):
        self.start_demo(); (self.exam.workspace / 'asset.bin').write_bytes(b'\x00\xff\x11')
        self.exam.submit(); bundle = Evidence(self.exam).build()
        self.assertEqual(bundle['coverage']['binary_metadata_only_files'], 1)


    def test_09_numeric_score_pipeline_with_explicit_test_double(self):
        from unittest.mock import patch
        self.complete_demo()
        session = self.exam.session(); session['mode'] = 'llm'; self.exam.store.set('session', session)
        class GroundedJsonDouble:
            def run(_, prompt, cwd, role, **kwargs):
                schema = kwargs['schema'].name
                body = json.loads(prompt.split('<untrusted_evidence>\n', 1)[1].split('\n</untrusted_evidence>', 1)[0])
                if schema == 'evidence_map.json':
                    refs = [x['id'] for x in body]
                    value = {'findings': [{'criterion': prefix+str(i), 'observation': 'SYNTHETIC TEST ONLY',
                              'evidence_ids': refs, 'polarity': 'positive', 'actor': 'mixed'}
                              for prefix in 'TIC' for i in range(1,4)], 'limitations': ['test double']}
                else:
                    axis = re.search(r'AXIS=(\w+)', prompt).group(1)
                    refs = [a['id'] for a in body['raw_excerpts'] if axis != 'cognition' or
                            (a.get('channel') == 'interview' and a.get('actor') == 'candidate')]
                    assert refs, 'numeric test requires a real candidate-answer evidence id'
                    value = {'axis': axis, 'criteria': [{'id': key, 'rating': 3, 'status': 'observed',
                             'reason': 'SYNTHETIC TEST ONLY', 'evidence_ids': refs[:1], 'confidence': .5}
                             for key in RUBRIC[axis]['criteria']], 'limitations': ['test double']}
                return {'text': json.dumps(value), 'call_id': 'synthetic-test', 'usage': {}}
        with patch.object(self.exam, 'provider', return_value=GroundedJsonDouble()):
            result = self.exam.evaluate()
        self.assertEqual(result['total_100'], 75.0)
        self.assertEqual(result['score_status'], 'complete')
        self.assertTrue(all(x['score_100'] == 75 for x in result['axes'].values()))

    def test_10_invalid_model_map_fails_closed(self):
        from unittest.mock import patch
        self.complete_demo()
        class Broken:
            def run(_, *a, **k): return {'text': '{"findings":[{"criterion":"T1","observation":"fabricated","evidence_ids":["NOT_REAL"],"polarity":"positive","actor":"tool"}],"limitations":[]}'}
        with patch.object(self.exam, 'provider', return_value=Broken()):
            with self.assertRaises(ValueError): self.exam.evaluate()
        self.assertFalse((self.root / 'reports/final_report.json').exists())


class WebSecurityTests(Fixture):
    def test_01_loopback_api_token_origin_and_file_boundary(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        proc = subprocess.Popen([sys.executable, 'exam.py', 'serve', '--port', str(port)], cwd=self.root,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        try:
            line = proc.stdout.readline()
            token = re.search(r'#token=([^\s]+)', line).group(1)
            base = f'http://127.0.0.1:{port}'
            with self.assertRaises(HTTPError) as error: urlopen(base+'/api/state', timeout=3)
            self.assertEqual(error.exception.code, 403)
            headers = {'X-COFA-Token': token}
            with urlopen(Request(base+'/api/state', headers=headers), timeout=3) as r:
                self.assertEqual(json.load(r)['phase'], 'not_started')
            malicious = {**headers, 'Origin': 'https://evil.example'}
            with self.assertRaises(HTTPError): urlopen(Request(base+'/api/state', headers=malicious), timeout=3)
            with self.assertRaises(HTTPError): urlopen(Request(base+'/api/file?path=../private/stakeholder.json', headers=headers), timeout=3)
            with urlopen(base+'/', timeout=3) as r:
                self.assertIn(b'COFA-style', r.read())
        finally:
            proc.terminate()
            try: proc.communicate(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.communicate()


if __name__ == '__main__': unittest.main(verbosity=2)
