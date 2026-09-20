"""Fail-closed publishing configuration; no GitHub credentials needed."""
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def text(self):
        file = ROOT / '.github/workflows/public-site.yml'
        self.assertTrue(file.is_file(), 'opt-in publication workflow must exist')
        return file.read_text()

    def test_read_only_default_and_scoped_deploy(self):
        text = self.text()
        self.assertIn('permissions:\n  contents: read', text)
        self.assertEqual(text.count('pages: write'), 1)
        self.assertEqual(text.count('id-token: write'), 1)
        self.assertIn('name: github-pages', text)
        self.assertNotIn('pull_request_target', text)

    def test_disabled_by_default_and_no_pr_deploy(self):
        text = self.text()
        self.assertIn("vars.PUBLIC_SITE_ENABLED == 'true'", text)
        self.assertIn("github.event_name == 'release'", text)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn('github.event.repository.default_branch', text)
        self.assertIn('!github.event.release.prerelease', text)

    def test_only_generated_artifact_and_immutable_actions(self):
        text = self.text()
        self.assertIn('path: _site', text)
        self.assertNotIn("path: '.'", text)
        import re
        actions = re.findall(r'uses:\s+actions/[^\s]+', text)
        self.assertGreaterEqual(len(actions), 4)
        for action in actions:
            self.assertRegex(action, r'@[0-9a-f]{40}$')
        self.assertIn('persist-credentials: false', text)

    def test_no_untrusted_shell_expression(self):
        text = self.text()
        self.assertIn('EXPECTED_VERSION:', text)
        self.assertIn('--expected-version "$EXPECTED_VERSION"', text)
        refs = re.findall(r'^\s+ref: (.+)$', text, re.MULTILINE)
        self.assertEqual(refs, ['${{ github.sha }}', '${{ needs.build.outputs.commit }}'])
        self.assertIn('needs: build', text)
        self.assertIn("steps.deployment.outcome == 'success'", text)
        self.assertIn('continue-on-error: true', text)

    def test_workflow_concurrency_separates_preview_from_eligible_publication(self):
        text = self.text()
        block = text.split('concurrency:\n', 1)[1].split('\njobs:', 1)[0]
        self.assertIn('cancel-in-progress: true', block)
        group = re.search(r'group: public-site-\$\{\{ (.+) \}\}', block).group(1)
        policy = re.search(r'PUBLISH: \$\{\{ (.+) \}\}', text).group(1)

        def evaluate(expression, enabled, url, event, ref, prerelease=False, draft=False):
            # Evaluate only the expression operators used here, against offline event fixtures.
            expression = expression.replace('&&', ' and ').replace('||', ' or ')
            expression = re.sub(r'!(?!=)', 'not ', expression)
            github = SimpleNamespace(event_name=event, ref=ref,
                                     event=SimpleNamespace(number=7,
                                         release=SimpleNamespace(prerelease=prerelease, draft=draft),
                                         repository=SimpleNamespace(default_branch='main')))
            variables = SimpleNamespace(PUBLIC_SITE_ENABLED=enabled, PUBLIC_SITE_URL=url)
            return eval(expression, {'__builtins__': {}},
                        {'github': github, 'vars': variables, 'format': lambda s, *v: s.format(*v)})

        events = [('release', 'refs/tags/v1.2.3', False, False, True),
                  ('release', 'refs/tags/v1.2.3-rc1', True, False, False),
                  ('release', 'refs/tags/v1.2.3', False, True, False),
                  ('workflow_dispatch', 'refs/heads/main', False, False, True),
                  ('workflow_dispatch', 'refs/heads/feature', False, False, False),
                  ('workflow_dispatch', 'refs/tags/v1.2.3', False, False, False),
                  ('pull_request', 'refs/pull/7/merge', False, False, False),
                  ('push', 'refs/heads/main', False, False, False)]
        for enabled in ('true', 'false', ''):
            for url in ('https://example.org/project/', ''):
                for event, ref, prerelease, draft, allowed in events:
                    with self.subTest(enabled=enabled, url=url, event=event, ref=ref, prerelease=prerelease, draft=draft):
                        args = (enabled, url, event, ref, prerelease, draft)
                        eligible = enabled == 'true' and bool(url) and allowed
                        self.assertEqual(evaluate(policy, *args), eligible)
                        selected = evaluate(group, *args)
                        if eligible:
                            self.assertEqual(selected, 'deployment')
                        else:
                            self.assertTrue(str(selected).startswith('preview-'), selected)

    def test_concurrency_eligibility_matches_publish_policy_exactly(self):
        text = self.text()
        policy = re.search(r'PUBLISH: \$\{\{ (.+) \}\}', text).group(1)
        group = re.search(r'group: public-site-\$\{\{ (.+) \}\}', text).group(1)
        self.assertTrue(group.startswith(policy + " && 'deployment' || "))


if __name__ == '__main__':
    unittest.main()
