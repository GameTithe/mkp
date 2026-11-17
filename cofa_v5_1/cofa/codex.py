"""Logged Codex exec adapter. Prompts go through stdin, never shell interpolation."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from .storage import Store, write_json


class ProviderError(RuntimeError):
    pass


def resolve_command(config: dict) -> list[str]:
    configured = config.get('codex_command') or ['codex']
    if not isinstance(configured, list) or not all(isinstance(x, str) for x in configured):
        raise ProviderError('config.json: codex_command는 문자열 배열이어야 합니다.')
    executable = shutil.which(configured[0])
    if not executable:
        raise ProviderError('Codex CLI 없음: npm install -g @openai/codex 후 codex login을 실행하세요.')
    if os.name == 'nt' and Path(executable).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
        # Avoid cmd.exe parsing of arguments. npm's official wrapper sits next to node_modules.
        script = Path(executable).parent / 'node_modules' / '@openai' / 'codex' / 'bin' / 'codex.js'
        node = shutil.which('node')
        if not script.exists() or not node:
            raise ProviderError('Windows npm wrapper를 해석할 수 없습니다. config.json의 codex_command를 '
                                '["node", "C:/.../node_modules/@openai/codex/bin/codex.js"]로 지정하세요.')
        return [node, str(script), *configured[1:]]
    return [executable, *configured[1:]]


def kill_tree(process: subprocess.Popen):
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()


class Codex:
    def __init__(self, root: Path, store: Store, config: dict):
        self.root, self.store, self.config = root, store, config

    def run(self, prompt: str, cwd: Path, role: str, *, write=False, thread=None,
            schema: Path | None = None, deadline: float | None = None,
            public_events=False) -> dict:
        call_id = uuid.uuid4().hex[:16]
        folder = self.root / 'logs' / 'calls' / call_id
        folder.mkdir(parents=True)
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        final = folder / 'final.txt'
        cmd = resolve_command(self.config)
        cmd += ['exec', '--json', '--skip-git-repo-check', '--color', 'never',
                '--sandbox', 'workspace-write' if write else 'read-only',
                '-c', 'approval_policy="never"', '-c', 'agents.enabled=false',
                '-c', 'features.multi_agent=false', '-o', str(final)]
        if self.config.get('model'):
            cmd += ['--model', self.config['model']]
        if schema:
            cmd += ['--output-schema', str(schema)]
        if thread:
            cmd += ['resume', str(thread), '-']
        else:
            cmd += ['-']
        meta = {'role': role, 'call_id': call_id, 'cwd': str(cwd), 'command': cmd,
                'thread': thread, 'schema': str(schema) if schema else None,
                'sandbox': 'workspace-write' if write else 'read-only'}
        write_json(folder / 'metadata.json', meta)
        self.store.event('llm_request', role, 'internal', **meta, prompt=prompt)
        timeout = float(self.config.get('call_timeout_seconds', 600))
        if deadline is not None:
            timeout = min(timeout, deadline - time.time())
        if timeout <= 0:
            raise ProviderError('허용 시간이 종료되어 LLM을 호출하지 않았습니다.')
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        info = {'thread_id': thread, 'messages': [], 'usage': {}, 'reader_errors': []}
        visibility = 'candidate' if public_events else 'internal'

        def drain_stdout():
            try:
                with (folder / 'stdout.jsonl').open('wb') as out:
                    for raw in iter(process.stdout.readline, b''):
                        out.write(raw)
                        out.flush()
                        text = raw.decode('utf-8', errors='replace').rstrip('\r\n')
                        try:
                            event = json.loads(text)
                        except json.JSONDecodeError:
                            event = {'type': 'unparsed_stdout', 'text': text}
                        self.store.event('codex_event', role, visibility, call_id=call_id, event=event)
                        if event.get('type') == 'thread.started':
                            info['thread_id'] = event.get('thread_id')
                        if event.get('type') == 'turn.completed':
                            info['usage'] = event.get('usage', {})
                        item = event.get('item', {})
                        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                            info['messages'].append(item.get('text', ''))
            except Exception as exc:
                info['reader_errors'].append(repr(exc))

        def drain_stderr():
            try:
                with (folder / 'stderr.txt').open('wb') as out:
                    for raw in iter(process.stderr.readline, b''):
                        out.write(raw)
                        out.flush()
                        self.store.event('codex_stderr', role, 'internal', call_id=call_id,
                                         text=raw.decode('utf-8', errors='replace'))
            except Exception as exc:
                info['reader_errors'].append(repr(exc))

        readers = [threading.Thread(target=drain_stdout, daemon=True),
                   threading.Thread(target=drain_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        timed_out = False
        interrupted = False
        try:
            try:
                process.stdin.write(prompt.encode('utf-8'))
                process.stdin.close()
            except BrokenPipeError:
                pass
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(process)
        except KeyboardInterrupt:
            interrupted = True
            kill_tree(process)
        finally:
            process.wait()
            for reader in readers:
                reader.join(timeout=15)
            if any(reader.is_alive() for reader in readers):
                info['reader_errors'].append('output reader did not finish')
            else:
                process.stdout.close()
                process.stderr.close()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        if role == 'main' and info['thread_id']:
            self.store.set('thread:main', info['thread_id'])
        text = final.read_text(encoding='utf-8') if final.exists() else '\n'.join(info['messages'])
        result = dict(call_id=call_id, thread_id=info['thread_id'], text=text,
                      usage=info['usage'], returncode=process.returncode, timed_out=timed_out,
                      interrupted=interrupted, capture_errors=info['reader_errors'])
        write_json(folder / 'result.json', result)
        self.store.event('llm_complete', role, 'internal', **result)
        if info['reader_errors']:
            raise ProviderError('출력 기록 실패: ' + '; '.join(info['reader_errors']))
        if timed_out or interrupted or process.returncode != 0:
            err = (folder / 'stderr.txt').read_text(encoding='utf-8', errors='replace')
            # Full diagnostic is retained in the call directory; console excerpt is bounded.
            raise ProviderError(f'Codex 호출 실패 ({call_id}, rc={process.returncode}, timeout={timed_out}). '
                                f'logs/calls/{call_id} 확인.\n' + err[-2000:])
        if not text.strip():
            raise ProviderError(f'Codex 최종 응답이 비었습니다: {call_id}')
        return result


class DemoCodex:
    """Deterministic I/O smoke test, explicitly not a simulated ability score."""
    def __init__(self, root, store, config):
        self.root, self.store = root, store

    def run(self, prompt, cwd, role, **kwargs):
        call_id = 'demo-' + uuid.uuid4().hex[:12]
        folder = self.root / 'logs' / 'calls' / call_id
        folder.mkdir(parents=True)
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        self.store.event('llm_request', role, 'internal', prompt=prompt, call_id=call_id, demo=True)
        schema = kwargs.get('schema')
        if schema and schema.name == 'context_route.json':
            from .contextual import demo_route
            body = json.loads(prompt.split('CONTEXT_INPUT_JSON\n', 1)[1])
            text = json.dumps(demo_route(body), ensure_ascii=False)
        elif schema and schema.name == 'requirements_confirm.json':
            body = json.loads(prompt.split('CONFIRM_INPUT_JSON\n', 1)[1])
            # Offline mode never pretends to understand or confirm a human's requirement.
            text = json.dumps({'items':[{'index':i,'status':'ambiguous','fact_ids':[]} for i in range(len(body['claims']))]}, ensure_ascii=False)
        elif schema and schema.name == 'stakeholder_route.json':
            from .stakeholder import demo_choice
            body = json.loads(prompt.split('ROUTER_INPUT_JSON\n', 1)[1])
            text = json.dumps(demo_choice(body), ensure_ascii=False)
        elif schema and schema.name == 'evidence_map.json':
            text = json.dumps({'findings': [], 'limitations': ['DEMO: 실제 LLM 분석 미수행']}, ensure_ascii=False)
        elif schema and schema.name == 'axis_score.json':
            axis = next((x for x in ('technique', 'intent', 'cognition') if f'AXIS={x}' in prompt), 'technique')
            prefixes = {'technique': 'T', 'intent': 'I', 'cognition': 'C'}
            text = json.dumps({'axis': axis, 'criteria': [
                {'id': prefixes[axis] + str(i), 'rating': None, 'status': 'insufficient_evidence',
                 'reason': 'DEMO 모드: 능력 점수를 생성하지 않습니다.', 'evidence_ids': [], 'confidence': 0.0}
                for i in range(1, 4)], 'limitations': ['실제 LLM 평가 아님']}, ensure_ascii=False)
        elif schema and schema.name == 'interview.json':
            # Actual evidence identifier from the supplied evidence bundle, when available.
            import re
            refs = re.findall(r'"id":\s*"([A-Z][^"]*)"', prompt)
            text = json.dumps({'question': '[DEMO] 제출한 구조를 선택한 이유와 실제 검증 결과를 설명하세요.',
                               'axis': 'cognition', 'evidence_ids': refs[:1],
                               'followup_reason': '연결 상태 점검 질문'}, ensure_ascii=False)
        elif role == 'stakeholder':
            text = '[DEMO/규칙 응답] 사용자는 LiveOps와 QA 담당자입니다. 원본 CSV는 수정하지 말고, 잘못된 행은 경고 후 건너뛰어 주세요.'
        elif role == 'auditor':
            text = '[DEMO] 독립 검증 연결 확인입니다. 실제 코드 검토 결과가 아닙니다.'
        else:
            text = '[DEMO] 메시지·이벤트 저장을 확인했습니다. 실제 코드를 생성하거나 변경하지 않았습니다.'
        event = {'type': 'item.completed', 'item': {'id': 'demo', 'type': 'agent_message', 'text': text}}
        self.store.event('codex_event', role, 'candidate' if kwargs.get('public_events') else 'internal',
                         call_id=call_id, event=event, demo=True)
        (folder / 'stdout.jsonl').write_text(json.dumps(event, ensure_ascii=False) + '\n', encoding='utf-8')
        (folder / 'final.txt').write_text(text, encoding='utf-8')
        result = {'text': text, 'thread_id': 'demo-thread', 'call_id': call_id, 'usage': {}, 'returncode': 0}
        write_json(folder / 'result.json', result)
        self.store.event('llm_complete', role, 'internal', demo=True, **result)
        return result
