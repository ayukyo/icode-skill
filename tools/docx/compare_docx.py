#!/usr/bin/env python3
"""Compare visible Word content and embedded figures; optionally enforce edit scope.

This is a preservation check, not a semantic review or a layout comparison.
Formatting and pagination still require rendering and page inspection.
"""
from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import posixpath
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from inspect_docx import A_NS, R_NS, REL_NS, W_NS, paragraph_content, sha256_file


def snapshot(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        body = document.find(W_NS + 'body')
        if body is None:
            raise ValueError('DOCX has no document body')
        relationships = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
        targets = {rel.get('Id'): posixpath.normpath('word/' + rel.get('Target', ''))
                   for rel in relationships.findall(REL_NS + 'Relationship')
                   if rel.get('TargetMode') != 'External'}
        blocks, figures, headings = [], [], []
        section = []
        for block in body:
            if block.tag == W_NS + 'sectPr':
                continue
            paragraphs = [block] if block.tag == W_NS + 'p' else block.findall('.//' + W_NS + 'p')
            text = [paragraph_content(p) for p in paragraphs]
            style = block.find(W_NS + 'pPr/' + W_NS + 'pStyle')
            style_name = style.get(W_NS + 'val', '') if style is not None else ''
            match = re.fullmatch(r'Heading([1-9])', style_name)
            if match or style_name == 'Title':
                level = int(match[1]) if match else 0
                while section and section[-1][0] >= level:
                    section.pop()
                section.append((level, text[0]))
                headings.append(text[0])
            image_count = 0
            for blip in block.findall('.//' + A_NS + 'blip'):
                target = targets.get(blip.get(R_NS + 'embed'), '')
                if not target.startswith('word/media/') or target not in archive.namelist():
                    raise ValueError('figure has a missing/external image relationship')
                figures.append({'number': len(figures) + 1, 'section': [item[1] for item in section],
                                'sha256': hashlib.sha256(archive.read(target)).hexdigest()})
                image_count += 1
            if block.tag == W_NS + 'tbl':
                content = [[ [paragraph_content(p) for p in cell.findall('.//' + W_NS + 'p')]
                             for cell in row.findall(W_NS + 'tc')]
                           for row in block.findall(W_NS + 'tr')]
            else:
                content = text
            blocks.append({'kind': 'table' if block.tag == W_NS + 'tbl' else 'paragraph',
                           'section': [item[1] for item in section], 'text': content,
                           'style': style_name, 'images': image_count})
        headers_footers = {name: [paragraph_content(p) for p in ET.fromstring(archive.read(name)).iter(W_NS + 'p')]
                           for name in archive.namelist() if re.fullmatch(r'word/(header|footer)\d+\.xml', name)}
    return {'sha256': sha256_file(path), 'blocks': blocks, 'figures': figures,
            'headings': headings, 'headers_footers': headers_footers}


def compare(before: Path, after: Path, sections: list[str], figure_numbers: list[int]) -> dict:
    old, new = snapshot(before), snapshot(after)
    for title in sections:
        if old['headings'].count(title) != 1 or new['headings'].count(title) != 1:
            raise ValueError('allowed section must exist uniquely in both versions: ' + title)
    for number in figure_numbers:
        if number < 1 or number > min(len(old['figures']), len(new['figures'])):
            raise ValueError('allowed figure must exist in both versions: ' + str(number))

    def outside(blocks):
        return [block for block in blocks if not any(title in block['section'] for title in sections)]

    def changes(left, right):
        encode = lambda block: json.dumps(block, ensure_ascii=False, sort_keys=True)
        matcher = SequenceMatcher(a=[encode(b) for b in left], b=[encode(b) for b in right], autojunk=False)
        return [{'kind': kind, 'before': left[a:b], 'after': right[c:d]}
                for kind, a, b, c, d in matcher.get_opcodes() if kind != 'equal']

    figure_changes = []
    for index in range(max(len(old['figures']), len(new['figures']))):
        left = old['figures'][index] if index < len(old['figures']) else None
        right = new['figures'][index] if index < len(new['figures']) else None
        if left != right:
            figure_changes.append({'number': index + 1, 'before': left, 'after': right})
    outside_figures_preserved = len(old['figures']) == len(new['figures']) and all(
        item['number'] in figure_numbers for item in figure_changes)
    checks = {'outside_sections_preserved': outside(old['blocks']) == outside(new['blocks']),
              'outside_figures_preserved': outside_figures_preserved,
              'headers_footers_preserved': old['headers_footers'] == new['headers_footers']}
    # Report removed text occurrences, including formulas and coordinate labels.
    def text_counter(data):
        def flatten(value):
            if isinstance(value, str):
                return [value] if value.strip() else []
            return [text for item in value for text in flatten(item)]
        return Counter(text for block in data['blocks'] for text in flatten(block['text']))
    return {'schema_version': 1, 'before': {'path': str(before.resolve()), 'sha256': old['sha256']},
            'after': {'path': str(after.resolve()), 'sha256': new['sha256']},
            'allowed_sections': sections, 'allowed_figures': figure_numbers, 'checks': checks,
            'passed': all(checks.values()), 'changes': changes(old['blocks'], new['blocks']),
            'figure_changes': figure_changes, 'removed_text': list((text_counter(old) - text_counter(new)).elements()),
            'scope': 'visible body/table/formula text, figure hashes and header/footer text; not correctness or layout'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--allow-section', action='append', default=[], help='exact unique Word heading; may repeat')
    parser.add_argument('--allow-figure', action='append', type=int, default=[], help='1-based embedded picture occurrence; may repeat')
    parser.add_argument('--report-only', action='store_true', help='list differences without enforcing scope')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report = compare(args.before, args.after, args.allow_section, args.allow_figure)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.report:
            args.report.write_text(payload, encoding='utf-8')
        print(payload, end='')
        return 0 if args.report_only or report['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError) as exc:
        print('ERROR: DOCX comparison failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
