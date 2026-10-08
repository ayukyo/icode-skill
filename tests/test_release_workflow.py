"""Release authorization, qualification and tag/source binding contracts."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.data = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())

    def test_only_tag_pushes_can_publish_after_qualification(self):
        triggers = self.data.get('on', self.data.get(True))
        self.assertEqual(triggers, {'push': {'tags': ['v*']}})
        self.assertEqual(self.data['permissions'], {'contents': 'read'})
        jobs = self.data['jobs']
        self.assertEqual(jobs['renderer']['uses'], './.github/workflows/docx-renderer.yml')
        self.assertEqual(jobs['publish']['needs'], 'renderer')
        self.assertEqual(jobs['publish']['permissions'], {'contents': 'write'})
        self.assertNotIn('if', jobs['publish'])  # No always() bypass of failed qualification.
        steps = jobs['publish']['steps']
        self.assertFalse(steps[0]['with']['persist-credentials'])
        self.assertEqual(steps[0]['with']['ref'], '${{ github.sha }}')
        publish = steps[-1]
        self.assertEqual(publish['env'], {'GH_TOKEN': '${{ github.token }}'})
        self.assertIn('gh release create', publish['run'])
        self.assertIn('--verify-tag', publish['run'])
        self.assertIn('--target "$GITHUB_SHA"', publish['run'])
        self.assertIn('.tar.gz.sha256', publish['run'])
        self.assertNotIn('--clobber', publish['run'])

    def test_binding_rejects_mismatch_invalid_tag_and_missing_notes(self):
        steps = self.data['jobs']['publish']['steps']
        script = next(s['run'] for s in steps if s.get('name') == 'Bind tag to source version and reviewed notes')
        for tag, source, notes, expected in [('v2.32.1', 'v2.32.1', True, 0),
                                            ('v2.32.1', 'v2.32.0', True, 1),
                                            ('v2.32.1', 'v2.32.1', False, 1),
                                            ('v2.32.1-rc1', 'v2.32.1', True, 1),
                                            ('../../outside', 'v2.32.1', True, 1)]:
            with self.subTest(tag=tag, source=source, notes=notes), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'SKILL.md').write_text('**版本**: ' + source + '\n')
                if notes:
                    (root / 'docs/releases').mkdir(parents=True)
                    (root / 'docs/releases/v2.32.1.md').write_text('Reviewed notes\n')
                result = subprocess.run(['bash', '-e', '-c', textwrap.dedent(script)], cwd=root,
                                        env={**os.environ, 'GITHUB_REF_NAME': tag},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == '__main__':
    unittest.main()
