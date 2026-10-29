"""Native SVG sequence output preserves the existing JSON diagram contract."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/docx'))
import draw_sequence as sequence

SVG = '{http://www.w3.org/2000/svg}'


class SequenceSvgTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='icode-sequence-svg-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.spec = self.base / 'sequence.json'
        self.palette = self.base / 'palette.json'
        self.output = self.base / 'nested' / 'sequence.svg'
        self.data = {'title': '请求 <&> "完成"', 'participants': [
            {'id': 'app', 'label': '应用 <&>', 'role': 'application'},
            {'id': 'sdk', 'label': 'SDK\n参数检查', 'role': 'sdk'}],
            'steps': [{'from': 'app', 'to': 'sdk', 'text': '发送 <&>'},
                      {'from': 'sdk', 'to': 'sdk', 'text': '内部检查'},
                      {'from': 'sdk', 'to': 'app', 'text': '返回', 'reply': True, 'condition': '成功 & 合法'}]}
        self.colors = {'application': {'fill': '#FFE0D0', 'stroke': '#B54E16', 'text': '#47200D'},
                       'sdk': {'fill': '#D6E9FF', 'stroke': '#1463AD', 'text': '#0A345B'}}
        self.write()

    def write(self):
        self.spec.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        self.palette.write_text(json.dumps(self.colors), encoding='utf-8')

    def render(self):
        try:
            report = sequence.draw(self.spec, self.palette, self.output)
        except ValueError as exc:
            self.fail(f'valid SVG output must be accepted: {exc}')
        return report, ET.parse(self.output).getroot()

    def test_svg_has_native_copyable_escaped_text_and_accessible_metadata(self):
        report, root = self.render()
        self.assertEqual(root.tag, SVG + 'svg')
        self.assertEqual(root.find(SVG + 'title').text, self.data['title'])
        self.assertTrue(root.find(SVG + 'desc').text)
        text = '\n'.join(''.join(node.itertext()) for node in root.findall(SVG + 'text'))
        for value in ('应用 <&>', 'SDK参数检查', '1. 发送 <&>', '2. 内部检查', '3. 返回（成功 & 合法）'):
            self.assertIn(value, text)
        for node in root.findall(SVG + 'text'):
            self.assertIn('Microsoft YaHei', node.attrib['font-family'])
            self.assertIn('Noto Sans CJK', node.attrib['font-family'])
            self.assertGreater(float(node.attrib['font-size']), 0)
        self.assertTrue(root.findall('.//' + SVG + 'tspan'))
        for node in root.iter():
            self.assertNotIn(node.tag.removeprefix(SVG), ('image', 'script', 'foreignObject', 'style'))
            self.assertFalse(any(key.endswith('href') or key.startswith('on') for key in node.attrib))
        self.assertNotIn('data:image', self.output.read_text(encoding='utf-8'))
        self.assertEqual(report['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_svg_geometry_keeps_palette_arrows_self_call_and_dashes(self):
        report, root = self.render()
        width, height = report['size']
        self.assertEqual(root.attrib['viewBox'], f'0 0 {width} {height}')
        self.assertEqual(root[2].tag, SVG + 'rect')
        self.assertEqual(root[2].attrib['fill'], 'white')
        self.assertEqual(float(root[2].attrib['width']), width)
        self.assertEqual(float(root[2].attrib['height']), height)
        boxes = [node for node in root.findall(SVG + 'rect') if 'rx' in node.attrib]
        self.assertEqual(len(boxes), 2)
        for box, role in zip(boxes, ('application', 'sdk')):
            self.assertEqual(box.attrib['fill'], self.colors[role]['fill'])
            self.assertEqual(box.attrib['stroke'], self.colors[role]['stroke'])
        lines = root.findall(SVG + 'line')
        # One solid request plus the three segments of the self call.
        solid = [node for node in lines if node.attrib['stroke-width'] == '3' and
                 (abs(float(node.attrib['x2']) - float(node.attrib['x1'])) > 18 or
                  abs(float(node.attrib['y2']) - float(node.attrib['y1'])) > 18)]
        self.assertEqual(len(solid), 4)
        self.assertEqual([float(solid[0].attrib[key]) for key in ('x1', 'x2')], [200, 520])
        self.assertEqual([float(solid[1].attrib[key]) for key in ('x1', 'x2')], [520, 610])
        self.assertEqual([float(solid[3].attrib[key]) for key in ('x1', 'x2')], [610, 520])
        dashes = [node for node in lines if node.attrib['stroke'] == '#1463AD' and
                  node.attrib['stroke-width'] == '3' and node not in solid]
        self.assertGreater(len(dashes), 3)
        self.assertTrue(all(0 < abs(float(node.attrib['x2']) - float(node.attrib['x1'])) <= 10 for node in dashes))
        arrows = root.findall(SVG + 'polygon')
        self.assertEqual(len(arrows), 3)
        for arrow, direction in zip(arrows, (1, -1, -1)):
            points = [tuple(map(float, point.split(','))) for point in arrow.attrib['points'].split()]
            self.assertEqual((points[0][0] - points[1][0]) / 13, direction)

    def test_layout_and_manifest_match_png_without_claiming_office_validation(self):
        report, root = self.render()
        png = self.base / 'sequence.png'
        png_report = sequence.draw(self.spec, self.palette, png)
        with Image.open(png) as image:
            self.assertEqual(list(image.size), report['size'])
        for key in ('participants', 'steps', 'size', 'spec_sha256', 'palette_sha256', 'scope', 'schema_version'):
            self.assertEqual(report[key], png_report[key])
        self.assertEqual(report['source_format'], 'json')
        self.assertEqual(report['output_format'], 'svg')
        self.assertTrue(report['native_svg_source'])
        self.assertEqual(report['office_validation'], 'not_performed')
        self.assertEqual(png_report['output_format'], 'png')
        self.assertFalse(png_report['native_svg_source'])

    def test_wrapped_labels_fit_canvas_and_keep_all_characters(self):
        self.data['participants'] += [
            {'id': f'p{number}', 'label': f'模块 {number}', 'role': 'sdk'} for number in range(3)]
        label = '宽标签&' * 6
        self.data['steps'][0]['text'] = label
        self.write()
        report, root = self.render()
        self.assertEqual(report['size'][0], 1680)
        node = next(node for node in root.findall(SVG + 'text') if ''.join(node.itertext()).startswith('1. '))
        self.assertEqual(''.join(node.itertext()), '1. ' + label)
        self.assertGreater(len(node.findall(SVG + 'tspan')), 1)
        for line in node.findall(SVG + 'tspan'):
            x, y = float(line.attrib['x']), float(line.attrib['y'])
            self.assertGreaterEqual(x, 0)
            self.assertLess(x, report['size'][0])
            self.assertGreaterEqual(y, 0)
            self.assertLess(y, report['size'][1])

    def test_invalid_inputs_fail_before_writing_svg(self):
        original = copy.deepcopy(self.data)
        cases = []
        for key, value in (('participants', []), ('steps', []), ('title', 3), ('parallel', True)):
            invalid = copy.deepcopy(original)
            invalid[key] = value
            cases.append(invalid)
        invalid = copy.deepcopy(original)
        invalid['steps'][0]['to'] = 'missing'
        cases.append(invalid)
        invalid = copy.deepcopy(original)
        invalid['steps'][0]['text'] = '过长说明' * 100
        cases.append(invalid)
        for invalid in cases:
            with self.subTest(invalid=invalid):
                self.data = invalid
                self.write()
                with self.assertRaises(ValueError):
                    sequence.draw(self.spec, self.palette, self.output)
                self.assertFalse(self.output.exists())
        self.data = original
        self.colors['sdk']['fill'] = 'url(https://invalid)'
        self.write()
        with self.assertRaises(ValueError):
            sequence.draw(self.spec, self.palette, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_svg_writes_parseable_manifest_and_rejects_other_extensions(self):
        manifest = self.base / 'manifest.json'
        result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'tools/docx/draw_sequence.py'),
                                 str(self.spec), str(self.output), '--palette', str(self.palette),
                                 '--manifest', str(manifest)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(manifest.read_text(encoding='utf-8'))['output_format'], 'svg')
        ET.parse(self.output)
        with self.assertRaisesRegex(ValueError, 'PNG or SVG'):
            sequence.draw(self.spec, self.palette, self.base / 'unsafe.html')

    def test_svg_rejects_invalid_xml_characters_before_creating_output(self):
        original = copy.deepcopy(self.data)
        for index, bad_character in enumerate(('\x00', '\x0b', '\ufffe', '\uffff')):
            for field in ('title', 'label', 'text', 'condition'):
                with self.subTest(character=repr(bad_character), field=field):
                    self.data = copy.deepcopy(original)
                    if field == 'title':
                        self.data[field] = 'invalid' + bad_character
                    elif field == 'label':
                        self.data['participants'][0][field] = 'invalid' + bad_character
                    else:
                        self.data['steps'][0][field] = 'invalid' + bad_character
                    self.write()
                    self.output = self.base / f'invalid-{index}-{field}.svg'
                    with self.assertRaisesRegex(ValueError, 'XML'):
                        sequence.draw(self.spec, self.palette, self.output)
                    self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
