"""Stage legacy implementations for a direct unified installer invocation."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '_lib'))
from install_reconcile import _safe, check_payload_dependencies
from install_reconcile import REPLACEMENT_PAYLOAD_FILES as PAYLOAD_FILES

MEMBERS = ('icode-workspace', 'icode-evidence', 'icode-local-index',
           'icode-mcp-health', 'icode-mcp-policy')


def stage_payload(source: Path, target: Path, *, module_configs: dict | None = None) -> None:
    source = _safe(source)
    target = _safe(target)
    profile_path = target / 'config.json'
    check_payload_dependencies(source.parent, [target, *(target.parent / name for name in MEMBERS)],
                               profile_path=profile_path if profile_path.exists() else None,
                               module_configs=module_configs)
    for member in MEMBERS:
        if not _safe(source.parent / member / 'server.py').is_file():
            raise RuntimeError('legacy implementation payload is unavailable')
    for member in MEMBERS:
        origin = source.parent / member
        destination = target.parent / member
        if origin == destination:
            continue
        # Explicit distribution files avoid copying private files when the
        # installer runs from an existing installation containing user data.
        for name in PAYLOAD_FILES:
            original = _safe(origin / name)
            if not original.is_file():
                continue
            output = _safe(destination / name)
            _safe(output.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    try:
        # Re-read the selected registrations just before publication; the
        # initial installer preflight must not be the only conflict check.
        from profile import prepare_profile
        profile = prepare_profile(args.target, os.environ.get('ICODE_MCP_SELECTED_HOSTS', 'claude').split(','))
        stage_payload(args.source, args.target, module_configs=profile['module_configs'])
    except (OSError, RuntimeError, ValueError):
        print('Local implementation staging failed; old registrations are unchanged.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
