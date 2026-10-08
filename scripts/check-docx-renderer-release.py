#!/usr/bin/env python3
"""Real release smoke: pinned install -> build -> structure -> owned page render."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools/docx'


def run(command):
    result = subprocess.run(command, text=True, capture_output=True, timeout=1800)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.expanduser().absolute()
    if output.exists():
        parser.error('output-dir must be a fresh directory')
    output.mkdir(parents=True)
    try:
        runtime = run([sys.executable, str(TOOLS / 'bootstrap_runtime.py')])
        renderer = run([sys.executable, str(TOOLS / 'bootstrap_renderer.py')])
        if renderer['status'] not in ('ready', 'installed'):
            raise ValueError('release smoke requires a supported renderer host: ' + renderer['status'])
        source = output / 'smoke.md'
        source.write_text('# ICODE 渲染验收\n\n中文与 English 必须完整。\n\n| 内容 | 状态 |\n|---|---|\n| 表格 | 保留 |\n\n```mermaid\nflowchart LR\nSource[源码] --> Word[文档]\n```\n\n```python\nprint("render ready")\n```\n', encoding='utf-8')
        document = output / 'smoke.docx'
        manifest = output / 'smoke.manifest.json'
        python = runtime['python']
        run([python, str(TOOLS / 'build_docx.py'), str(source), str(document), '--manifest', str(manifest)])
        run([python, str(TOOLS / 'inspect_docx.py'), str(document), '--source', str(source), '--manifest', str(manifest)])
        pages = run([python, str(TOOLS / 'render_docx.py'), str(document), str(output / 'preview'), '--result-manifest', str(manifest)])
        if pages['status'] != 'passed' or not pages['pages']:
            raise ValueError('release smoke did not produce renderer pages')
        result = {'status': 'passed', 'renderer': renderer['renderer'], 'pages': len(pages['pages']),
                  'manifest': str(manifest), 'scope': 'real render smoke; human page inspection is separate'}
        (output / 'release_check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print('ERROR: DOCX release check: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
