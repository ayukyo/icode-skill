#!/usr/bin/env python3
"""Resolve a hash-verified shipped or persistently installed owned renderer."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from renderer_support import TOOL_DIR, host, runtime_root, sha256_file, validate_entry, version_at_least, matches


def resolve(manifest_path: Path | None = None) -> dict:
    current = host()
    manifests = [manifest_path] if manifest_path is not None else [TOOL_DIR / 'renderer_manifest.json']
    registry = runtime_root() / 'renderer_manifest.json'
    if manifest_path is None and registry.exists():
        manifests.append(registry)
    for path in manifests:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('schema_version') != 1 or not isinstance(data.get('renderers'), list):
                raise ValueError('invalid renderer manifest schema')
        except (OSError, ValueError, AttributeError) as exc:
            return {'status': 'invalid_manifest', 'host': current, 'reason': str(exc)}
        for entry in data['renderers']:
            if not isinstance(entry, dict):
                return {'status': 'invalid_manifest', 'host': current, 'reason': 'renderer entry must be an object'}
            try:
                if not matches(entry, current):
                    continue
                renderer = validate_entry(entry, path.parent)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
                return {'status': 'invalid_bundle', 'host': current, 'reason': str(exc)}
            return {'status': 'ready', 'host': current, 'renderer': renderer}
    return {'status': 'unavailable', 'host': current,
            'reason': 'No matching managed renderer; run tools/docx/bootstrap_renderer.py (never use PATH LibreOffice)'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, help='inspect only this manifest; bypass the persistent registry')
    args = parser.parse_args()
    print(json.dumps(resolve(args.manifest), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
