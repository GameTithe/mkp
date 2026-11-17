#!/usr/bin/env python3
"""Protocol test double only. Never use for a real training assessment."""
import json
from pathlib import Path
import sys
import time

args = sys.argv[1:]
if args == ['--version']:
    print('fake-codex-test-double'); raise SystemExit(0)
if args[:2] == ['login', 'status']:
    print('FAKE logged in'); raise SystemExit(0)
if '--help' in args:
    print('--json --output-last-message --output-schema --sandbox resume -'); raise SystemExit(0)
prompt = sys.stdin.read()
thread = args[args.index('resume') + 1] if 'resume' in args else 'fake-session-0001'

def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)

emit({'type': 'thread.started', 'thread_id': thread})
emit({'type': 'turn.started'})
if 'FAKE_SLEEP' in prompt:
    time.sleep(8)
if 'FAKE_EXIT_FAIL' in prompt:
    print('intentional protocol-test failure', file=sys.stderr, flush=True)
    raise SystemExit(7)
if 'FAKE_NO_FINAL' in prompt:
    emit({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 0}})
    raise SystemExit(0)
output = '테스트 도구 출력\n' * 2000
emit({'type': 'item.started', 'item': {'id': 'cmd', 'type': 'command_execution', 'command': 'test-double-only'}})
emit({'type': 'item.completed', 'item': {'id': 'cmd', 'type': 'command_execution',
                                      'command': 'test-double-only', 'aggregated_output': output,
                                      'exit_code': 0, 'status': 'completed'}})
emit({'type': 'future.event', 'payload': {'preserve': 'unknown event types must not disappear'}})
text = 'TEST DOUBLE: ' + prompt[-80:]
emit({'type': 'item.completed', 'item': {'id': 'message', 'type': 'agent_message', 'text': text}})
emit({'type': 'turn.completed', 'usage': {'input_tokens': 42, 'output_tokens': 7}})
print('test stderr preserved', file=sys.stderr)
if '-o' in args:
    Path(args[args.index('-o') + 1]).write_text(text, encoding='utf-8')
