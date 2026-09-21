"""Offline listing drafts are neither publication nor installable packages."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ListingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
            spec = importlib.util.spec_from_file_location('listing', ROOT / 'tools/prepare_skill_listing.py')
            cls.tool = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.tool)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='icode-listing-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.skill = self.root / 'SKILL.md'
        self.text = '---\nname: icode\ndescription: ICODE AI coding workflow\n---\n\n**版本**: v3.4.5\n'
        self.skill.write_text(self.text)

    def test_official_platform_fields_are_source_versioned_and_not_submitted(self):
        report = self.tool.drafts(self.root, 'all')
        self.assertEqual(report['status'], 'draft_only')
        self.assertFalse(report['submitted'])
        self.assertFalse(report['publish_ready'])
        hub, smithery = report['drafts']
        self.assertEqual(hub['platform'], 'skillhub')
        self.assertEqual(hub['frontmatter']['version'], '3.4.5')
        self.assertEqual(hub['frontmatter']['slug'], 'icode')
        self.assertTrue(hub['frontmatter']['displayName'])
        self.assertEqual(hub['frontmatter']['description'], 'ICODE AI coding workflow')
        self.assertEqual(smithery['request']['body'], {'gitUrl': 'https://github.com/ayukyo/icode-skill'})
        self.assertNotIn('headers', smithery['request'])
        self.assertTrue(all(d['requires'] and d['official_source'] for d in report['drafts']))

    def test_skillhub_license_requires_manual_complete_package_review(self):
        for platform in ('skillhub', 'all'):
            with self.subTest(platform=platform):
                hub = self.tool.drafts(self.root, platform)['drafts'][0]
                self.assertNotIn('license', hub['frontmatter'])
                self.assertIs(hub.get('license_required'), True)
                requirements = ' '.join(hub['requires']).lower()
                self.assertIn('manually verify', requirements)
                self.assertIn('complete package', requirements)
                self.assertIn('dependencies', requirements)
                self.assertIn('license', requirements)
                self.assertIn('not verified', requirements)

    def test_smithery_uses_web_draft_without_assuming_a_namespace(self):
        for platform in ('smithery', 'all'):
            with self.subTest(platform=platform):
                smithery = self.tool.drafts(self.root, platform)['drafts'][-1]
                self.assertNotIn('method', smithery['request'])
                self.assertEqual(smithery['request']['url'], 'https://smithery.ai/skills/new')
                self.assertEqual(smithery['official_source'], 'https://smithery.ai/skills/new')
                self.assertEqual(smithery['request']['body'],
                                 {'gitUrl': 'https://github.com/ayukyo/icode-skill'})
                self.assertIs(smithery.get('namespace_verified'), False)
                requirements = ' '.join(smithery['requires']).lower()
                self.assertIn('namespace is not verified', requirements)
                self.assertIn('ownership and availability', requirements)
                self.assertNotIn('api.smithery.ai', json.dumps(smithery))
                self.assertNotIn('ayukyo', requirements)

    def test_only_reads_public_entry_and_never_connects_or_writes(self):
        (self.root / '.env').write_text('SECRET_CANARY')
        original = Path.read_text
        reads = []
        def bounded_read(path, *args, **kwargs):
            reads.append(path)
            self.assertEqual(path, self.skill)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', bounded_read), \
                patch.object(Path, 'write_text', side_effect=AssertionError('write')), \
                patch.object(Path, 'write_bytes', side_effect=AssertionError('write')), \
                patch('socket.socket', side_effect=AssertionError('network')):
            result = self.tool.drafts(self.root, 'all')
        self.assertEqual(reads, [self.skill])
        self.assertNotIn('SECRET_CANARY', json.dumps(result))

    def test_single_platform_and_repeatable_stdout(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.tool.main(['--source', str(self.root), '--platform', 'smithery']), 0)
        self.assertEqual([d['platform'] for d in json.loads(output.getvalue())['drafts']], ['smithery'])
        self.assertEqual(self.tool.drafts(self.root, 'all'), self.tool.drafts(self.root, 'all'))

    def test_invalid_or_ambiguous_version_and_identity_fail_closed(self):
        for text in (self.text.replace('3.4.5', '03.4.5'), self.text.replace('3.4.5', '3.4'),
                     self.text.replace('3.4.5', '3.4.5-rc1'), self.text.replace('name: icode', 'name: other'),
                     self.text + '**版本**: v3.4.6\n', self.text.replace('description: ICODE AI coding workflow', 'description: ')):
            self.skill.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.tool.drafts(self.root, 'all')

    def test_unsupported_description_syntax_fails_closed(self):
        descriptions = (
            '"ICODE workflow"', "'ICODE workflow'", '"unterminated',
            'ICODE workflow # editorial note', '# comment only',
            '[foo, bar]', '{name: value}', 'foo: bar', 'workflow:',
            'null', 'Null', 'NULL', '~', 'true', 'FALSE', 'yes', 'NO', 'On', 'off', 'y', 'N',
            '123', '1.25', '-3', '+4', '1e3', '0x10', '.inf', '.NaN', '2026-09-20',
            '|', '>', '|\n  ICODE workflow', '>\n  ICODE workflow',
            'ICODE\n  workflow', 'ICODE\rworkflow',
            '!!str workflow', '!custom workflow', '&desc workflow', '*desc',
            '- workflow', '? workflow', '@workflow', '`workflow',
            '\tICODE', 'ICODE\tworkflow', 'ICODE\x00workflow',
            'ICODE\x85workflow', 'ICODE\u2028workflow', 'ICODE\u2029workflow',
        )
        for description in descriptions:
            self.skill.write_text(self.text.replace('ICODE AI coding workflow', description))
            with self.subTest(description=description), self.assertRaisesRegex(ValueError, 'plain'):
                self.tool.drafts(self.root, 'all')

    def test_plain_description_content_is_preserved(self):
        for description in (
            'ICODE AI coding workflow', '中文工作流，代码审查与验证。',
            'ICODE uses https://example.org/docs#usage and key:value',
            'ICODE supports C# and "code review" (steps [1, 2]); use /icode or $icode.',
            'False positives and null handling',
        ):
            self.skill.write_text(self.text.replace('ICODE AI coding workflow', description))
            with self.subTest(description=description):
                report = self.tool.drafts(self.root, 'skillhub')
                self.assertEqual(report['drafts'][0]['frontmatter']['description'], description)

    def test_unicode_version_digits_are_rejected(self):
        for version in ('1２.3.4', '1.2３.4', '1.2.3４', '1\u0662.3.4'):
            self.skill.write_text(self.text.replace('3.4.5', version))
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'ASCII'):
                self.tool.drafts(self.root, 'all')

    def test_unsupported_description_cli_error_does_not_disclose_content(self):
        self.skill.write_text(self.text.replace('ICODE AI coding workflow', 'ICODE # SECRET_CANARY'))
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = self.tool.main(['--source', str(self.root)])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('plain', json.loads(stderr.getvalue())['error'])
        self.assertNotIn('SECRET_CANARY', stderr.getvalue())

    def test_repository_header_is_supported(self):
        text = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        report = self.tool.drafts(ROOT, 'all')
        self.assertEqual(report['drafts'][0]['frontmatter']['description'],
                         text.splitlines()[2].removeprefix('description: '))
        self.assertIn('**版本**: v' + report['source_version'], text.splitlines())

    def test_missing_symlink_or_oversized_input_is_rejected(self):
        with self.assertRaises(ValueError):
            self.tool.drafts(self.root / 'missing', 'all')
        link = self.root / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.tool.drafts(link, 'all')
        self.skill.write_text(self.text + 'X' * 1_000_001)
        with self.assertRaises(ValueError):
            self.tool.drafts(self.root, 'all')

    def test_error_cli_is_concise_without_body_disclosure(self):
        self.skill.write_text('invalid SECRET_CANARY')
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(self.tool.main(['--source', str(self.root)]), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('error', json.loads(stderr.getvalue()))
        self.assertNotIn('SECRET_CANARY', stderr.getvalue())

    def test_clawhub_and_other_channels_are_not_fake_submission_formats(self):
        with self.assertRaises(ValueError):
            self.tool.drafts(self.root, 'clawhub')


if __name__ == '__main__':
    unittest.main()
