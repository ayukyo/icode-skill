#!/usr/bin/env python3
"""Build Chinese SVG fixtures; managed LibreOffice and visual review stay pending.

Use the same installed tools/docx and tools/ppt/scripts as the delivery builders.
The output directory must be new and outside the repository. No dependencies or
fonts are installed. Repeat --font to supply explicit local font files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile

FONT_STACK = 'Noto Sans CJK SC, Microsoft YaHei, WenQuanYi Zen Hei, PingFang SC, sans-serif'
SVG_NS = 'http://www.w3.org/2000/svg'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
DIAGRAMS = (
    ('capture', '双路 IMU / RGB 采集路线', 'IMU 与 RGB 分别采集，统一时间戳后进入各自队列。'),
    ('timeline', '采样与曝光时间轴', '以毫秒相对时间示意 IMU 采样、RGB 曝光与主机接收关系，非实测数据。'),
    ('exception', '带异常分支的数据处理流程', '时间戳有效时正常发布，异常时记录并重试或丢弃。'),
)


def _text(root, x, y, text, size=34, anchor='middle', color='#172B4D'):
    ET.SubElement(root, 'text', {'x': str(x), 'y': str(y), 'font-size': str(size),
                  'font-family': FONT_STACK, 'text-anchor': anchor, 'fill': color}).text = text


def _box(root, x, y, text, fill='#E7F0FF', width=250, height=90):
    ET.SubElement(root, 'rect', {'x': str(x), 'y': str(y), 'width': str(width), 'height': str(height),
                  'rx': '12', 'fill': fill, 'stroke': '#285A89', 'stroke-width': '3'})
    _text(root, x + width / 2, y + height / 2 + 12, text)


def _arrow(root, x1, y1, x2, y2, *, dashed=False, head=True):
    attrs = {'x1': str(x1), 'y1': str(y1), 'x2': str(x2), 'y2': str(y2),
             'stroke': '#285A89', 'stroke-width': '3'}
    if head:
        attrs['marker-end'] = 'url(#arrow)'
    if dashed:
        attrs['stroke-dasharray'] = '10 7'
    ET.SubElement(root, 'line', attrs)


def write_diagram(path: Path, kind: str, title: str, description: str) -> None:
    root = ET.Element('svg', {'xmlns': SVG_NS, 'width': '1400', 'height': '680', 'viewBox': '0 0 1400 680'})
    ET.SubElement(root, 'title').text = title
    ET.SubElement(root, 'desc').text = description
    ET.SubElement(root, 'rect', {'x': '0', 'y': '0', 'width': '1400', 'height': '680', 'fill': 'white'})
    marker = ET.SubElement(ET.SubElement(root, 'defs'), 'marker', {'id': 'arrow', 'viewBox': '0 0 10 10',
                           'refX': '10', 'refY': '5', 'markerWidth': '8', 'markerHeight': '8', 'orient': 'auto'})
    ET.SubElement(marker, 'path', {'d': 'M 0 0 L 10 5 L 0 10 Z', 'fill': '#285A89'})
    _text(root, 700, 65, title, 42)
    if kind == 'capture':
        for y, labels, fill in ((170, ('IMU 采样', '惯性驱动', '统一时间戳', '惯性队列'), '#E0F3EC'),
                                (370, ('RGB 曝光', '图像驱动', '统一时间戳', '图像队列'), '#E7F0FF')):
            for x, label in zip((60, 400, 740, 1080), labels):
                _box(root, x, y, label, fill)
                if x < 1080:
                    _arrow(root, x + 250, y + 45, x + 330, y + 45)
        _text(root, 700, 570, '两路独立采集；时间戳统一后供上层对齐', 34)
        _text(root, 700, 625, '箭头表示数据传递方向', 30)
    elif kind == 'timeline':
        _text(root, 720, 135, '相对时间（ms），示意图，非实测数据', 34)
        _arrow(root, 230, 160, 1330, 160)
        for number, x in zip(range(0, 51, 10), range(300, 1301, 200)):
            _arrow(root, x, 160, x, 173, head=False)
            _text(root, x, 197, f'{number} ms', 28)
        for y, label, positions, fill in ((230, 'IMU 采样', (300, 520, 740, 960, 1180), '#E0F3EC'),
                                         (370, 'RGB 曝光', (410, 850), '#E7F0FF'),
                                         (510, '主机接收', (520, 960), '#FFF0D8')):
            _text(root, 115, y + 12, label, 32)
            _arrow(root, 230, y, 1330, y)
            for index, x in enumerate(positions, 1):
                ET.SubElement(root, 'rect', {'x': str(x - 40), 'y': str(y - 26), 'width': '80', 'height': '52',
                              'fill': fill, 'stroke': '#285A89', 'stroke-width': '3'})
                _text(root, x, y + 12, str(index), 30)
        _arrow(root, 410, 404, 520, 478, dashed=True)
        _arrow(root, 850, 404, 960, 478, dashed=True)
        _text(root, 700, 630, '虚线表示曝光帧到主机接收的对应关系', 32)
    elif kind == 'exception':
        _box(root, 490, 110, '接收数据帧', width=340, height=75)
        _box(root, 490, 250, '时间戳有效？', '#FFF0D8', width=340, height=75)
        _box(root, 490, 390, '校正并对齐', width=340, height=75)
        _box(root, 490, 540, '发布有效数据', '#E0F3EC', width=340, height=75)
        _box(root, 1000, 250, '记录异常', '#FFE6E0', width=300, height=75)
        _box(root, 1000, 390, '重试或丢弃', '#FFE6E0', width=300, height=75)
        _arrow(root, 660, 185, 660, 240)
        _arrow(root, 660, 325, 660, 380)
        _text(root, 600, 365, '是', 30)
        _arrow(root, 660, 465, 660, 530)
        _arrow(root, 830, 287, 990, 287)
        _text(root, 910, 270, '否', 30)
        _arrow(root, 1150, 325, 1150, 380)
        _text(root, 1145, 525, '保留错误记录', 30)
        _text(root, 255, 440, '有效路径', 32)
        _text(root, 700, 660, '异常分支不发布无效数据', 30)
    else:
        raise ValueError('unknown qualification diagram kind')
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def _paginate_docx(path: Path, manifest: Path) -> None:
    """Insert page breaks without reserializing or dropping SVG media parts."""
    # lxml keeps namespace declarations referenced only by mc:Ignorable;
    # ElementTree would rename/drop them and could force an Office repair.
    from lxml import etree
    with zipfile.ZipFile(path) as archive:
        parts = [(info, archive.read(info.filename)) for info in archive.infolist()]
    root = etree.fromstring(next(payload for info, payload in parts if info.filename == 'word/document.xml'))
    headings = [p for p in root.findall('.//' + W + 'p')
                if p.find('./' + W + 'pPr/' + W + 'pStyle') is not None and
                p.find('./' + W + 'pPr/' + W + 'pStyle').get(W + 'val') == 'Heading2']
    if len(headings) != 3:
        raise ValueError('qualification DOCX requires exactly three diagram headings')
    for paragraph in headings[1:]:
        etree.SubElement(paragraph.find(W + 'pPr'), W + 'pageBreakBefore')
    temporary = path.with_suffix('.layout.tmp')
    with zipfile.ZipFile(temporary, 'w') as archive:
        for info, payload in parts:
            archive.writestr(info, etree.tostring(root, encoding='utf-8', xml_declaration=True)
                             if info.filename == 'word/document.xml' else payload)
    temporary.replace(path)
    report = json.loads(manifest.read_text(encoding='utf-8'))
    report['output']['sha256'] = _sha256(path)
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualify(output_dir: Path, font_files: list[Path] | None = None) -> dict:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise ValueError('output directory must be new; refusing to overwrite existing files')
    repository = next((parent for parent in Path(__file__).resolve().parents if (parent / '.git').exists()), None)
    if repository and output_dir.is_relative_to(repository):
        raise ValueError('output directory must be outside the repository')
    import build_docx
    import diagram_svg
    # The PPT builder is a sibling of tools/docx in a complete installation.
    sibling = Path(__file__).resolve().parents[1] / 'ppt/scripts'
    if sibling.is_dir() and str(sibling) not in sys.path:
        sys.path.append(str(sibling))
    import build_pptx
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    output_dir.mkdir(parents=True)
    diagrams = []
    lines = ['# 中文 SVG 资格样例', '',
             '构建通过仅说明产物生成完成；受管 LibreOffice 打开、导出、再保存和视觉审核必须另行完成；Microsoft Office / WPS 为可选验证。', '']
    for kind, title, description in DIAGRAMS:
        source = output_dir / f'{kind}.svg'
        write_diagram(source, kind, title, description)
        record = diagram_svg.prepare_svg(source, output_dir / 'source-assets', width_inches=6.25,
                                         dpi=192, font_files=font_files)
        diagrams.append({'id': kind, 'title': title, 'description': description, 'source': str(source),
                         'sha256': _sha256(source), 'fallback_png': record['fallback_png']})
        lines += [f'## {title}', '', f'![{title}]({source.name})', '', description, '']
    guide = output_dir / 'guide.md'
    guide.write_text('\n'.join(lines), encoding='utf-8')
    template = output_dir / 'qualification-template.pptx'
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333333), Inches(7.5)
    for _, title, _ in DIAGRAMS:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(255, 255, 255)
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.25), Inches(12.3), Inches(0.7))
        run = title_box.text_frame.paragraphs[0].add_run()
        run.text, run.font.name, run.font.size = title, 'Noto Sans CJK SC', Pt(28)
        run.font.color.rgb = RGBColor(23, 43, 77)
    deck.save(template)
    edits = output_dir / 'qualification-edits.json'
    edits.write_text(json.dumps({'selected_slides': [1, 2, 3], 'edits': [], 'images': [
        {'slide': index, 'source': Path(item['source']).name, 'alt_text': item['title'],
         'fit': 'contain', 'box_inches': [0.5, 1.2, 12.3, 5.8]}
        for index, item in enumerate(diagrams, 1)]}, ensure_ascii=False, indent=2), encoding='utf-8')
    packages = []
    for mode in ('raster', 'dual'):
        docx = output_dir / f'qualification-{mode}.docx'
        manifest = output_dir / f'qualification-{mode}.docx.manifest.json'
        build_docx.build(guide, docx, manifest, svg_mode=mode, svg_dpi=192, svg_font_files=font_files)
        _paginate_docx(docx, manifest)
        packages.append({'path': str(docx), 'mode': mode, 'manifest': str(manifest)})
        pptx = output_dir / f'qualification-{mode}.pptx'
        result = build_pptx.run(SimpleNamespace(template=template, edits=edits, output=pptx,
                    detail=None, strict=True, dry_run=False, no_lint=True,
                    svg_mode=mode, svg_dpi=192, svg_font=font_files))
        if result != 0:
            raise ValueError(f'PPT qualification build failed with exit code {result}')
        packages.append({'path': str(pptx), 'mode': mode, 'manifest': str(pptx.with_suffix('.manifest.json'))})
    artifacts = [{'path': str(path), 'sha256': _sha256(path), 'bytes': path.stat().st_size}
                 for path in sorted(output_dir.rglob('*')) if path.is_file()]
    report = {'schema_version': 1, 'status': {'build': 'passed', 'libreoffice': 'pending', 'visual_review': 'pending'},
              'output_dir': str(output_dir), 'diagrams': diagrams, 'packages': packages, 'artifacts': artifacts,
              'scope': 'Build evidence only; managed LibreOffice open/export/resave and visual review required; '
                       'Microsoft Office/WPS optional. qualification.json excludes itself from the artifact hash list.'}
    (output_dir / 'qualification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--font', type=Path, action='append')
    args = parser.parse_args()
    try:
        report = qualify(args.output_dir, args.font)
        print(json.dumps({'qualification': str(Path(report['output_dir']) / 'qualification.json'),
                          'status': report['status']}, ensure_ascii=False, indent=2))
        return 0
    except (ImportError, OSError, ValueError, RuntimeError, TypeError) as exc:
        print('ERROR: SVG qualification failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
