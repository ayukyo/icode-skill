#!/usr/bin/env python3
"""Draw a local, labeled sequence diagram with an explicit shared role palette.

Input is JSON, not Mermaid. Requests, replies, self calls and per-step condition
labels are supported. Loops/parallel blocks are not inferred or simplified.
Split long sequences into real stages rather than shrinking an unreadable image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw
from build_docx import font_for_mermaid


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('JSON input must be an object')
    return data


def validate(spec: dict, palette: dict) -> None:
    if set(spec) - {'title', 'participants', 'steps'}:
        raise ValueError('unsupported sequence fields; do not silently simplify loops or parallel blocks')
    participants, steps = spec.get('participants'), spec.get('steps')
    if not isinstance(participants, list) or not 2 <= len(participants) <= 5:
        raise ValueError('sequence requires 2 to 5 participants')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 10:
        raise ValueError('sequence requires 1 to 10 steps; split longer diagrams into stages')
    if not isinstance(spec.get('title', ''), str):
        raise ValueError('title must be text')
    ids = set()
    for participant in participants:
        if not isinstance(participant, dict) or set(participant) != {'id', 'label', 'role'}:
            raise ValueError('participant requires id, label and role only')
        for key in ('id', 'label', 'role'):
            if not isinstance(participant[key], str) or not participant[key].strip():
                raise ValueError('participant fields must be nonempty text')
        if participant['id'] in ids:
            raise ValueError('participant IDs must be unique')
        ids.add(participant['id'])
        colors = palette.get(participant['role'])
        if not isinstance(colors, dict) or set(colors) != {'fill', 'stroke', 'text'}:
            raise ValueError('each participant role needs explicit fill/stroke/text colors in the shared palette')
        if any(not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value) for value in colors.values()):
            raise ValueError('palette colors must be #RRGGBB')
    for step in steps:
        if not isinstance(step, dict) or set(step) - {'from', 'to', 'text', 'reply', 'condition'}:
            raise ValueError('unsupported step fields')
        if step.get('from') not in ids or step.get('to') not in ids:
            raise ValueError('every step must refer to declared participants')
        if not isinstance(step.get('text'), str) or not step['text'].strip():
            raise ValueError('every step requires nonempty text')
        if type(step.get('reply', False)) is not bool or not isinstance(step.get('condition', ''), str):
            raise ValueError('reply must be boolean and condition must be text')


def wrap(text: str, font, width: int) -> str:
    lines = []
    for raw in text.splitlines():
        current = ''
        for char in raw:
            bbox = font.getbbox(current + char)
            if current and bbox[2] - bbox[0] > width:
                lines.append(current)
                current = ''
            current += char
        lines.append(current)
    if len(lines) > 4:
        raise ValueError('label is too long; move explanation to the caption or split the diagram')
    return '\n'.join(lines)


class SvgCanvas:
    """Small SVG adapter for the drawing primitives used by this diagram.

    Pillow still measures text, so both output formats share wrapping, spacing
    and geometry. SVG contains native shapes and editable text, never a bitmap.
    """

    def __init__(self, width: int, height: int, title: str):
        self.root = ET.Element('svg', {'xmlns': 'http://www.w3.org/2000/svg',
                                     'width': str(width), 'height': str(height),
                                     'viewBox': f'0 0 {width} {height}',
                                     'role': 'img', 'aria-labelledby': 'title description'})
        ET.SubElement(self.root, 'title', {'id': 'title'}).text = title or 'Sequence diagram'
        ET.SubElement(self.root, 'desc', {'id': 'description'}).text = (
            'Labeled sequence diagram. Arrows follow numbered messages; dashed arrows indicate replies.')
        self.measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        self.rectangle((0, 0, width, height), fill='white')

    def rectangle(self, box, fill):
        left, top, right, bottom = box
        return ET.SubElement(self.root, 'rect', {'x': str(left), 'y': str(top),
                             'width': str(right - left), 'height': str(bottom - top), 'fill': fill})

    def rounded_rectangle(self, box, radius, fill, outline, width):
        node = self.rectangle(box, fill)
        node.set('rx', str(radius))
        node.set('stroke', outline)
        node.set('stroke-width', str(width))

    def line(self, points, fill, width):
        if len(points) == 2:
            (x1, y1), (x2, y2) = points
        else:
            x1, y1, x2, y2 = points
        ET.SubElement(self.root, 'line', {'x1': str(x1), 'y1': str(y1),
                      'x2': str(x2), 'y2': str(y2), 'stroke': fill, 'stroke-width': str(width)})

    def polygon(self, points, fill):
        ET.SubElement(self.root, 'polygon', {'points': ' '.join(f'{x},{y}' for x, y in points),
                      'fill': fill})

    def multiline_textbbox(self, xy, text, font, spacing):
        return self.measure.multiline_textbbox(xy, text, font=font, spacing=spacing)

    def multiline_text(self, xy, text, fill, font, spacing):
        # Pillow's default horizontal anchor is left/ascender. SVG positions
        # text at the baseline; use the same font ascent and line advance.
        ascent = font.getmetrics()[0] if hasattr(font, 'getmetrics') else font.getbbox('A')[3]
        advance = self.measure.textbbox((0, 0), 'A', font=font)[3] + spacing
        node = ET.SubElement(self.root, 'text', {'fill': fill, 'font-size': str(getattr(font, 'size', 10)),
                             'font-family': 'Microsoft YaHei, Noto Sans CJK SC, Droid Sans Fallback, PingFang SC, sans-serif',
                             '{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
        for index, line in enumerate(text.split('\n')):
            ET.SubElement(node, 'tspan', {'x': str(xy[0]),
                          'y': str(xy[1] + ascent + index * advance)}).text = line

    def save(self, output: Path):
        ET.ElementTree(self.root).write(output, encoding='utf-8', xml_declaration=True)


def draw(spec_path: Path, palette_path: Path, output: Path) -> dict:
    output_format = output.suffix.lower().lstrip('.')
    if output_format not in {'png', 'svg'}:
        raise ValueError('output must be a PNG or SVG file')
    spec, palette = read_json(spec_path), read_json(palette_path)
    validate(spec, palette)
    if output_format == 'svg':
        # ElementTree escapes markup but does not reject XML 1.0 control
        # characters. Check original labels before wrapping can remove them.
        texts = [spec.get('title', '')] + [p['label'] for p in spec['participants']]
        texts += [value for step in spec['steps'] for value in (step['text'], step.get('condition', ''))]
        if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', text) for text in texts):
            raise ValueError('SVG labels must contain valid XML 1.0 characters')
    font = font_for_mermaid()
    participants = spec['participants']
    width = max(840, len(participants) * 320 + 80)
    positions = {participant['id']: 200 + index * 320 for index, participant in enumerate(participants)}
    title = wrap(spec.get('title', ''), font, width - 80)
    title_height = max(0, len(title.splitlines()) * 36)
    top = 30 + title_height + 20
    labels = [wrap(p['label'], font, 252) for p in participants]
    box_height = max(68, max(len(label.splitlines()) for label in labels) * 36 + 20)
    cursor = top + box_height + 50
    rows = []
    for number, step in enumerate(spec['steps'], 1):
        left, right = positions[step['from']], positions[step['to']]
        condition = f"（{step['condition']}）" if step.get('condition') else ''
        available = max(270, abs(right - left) - 30)
        text = wrap(f"{number}. {step['text']}{condition}", font, available)
        label_height = len(text.splitlines()) * 36
        rows.append((step, text, cursor, cursor + label_height + 12))
        cursor += label_height + (84 if left == right else 60)
    height = cursor + 30
    if output_format == 'svg':
        canvas = SvgCanvas(width, height, spec.get('title', ''))
    else:
        image = Image.new('RGB', (width, height), 'white')
        canvas = ImageDraw.Draw(image)
    if title:
        canvas.multiline_text((40, 20), title, fill='#172B4D', font=font, spacing=4)
    for participant, label in zip(participants, labels):
        x = positions[participant['id']]
        for y in range(top + box_height, height - 25, 18):
            canvas.line((x, y, x, min(y + 9, height - 25)), fill='#CAD2DC', width=2)
        colors = palette[participant['role']]
        canvas.rounded_rectangle((x - 145, top, x + 145, top + box_height), radius=12,
                                 fill=colors['fill'], outline=colors['stroke'], width=3)
        bbox = canvas.multiline_textbbox((0, 0), label, font=font, spacing=4)
        canvas.multiline_text((x - (bbox[2] - bbox[0]) / 2, top + 10), label,
                              fill=colors['text'], font=font, spacing=4)
    for step, text, text_y, arrow_y in rows:
        left, right = positions[step['from']], positions[step['to']]
        center = (left + right) / 2
        bbox = canvas.multiline_textbbox((0, 0), text, font=font, spacing=4)
        tx = min(width - 30 - (bbox[2] - bbox[0]), max(30, center - (bbox[2] - bbox[0]) / 2))
        canvas.rectangle((tx - 6, text_y, tx + bbox[2] - bbox[0] + 6,
                          text_y + bbox[3] - bbox[1] + 12), fill='white')
        canvas.multiline_text((tx, text_y), text, fill='#172B4D', font=font, spacing=4)
        color = palette[next(p['role'] for p in participants if p['id'] == step['from'])]['stroke']
        if left == right:
            points = [(left, arrow_y), (left + 90, arrow_y), (left + 90, arrow_y + 30), (left, arrow_y + 30)]
        else:
            points = [(left, arrow_y), (right, arrow_y)]
        for start, end in zip(points, points[1:]):
            if step.get('reply'):
                distance = abs(end[0] - start[0]) + abs(end[1] - start[1])
                for offset in range(0, distance, 18):
                    first, last = offset / distance, min(offset + 10, distance) / distance
                    canvas.line((start[0] + (end[0] - start[0]) * first, start[1] + (end[1] - start[1]) * first,
                                 start[0] + (end[0] - start[0]) * last, start[1] + (end[1] - start[1]) * last), fill=color, width=3)
            else:
                canvas.line([start, end], fill=color, width=3)
        start, end = points[-2:]
        sign = 1 if end[0] > start[0] else -1
        canvas.polygon([end, (end[0] - sign * 13, end[1] - 7), (end[0] - sign * 13, end[1] + 7)], fill=color)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output_format == 'svg':
        canvas.save(output)
    else:
        image.save(output, format='PNG')
    return {'schema_version': 1, 'output': str(output.resolve()), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'spec_sha256': hashlib.sha256(spec_path.read_bytes()).hexdigest(),
            'palette_sha256': hashlib.sha256(palette_path.read_bytes()).hexdigest(),
            'participants': participants, 'steps': spec['steps'], 'size': [width, height],
            'source_format': 'json', 'output_format': output_format,
            'native_svg_source': output_format == 'svg', 'office_validation': 'not_performed',
            'scope': 'labeled sequence rendering; implementation correctness and page readability require review'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--palette', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    try:
        report = draw(args.spec, args.palette, args.output)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.manifest:
            args.manifest.write_text(payload, encoding='utf-8')
        print(payload, end='')
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print('ERROR: DOCX sequence drawing failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
