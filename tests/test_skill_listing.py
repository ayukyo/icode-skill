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
        self.assertEqual(hub['frontmatter']['license'], 'MIT')
        self.assertTrue(hub['frontmatter']['displayName'])
        self.assertEqual(hub['frontmatter']['description'], 'ICODE AI coding workflow')
        self.assertEqual(smithery['request']['method'], 'PUT')
        self.assertEqual(smithery['request']['url'], 'https://api.smithery.ai/skills/ayukyo/icode')
        self.assertEqual(smithery['request']['body'], {'gitUrl': 'https://github.com/ayukyo/icode-skill'})
        self.assertNotIn('headers', smithery['request'])
        self.assertTrue(all(d['requires'] and d['official_source'] for d in report['drafts']))

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
