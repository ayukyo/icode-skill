"""Offline guards for public docs and local-only publication records."""
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]


class DocsPublicationTests(unittest.TestCase):
    def git_paths(self, *args):
        result = subprocess.run(
            ['git', *args], cwd=ROOT, check=True, capture_output=True,
        )
        return set(result.stdout.decode('utf-8').rstrip('\0').split('\0')) - {''}

    def ignored_paths(self, paths):
        if not paths:
            return set()
        # --no-index also checks tracked paths, so accidental git add -f cannot
        # hide a leak. NUL delimiters preserve spaces and newlines in filenames.
        result = subprocess.run(
            ['git', '-c', 'core.excludesFile=/dev/null', 'check-ignore',
             '--no-index', '-z', '--stdin'],
            cwd=ROOT, input=('\0'.join(sorted(paths)) + '\0').encode('utf-8'),
            capture_output=True,
        )
        self.assertIn(result.returncode, (0, 1), 'git check-ignore failed')
        return set(result.stdout.decode('utf-8').rstrip('\0').split('\0')) - {''}

    def test_internal_paths_are_ignored(self):
        paths = {
            'docs/nbl/notes.md',
            'docs/discovery-status-2026-09-21.md',
            'docs/discovery-status-2099-01-01.md',
            'docs/ppt-package-size-2026-09-21.md',
            'docs/ppt-package-size-2099-01-01.md',
            'docs/private/notes.md', 'docs/private/nested/notes.md',
            'docs/local/notes.md', 'docs/local/nested/notes.md',
        }
        self.assertEqual(self.ignored_paths(paths), paths)

    def test_public_docs_are_not_ignored(self):
        paths = {
            'docs/agent-skill-discovery.md', 'docs/skill-catalog-submission.md',
            'docs/skill-distribution.md', 'docs/workbuddy-support.md',
            'docs/public-discovery.md', 'docs/install.md',
            'docs/adr/ADR-0001-optimization-proposal-provenance.md',
            'docs/adr/future-decision.md',
        }
        self.assertEqual(self.ignored_paths(paths), set())

    def test_tracked_docs_do_not_leak_ignored_paths(self):
        tracked = self.git_paths('ls-files', '-z', '--', 'docs/')
        self.assertTrue(tracked, 'expected tracked public docs')
        self.assertEqual(self.ignored_paths(tracked), set(),
                         'local-only docs must not remain in the Git index')

    def test_ci_checks_publication_for_all_docs_pull_requests(self):
        workflow = (ROOT / '.github/workflows/public-site.yml').read_text(encoding='utf-8')
        # Scope exact lines to the PR trigger and executable regression step;
        # a matching comment or a path listed under another event is not enough.
        trigger = re.search(r'(?ms)^  pull_request:\n(.*?)(?=^  \S|\Z)', workflow)
        self.assertIsNotNone(trigger, 'expected pull_request trigger')
        self.assertIn('    paths:', trigger[1].splitlines())
        self.assertIn("      - 'docs/**'", trigger[1].splitlines())
        regression = re.search(
            r'(?ms)^      - name: Offline regression tests\n(.*?)(?=^      - |\Z)',
            workflow,
        )
        self.assertIsNotNone(regression, 'expected offline regression step')
        self.assertIn('        run: |', regression[1].splitlines())
        self.assertIn(
            '          python3 -m unittest discover -s tests -p test_docs_publication.py -v',
            regression[1].splitlines(),
        )

    def test_public_docs_do_not_reference_internal_reports(self):
        # Cover root entrypoints and host guidance as well as new public docs
        # before staging; never read local reports or private directories.
        # Inspect destinations in Markdown and HTML
        # alike by rejecting internal report filenames anywhere in public text.
        entrypoints = {'README.md', 'README.zh-CN.md', 'references/host_adapters.md'}
        candidates = self.git_paths(
            'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'docs/',
            *sorted(entrypoints),
        )
        public = candidates - self.ignored_paths(candidates)
        self.assertTrue(entrypoints <= public, 'public entrypoints must be checked')
        self.assertIn('docs/agent-skill-discovery.md', public)
        internal_report = re.compile(r'(?:discovery-status|ppt-package-size)-[^\s/<>]*\.md')
        for name in sorted(public):
            path = ROOT / name
            # A broken ignore rule must fail the path tests without causing
            # this content check to read the very records we keep private.
            if (name.startswith(('docs/nbl/', 'docs/private/', 'docs/local/'))
                    or internal_report.search(path.name)):
                continue
            if path.suffix.lower() not in {'.md', '.html'}:
                continue
            with self.subTest(path=name):
                self.assertFalse(path.is_symlink(), 'public docs must not redirect to private files')
                content = unquote(path.read_text(encoding='utf-8'))
                # Report only the path, never copy document contents into logs.
                self.assertIsNone(internal_report.search(content),
                                  'public doc references an unpublished internal report')


if __name__ == '__main__':
    unittest.main()
