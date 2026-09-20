"""Offline contract tests for the public-only site builder."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://ayukyo.github.io/icode-skill/"


class PublicSiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def builder(self):
        path = ROOT / "tools/build_public_site.py"
        self.assertTrue(path.is_file(), "public site builder must exist")
        spec = importlib.util.spec_from_file_location("public_builder", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def build(self, output="out", base=BASE, source=None, key=None):
        return self.builder().build(source or ROOT, self.work / output, base, key)

    def fixture(self):
        root = self.work / "source"
        root.mkdir()
        shutil.copytree(ROOT / "site", root / "site")
        shutil.copy2(ROOT / "SKILL.md", root / "SKILL.md")
        return root

    def test_bilingual_pages_and_exact_output_allowlist(self):
        self.build()
        files = {p.relative_to(self.work / "out").as_posix()
                 for p in (self.work / "out").rglob("*") if p.is_file()}
        self.assertEqual(files, {"index.html", "en/index.html", "style.css",
                                 "sitemap.xml", "feed.xml", "public-manifest.json", ".nojekyll",
                                 "llms.txt", "index.md", "en/index.md"})
        zh = (self.work / "out/index.html").read_text()
        en = (self.work / "out/en/index.html").read_text()
        for page, lang in [(zh, "zh-CN"), (en, "en")]:
            self.assertIn(f'lang="{lang}"', page)
            self.assertIn('rel="canonical"', page)
            self.assertIn('hreflang="zh-CN"', page)
            self.assertIn('hreflang="en"', page)
            self.assertIn('name="description"', page)
            self.assertIn('./install.sh --dry-run --client all', page)
            self.assertIn('/icode crosscheck', page)
            self.assertIn('/icode verify --listen', page)
            self.assertNotIn('<script', page)
        self.assertIn('href="style.css"', zh)
        self.assertIn('href="../style.css"', en)

    def test_xml_and_manifest_match_public_pages(self):
        self.build()
        manifest = json.loads((self.work / "out/public-manifest.json").read_text())
        self.assertEqual(manifest['schema_version'], 1)
        self.assertEqual(manifest['urls'], [BASE, BASE + 'en/'])
        tree = ET.parse(self.work / "out/sitemap.xml")
        self.assertEqual([n.text for n in tree.findall('.//{*}loc')], manifest['urls'])
        rss = ET.parse(self.work / "out/feed.xml")
        self.assertTrue(rss.findall('./channel/item'))

    def test_repeatable_bytes(self):
        self.build('first')
        self.build('second')
        for file in (self.work / 'first').rglob('*'):
            if file.is_file():
                self.assertEqual(file.read_bytes(), (self.work / 'second' / file.relative_to(self.work / 'first')).read_bytes())

    def test_install_columns_allow_code_block_scrolling(self):
        self.build()
        css = (self.work / 'out/style.css').read_text()
        # Grid items default to min-width:auto, allowing long commands to expand
        # the whole page instead of scrolling inside the existing pre block.
        self.assertRegex(css, r'\.install-grid\s*>\s*div\s*\{\s*min-width:\s*0;?\s*\}')

    def test_private_files_not_copied_or_read(self):
        root = self.fixture()
        for path in ['.icode_output/ticket/log.txt', 'mcp/tool/config.json', 'demo/private.txt', 'site/secret.txt']:
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('PRIVATE-CANARY-DO-NOT-PUBLISH')
        self.build(source=root)
        for file in (self.work / 'out').rglob('*'):
            if file.is_file():
                self.assertNotIn(b'PRIVATE-CANARY', file.read_bytes())

    def test_text_is_escaped_not_executed(self):
        root = self.fixture()
        file = root / 'site/content.json'
        data = json.loads(file.read_text())
        data['locales']['zh-CN']['title'] = '<script>alert("x")</script>'
        file.write_text(json.dumps(data))
        self.build(source=root)
        page = (self.work / 'out/index.html').read_text()
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_bad_urls_rejected_before_output(self):
        for base in ['http://example.com/', 'https://x:y@example.com/', 'https://example.com/?x=1',
                     'https://example.com/#frag', 'https://example.com/a/../b/',
                     'https://example.com/%2e%2e/', 'https://example.com/a\\b/',
                     'https://localhost/', 'https://127.0.0.1/', 'https://example.com:42/',
                     'https://example.com/\nx/', 'https://example.com//x/',
                     'https://a..example.org/', 'https://-a.example.org/', 'https://a-.example.org/',
                     'https://@example.org/', 'https://host.localdomain/', 'https://host.test/',
                     'https://example.org/\x7f/', 'https://abc.123/']:
            with self.subTest(base=base), self.assertRaises(ValueError):
                self.build(base=base)
        self.assertFalse((self.work / 'out').exists())

    def test_existing_output_never_overwritten(self):
        self.build()
        file = self.work / 'out/index.html'
        before = file.read_bytes()
        with self.assertRaises((ValueError, FileExistsError)):
            self.build()
        self.assertEqual(before, file.read_bytes())

    def test_symlink_input_and_parent_output_rejected(self):
        root = self.fixture()
        file = root / 'site/content.json'
        file.rename(self.work / 'outside.json')
        file.symlink_to(self.work / 'outside.json')
        with self.assertRaises(ValueError):
            self.build(source=root)
        (self.work / 'alias').symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build(output='alias/escape')

    def test_ticket_output_path_rejected(self):
        root = self.fixture()
        path = root / '.icode_output/new-site'
        with self.assertRaises(ValueError):
            self.builder().build(root, path, BASE, None)
        self.assertFalse(path.exists())

    def test_valid_key_adds_only_ownership_file(self):
        self.build(key='0123456789abcdef')
        self.assertEqual((self.work / 'out/0123456789abcdef.txt').read_text().strip(), '0123456789abcdef')

    def test_bad_key_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            self.build(key='../private')
        self.assertFalse((self.work / 'out').exists())

    def test_missing_content_or_invalid_version_fail_closed(self):
        root = self.fixture()
        (root / 'SKILL.md').write_text('missing version')
        with self.assertRaises(ValueError):
            self.build(source=root)
        self.assertFalse((self.work / 'out').exists())

    def test_custom_domain_root(self):
        self.build(base='https://icode.example.org')
        data = json.loads((self.work / 'out/public-manifest.json').read_text())
        self.assertEqual(data['base_url'], 'https://icode.example.org/')

    def test_cli_argument_error_is_concise(self):
        self.builder()
        result = subprocess.run([sys.executable, str(ROOT / 'tools/build_public_site.py'),
                                 '--output', str(self.work / 'out'), '--base-url', 'bad'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)

    def test_release_version_mismatch_rejected(self):
        self.builder()
        result = subprocess.run([sys.executable, str(ROOT / 'tools/build_public_site.py'),
                                 '--output', str(self.work / 'out'),
                                 '--expected-version', 'v0.0.0'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('version mismatch', result.stderr)
        self.assertFalse((self.work / 'out').exists())

    def test_hero_uses_full_flow_not_design_only_request(self):
        self.build()
        from html.parser import HTMLParser
        class Prompt(HTMLParser):
            def __init__(self):
                super().__init__()
                self.reading = False
                self.text = ''
            def handle_starttag(self, tag, attrs):
                if tag == 'p' and ('class', 'prompt') in attrs:
                    self.reading = True
            def handle_endtag(self, tag):
                if tag == 'p':
                    self.reading = False
            def handle_data(self, text):
                if self.reading:
                    self.text += text
        for path in ['index.html', 'en/index.html']:
            parser = Prompt()
            parser.feed((self.work / 'out' / path).read_text())
            self.assertTrue(parser.text.startswith('/icode start '))

    def test_malformed_content_fails_before_writes(self):
        root = self.fixture()
        (root / 'site/content.json').write_text('{"locales": [], "updates": []}')
        with self.assertRaises(ValueError):
            self.build(source=root)
        self.assertFalse((self.work / 'out').exists())

    def test_too_few_examples_fail_validation_not_traceback(self):
        root = self.fixture()
        file = root / 'site/content.json'
        data = json.loads(file.read_text())
        data['locales']['zh-CN']['examples'] = [['only', 'one']]
        file.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.build(source=root)
        self.assertFalse((self.work / 'out').exists())

    def test_no_optional_packages_required(self):
        self.builder()
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'tools/build_public_site.py'),
                                 '--output', str(self.work / 'out')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_broken_symlink_output_rejected(self):
        (self.work / 'out').symlink_to(self.work / 'nonexistent')
        with self.assertRaises(ValueError):
            self.build()

    def test_output_traversal_rejected_before_reads_or_directory_creation(self):
        builder = self.builder()
        with patch.object(builder, 'public_input', side_effect=AssertionError('unexpected input read')):
            with self.assertRaisesRegex(ValueError, 'traversal'):
                builder.build(ROOT, self.work / 'nominal/../escaped', BASE)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_source_traversal_rejected_before_reads(self):
        root = self.fixture()
        builder = self.builder()
        with patch.object(builder, 'public_input', side_effect=AssertionError('unexpected input read')):
            with self.assertRaisesRegex(ValueError, 'traversal'):
                builder.build(root / 'site/..', self.work / 'out', BASE)
        self.assertFalse((self.work / 'out').exists())

    def test_public_input_traversal_rejected_before_read(self):
        builder = self.builder()
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected file read')):
            with self.assertRaisesRegex(ValueError, 'traversal'):
                builder.public_input(ROOT, 'site/../SKILL.md')

    def test_invalid_xml_characters_in_nested_strings_rejected_before_output(self):
        root = self.fixture()
        file = root / 'site/content.json'
        original = file.read_text()
        builder = self.builder()
        locations = [('updates', 0, 'en'), ('locales', 'zh-CN', 'title'),
                     ('locales', 'en', 'features', 0, 1), ('locales', 'en', 'flow', 0)]
        for index, location in enumerate(locations):
            for char in ['\x00', '\x01', '\x0b', '\x1f', '\ud800', '\udfff', '\ufffe', '\uffff']:
                with self.subTest(location=location, char=repr(char)):
                    data = json.loads(original)
                    parent = data
                    for key in location[:-1]:
                        parent = parent[key]
                    parent[location[-1]] = 'text' + char
                    file.write_text(json.dumps(data))
                    output = self.work / ('out-' + str(index) + '-' + str(ord(char)))
                    with self.assertRaisesRegex(ValueError, 'XML'):
                        builder.build(root, output, BASE)
                    self.assertFalse(output.exists())

    def test_xml_whitespace_and_unicode_remain_supported(self):
        root = self.fixture()
        file = root / 'site/content.json'
        data = json.loads(file.read_text())
        text = 'Tabs\tNewlines\nCarriage\r中文😀\u0020\ud7ff\ue000\ufffd\U0010ffff'
        data['updates'][0]['en'] = text
        file.write_text(json.dumps(data))
        self.build(source=root)
        description = ET.parse(self.work / 'out/feed.xml').findtext('./channel/item/description')
        self.assertIn(text.replace('\r', '\n'), description)
        self.assertIn(text, (self.work / 'out/en/index.html').read_bytes().decode())

    def test_public_links_and_fragment_targets_exist(self):
        self.build()
        from html.parser import HTMLParser
        from urllib.parse import urlsplit
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
                self.ids = set()
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.ids.add(attrs['id'])
                if 'href' in attrs:
                    self.links.append(attrs['href'])
        for relative in ['index.html', 'en/index.html']:
            file = self.work / 'out' / relative
            parser = Links()
            parser.feed(file.read_text())
            for link in parser.links:
                url = urlsplit(link)
                if url.scheme:
                    self.assertEqual(url.scheme, 'https')
                elif not url.path:
                    self.assertIn(url.fragment, parser.ids)
                else:
                    target = file.parent / url.path
                    self.assertTrue(target.exists(), link)


if __name__ == '__main__':
    unittest.main()
