"""Contracts for the actual bilingual, script-free workflow presentation."""
import copy
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('plan', 'review', 'merge', 'code', 'deepcheck', 'audit')
DOCS = ('01_plan.md', '02_review.md', '03_merge.md', '04_code.md',
        '05_deepcheck.md', '06_audit.md')


class Element:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ''.join(child if isinstance(child, str) else child.text()
                       for child in self.children)

    def find(self, tag=None, cls=None):
        result = []
        for child in self.children:
            if isinstance(child, Element):
                if ((tag is None or child.tag == tag)
                        and (cls is None or cls in child.attrs.get('class', '').split())):
                    result.append(child)
                result.extend(child.find(tag, cls))
        return result


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = Element('document')
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs)
        self.stack[-1].children.append(element)
        if tag not in {'meta', 'link', 'br', 'hr', 'img', 'input'}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        if self.stack[-1].tag != tag:
            raise AssertionError('unbalanced markup: ' + tag)
        self.stack.pop()

    def handle_data(self, text):
        self.stack[-1].children.append(text)


class PublicDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icode-design-test-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('design_builder', ROOT / 'tools/build_public_site.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.data = json.loads((ROOT / 'site/content.json').read_text())

    def pages(self):
        self.builder.build(ROOT, self.work / 'public', self.builder.DEFAULT_BASE)
        return [Page((self.work / 'public' / path).read_text()).root
                for path in ('index.html', 'en/index.html')]

    def test_six_native_stages_in_order_with_real_doc_links(self):
        for page in self.pages():
            stages = page.find('details', 'stage')
            self.assertEqual([s.attrs.get('id') for s in stages], ['stage-' + s for s in STAGES])
            self.assertEqual([i for i, s in enumerate(stages) if 'open' in s.attrs], [])
            for stage, doc in zip(stages, DOCS):
                self.assertEqual(len(stage.find('summary')), 1)
                self.assertEqual(len(stage.find('dt')), 3)
                self.assertTrue(stage.find('p')[0].text().strip())
                self.assertIn(self.builder.REPO + '/blob/main/steps/' + doc,
                              [a.attrs.get('href') for a in stage.find('a')])
                self.assertTrue((ROOT / 'steps' / doc).is_file())
            self.assertIn('03_plan_final.md', stages[2].text())
            self.assertNotIn('04_code.md', stages[3].text())  # doc link != fake output
            self.assertRegex(stages[3].text(), r'(?i)source|源码')

    def test_hosts_navigation_and_illustration_are_explicit(self):
        for page in self.pages():
            hosts = page.find('ul', 'hosts')
            self.assertEqual(len(hosts), 1)
            self.assertEqual([li.text() for li in hosts[0].find('li')], ['Claude Code', 'Codex', 'CodeBuddy'])
            self.assertRegex(page.find('aside')[0].text(), r'(?i)not a live run|非实际运行')
            links = {a.attrs.get('href') for a in page.find('a')}
            self.assertTrue({'#flow', '#capabilities', '#install'} <= links)
            self.assertFalse(page.find('script') or page.find('form') or page.find('iframe'))

    def test_optional_paths_scenes_and_evidence_boundaries(self):
        for page in self.pages():
            self.assertEqual(len(page.find('div', 'optional-grid')), 1)
            paths = page.find('div', 'optional-grid')[0].find('article')
            self.assertEqual(len(paths), 3)
            self.assertRegex(paths[-1].text(), r'(?i)completed|已完成')
            self.assertRegex(paths[-1].text(), r'(?i)optional|可选')
            scenes = page.find('div', 'scenes')[0].find('article')
            self.assertEqual(len(scenes), 6)
            for scene in scenes:
                self.assertTrue(scene.find('code', 'example-command'))
                self.assertEqual(len(scene.find('details')), 1)
                self.assertNotIn('open', scene.find('details')[0].attrs)
            self.assertRegex(scenes[0].text(), r'(?i)do not change code|不改代码')
            self.assertIn('/icode verify --listen', scenes[3].text())
            self.assertRegex(scenes[3].text(), r'(?i)does not implicitly|不隐式')
            self.assertRegex(scenes[4].text(), r'(?i)text-only|纯文本')
            self.assertRegex(scenes[5].text(), r'(?i)does not connect|不连接')
            evidence = page.find('div', 'delivery-grid')[0].find('article')
            self.assertEqual(len(evidence), 4)
            self.assertIn('pending', evidence[-1].attrs.get('class', '').split())
            self.assertRegex(evidence[-1].text(), r'(?i)not verified|未验证')

    def test_structured_content_is_strict_and_bilingual(self):
        shapes = {'stages': STAGES, 'paths': ('intake', 'verify', 'crosscheck'),
                  'scenes': ('develop', 'logs', 'review', 'verify', 'docs', 'manage'),
                  'delivery': ('design', 'code', 'evidence', 'pending')}
        for field, ids in shapes.items():
            for lang, locale in self.data['locales'].items():
                self.assertIn(field, locale)
                self.assertEqual(tuple(r['id'] for r in locale[field]), ids)
                mutations = [None, [], locale[field][:-1], list(reversed(locale[field]))]
                for record_change in ({'id': 'unknown'}, {'extra': 'reject'}, {'title': None}):
                    records = copy.deepcopy(locale[field])
                    records[0].update(record_change)
                    mutations.append(records)
                for replacement in mutations:
                    invalid = copy.deepcopy(self.data)
                    invalid['locales'][lang][field] = replacement
                    with self.subTest(field=field, lang=lang, replacement=replacement):
                        with self.assertRaises(ValueError):
                            self.builder.content_checked(invalid)

    def test_rendered_verify_commands_match_the_real_parser(self):
        spec = importlib.util.spec_from_file_location('site_verify_parser', ROOT / 'tools/verify_request.py')
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
        for page in self.pages():
            commands = {line.strip() for code in page.find('code') for line in code.text().splitlines()
                        if line.strip().startswith('/icode verify ')}
            self.assertGreaterEqual(len(commands), 3)
            for command in commands:
                with self.subTest(command=command):
                    try:
                        parsed = parser.parse_request(command)
                    except ValueError as exc:
                        self.fail(f'public example rejected: {command}: {exc}')
                    self.assertTrue(parsed['actions'])
                    if 'device_test' in parsed['actions']:
                        self.assertTrue(parsed['target'])

    def test_new_nested_text_is_escaped_and_bad_input_creates_no_output(self):
        self.assertIn('stages', self.data['locales']['en'])
        root = self.work / 'source'
        shutil.copytree(ROOT / 'site', root / 'site')
        shutil.copy2(ROOT / 'SKILL.md', root / 'SKILL.md')
        file = root / 'site/content.json'
        self.data['locales']['en']['stages'][0]['purpose'] = '<img src=x onerror=alert(1)>'
        file.write_text(json.dumps(self.data))
        self.builder.build(root, self.work / 'escaped', self.builder.DEFAULT_BASE)
        html = (self.work / 'escaped/en/index.html').read_text()
        self.assertIn('&lt;img', html)
        self.assertFalse(Page(html).root.find('img'))
        for index, value in enumerate(('', '  ', None, ['wrong type'], '\x00')):
            self.data['locales']['en']['stages'][0]['purpose'] = value
            file.write_text(json.dumps(self.data))
            out = self.work / ('bad-' + str(index))
            with self.assertRaises(ValueError):
                self.builder.build(root, out, self.builder.DEFAULT_BASE)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
