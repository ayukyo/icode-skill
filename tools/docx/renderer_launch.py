#!/usr/bin/env python3
"""Copied to the owned runtime; no dependency on the installed skill directory."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    bundle = Path(__file__).resolve().parents[1]
    record = json.loads((bundle / 'payload_manifest.json').read_text(encoding='utf-8'))
    for relative, target in record.get('symlinks', {}).items():
        path = bundle / relative
        if not path.is_symlink() or os.readlink(path) != target or not path.resolve().is_relative_to(bundle):
            raise ValueError('renderer symlink changed or escaped owned bundle: ' + relative)
    for relative, expected in record['payload_sha256'].items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle):
            raise ValueError('renderer path escaped owned bundle')
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != expected:
            raise ValueError('renderer payload SHA-256 mismatch: ' + relative)
    name = Path(sys.argv[0]).name
    executable = (bundle / record['executables'][name]).resolve()
    if not executable.is_relative_to(bundle) or str(executable.relative_to(bundle)) not in record['payload_sha256']:
        raise ValueError('unverified renderer executable')
    env = os.environ.copy()
    if hasattr(os, 'nice'):
        os.nice(5)
    if name == 'soffice':
        env['SAL_USE_VCLPLUGIN'] = 'svp'
        env['OMP_NUM_THREADS'] = '2'
        with tempfile.TemporaryDirectory(prefix='icode-docx-profile-') as profile:
            return subprocess.run([str(executable), '-env:UserInstallation=' + Path(profile).as_uri(), *sys.argv[1:]],
                                  env=env, timeout=240).returncode
    env['LD_LIBRARY_PATH'] = str(bundle / record['library_dir'])
    return subprocess.run([str(executable), *sys.argv[1:]], env=env, timeout=240).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print('ERROR: owned renderer: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
