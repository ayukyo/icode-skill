"""Preserve SVG with a PNG fallback; inspect representation, not visual quality.

The Microsoft Drawing SVG extension is shared by Word and PowerPoint. PNG
remains the standard a:blip, so consumers without SVG support can display it.
No consumer compatibility claim is implied by creating the extension.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import posixpath
import tempfile
import zipfile

from lxml import etree as ET


A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
SVG = 'http://schemas.microsoft.com/office/drawing/2016/SVG/main'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
SVG_EXTENSION = '{96DAC541-7B7A-43D3-8B79-37D633B846F1}'
IMAGE_REL = R + '/image'


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse(payload: bytes):
    try:
        return ET.fromstring(payload, ET.XMLParser(resolve_entities=False, no_network=True))
    except ET.XMLSyntaxError as exc:
        raise ValueError('invalid diagram OOXML') from exc


def xml_bytes(root) -> bytes:
    return ET.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)


def rels_name(part: str) -> str:
    return posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')


def target_name(part: str, target: str) -> str:
    result = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                               posixpath.join(posixpath.dirname(part), target))
    if result.startswith('../') or result == '..' or '\\' in result:
        raise ValueError('image relationship escapes OOXML package')
    return result


def drawing_parts(payload: dict[str, bytes]):
    return [name for name in payload if name == 'word/document.xml' or
            (name.startswith('ppt/slides/slide') and name.endswith('.xml') and '/_rels/' not in name)]


def slide_order(payload):
    if 'ppt/presentation.xml' not in payload:
        return {}
    part = 'ppt/presentation.xml'
    rels = {el.get('Id'): el for el in parse(payload[rels_name(part)])}
    names = [target_name(part, rels[el.get('{' + R + '}id')].get('Target'))
             for el in parse(payload[part]).iter('{http://schemas.openxmlformats.org/presentationml/2006/main}sldId')]
    return {name: index for index, name in enumerate(names, 1)}


def drawing_container(blip, word):
    ancestor = blip
    tag = '{' + W + '}drawing' if word else '{' + P + '}pic'
    while ancestor.getparent() is not None and ancestor.tag != tag:
        ancestor = ancestor.getparent()
    return ancestor


def matches_location(item, part, container, order):
    if part.startswith('word/') and 'drawing_id' in item:
        return any(el.get('id') == str(item['drawing_id']) for el in container.iter('{' + WP + '}docPr'))
    if part.startswith('ppt/') and 'shape_id' in item and 'output_slide' in item:
        return order.get(part) == item['output_slide'] and any(
            el.get('id') == str(item['shape_id']) for el in container.iter('{' + P + '}cNvPr'))
    return True


def content_type_index(root):
    """Validate unique OPC declarations before applying Override precedence."""
    if root.tag != '{' + CT + '}Types':
        raise ValueError('invalid content types namespace')
    defaults, overrides = {}, {}
    for element in root:
        if element.tag == '{' + CT + '}Default':
            key = element.get('Extension', '').lower()
            index = defaults
        elif element.tag == '{' + CT + '}Override':
            key = element.get('PartName', '')
            index = overrides
        else:
            raise ValueError('invalid content type declaration')
        value = element.get('ContentType', '')
        if not key or not value or key in index:
            raise ValueError('missing or duplicate content type declaration')
        index[key] = value
    return defaults, overrides


def effective_content_type(index, name):
    defaults, overrides = index
    return overrides.get('/' + name, defaults.get(posixpath.splitext(name)[1][1:].lower()))


def embed_svg_sources(package: Path, diagrams: list[dict]) -> None:
    """Atomically add explicitly requested dual representations to a new package."""
    requested = [item for item in diagrams if item.get('embedded_format') == 'svg_with_png_fallback']
    if not requested:
        return
    by_fallback, source_bytes = {}, {}
    for item in requested:
        source = Path(item['source']).read_bytes()
        fallback = Path(item['fallback_png']).read_bytes()
        if digest(source) != item['sha256'] or digest(fallback) != item['fallback_sha256']:
            raise ValueError('diagram source/fallback changed before embedding')
        key = item['fallback_sha256']
        if key in by_fallback and by_fallback[key]['sha256'] != item['sha256']:
            raise ValueError('ambiguous SVG sources share one PNG fallback')
        by_fallback[key] = item
        source_bytes[item['sha256']] = source
    with zipfile.ZipFile(package) as archive:
        payload = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    order = slide_order(payload)
    content_types = parse(payload['[Content_Types].xml'])
    type_index = content_type_index(content_types)
    if 'svg' not in type_index[0]:
        ET.SubElement(content_types, '{' + CT + '}Default', Extension='svg', ContentType='image/svg+xml')
        type_index = content_type_index(content_types)
    matched = set()
    for part in drawing_parts(payload):
        root = parse(payload[part])
        rel_path = rels_name(part)
        if rel_path not in payload:
            continue
        rels = parse(payload[rel_path])
        indexed = {el.get('Id'): el for el in rels}
        changed = False
        for blip in root.iter('{' + A + '}blip'):
            rel = indexed.get(blip.get('{' + R + '}embed'))
            if rel is None or rel.get('TargetMode') == 'External':
                continue
            image_name = target_name(part, rel.get('Target', ''))
            if image_name not in payload:
                raise ValueError('fallback media is missing')
            key = digest(payload[image_name])
            if key not in by_fallback:
                continue
            container = drawing_container(blip, part.startswith('word/'))
            candidates = [(index, item) for index, item in enumerate(requested)
                          if item['fallback_sha256'] == key and matches_location(item, part, container, order)]
            if not candidates:
                continue
            if len(candidates) != 1:
                raise ValueError('ambiguous SVG drawing records')
            record_index, item = candidates[0]
            if blip.find('.//{' + SVG + '}svgBlip') is not None:
                raise ValueError('diagram already has an SVG representation')
            prefix = part.split('/')[0]
            media = f'{prefix}/media/icode-{item["sha256"]}.svg'
            if effective_content_type(type_index, media) != 'image/svg+xml':
                raise ValueError('conflicting SVG content type')
            payload[media] = source_bytes[item['sha256']]
            rid_number = 1
            while f'rId{rid_number}' in indexed:
                rid_number += 1
            rid = f'rId{rid_number}'
            relation = ET.SubElement(rels, '{' + REL + '}Relationship', Id=rid,
                                     Type=IMAGE_REL,
                                     Target=posixpath.relpath(media, posixpath.dirname(part)))
            indexed[rid] = relation
            ext_list = blip.find('{' + A + '}extLst')
            if ext_list is None:
                ext_list = ET.SubElement(blip, '{' + A + '}extLst')
            extension = ET.SubElement(ext_list, '{' + A + '}ext', uri=SVG_EXTENSION)
            ET.SubElement(extension, '{' + SVG + '}svgBlip',
                          {'{' + R + '}embed': rid}, nsmap={'asvg': SVG})
            changed = True
            matched.add(record_index)
        if changed:
            payload[part] = xml_bytes(root)
            payload[rel_path] = xml_bytes(rels)
    if matched != set(range(len(requested))):
        raise ValueError('not every SVG record has its bound PNG fallback')
    payload['[Content_Types].xml'] = xml_bytes(content_types)
    with tempfile.NamedTemporaryFile(prefix='.svg-ooxml-', suffix='.zip', dir=package.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, value in payload.items():
                archive.writestr(name, value)
        temporary.replace(package)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_diagram_package(package: Path, diagrams: list[dict]) -> dict[str, bool]:
    """Bind source, generated PNG and actual drawing relationships to records."""
    checks = {key: True for key in ('diagram_sources_unchanged', 'diagram_fallbacks_unchanged',
              'diagram_media_embedded', 'diagram_svg_relationships_valid',
              'diagram_dimensions_match', 'diagram_alt_text_present', 'diagram_svg_content_types_valid')}
    with zipfile.ZipFile(package) as archive:
        payload = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    types = parse(payload['[Content_Types].xml'])
    order = slide_order(payload)
    type_index = content_type_index(types)
    occurrences = []
    for part in drawing_parts(payload):
        root = parse(payload[part])
        if rels_name(part) not in payload:
            continue
        rels = {el.get('Id'): el for el in parse(payload[rels_name(part)])}
        for blip in root.iter('{' + A + '}blip'):
            rel = rels.get(blip.get('{' + R + '}embed'))
            png_hash = None
            if rel is not None and rel.get('Type') == IMAGE_REL and rel.get('TargetMode') != 'External':
                name = target_name(part, rel.get('Target', ''))
                if name in payload:
                    png_hash = digest(payload[name])
            svg_hashes = []
            for svg in blip.iter('{' + SVG + '}svgBlip'):
                extension = svg.getparent()
                extension_list = extension.getparent() if extension is not None else None
                valid_extension = (extension is not None and extension.tag == '{' + A + '}ext'
                                   and extension.get('uri') == SVG_EXTENSION
                                   and extension_list is not None and extension_list.tag == '{' + A + '}extLst'
                                   and extension_list.getparent() is blip)
                svg_rel = rels.get(svg.get('{' + R + '}embed'))
                svg_hash = None
                if svg_rel is not None and svg_rel.get('Type') == IMAGE_REL and svg_rel.get('TargetMode') != 'External':
                    name = target_name(part, svg_rel.get('Target', ''))
                    if name in payload and name.endswith('.svg'):
                        svg_hash = digest(payload[name])
                        checks['diagram_svg_content_types_valid'] &= effective_content_type(type_index, name) == 'image/svg+xml'
                svg_hashes.append(svg_hash)
                checks['diagram_svg_relationships_valid'] &= bool(svg_hash and png_hash and valid_extension)
            ancestor = drawing_container(blip, part.startswith('word/'))
            property_tag = '{' + WP + '}docPr' if part.startswith('word/') else '{' + P + '}cNvPr'
            alt = [el.get('descr', '') for el in ancestor.iter(property_tag)]
            extent_tag = '{' + WP + '}extent' if part.startswith('word/') else '{' + A + '}ext'
            extents = [el for el in ancestor.iter(extent_tag) if el.get('cx') and el.get('cy')]
            ratio = float(extents[0].get('cx')) / float(extents[0].get('cy')) if extents and float(extents[0].get('cy')) else None
            occurrences.append((png_hash, svg_hashes, ratio, alt, part, ancestor))
    for item in diagrams:
        try:
            checks['diagram_sources_unchanged'] &= digest(Path(item['source']).read_bytes()) == item['sha256']
            checks['diagram_fallbacks_unchanged'] &= digest(Path(item['fallback_png']).read_bytes()) == item['fallback_sha256']
        except OSError:
            checks['diagram_sources_unchanged'] = False
            checks['diagram_fallbacks_unchanged'] = False
        matches = [occ for occ in occurrences if occ[0] == item['fallback_sha256']
                   and matches_location(item, occ[4], occ[5], order)]
        checks['diagram_media_embedded'] &= bool(matches)
        width, height = item['pixel_width'], item['pixel_height']
        if (isinstance(width, bool) or isinstance(height, bool) or
                not isinstance(width, (int, float)) or not isinstance(height, (int, float)) or
                not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0):
            raise ValueError('invalid diagram pixel dimensions in manifest')
        for _, svg_hashes, ratio, alt, _, _ in matches:
            checks['diagram_dimensions_match'] &= bool(ratio and math.isclose(ratio, width / height, rel_tol=0.002))
            expected_alt = item.get('alt_text')
            checks['diagram_alt_text_present'] &= (
                isinstance(expected_alt, str) and bool(expected_alt.strip()) and expected_alt in alt
                if expected_alt is not None else any(text.strip() for text in alt))
            if item.get('embedded_format') == 'svg_with_png_fallback':
                checks['diagram_svg_relationships_valid'] &= svg_hashes == [item['sha256']]
            else:
                checks['diagram_svg_relationships_valid'] &= not svg_hashes
    return checks
