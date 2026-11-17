#!/usr/bin/env python3
"""CLI adapter for the FrameScope reference solution. Python standard library only."""
from __future__ import annotations
import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
from framescope import analyze


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('INVALID_ARGS')


def emit(value, status=0):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return status


def fail(code):
    messages = {'INVALID_ARGS': '명령 옵션을 확인하세요.', 'IO_ERROR': '입력 파일을 읽을 수 없습니다.',
                'INVALID_INPUT': '지원하는 JSON 입력 구조를 확인하세요.'}
    return emit({'error': {'code': code, 'message': messages[code]}}, 2)


def invalid_constant(value):
    raise ValueError('Non-standard JSON constant')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = Arguments(description='FrameScope: offline frame regression inspection')
    parser.add_argument('--input', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--scenario')
    parser.add_argument('--status', choices=['regressed', 'stable', 'insufficient'])
    try:
        args = parser.parse_args(argv)
    except ValueError:
        return fail('INVALID_ARGS')
    try:
        raw = Path(args.input).read_text(encoding='utf-8-sig')
    except UnicodeError:
        return fail('INVALID_INPUT')
    except OSError:
        return fail('IO_ERROR')
    try:
        data = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant)
        output = analyze(data, args.scenario, args.status)
    except (ValueError, ArithmeticError, RecursionError):
        return fail('INVALID_INPUT')
    return emit(output)


if __name__ == '__main__':
    raise SystemExit(main())
