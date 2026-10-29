"""Integration contracts for source SVG, fallback media and Office extensions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from docx import Document
from PIL import Image

TOOLS = Path(__file__).resolve().parents[1] / 'tools/docx'
sys.path.insert(0, str(TOOLS))
import build_docx


class SvgDocxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icode-svg-docx-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.svg = self.root / 'figure.svg'
        self.svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60">'
                            '<title>Route</title><desc>Two linked nodes</desc>'
                            '<rect x="2" y="2" width="116" height="56" fill="#12AAA8"/></svg>', encoding='utf-8')
        self.md = self.root / 'guide.md'
        self.md.write_text('# Guide\n\n![Route](figure.svg)\n', encoding='utf-8')
        self.output = self.root / 'guide.docx'
        self.manifest = self.root / 'guide.manifest.json'

    def check(self):
        p = subprocess.run([sys.executable, '-B', str(TOOLS / 'inspect_docx.py'), str(self.output),
                            '--source', str(self.md), '--manifest', str(self.manifest)],
                           capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return json.loads(p.stdout)

    def test_svg_defaults_to_traced_png_and_passes_inspection(self):
        data = build_docx.build(self.md, self.output, self.manifest)
        record = data['source_images'][0]
        self.assertEqual(record['source_format'], 'svg')
        self.assertEqual(record['embedded_format'], 'png_fallback')
        self.assertFalse(record['vector_preserved'])
        self.assertNotEqual(record['sha256'], record['fallback_sha256'])
        self.assertEqual(data['diagram_status'], 'raster_fallback')
        shape = Document(self.output).inline_shapes[0]
        self.assertAlmostEqual(shape.width / shape.height, 2, places=4)
        self.assertEqual(shape._inline.docPr.get('descr'), 'Route')
        self.check()

    def test_dual_retains_source_and_fallback_but_compatibility_pending(self):
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        self.assertEqual(data['diagram_status'], 'native_vector_pending')
        self.assertEqual(data['consumer_compatibility'], 'pending')
        self.assertTrue(data['source_images'][0]['vector_preserved'])
        with zipfile.ZipFile(self.output) as z:
            self.assertTrue(any(n.endswith('.svg') for n in z.namelist()))
            self.assertIn(b'svgBlip', z.read('word/document.xml'))
            self.assertEqual(len([n for n in z.namelist() if n.endswith('.png')]), 1)
        self.check()

    def test_changed_svg_or_fallback_is_rejected(self):
        build_docx.build(self.md, self.output, self.manifest)
        self.svg.write_text(self.svg.read_text().replace('#12AAA8', '#FFFFFF'))
        p = subprocess.run([sys.executable, '-B', str(TOOLS / 'inspect_docx.py'), str(self.output),
                            '--manifest', str(self.manifest)], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)

    def test_dual_missing_content_type_or_svg_target_is_rejected(self):
        for missing in ('type', 'target'):
            with self.subTest(missing=missing):
                build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
                from lxml import etree as ET
                with zipfile.ZipFile(self.output) as z:
                    payload = {name: z.read(name) for name in z.namelist()}
                if missing == 'target':
                    payload = {name: value for name, value in payload.items() if not name.endswith('.svg')}
                else:
                    root = ET.fromstring(payload['[Content_Types].xml'])
                    for child in list(root):
                        if child.get('Extension') == 'svg':
                            root.remove(child)
                    payload['[Content_Types].xml'] = ET.tostring(root)
                with zipfile.ZipFile(self.output, 'w') as z:
                    for name, value in payload.items():
                        z.writestr(name, value)
                data = json.loads(self.manifest.read_text())
                data['output']['sha256'] = build_docx.sha256_file(self.output)
                self.manifest.write_text(json.dumps(data))
                p = subprocess.run([sys.executable, '-B', str(TOOLS / 'inspect_docx.py'), str(self.output),
                                    '--manifest', str(self.manifest)], capture_output=True, text=True)
                self.assertNotEqual(p.returncode, 0)

    def test_invalid_svg_cannot_replace_existing_output(self):
        self.output.write_bytes(b'original')
        self.svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script/></svg>')
        with self.assertRaises((ValueError, RuntimeError)):
            build_docx.build(self.md, self.output, self.manifest)
        self.assertEqual(self.output.read_bytes(), b'original')

    def test_png_behavior_remains_valid(self):
        Image.new('RGB', (20, 10), 'red').save(self.root / 'image.png')
        self.md.write_text('# Guide\n\n![Raster](image.png)\n')
        build_docx.build(self.md, self.output, self.manifest)
        self.check()

    def test_dual_does_not_change_an_unrelated_identical_png(self):
        from diagram_svg import prepare_svg
        record = prepare_svg(self.svg, self.root / 'preflight')
        (self.root / 'identical.png').write_bytes(Path(record['fallback_png']).read_bytes())
        self.md.write_text('# Guide\n\n![SVG](figure.svg)\n\n![Unrelated](identical.png)\n')
        build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        with zipfile.ZipFile(self.output) as z:
            self.assertEqual(z.read('word/document.xml').count(b'<asvg:svgBlip'), 1)
        self.check()

    def test_delivery_staging_cannot_remove_tmp_named_image_inputs(self):
        for target in ('guide.docx.tmp', 'guide.manifest.json.tmp'):
            with self.subTest(target=target):
                image = self.root / target
                Image.new('RGB', (20, 10), 'red').save(image, format='PNG')
                original = image.read_bytes()
                self.md.write_text(f'# Guide\n\n![Image]({target})\n', encoding='utf-8')
                build_docx.build(self.md, self.output, self.manifest)
                self.assertTrue(image.is_file(), 'staging removed an image input')
                self.assertEqual(image.read_bytes(), original, 'staging overwrote an image input')
                self.check()

    def test_invalid_manifest_destination_does_not_replace_existing_output(self):
        self.output.write_bytes(b'original output')
        self.manifest.mkdir()
        with self.assertRaises((OSError, ValueError)):
            build_docx.build(self.md, self.output, self.manifest)
        self.assertEqual(self.output.read_bytes(), b'original output')

    def test_svg_alt_text_must_match_its_bound_record(self):
        from lxml import etree as ET
        from diagram_ooxml import inspect_diagram_package
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        with zipfile.ZipFile(self.output) as z:
            payload = {name: z.read(name) for name in z.namelist()}
        root = ET.fromstring(payload['word/document.xml'])
        for drawing in root.iter('{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr'):
            drawing.set('descr', 'WRONG ALT')
        payload['word/document.xml'] = ET.tostring(root)
        with zipfile.ZipFile(self.output, 'w') as z:
            for name, value in payload.items():
                z.writestr(name, value)
        self.assertFalse(all(inspect_diagram_package(self.output, data['diagrams']).values()))

    def test_svg_extension_uri_must_be_the_native_svg_uri(self):
        from lxml import etree as ET
        from diagram_ooxml import inspect_diagram_package, SVG_EXTENSION
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        with zipfile.ZipFile(self.output) as z:
            payload = {name: z.read(name) for name in z.namelist()}
        root = ET.fromstring(payload['word/document.xml'])
        for extension in root.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}ext'):
            if extension.get('uri') == SVG_EXTENSION:
                extension.set('uri', '{00000000-0000-0000-0000-000000000000}')
        payload['word/document.xml'] = ET.tostring(root)
        with zipfile.ZipFile(self.output, 'w') as z:
            for name, value in payload.items():
                z.writestr(name, value)
        self.assertFalse(all(inspect_diagram_package(self.output, data['diagrams']).values()))

    def test_content_type_override_takes_precedence_over_svg_default(self):
        from lxml import etree as ET
        from diagram_ooxml import inspect_diagram_package
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        with zipfile.ZipFile(self.output) as z:
            payload = {name: z.read(name) for name in z.namelist()}
        root = ET.fromstring(payload['[Content_Types].xml'])
        svg_name = next(name for name in payload if name.endswith('.svg'))
        ET.SubElement(root, '{http://schemas.openxmlformats.org/package/2006/content-types}Override',
                      PartName='/' + svg_name, ContentType='application/octet-stream')
        payload['[Content_Types].xml'] = ET.tostring(root)
        with zipfile.ZipFile(self.output, 'w') as z:
            for name, value in payload.items():
                z.writestr(name, value)
        self.assertFalse(all(inspect_diagram_package(self.output, data['diagrams']).values()))

    def test_svg_alt_is_checked_on_bound_word_docpr(self):
        from lxml import etree as ET
        from diagram_ooxml import inspect_diagram_package
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        with zipfile.ZipFile(self.output) as z:
            payload = {name: z.read(name) for name in z.namelist()}
        root = ET.fromstring(payload['word/document.xml'])
        for drawing in root.iter('{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr'):
            drawing.set('descr', 'WRONG ALT')
        for props in root.iter('{http://schemas.openxmlformats.org/drawingml/2006/picture}cNvPr'):
            props.set('descr', data['diagrams'][0]['alt_text'])
        payload['word/document.xml'] = ET.tostring(root)
        with zipfile.ZipFile(self.output, 'w') as z:
            for name, value in payload.items():
                z.writestr(name, value)
        self.assertFalse(all(inspect_diagram_package(self.output, data['diagrams']).values()))

    def test_every_requested_drawing_must_exist_before_embedding(self):
        from diagram_ooxml import embed_svg_sources
        data = build_docx.build(self.md, self.output, self.manifest)
        record = {**data['diagrams'][0], 'embedded_format': 'svg_with_png_fallback'}
        missing = {**record, 'drawing_id': record['drawing_id'] + 100}
        original = self.output.read_bytes()
        with self.assertRaises(ValueError):
            embed_svg_sources(self.output, [record, missing])
        self.assertEqual(self.output.read_bytes(), original)

    def test_repeated_svg_with_distinct_alt_is_checked_per_drawing(self):
        self.md.write_text('# Guide\n\n![First](figure.svg)\n\n![Second](figure.svg)\n', encoding='utf-8')
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        self.assertEqual([item['alt_text'] for item in data['diagrams']], ['First', 'Second'])
        self.assertNotEqual(data['diagrams'][0]['drawing_id'], data['diagrams'][1]['drawing_id'])
        self.check()

    def test_duplicate_content_type_declarations_are_rejected(self):
        from lxml import etree as ET
        from diagram_ooxml import inspect_diagram_package
        data = build_docx.build(self.md, self.output, self.manifest, svg_mode='dual')
        with zipfile.ZipFile(self.output) as z:
            payload = {name: z.read(name) for name in z.namelist()}
        original_types = payload['[Content_Types].xml']
        for declaration, value in [('Default', 'image/svg+xml'), ('Default', 'application/octet-stream'),
                                   ('Override', 'image/svg+xml'), ('Override', 'application/octet-stream')]:
            with self.subTest(declaration=declaration, value=value):
                root = ET.fromstring(original_types)
                if declaration == 'Default':
                    ET.SubElement(root, '{http://schemas.openxmlformats.org/package/2006/content-types}Default',
                                  Extension='svg', ContentType=value)
                else:
                    svg_name = next(name for name in payload if name.endswith('.svg'))
                    for content_type in ('image/svg+xml', value):
                        ET.SubElement(root, '{http://schemas.openxmlformats.org/package/2006/content-types}Override',
                                      PartName='/' + svg_name, ContentType=content_type)
                payload['[Content_Types].xml'] = ET.tostring(root)
                with zipfile.ZipFile(self.output, 'w') as z:
                    for name, content in payload.items():
                        z.writestr(name, content)
                with self.assertRaises(ValueError):
                    inspect_diagram_package(self.output, data['diagrams'])


if __name__ == '__main__':
    unittest.main()
