"""Agent-readable pages share reviewed public inputs, not a private crawler."""
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class AgentReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icode-agent-reading-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('site', ROOT / 'tools/build_public_site.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)

    def build(self, root=ROOT, base='https://ayukyo.github.io/icode-skill/'):
        self.builder.build(root, self.work / 'out', base)
        return self.work / 'out'

    def test_both_languages_link_to_markdown_and_scoped_llms_index(self):
        out = self.build()
        for path, prefix in [('index.html', ''), ('en/index.html', '../')]:
            tags = Tags((out / path).read_text()).tags
            self.assertIn(('link', {'rel': 'alternate', 'type': 'text/markdown', 'href': 'index.md'}), tags)
            self.assertIn(('link', {'rel': 'describedby', 'type': 'text/plain', 'href': prefix + 'llms.txt'}), tags)
            self.assertTrue((out / Path(path).parent / 'index.md').is_file())

    def test_llms_index_is_small_bilingual_and_links_to_authoritative_resources(self):
        out = self.build(base='https://icode.example.org/project/')
        index = (out / 'llms.txt').read_text()
        self.assertTrue(index.startswith('# ICODE\n\n> '))
        self.assertIn('AI coding workflow', index)
        self.assertIn('工单', index)
        for path in ['index.md', 'en/index.md']:
            self.assertIn('https://icode.example.org/project/' + path, index)
        self.assertIn('https://raw.githubusercontent.com/ayukyo/icode-skill/main/SKILL.md', index)
        self.assertIn('does not imply marketplace listing', index)
        self.assertIn('install.sh', index)
        self.assertLess(len(index.encode()), 8000)

    def test_markdown_contains_flow_use_cases_examples_and_truth_boundaries(self):
        out = self.build()
        for path in ['index.md', 'en/index.md']:
            text = (out / path).read_text()
            for command in ['/icode verify --build --deploy --listen', '/icode crosscheck', '/icode ui']:
                self.assertIn(command, text)
            for doc in self.builder.STAGE_DOCS.values():
                self.assertIn('/steps/' + doc, text)
            self.assertIn('./install.sh --dry-run --client all', text)
            self.assertIn('CodeBuddy', text)
            self.assertRegex(text, r'(?i)not.*(?:evidence|verified)|不是.*(?:证据|结果)|不代表设备已验证')
            self.assertRegex(text, r'(?i)does not.*(?:ordinary|unrelated) requests|不接管.*普通请求')

    def test_microdata_describes_visible_source_without_inline_script_or_fake_ratings(self):
        out = self.build()
        for path in ['index.html', 'en/index.html']:
            page = (out / path).read_text()
            tags = Tags(page).tags
            self.assertTrue(any(attrs.get('itemtype') == 'https://schema.org/SoftwareSourceCode' for _, attrs in tags))
            self.assertIn(('meta', {'itemprop': 'name', 'content': 'ICODE'}), tags)
            self.assertTrue(any(attrs.get('itemprop') == 'codeRepository' and attrs.get('href') == self.builder.REPO for _, attrs in tags))
            self.assertTrue(any(attrs.get('itemprop') == 'license' for _, attrs in tags))
            self.assertEqual(len([tag for tag, _ in tags if tag == 'script']), 1)
            self.assertNotIn('<script>', page)
            self.assertNotIn('aggregateRating', page)

    def test_only_explicit_text_and_brand_inputs_are_read_without_network(self):
        seen_text = []
        seen_assets = []
        original_text = self.builder.public_input
        original_asset = self.builder.public_asset
        def audited_text(root, relative):
            seen_text.append(relative)
            return original_text(root, relative)
        def audited_asset(root, relative):
            seen_assets.append(relative)
            return original_asset(root, relative)
        with patch.object(self.builder, 'public_input', side_effect=audited_text), \
                patch.object(self.builder, 'public_asset', side_effect=audited_asset), \
                patch('socket.getaddrinfo', side_effect=AssertionError('offline')):
            self.build()
        self.assertEqual(set(seen_text), {'site/content.json', 'site/style.css', 'site/locale.js', 'SKILL.md'})
        self.assertEqual(tuple(seen_assets), self.builder.PUBLIC_ASSETS)

    def test_markdown_does_not_turn_public_text_into_html_or_injected_links(self):
        source = self.work / 'source'
        source.mkdir()
        shutil.copytree(ROOT / 'site', source / 'site')
        shutil.copytree(ROOT / 'assets', source / 'assets')
        shutil.copy2(ROOT / 'SKILL.md', source / 'SKILL.md')
        content = source / 'site/content.json'
        data = json.loads(content.read_text())
        data['locales']['en']['intro'] = '<script>alert(1)</script> [bad](https://bad.example.org)'
        data['locales']['en']['scenes'][0]['command'] = '```\n<script>not executable</script>\n```'
        content.write_text(json.dumps(data))
        out = self.build(source)
        text = (out / 'en/index.md').read_text()
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('[bad](https://bad.example.org)', text)
        self.assertIn('````\n```', text, 'command fence cannot be closed by public command text')


if __name__ == '__main__':
    unittest.main()
