"""Fail-closed publishing configuration; no GitHub credentials needed."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import textwrap
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

    def test_metadata_regression_has_pinned_ci_dependencies(self):
        text = self.text().split('\n  deploy:\n', 1)[0]
        self.assertIn('python3 -m pip install --disable-pip-version-check --no-cache-dir pytest==9.0.3 PyYAML==6.0.2', text)
        self.assertIn('python3 -m pytest -p no:anyio tests/test_skill_discovery_metadata.py -q', text)
        self.assertLess(text.index('python3 -m pip install'), text.index('python3 -m pytest'))
        self.assertNotIn('--user', text)

    def test_metadata_and_install_chain_changes_trigger_pr_regression(self):
        paths = self.text().split('  pull_request:\n', 1)[1].split('  release:\n', 1)[0]
        for path in ('tests/test_skill_discovery_metadata.py', 'tests/run_public_site_checks.py',
                     'install.sh', 'scripts/sync-to-global.sh', 'tools/install_skill_pack.py',
                     'tools/validate_skill_pack.py', 'mcp/workflow-gate/skill-routes.json',
                     'integrations/codebuddy/**', '.gitignore', 'agents/**', 'skill-packs/**'):
            with self.subTest(path=path):
                self.assertIn("- '" + path + "'", paths)

    def test_disabled_by_default_and_no_pr_deploy(self):
        text = self.text()
        self.assertIn('  push:\n    branches: [main]\n', text)
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
        self.assertIn('cancel-in-progress: false', block)
        self.assertIn('queue: max', block)
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
                  ('push', 'refs/heads/main', False, False, True),
                  ('push', 'refs/heads/feature', False, False, False),
                  ('push', 'refs/tags/v1.2.3', False, False, False)]
        for enabled in ('true', 'false', ''):
            for url in ('https://example.org/project/', ''):
                for event, ref, prerelease, draft, allowed in events:
                    with self.subTest(enabled=enabled, url=url, event=event, ref=ref, prerelease=prerelease, draft=draft):
                        args = (enabled, url, event, ref, prerelease, draft)
                        eligible = enabled == 'true' and bool(url) and allowed
                        self.assertEqual(evaluate(policy, *args), eligible)
                        selected = evaluate(group, *args)
                        if eligible:
                            self.assertEqual(selected, 'deployment-v2')
                        else:
                            self.assertTrue(str(selected).startswith('preview-'), selected)

    def test_concurrency_eligibility_matches_publish_policy_exactly(self):
        text = self.text()
        policy = re.search(r'PUBLISH: \$\{\{ (.+) \}\}', text).group(1)
        group = re.search(r'group: public-site-\$\{\{ (.+) \}\}', text).group(1)
        self.assertTrue(group.startswith(policy + " && 'deployment-v2' || "))

    def test_old_rerun_cannot_cancel_active_or_pending_latest_publication(self):
        block = self.text().split('concurrency:\n', 1)[1].split('\njobs:', 1)[0]
        cancel = re.search(r'cancel-in-progress: (\w+)', block).group(1) == 'true'
        queue_match = re.search(r'queue: (\w+)', block)
        capacity = 100 if queue_match and queue_match.group(1) == 'max' else 1
        # Model GitHub's documented queue semantics with the real workflow settings.
        # A stale rerun must neither cancel an active current SHA nor replace its pending retry.
        active = 'current'
        pending = ['current']
        cancelled = []
        if cancel:
            cancelled.append(active)
            active = None
        if capacity == 1:
            cancelled.extend(pending)
            pending.clear()
        pending.append('stale-rerun')
        self.assertEqual(cancelled, [])
        self.assertEqual(active, 'current')
        self.assertEqual(pending, ['current', 'stale-rerun'])
        # The separately executed Git regression proves stale source fails closed;
        # the queue must retain every current publication until it reaches that gate.
        published = [source for source in [active, *pending] if source == 'current']
        self.assertEqual(len(published), 2)

    def test_stale_publications_fail_closed_before_upload_and_deploy(self):
        text = self.text()
        scripts = re.findall(r'- name: Reject superseded publication source\n(.*?)(?=      - )', text, re.S)
        self.assertEqual(len(scripts), 2, 'check freshness before upload and again before deployment')
        build, deploy = text.split('\n  deploy:\n', 1)
        self.assertLess(build.index('Reject superseded'), build.index('Package only generated'))
        self.assertLess(deploy.index('needs.build.outputs.commit'), deploy.index('Reject superseded'))
        self.assertLess(deploy.index('Reject superseded'), deploy.index('actions/deploy-pages@'))
        for block in scripts:
            self.assertIn('DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}', block)
            self.assertIn('shell: bash', block)
        script = textwrap.dedent(scripts[0].split('run: |\n', 1)[1])
        self.assertEqual(script, textwrap.dedent(scripts[1].split('run: |\n', 1)[1]))
        with tempfile.TemporaryDirectory() as tmp:
            origin, checkout = Path(tmp) / 'origin', Path(tmp) / 'checkout'
            def git(*args, cwd=origin):
                return subprocess.run(['git', *args], cwd=cwd, check=True,
                                      capture_output=True, text=True, timeout=10)
            origin.mkdir()
            git('init', '-b', 'main')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            git('commit', '--allow-empty', '-m', 'initial')
            git('clone', str(origin), str(checkout), cwd=Path(tmp))
            def run(branch='main'):
                return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script],
                                      cwd=checkout, env=dict(os.environ, DEFAULT_BRANCH=branch),
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(run().returncode, 0)
            git('commit', '--allow-empty', '-m', 'newer source')
            stale = run()
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn('Superseded publication source', stale.stdout)
            self.assertNotEqual(run('missing-branch').returncode, 0)
            git('remote', 'set-url', 'origin', str(Path(tmp) / 'unavailable'), cwd=checkout)
            self.assertNotEqual(run().returncode, 0)

    def test_notification_preserves_real_outcome_and_machine_result(self):
        block = self.text().split('- name: Notify participating search engines (optional)', 1)[1]
        block = block.split('- name: Summarize evidence boundary', 1)[0]
        self.assertIn('id: search_notification', block)
        self.assertIn('shell: bash', block)  # Explicit Bash enables pipefail in Actions.
        self.assertIn('continue-on-error: true', block)
        self.assertIn('--submit | tee "$RUNNER_TEMP/indexnow-result.json"', block)

    def test_summary_distinguishes_delivery_failure_skipped_and_missing_evidence(self):
        block = self.text().split('- name: Summarize evidence boundary', 1)[1]
        self.assertIn('if: always()', block)
        self.assertIn('DEPLOYMENT_OUTCOME: ${{ steps.deployment.outcome }}', block)
        self.assertIn('NOTIFICATION_OUTCOME: ${{ steps.search_notification.outcome }}', block)
        self.assertIn('run: |\n', block)
        script = textwrap.dedent(block.split('run: |\n', 1)[1])
        for outcome, result in [('success', '{"status":"received","http_status":200}'),
                                ('success', '{"status":"pending","http_status":202}'),
                                ('failure', '{"status":"failed","stage":"ownership","http_status":404}'),
                                ('success', '{"status":"skipped","reason":"missing_key"}'),
                                ('skipped', None), ('failure', None)]:
            with self.subTest(outcome=outcome, result=result), tempfile.TemporaryDirectory() as tmp:
                temp = Path(tmp)
                if result is not None:
                    (temp / 'indexnow-result.json').write_text(result + '\n')
                env = dict(os.environ, RUNNER_TEMP=tmp, GITHUB_STEP_SUMMARY=str(temp / 'summary.md'),
                           DEPLOYMENT_OUTCOME='success', NOTIFICATION_OUTCOME=outcome,
                           INDEXNOW_KEY='NOT-A-LOGGABLE-KEY')
                proc = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script],
                                      env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                rendered = (temp / 'summary.md').read_text()
                self.assertIn('Pages deployment: success', rendered)
                self.assertIn('IndexNow step outcome: ' + outcome, rendered)
                self.assertIn('not proof of indexing or ranking', rendered)
                self.assertNotIn('NOT-A-LOGGABLE-KEY', rendered)
                if result is None:
                    self.assertIn('No notification result was produced', rendered)
                    self.assertNotIn('"status":"received"', rendered)
                else:
                    self.assertIn(result, rendered)


if __name__ == '__main__':
    unittest.main()
