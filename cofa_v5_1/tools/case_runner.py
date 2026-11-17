#!/usr/bin/env python3
"""Declarative black-box case adapter. Expected results are frozen, not derived from the reference.
Runs candidate programs: this is NOT a security sandbox. Only use trusted problem packs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cofa.codex import kill_tree


def compare(expected, actual, path='$'):
    if isinstance(expected, dict):
        if not isinstance(actual, dict): return path + ': expected object'
        for key, value in expected.items():
            if key not in actual: return path + '.' + key + ': missing'
            error = compare(value, actual[key], path + '.' + key)
            if error: return error
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual): return path + ': array length/type'
        for i, (a, b) in enumerate(zip(expected, actual)):
            error = compare(a, b, f'{path}[{i}]')
            if error: return error
        return None
    if type(expected) in (int, float):
        if type(actual) not in (int, float) or not math.isfinite(actual) or not math.isclose(expected, actual, rel_tol=0, abs_tol=1e-6):
            return path + ': numeric mismatch'
        return None
    return None if type(expected) is type(actual) and expected == actual else path + ': mismatch'


def execute_case(case, workspace, runner):
    result = {'id': case['id'], 'requirement_ids': case['requirement_ids'], 'passed': False}
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    for key in list(env):
        if key.endswith(('_API_KEY', '_ACCESS_TOKEN', '_SECRET_ACCESS_KEY')): env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='cofa-case-') as td:
        path = Path(td) / 'private-secret 프레임 input.json'
        if 'input_hex' in case: raw = bytes.fromhex(case['input_hex'])
        elif 'input_text' in case: raw = case['input_text'].encode('utf-8')
        else: raw = json.dumps(case.get('input', {}), ensure_ascii=False, allow_nan=False).encode('utf-8')
        if not case.get('missing_file'): path.write_bytes(raw)
        argv = [v.replace('{python}', sys.executable).replace('{workspace}', str(workspace)) for v in runner['argv']]
        if not case.get('omit_input'): argv += ['--input', str(path), '--json']
        argv += case.get('args', [])
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            p = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env, **kwargs)
        except OSError as exc:
            result['error'] = 'launch error: ' + type(exc).__name__; return result
        try:
            stdout, stderr = p.communicate(timeout=runner.get('case_timeout_seconds', 5))
        except subprocess.TimeoutExpired:
            kill_tree(p); p.communicate(); result['error'] = 'timeout'; return result
        out = stdout.decode('utf-8', errors='replace'); err = stderr.decode('utf-8', errors='replace')
        result['returncode'] = p.returncode
        if p.returncode != case.get('expected_exit',0):
            result['error'] = 'unexpected exit code'; return result
        if not case.get('missing_file') and (not path.exists() or path.read_bytes() != raw):
            result['error'] = 'input file changed'; return result
        for value in case.get('forbidden', []):
            if value in out or value in err:
                result['error'] = 'forbidden sensitive value echoed'; return result
        try:
            obj = json.loads(out, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non-finite output')))
        except (ValueError, UnicodeError):
            result['error'] = 'stdout must contain one standard JSON object'; return result
        if not isinstance(obj, dict): result['error'] = 'not an object'; return result
        if case.get('expected_exit',0) != 0:
            message = obj.get('error',{}).get('message')
            if not isinstance(message, str) or not message.strip():
                result['error'] = 'error.message missing'; return result
        error = compare(case['expected'], obj)
        if error:
            result['error'] = error
            # No actual values or expected private values in console output.
        else:
            result['passed'] = True
    return result


def run(pack, workspace, suite):
    manifest = json.loads((pack/'problem.json').read_text(encoding='utf-8-sig'))
    path = pack / manifest['suites'][suite]
    if not path.resolve().is_relative_to(pack.resolve()): raise ValueError('suite path escapes pack')
    cases = json.loads(path.read_text(encoding='utf-8-sig'))['cases']
    results = [execute_case(c,workspace,manifest['runner']) for c in cases]
    manual = manifest.get('manual_checks',[])
    return {'problem_id':manifest['id'],'problem_version':manifest['version'],'suite':suite,
            'passed':sum(x['passed'] for x in results),'total':len(results),
            'all_passed':bool(results) and all(x['passed'] for x in results),'cases':results,
            'manual_checks_not_automatically_certified':manual}


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--pack',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--suite',choices=['public','private'],required=True);p.add_argument('--report',type=Path)
    a=p.parse_args()
    try: result=run(a.pack.resolve(),a.workspace.resolve(),a.suite)
    except (OSError,ValueError,KeyError) as exc:
        print(json.dumps({'harness_error':str(exc)},ensure_ascii=False));return 2
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if a.report: a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(text+'\n',encoding='utf-8')
    print(text);return 0 if result['all_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
