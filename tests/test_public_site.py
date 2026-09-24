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
PUBLIC_ASSETS = (
    "assets/icode-ticket-hex.svg",
    "assets/icode-ticket-hex-128.png",
    "assets/icode-ticket-hex-512.png",
)
GUIDE_SLUGS = ("ai-coding-workflow", "multi-model-code-review")


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

    def build(self, output="out", base=BASE, source=None, key=None, **kwargs):
        return self.builder().build(source or ROOT, self.work / output, base, key, **kwargs)

    def fixture(self):
        root = self.work / "source"
        root.mkdir()
        shutil.copytree(ROOT / "site", root / "site")
        shutil.copytree(ROOT / "assets", root / "assets")
        shutil.copy2(ROOT / "SKILL.md", root / "SKILL.md")
        return root

    def test_bilingual_pages_and_exact_output_allowlist(self):
        self.build()
        files = {p.relative_to(self.work / "out").as_posix()
                 for p in (self.work / "out").rglob("*") if p.is_file()}
        self.assertEqual(files, {"index.html", "en/index.html", "style.css", "locale.js",
                                 "sitemap.xml", "feed.xml", "public-manifest.json", ".nojekyll",
                                 "llms.txt", "index.md", "en/index.md", *PUBLIC_ASSETS,
                                 *(f"{slug}/index.html" for slug in GUIDE_SLUGS),
                                 *(f"en/{slug}/index.html" for slug in GUIDE_SLUGS)})
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
            self.assertEqual(page.count('<script'), 1)
            self.assertNotIn('<script>', page)
        self.assertIn('href="style.css"', zh)
        self.assertIn('href="../style.css"', en)
        self.assertIn('<script src="locale.js"></script>', zh)
        self.assertIn('<script src="../locale.js"></script>', en)
        self.assertIn('href="en/?lang=en"', zh)
        self.assertIn('href="../?lang=zh-CN"', en)
        for slug in GUIDE_SLUGS:
            self.assertIn(f'href="{slug}/"', zh)
            self.assertIn(f'href="{slug}/"', en)

    def test_brand_asset_allowlist_is_fixed_and_outputs_exact_bytes(self):
        builder = self.builder()
        self.assertEqual(builder.PUBLIC_ASSETS, PUBLIC_ASSETS)
        self.build()
        for relative in PUBLIC_ASSETS:
            with self.subTest(relative=relative):
                self.assertEqual((self.work / "out" / relative).read_bytes(),
                                 (ROOT / relative).read_bytes())

    def test_bilingual_pages_use_brand_assets_with_depth_correct_paths(self):
        self.build()
        expected = {
            "index.html": "",
            "en/index.html": "../",
        }
        for page_path, prefix in expected.items():
            with self.subTest(page=page_path):
                page = (self.work / "out" / page_path).read_text()
                self.assertIn(
                    f'<link rel="icon" type="image/svg+xml" href="{prefix}assets/icode-ticket-hex.svg">',
                    page,
                )
                self.assertIn(
                    f'<img src="{prefix}assets/icode-ticket-hex-128.png" alt="ICODE" width="40" height="40">',
                    page,
                )
                self.assertIn(
                    f'<meta property="og:image" content="{BASE}assets/icode-ticket-hex-512.png">',
                    page,
                )
                self.assertIn(f'<script src="{prefix}locale.js"></script>', page)

    def test_binary_asset_reader_rejects_non_whitelisted_input_before_read(self):
        builder = self.builder()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unexpected file read')):
            with self.assertRaisesRegex(ValueError, 'allowlist'):
                builder.public_asset(ROOT, 'assets/unlisted.png')

    def test_symlink_and_oversized_brand_assets_fail_before_output(self):
        builder = self.builder()
        symlink_root = self.fixture()
        symlink_asset = symlink_root / PUBLIC_ASSETS[0]
        outside = self.work / 'outside.svg'
        outside.write_bytes(symlink_asset.read_bytes())
        symlink_asset.unlink()
        symlink_asset.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            builder.build(symlink_root, self.work / 'symlink-out', BASE)
        self.assertFalse((self.work / 'symlink-out').exists())

        oversized_root = self.work / 'oversized-source'
        oversized_root.mkdir()
        shutil.copytree(ROOT / 'site', oversized_root / 'site')
        shutil.copytree(ROOT / 'assets', oversized_root / 'assets')
        shutil.copy2(ROOT / 'SKILL.md', oversized_root / 'SKILL.md')
        (oversized_root / PUBLIC_ASSETS[1]).write_bytes(b'x' * 1_000_001)
        with self.assertRaisesRegex(ValueError, '1 MB'):
            builder.build(oversized_root, self.work / 'oversized-out', BASE)
        self.assertFalse((self.work / 'oversized-out').exists())

    def test_xml_and_manifest_match_public_pages(self):
        self.build()
        manifest = json.loads((self.work / "out/public-manifest.json").read_text())
        self.assertEqual(manifest['schema_version'], 1)
        expected = [BASE, BASE + 'en/']
        for slug in GUIDE_SLUGS:
            expected.extend([BASE + slug + '/', BASE + 'en/' + slug + '/'])
        self.assertEqual(manifest['urls'], expected)
        tree = ET.parse(self.work / "out/sitemap.xml")
        self.assertEqual([n.text for n in tree.findall('.//{*}loc')], manifest['urls'])
        rss = ET.parse(self.work / "out/feed.xml")
        self.assertTrue(rss.findall('./channel/item'))

    def test_focused_bilingual_guides_are_unique_crawlable_and_linked(self):
        self.build()
        root = self.work / 'out'
        home = (root / 'index.html').read_text()
        english_home = (root / 'en/index.html').read_text()
        seen_titles = set()
        for slug in GUIDE_SLUGS:
            self.assertIn(f'href="{slug}/"', home)
            self.assertIn(f'href="{slug}/"', english_home)
            for lang, relative, prefix in (
                    ('zh-CN', f'{slug}/index.html', '../'),
                    ('en', f'en/{slug}/index.html', '../../')):
                with self.subTest(slug=slug, lang=lang):
                    page = (root / relative).read_text()
                    self.assertIn(f'<html lang="{lang}">', page)
                    self.assertIn('rel="canonical"', page)
                    self.assertIn('hreflang="zh-CN"', page)
                    self.assertIn('hreflang="en"', page)
                    self.assertIn(f'href="{prefix}style.css"', page)
                    self.assertIn('Claude Code', page)
                    self.assertIn('Codex', page)
                    self.assertIn('CodeBuddy', page)
                    self.assertIn('WorkBuddy', page)
                    self.assertIn(f'<script src="{prefix}locale.js"></script>', page)
                    self.assertIn('?lang=', page)
                    title = page.split('<title>', 1)[1].split('</title>', 1)[0]
                    self.assertNotIn(title, seen_titles)
                    seen_titles.add(title)
                    if slug == 'ai-coding-workflow':
                        self.assertIn('/icode start', page)
                        self.assertIn('/icode verify', page)
                    else:
                        self.assertIn('/icode crosscheck', page)
                        self.assertRegex(page, r'(?i)switch|切换')

    def test_invalid_guide_content_fails_before_output(self):
        root = self.fixture()
        content = root / 'site/content.json'
        data = json.loads(content.read_text())
        data['guides']['ai-coding-workflow']['en']['sections'] = []
        content.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.build(source=root)
        self.assertFalse((self.work / 'out').exists())

    def test_search_verification_social_cards_and_truthful_sitemap_lastmod(self):
        self.build(source_lastmod='2026-09-21',
                   google_site_verification='google_test-token_12345678',
                   bing_site_verification='BING_test-token_12345678')
        manifest = json.loads((self.work / 'out/public-manifest.json').read_text())
        self.assertEqual(manifest['lastmod'], '2026-09-21')
        tree = ET.parse(self.work / 'out/sitemap.xml')
        entries = tree.findall('.//{*}url')
        self.assertEqual([entry.findtext('{*}lastmod') for entry in entries],
                         ['2026-09-21'] * (2 + 2 * len(GUIDE_SLUGS)))
        for relative in ('index.html', 'en/index.html'):
            page = (self.work / 'out' / relative).read_text()
            self.assertIn('<meta name="google-site-verification" content="google_test-token_12345678">', page)
            self.assertIn('<meta name="msvalidate.01" content="BING_test-token_12345678">', page)
            self.assertIn('<meta name="twitter:card" content="summary">', page)
            self.assertIn('<meta name="twitter:title"', page)
            self.assertIn('<meta name="twitter:description"', page)
            self.assertIn(f'<meta name="twitter:image" content="{BASE}assets/icode-ticket-hex-512.png">', page)
            self.assertIn('<meta property="og:image:alt" content="ICODE">', page)

    def test_optional_verification_is_absent_and_invalid_metadata_fails_before_write(self):
        self.build()
        page = (self.work / 'out/index.html').read_text()
        self.assertNotIn('google-site-verification', page)
        self.assertNotIn('msvalidate.01', page)
        builder = self.builder()
        invalid = [
            {'source_lastmod': '2026-9-1'},
            {'source_lastmod': 'not-a-date'},
            {'google_site_verification': 'short'},
            {'google_site_verification': 'bad\" content=\"injected'},
            {'bing_site_verification': '<meta>'},
            {'bing_site_verification': 'x' * 129},
        ]
        for index, kwargs in enumerate(invalid):
            output = self.work / f'invalid-seo-{index}'
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                builder.build(ROOT, output, BASE, **kwargs)
            self.assertFalse(output.exists())

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
