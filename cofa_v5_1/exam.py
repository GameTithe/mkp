#!/usr/bin/env python3
"""COFA-style practice v5.1: client / Codex / 120 + 30 minutes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from cofa.engine import Exam
from cofa.storage import write_json
from cofa.codex import resolve_command, Codex
from cofa.fs import inventory, changed

ROOT = Path(__file__).resolve().parent
_TUI_PROCESS = None


def display(value):
    if value is not None:
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def _rollout_file(thread=None):
    root = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions'
    files = root.rglob(f'*{thread}.jsonl') if thread else root.rglob('*.jsonl')
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def _item_text(item):
    return ''.join(x.get('text', '') for x in item.get('content', []) if isinstance(x, dict))


def _sync_codex_tui(exam, process, rollout, offset, known, initial_files):
    # ponytail: Codex rollout JSONL is the only local TUI transcript; update this mapper if its schema changes.
    manifest = initial_files
    while process.poll() is None or rollout:
        if rollout is None:
            candidates = [p for p in (Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions').rglob('*.jsonl') if p not in known]
            for candidate in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with candidate.open(encoding='utf-8') as metadata:
                        first = json.loads(metadata.readline())
                    cwd = first.get('payload', {}).get('cwd')
                    if cwd and os.path.normcase(str(Path(cwd).resolve())) == os.path.normcase(str(exam.workspace)):
                        rollout, offset = candidate, 0
                        break
                except (OSError, ValueError):
                    continue
        if rollout and rollout.exists():
            with rollout.open('rb') as source:
                source.seek(offset)
                while True:
                    start = source.tell()
                    raw = source.readline()
                    if not raw or not raw.endswith(b'\n'):
                        source.seek(start)
                        break
                    offset = source.tell()
                    try:
                        row = json.loads(raw.decode('utf-8'))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    payload = row.get('payload', {})
                    if row.get('type') == 'session_meta':
                        exam.store.set('thread:main', payload.get('id'))
                    if row.get('type') != 'event_msg':
                        continue
                    kind, item = payload.get('type'), payload.get('item', {})
                    if kind == 'task_started':
                        exam.store.event('codex_event', 'main', 'candidate', source='tui', call_id=payload.get('turn_id'), event={'type': 'turn.started'})
                    elif kind == 'item_completed':
                        item_type = item.get('type')
                        text = _item_text(item)
                        if item_type == 'UserMessage' and text:
                            exam.message('main', 'candidate', text, source='tui', call_id=payload.get('turn_id'))
                        elif item_type == 'AgentMessage' and item.get('phase') == 'final_answer' and text:
                            exam.message('main', 'ai', text, source='tui', call_id=payload.get('turn_id'))
                        elif item_type != 'UserMessage':
                            normalized = {**item, 'type': {'AgentMessage': 'agent_message', 'Reasoning': 'reasoning', 'CommandExecution': 'command_execution', 'FileChange': 'file_change', 'McpToolCall': 'mcp_tool_call', 'WebSearch': 'web_search'}.get(item_type, item_type)}
                            if item_type == 'AgentMessage': normalized['text'] = text
                            exam.store.event('codex_event', 'main', 'candidate', source='tui', call_id=payload.get('turn_id'), event={'type': 'item.completed', 'item': normalized})
                    elif kind == 'task_complete':
                        exam.store.event('codex_event', 'main', 'candidate', source='tui', call_id=payload.get('turn_id'), event={'type': 'turn.completed'})
                        current, excluded = inventory(exam.workspace)
                        exam.store.event('file_changes', 'main', source='tui', changes=changed(manifest, current), excluded=excluded)
                        manifest = current
        if process.poll() is not None:
            if rollout is None or not rollout.exists() or rollout.stat().st_size <= offset:
                break
        time.sleep(.25)


def _run_tui_sync(exam, process, *args):
    global _TUI_PROCESS
    try:
        _sync_codex_tui(exam, process, *args)
    except Exception as exc:
        exam.store.event('operation_error', 'main', action='sync_codex_tui', error=str(exc))
    finally:
        exam.store.event('cli_close', 'main', actor='system', pid=process.pid, returncode=process.returncode)
        if _TUI_PROCESS is process:
            _TUI_PROCESS = None


def open_chat_cli(exam):
    global _TUI_PROCESS
    state = exam.require_development()
    if state['mode'] != 'llm':
        raise RuntimeError('DEMO 모드는 실제 Codex CLI를 실행하지 않습니다.')
    if os.name != 'nt':
        raise RuntimeError('외부 Codex CLI 창은 Windows에서만 지원합니다.')
    if _TUI_PROCESS and _TUI_PROCESS.poll() is None:
        return {'pid': _TUI_PROCESS.pid, 'already_running': True, 'client': 'codex-tui'}
    exam.config = state['config']
    thread = exam.store.get('thread:main')
    rollout = _rollout_file(thread) if thread else None
    offset = rollout.stat().st_size if rollout else 0
    session_root = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions'
    known = set(session_root.rglob('*.jsonl'))
    command = resolve_command(exam.config)
    if thread:
        command += ['resume', '--include-non-interactive', thread]
    command += ['-C', str(exam.workspace), '-s', 'workspace-write', '-a', 'never',
                '-c', 'agents.enabled=false', '-c', 'features.multi_agent=false']
    if exam.config.get('model'):
        command += ['-m', exam.config['model']]
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    before, _ = inventory(exam.workspace)
    process = subprocess.Popen(command, cwd=exam.workspace,
                               creationflags=subprocess.CREATE_NEW_CONSOLE, env=env, close_fds=True)
    _TUI_PROCESS = process
    event = exam.store.event('cli_open', 'main', actor='system', pid=process.pid, thread=thread)
    threading.Thread(target=_run_tui_sync, args=(exam, process, rollout, offset, known, before), daemon=True).start()
    return {'pid': process.pid, 'event_id': event, 'thread': thread, 'client': 'codex-tui'}


def doctor(exam, live=False):
    report = {'python': sys.version.split()[0], 'python_ok': sys.version_info >= (3, 10),
              'platform': sys.platform, 'workspace': str(exam.workspace),
              'external_python_packages': 'none', 'live_check': False}
    try:
        command = resolve_command(exam.config)
        for name, flags in [('version', ['--version']), ('login_status', ['login', 'status']),
                            ('exec_help', ['exec', '--help']), ('resume_help', ['exec', 'resume', '--help'])]:
            proc = subprocess.run(command + flags, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace', timeout=20)
            output = (proc.stdout + proc.stderr).strip()
            if name.endswith('help'):
                report[name] = {'returncode': proc.returncode,
                                'json_flag_advertised': '--json' in output,
                                'output_flag_advertised': '--output-last-message' in output}
            else:
                report[name] = {'returncode': proc.returncode, 'output': output}
        if live:
            cwd = exam.root / 'runtime' / 'doctor'
            cwd.mkdir(parents=True, exist_ok=True)
            result = Codex(exam.root, exam.store, exam.config).run(
                'Reply only OK. Do not use tools. This is an authentication/transport check.', cwd, 'doctor')
            report['live_check'] = result['text'].strip() == 'OK'
            report['live_response'] = result['text']
    except Exception as exc:
        report['error'] = str(exc)
    write_json(exam.root / 'logs' / 'doctor.json', report)
    return report


def import_v2(exam, path):
    if exam.store.get('session'):
        raise RuntimeError('v2 가져오기는 새 세션 시작 전에만 가능합니다.')
    source = Path(path).resolve()
    if not (source / 'workspace').is_dir():
        raise ValueError('v2의 workspace 폴더를 포함하는 경로를 지정하세요.')
    backup = exam.root / 'workspace_before_import'
    if backup.exists():
        raise RuntimeError('이미 가져온 기록이 있습니다. 덮어쓰지 않았습니다.')
    shutil.copytree(exam.workspace, backup)
    from cofa.fs import copy_workspace
    copy_workspace(source / 'workspace', exam.workspace)
    for name in ('AGENTS.md', 'PLAN.md', 'ENV.md', 'HANDOFF.md', 'MISSION.md'):
        p = source / name
        if p.exists():
            shutil.copy2(p, exam.workspace / name)
    history = exam.root / 'imported_v2_logs'
    if (source / 'logs').is_dir():
        shutil.copytree(source / 'logs', history)
    exam.store.event('v2_import', 'system', source=str(source),
                     warning='기존에 수집되지 않은 Codex 대화는 복구되지 않음. 가져온 과거 로그는 별도 보관이며 새 회차 점수에 넣지 않음.')
    return {'imported': str(source), 'backup': str(backup), 'missing_old_transcripts_recovered': False}


def execute(exam, action, **a):
    """Shared CLI / authenticated browser dispatcher."""
    with exam.store.operation():
        try:
            if action == 'problem_select':
                from cofa.problems import select_problem
                return select_problem(exam, a['id'])
            if action == 'problem_validate':
                problem = exam.problem()
                if not problem: raise RuntimeError('문제팩을 선택하세요.')
                return problem.validate()
            if action == 'problem_debrief':
                from cofa.problems import debrief
                return debrief(exam)
            if action == 'stakeholder_reconsider':
                from cofa.contextual import reconsider
                return reconsider(exam, a.get('question_event'))
            if action == 'requirements_confirm':
                from cofa.contextual import confirm
                return confirm(exam, a['text'])
            if action == 'requirements_link':
                from cofa.contextual import link
                return link(exam, a['claim_id'], a['stage'], a['evidence_id'])
            if action == 'feedback':
                from cofa.feedback import generate
                return generate(exam, force=a.get('force',False))
            if action == 'feedback_chat':
                from cofa.feedback import chat
                return chat(exam,a['text'])
            if action == 'quality_validate':
                from cofa.benchmarks import validate
                return validate(exam)
            if action == 'quality_live':
                from cofa.benchmarks import run
                return run(exam,a.get('repeats',2),scope=a.get('scope','all'))
            if action == 'usage': return exam.usage()
            if action == 'prep_import':
                from cofa.preparation import import_preparation
                return import_preparation(exam, a['source'], a['label'], a['origin'], a['purpose'])
            if action == 'start': return exam.start(a.get('mode', 'llm'), a.get('model'))
            if action == 'model': return exam.set_model(a.get('model'))
            if action == 'ask': return exam.ask(a['text'])
            if action == 'chat': return exam.chat(a['text'])
            if action == 'open_cli': return open_chat_cli(exam)
            if action == 'note': return exam.note(a.get('category', 'decision'), a['text'])
            if action == 'run': return exam.run(a['argv'])
            if action == 'check': return exam.check()
            if action == 'audit': return exam.audit()
            if action == 'parallel': return exam.parallel(a['tasks'])
            if action == 'agent': return exam.agent(a['role'], a['task'])
            if action == 'merge': return exam.merge(a['id'])
            if action == 'submit': return exam.submit(a.get('reason', 'manual'))
            if action == 'qa_start': return exam.prepare_interview()
            if action == 'qa_answer': return exam.answer(a['text'])
            if action == 'qa_retry': return exam.retry_question()
            if action == 'qa_finish': return exam.finish_interview(a.get('reason', 'early_finish'))
            if action == 'evaluate': return exam.evaluate()
            if action == 'save_file': return exam.save_file(a['path'], a['content'])
            if action == 'doctor': return doctor(exam, a.get('live', False))
            if action == 'import_v2': return import_v2(exam, a['path'])
            if action == 'export':
                return {'events': str(exam.store.export()), 'integrity': exam.store.verify(),
                        'warning': '원본 대화에 민감정보가 포함될 수 있습니다. 공유 전 검토하세요.'}
            raise ValueError('지원하지 않는 동작: ' + action)
        except Exception as exc:
            exam.store.event('operation_error', 'system', action=action, error=str(exc))
            raise


def main():
    # UTF-8 console on Windows without requiring a system locale change.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='COFA-style v5.1 | 비공식 client 훈련 | Codex | 120+30분')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('doctor'); p.add_argument('--live', action='store_true')
    p = sub.add_parser('start'); p.add_argument('--mode', choices=['llm', 'demo'], default='llm'); p.add_argument('--model')
    p = sub.add_parser('model', help='개발 중 다음 Codex 호출부터 쓸 모델 (생략하면 Codex 기본값)'); p.add_argument('model', nargs='?')
    for name in ('status', 'check', 'audit', 'submit', 'evaluate', 'review', 'export', 'integrity', 'usage'):
        sub.add_parser(name)
    p = sub.add_parser('serve'); p.add_argument('--port', type=int, default=8765); p.add_argument('--open', action='store_true')
    p = sub.add_parser('ask'); p.add_argument('text')
    p = sub.add_parser('ask-review', help='원래 담당자 질문의 해석을 1회 재검토'); p.add_argument('question_event', nargs='?')
    p = sub.add_parser('chat'); p.add_argument('text', nargs='?'); p.add_argument('--file', type=Path)
    p = sub.add_parser('note'); p.add_argument('category'); p.add_argument('text')
    p = sub.add_parser('run'); p.add_argument('argv', nargs=argparse.REMAINDER)
    p = sub.add_parser('merge'); p.add_argument('id')
    p = sub.add_parser('import-v2'); p.add_argument('path')
    p = sub.add_parser('agent'); p.add_argument('action', choices=['parallel', 'run', 'list'])
    p.add_argument('--tasks', type=Path, default=ROOT / 'tasks.json')
    p.add_argument('--role', choices=['planner', 'implementer', 'tester', 'auditor'], default='planner')
    p.add_argument('--task')
    p = sub.add_parser('qa'); p.add_argument('action', choices=['start', 'answer', 'retry', 'finish'], nargs='?', default='start')
    p.add_argument('text', nargs='?'); p.add_argument('--file', type=Path)
    p = sub.add_parser('problem', help='문제팩 교체/검증/새 회차/출제')
    p.add_argument('action', choices=['list','show','validate','select','new','new-session','self-test','debrief'])
    p.add_argument('id', nargs='?'); p.add_argument('destination', nargs='?')
    p.add_argument('--title'); p.add_argument('--author', action='store_true')
    p = sub.add_parser('prep', help='시작 전 텍스트 지침 반입')
    p.add_argument('action', choices=['import', 'list'])
    p.add_argument('source', nargs='?')
    p.add_argument('--label'); p.add_argument('--origin'); p.add_argument('--purpose')
    p = sub.add_parser('requirements', help='본인이 작성한 요구사항 확인과 작업 근거 연결')
    p.add_argument('action', choices=['confirm','link'])
    p.add_argument('text', nargs='?');p.add_argument('--file',type=Path)
    p.add_argument('--claim-id');p.add_argument('--stage',choices=['planned','implemented','tested']);p.add_argument('--evidence-id')
    p = sub.add_parser('feedback', help='시험 종료 후 복습 리포트/대화')
    p.add_argument('text',nargs='?');p.add_argument('--retry',action='store_true')
    p = sub.add_parser('quality', help='AI 담당자/채점기 검사 (사람 기준 미교정)')
    p.add_argument('action',choices=['validate','live']);p.add_argument('--repeats',type=int,default=2);p.add_argument('--scope',choices=['all','judge','stakeholder'],default='all')
    args = parser.parse_args()
    exam = Exam(ROOT)
    try:
        if args.cmd == 'ask-review':
            return display(execute(exam,'stakeholder_reconsider',question_event=args.question_event))
        if args.cmd == 'requirements':
            if args.action == 'link':
                return display(execute(exam,'requirements_link',claim_id=args.claim_id,stage=args.stage,evidence_id=args.evidence_id))
            text = args.file.read_text(encoding='utf-8') if args.file else args.text
            return display(execute(exam,'requirements_confirm',text=text))
        if args.cmd == 'feedback':
            return display(execute(exam,'feedback_chat',text=args.text) if args.text else execute(exam,'feedback',force=args.retry))
        if args.cmd == 'quality':
            return display(execute(exam,'quality_'+args.action,repeats=args.repeats,scope=args.scope))
        if args.cmd == 'prep':
            if args.action == 'list':
                from cofa.preparation import list_preparation
                return display(list_preparation(exam))
            if not args.source: raise ValueError('지침 폴더를 지정하세요.')
            return display(execute(exam, 'prep_import', source=args.source, label=args.label, origin=args.origin, purpose=args.purpose))
        if args.cmd == 'problem':
            from cofa.problems import Problem, list_problems, select_problem, new_session, self_test, create_draft, debrief
            with exam.store.operation():
                action = args.action
                if action == 'list': return display(list_problems(ROOT))
                if action == 'debrief': return display(debrief(exam))
                ident = args.id or (exam.problem().manifest['id'] if exam.problem() else None)
                if not ident: raise ValueError('문제 ID를 지정하세요.')
                if action == 'select': return display(select_problem(exam,ident))
                if action == 'new': return display(create_draft(exam,ident,args.title))
                if action == 'new-session':
                    if not args.destination: raise ValueError('새 폴더 경로를 지정하세요.')
                    return display(new_session(exam,ident,args.destination))
                if action == 'self-test':
                    if not args.author: raise ValueError('정답 참조 구현을 실행합니다. 출제 검증에는 --author가 필요합니다.')
                    state = exam.store.get('session')
                    if state and state['phase'] != 'evaluated': raise RuntimeError('응시 도중에는 참조 정답을 실행할 수 없습니다.')
                    result = self_test(exam,ident); display(result)
                    if not result['reference_passed']: raise SystemExit(1)
                    return
                problem = Problem(ROOT,ident)
                return display(problem.summary() if action == 'show' else problem.validate(allow_draft=True))
        if args.cmd == 'serve':
            from cofa.webui import serve
            serve(exam, execute, args.port, args.open)
            return
        if args.cmd == 'status': return display(exam.status())
        if args.cmd == 'integrity': return display(exam.store.verify())
        if args.cmd == 'chat' and not args.text and not args.file:
            print('기록형 Codex 채팅. 여러 줄 입력 후 :send, 종료 :quit. 입력 중에도 시험 시간은 흐릅니다.')
            lines = []
            while True:
                try:
                    line = input('codex> ' if not lines else '.....> ')
                except EOFError:
                    break
                if line == ':quit': break
                if line == ':send':
                    if lines:
                        try: display(execute(exam, 'chat', text='\n'.join(lines)))
                        except Exception as exc: print('오류:', exc, file=sys.stderr)
                        lines = []
                else:
                    lines.append(line)
            return
        if args.cmd == 'agent':
            if args.action == 'list': return display(['planner', 'implementer', 'tester', 'auditor'])
            if args.action == 'parallel':
                return display(execute(exam, 'parallel', tasks=json.loads(args.tasks.read_text(encoding='utf-8-sig'))))
            return display(execute(exam, 'agent', role=args.role, task=args.task))
        if args.cmd == 'qa':
            data = {}
            if args.action == 'answer':
                data['text'] = args.file.read_text(encoding='utf-8') if args.file else args.text
            return display(execute(exam, 'qa_' + args.action, **data))
        values = vars(args).copy(); command = values.pop('cmd')
        if command == 'chat':
            path = values.pop('file')
            if path: values['text'] = path.read_text(encoding='utf-8')
        if command == 'run' and values['argv'][:1] == ['--']:
            values['argv'] = values['argv'][1:]
        if command == 'review': command = 'evaluate'
        if command == 'import-v2': command = 'import_v2'
        result = execute(exam, command, **values)
        display(result)
        if command == 'run' and not result['passed']:
            raise SystemExit(1)
        if command == 'check' and any(r.get('passed') is False for r in result):
            raise SystemExit(1)
    except KeyboardInterrupt:
        print('\n중단됨. 이미 수집된 로그는 유지됩니다.', file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print('오류: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
