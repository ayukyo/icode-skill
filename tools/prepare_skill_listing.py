#!/usr/bin/env python3
"""Print offline SkillHub/Smithery submission drafts. Never log in or publish.

Only the public root SKILL.md is read. These are review materials, not an
installable bundle, registry validation, permission to publish or proof of listing.
"""
import argparse
import json
from pathlib import Path
import re
import sys

from build_public_site import REPO, DEFAULT_BASE, public_input

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ('skillhub', 'smithery')


def drafts(source, platform):
    if platform not in (*PLATFORMS, 'all'):
        raise ValueError('no verified submission format for this platform')
    text = public_input(Path(source).expanduser().absolute(), 'SKILL.md')
    # This repository has a deliberately flat, plain-scalar discovery header.
    # Do not guess or partially interpret a changed YAML structure.
    header = re.match(r'\A---\r?\nname: icode\r?\ndescription: ([^\r\n]+)\r?\n---(?:\r?\n|\Z)', text)
    if not header:
        raise ValueError('unsupported ICODE header: expected name: icode and a single-line plain description')
    description = header[1].strip(' ')
    # Accept only a conservative subset of plain text, not general YAML:
    # a letter starts prose (including Chinese), not a number, tag or collection.
    # Preserve punctuation inside prose, but reject comment/mapping separators,
    # implicit null/boolean values and controls rather than changing their meaning.
    if (not description or not description[0].isalpha() or not description.isprintable()
            or description.casefold() in {'null', 'true', 'false', 'yes', 'no', 'on', 'off', 'y', 'n'}
            or re.search(r'\s#|:(?:\s|$)', description)):
        raise ValueError('unsupported description: expected single-line plain text starting with a letter; '
                         'YAML syntax and null/boolean scalars are not supported')
    versions = re.findall(r'^\*\*版本\*\*:\s*v([^\s]+)\s*$', text, re.MULTILINE)
    if (len(versions) != 1
            or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', versions[0])):
        raise ValueError('expected one stable ASCII source version (X.Y.Z)')
    data = {
        'skillhub': {
            'platform': 'skillhub',
            'official_source': 'https://skillhub.cn/ai/release.md',
            'frontmatter': {
                'slug': 'icode', 'version': versions[0], 'displayName': 'ICODE AI Coding Workflow',
                'summary': '工单式 AI 编码、设计与代码审查、证据验证和中断续接。',
                'description': description,
                'tags': ['ai-coding', 'workflow', 'code-review', 'verification', 'documentation'],
                'license': 'MIT', 'homepage': DEFAULT_BASE,
            },
            'requires': ['Account and platform identity verification',
                         'Review publishing terms and supply your own credential outside this tool',
                         'Stage a complete skill payload with shared skills and resource dependencies',
                         'Merge these fields only into that staging copy, then run the official dry-run',
                         'Verify host entrypoints and installation before publication'],
        },
        'smithery': {
            'platform': 'smithery',
            'official_source': 'https://smithery.ai/docs/api-reference/skills/create-or-update-a-skill',
            'request': {'method': 'PUT', 'url': 'https://api.smithery.ai/skills/ayukyo/icode',
                        'body': {'gitUrl': REPO}},
            'requires': ['Confirm ownership and availability of the ayukyo namespace',
                         'Review publishing terms and supply your own API key outside this tool',
                         'Review registry ingestion and complete-install instructions',
                         'Obtain explicit publication approval; this tool sends no request'],
        },
    }
    chosen = PLATFORMS if platform == 'all' else (platform,)
    return {'status': 'draft_only', 'submitted': False, 'publish_ready': False,
            'source_version': versions[0], 'drafts': [data[name] for name in chosen],
            'boundary': 'Metadata preparation only. No account, credential, upload, install or relicensing.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--platform', choices=(*PLATFORMS, 'all'), default='all')
    args = parser.parse_args(argv)
    try:
        result = drafts(args.source, args.platform)
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
