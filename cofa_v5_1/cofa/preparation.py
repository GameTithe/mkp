"""Explicit, pre-exam import of text-only working instructions.
This copies documents, not executable plugins, and never installs or runs them.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import shutil
import uuid
from .storage import sha, write_json
from .evaluation import redact
from .fs import safe_path

ALLOWED = {'.md', '.txt', '.json', '.yaml', '.yml'}
MAX_FILES = 100
MAX_BYTES = 2_000_000


def list_preparation(exam):
    index = safe_path(exam.workspace, 'prepared/manifest.json')
    return json.loads(index.read_text(encoding='utf-8')) if index.exists() else {'schema_version': 1, 'kits': []}


def import_preparation(exam, source, label, origin, purpose):
    if exam.store.get('session'):
        raise RuntimeError('사전 지침 반입은 시험 시작 전에만 가능합니다.')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', label or ''):
        raise ValueError('label은 소문자 시작 2~64자 영문/숫자/하이픈입니다.')
    if not isinstance(origin, str) or not origin.strip() or not isinstance(purpose, str) or not purpose.strip():
        raise ValueError('출처 origin과 사용 목적 purpose를 입력하세요.')
    src = Path(source).expanduser()
    if src.is_symlink() or any(p.is_symlink() for p in src.parents):
        raise ValueError('심볼릭 링크 경로는 반입하지 않습니다.')
    src = src.resolve()
    if not src.is_dir() or src.is_relative_to(exam.root):
        raise ValueError('현재 시험 폴더 외부의 사전 지침 폴더를 지정하세요.')
    dest = safe_path(exam.workspace, 'prepared/' + label)
    if dest.exists():
        raise ValueError('같은 label이 이미 있습니다. 덮어쓰지 않았습니다.')
    entries, ignored, total = [], [], 0
    for path in sorted(src.rglob('*')):
        if path.is_symlink():
            raise ValueError('심볼릭 링크가 포함된 지침 폴더는 반입하지 않습니다.')
        rel = path.relative_to(src).as_posix()
        if any(part.lower() in {'.git', '.env', '.ssh', 'node_modules', '.venv'} or
               part.lower().startswith('.env.') for part in path.relative_to(src).parts):
            raise ValueError('비밀정보/환경 폴더가 포함되어 있습니다: ' + rel)
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED:
            ignored.append(rel); continue
        if path.stat().st_size > 500_000:
            raise ValueError('개별 지침 파일은 500KB 이하만 허용합니다.')
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ValueError('UTF-8 텍스트만 반입할 수 있습니다: ' + rel) from exc
        if '\x00' in text:
            raise ValueError('바이너리 텍스트는 반입할 수 없습니다.')
        if redact(text)[1]:
            raise ValueError('인증 토큰으로 보이는 문자열을 제거하세요: ' + rel)
        total += len(raw)
        entries.append({'path': rel, 'sha256': sha(raw), 'bytes': len(raw)})
        if len(entries) > MAX_FILES or total > MAX_BYTES:
            raise ValueError('사전 지침은 100파일/총 2MB 이하만 허용합니다.')
    if not entries:
        raise ValueError('반입할 텍스트 지침이 없습니다.')
    manifest = list_preparation(exam)
    kit = {'label': label, 'origin': origin.strip(), 'purpose': purpose.strip(),
           'files': entries, 'ignored_nontext_files': ignored, 'bytes': total,
           'auto_executed': False, 'official_exam_permission': 'not_asserted'}
    stage = exam.root / 'runtime' / ('prep-' + uuid.uuid4().hex[:8])
    stage.mkdir(parents=True)
    try:
        for entry in entries:
            raw = (src / entry['path']).read_bytes()
            if sha(raw) != entry['sha256']:
                raise RuntimeError('검사 도중 원본 파일이 변경되었습니다.')
            target = stage / entry['path']; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(dest)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    manifest['kits'].append(kit)
    write_json(dest.parent / 'manifest.json', manifest)
    index = '# 반입한 사전 지침\n\n기존 자료이며 이번 회차에서 새로 작성한 작업으로 취급하지 않습니다. 자동 실행하지 않습니다.\n\n'
    for item in manifest['kits']:
        index += '## ' + item['label'] + '\n출처: ' + item['origin'] + '\n목적: ' + item['purpose'] + '\n'
        index += '\n'.join('- ' + item['label'] + '/' + f['path'] for f in item['files']) + '\n\n'
    (dest.parent / 'INDEX.md').write_text(index, encoding='utf-8')
    exam.store.event('preparation_import', 'candidate', actor='candidate', **kit)
    return kit
