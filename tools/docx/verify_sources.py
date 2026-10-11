#!/usr/bin/env python3
"""Check reviewed source file fingerprints and bounded code anchors, read-only.

The agent records anchors after reading implementation and callers. A matching
hash/snippet proves provenance, not that the associated explanation is correct.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def verify(record: Path, root: Path) -> dict:
    data = json.loads(record.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('files'), list) or not data['files']:
        raise ValueError('source record requires a nonempty files list')
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('source root must be a directory')
    checks = []
    for item in data['files']:
        relative = Path(item['path'])
        path = (root / relative).resolve(strict=True)
        if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(root):
            raise ValueError('source path must stay inside the selected root')
        expected = item['sha256']
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('source requires a lowercase SHA-256 fingerprint')
        payload = path.read_bytes()
        checks.append({'file': relative.as_posix(), 'kind': 'file_hash',
                       'passed': hashlib.sha256(payload).hexdigest() == expected})
        lines = payload.decode('utf-8').splitlines()
        anchors = item.get('anchors', [])
        if not isinstance(anchors, list) or not anchors:
            raise ValueError('each source file requires at least one bounded anchor')
        for anchor in anchors:
            start, end, contains = anchor['start'], anchor['end'], anchor['contains']
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                raise ValueError('anchor line range is outside source file')
            if not isinstance(contains, str) or not contains.strip():
                raise ValueError('anchor contains must be nonempty text')
            checks.append({'file': relative.as_posix(), 'kind': 'anchor', 'lines': [start, end],
                           'contains': contains, 'passed': contains in '\n'.join(lines[start - 1:end])})
    return {'schema_version': 1, 'root': str(root), 'checks': checks,
            'passed': all(item['passed'] for item in checks),
            'scope': 'source fingerprints and literal code anchors; not code semantics or hardware validation'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.record, args.root)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.report:
            args.report.write_text(payload, encoding='utf-8')
        print(payload, end='')
        return 0 if report['passed'] else 1
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print('ERROR: DOCX source check failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
