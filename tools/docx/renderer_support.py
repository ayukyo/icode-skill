"""Shared ownership, compatibility and integrity checks for DOCX renderers."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def runtime_root() -> Path:
    configured = os.environ.get('ICODE_DOCX_RENDERER_ROOT')
    return Path(configured).expanduser() if configured else Path.home() / '.local/share/icode/runtime/docx-renderer'


def host() -> dict:
    libc_name, libc_version = platform.libc_ver()
    flags = set()
    if platform.system() == 'Linux':
        try:
            rows = [set(line.split(':', 1)[1].split()) for line in Path('/proc/cpuinfo').read_text().splitlines()
                    if line.startswith('flags') and ':' in line]
            if rows:
                flags = set.intersection(*rows)
        except OSError:
            pass
    return {'os': platform.system().lower(), 'arch': platform.machine().lower(),
            'libc': libc_name.lower(), 'glibc': libc_version,
            'kernel': platform.release(), 'cpu_flags': sorted(flags)}


def version_at_least(actual: str, minimum: str | None) -> bool:
    if not minimum:
        return True
    try:
        current = tuple(map(int, re.match(r'^\d+(?:\.\d+)*', actual).group().split('.')))
        required = tuple(map(int, minimum.split('.')))
        length = max(len(current), len(required))
        return current + (0,) * (length - len(current)) >= required + (0,) * (length - len(required))
    except (AttributeError, ValueError, TypeError):
        return False


def matches(entry: dict, current: dict) -> bool:
    return (entry.get('os') == current['os'] and entry.get('arch') == current['arch']
            and (entry.get('libc') != 'glibc' or (current['libc'] == 'glibc'
                 and version_at_least(current['glibc'], entry.get('glibc_min'))))
            and version_at_least(current.get('kernel', ''), entry.get('kernel_min'))
            and set(entry.get('cpu_flags', [])) <= set(current.get('cpu_flags', [])))


def owned_path(bundle: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('invalid renderer relative path: ' + relative)
    result = bundle / path
    if not result.resolve().is_relative_to(bundle.resolve()):
        raise ValueError('renderer path escaped owned bundle: ' + relative)
    return result


def validate_entry(entry: dict, parent: Path) -> dict:
    bundle = (parent / entry['bundle']).resolve()
    paths = {}
    for label in ('soffice', 'pdftoppm'):
        path = owned_path(bundle, entry[label])
        expected = entry.get('sha256', {}).get(label, '')
        if not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError(label + ' is missing a valid SHA-256')
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(label + ' does not exist or is not executable: ' + str(path))
        if sha256_file(path) != expected:
            raise ValueError(label + ' SHA-256 mismatch')
        paths[label] = str(path)
    if entry.get('payload_manifest'):
        path = owned_path(bundle, entry['payload_manifest'])
        if sha256_file(path) != entry.get('payload_manifest_sha256'):
            raise ValueError('renderer payload manifest SHA-256 mismatch')
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not payload.get('payload_sha256'):
            raise ValueError('empty renderer payload hash record')
        for relative, target in payload.get('symlinks', {}).items():
            path = owned_path(bundle, relative)
            if not path.is_symlink() or os.readlink(path) != target:
                raise ValueError('renderer symlink changed: ' + relative)
        for relative, expected in payload['payload_sha256'].items():
            if sha256_file(owned_path(bundle, relative)) != expected:
                raise ValueError('renderer payload SHA-256 mismatch: ' + relative)
    return {'id': entry['id'], 'bundle': str(bundle), **paths}
