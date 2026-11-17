"""Workspace snapshots and conflict-checked explicit integration."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
from .storage import sha, write_json

EXCLUDED_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.codex'}
SECRET_NAMES = {'.env', 'auth.json', 'credentials.json', 'id_rsa', 'id_ed25519'}


def blocked(path: Path) -> bool:
    return any(x in EXCLUDED_DIRS for x in path.parts) or path.name in SECRET_NAMES or (
        path.name.startswith('.env.') and path.name not in {'.env.example', '.env.sample'}) or path.suffix.lower() in {'.pem', '.key', '.p12'}


def safe_path(root: Path, rel: str) -> Path:
    p = Path(rel)
    if not rel or p.is_absolute() or '..' in p.parts or blocked(p):
        raise ValueError('허용되지 않는 경로입니다.')
    result = root / p
    cur = root
    for bit in p.parts:
        cur = cur / bit
        if cur.is_symlink():
            raise ValueError('심볼릭 링크 경로는 허용하지 않습니다.')
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('프로젝트 밖 경로입니다.')
    return result


def inventory(root: Path):
    files = {}
    exclusions = []
    if not root.exists():
        return files, exclusions
    for parent, dirs, names in os.walk(root, followlinks=False):
        parent = Path(parent)
        keep = []
        for d in dirs:
            rel = (parent / d).relative_to(root)
            if blocked(rel) or (parent / d).is_symlink():
                exclusions.append(str(rel).replace('\\', '/') + '/')
            else:
                keep.append(d)
        dirs[:] = keep
        for name in sorted(names):
            path = parent / name
            rel = path.relative_to(root)
            key = rel.as_posix()
            if blocked(rel) or path.is_symlink():
                exclusions.append(key)
                continue
            files[key] = sha(path.read_bytes())
    return dict(sorted(files.items())), sorted(exclusions)


def copy_workspace(source: Path, dest: Path):
    files, excluded = inventory(source)
    dest.mkdir(parents=True, exist_ok=True)
    for rel in files:
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, p)
    copied, _ = inventory(dest)
    if copied != files:
        raise RuntimeError('복사 중 파일 변경이 감지되었습니다. 편집을 중단하고 다시 시도하세요.')
    return files, excluded


def changed(before: dict, after: dict):
    return {k: {'before': before.get(k), 'after': after.get(k)}
            for k in sorted(before.keys() | after.keys()) if before.get(k) != after.get(k)}


def merge_workspace(base: dict, branch: Path, live: Path):
    current, _ = inventory(live)
    branch_files, _ = inventory(branch)
    changes = changed(base, branch_files)
    conflicts = [k for k, v in changes.items() if current.get(k) not in (v['before'], v['after'])]
    if conflicts:
        raise RuntimeError('병합 충돌: ' + ', '.join(conflicts) + '. 메인 작업을 덮어쓰지 않았습니다.')
    # Preflight all paths before changing any of them.
    for rel in changes:
        safe_path(live, rel)
        safe_path(branch, rel)
    for rel, item in changes.items():
        dst = live / rel
        if item['after'] is None:
            if dst.exists():
                dst.unlink()
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(branch / rel, dst)
    return changes
