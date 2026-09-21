"""Static security and trigger contracts for release promotion automation."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-promotion.yml"
PUBLIC_SITE_WORKFLOW = ROOT / ".github" / "workflows" / "public-site.yml"
PUBLIC_SITE_CHECKS = ROOT / "tests" / "run_public_site_checks.py"
DOCUMENTATION = ROOT / "docs" / "release-promotion.md"


class ReleasePromotionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.is_file(), "release promotion workflow is missing")
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.data = yaml.safe_load(self.text)

    def test_only_published_releases_and_manual_runs_can_trigger(self):
        # PyYAML 1.1 may parse the YAML key `on` as boolean true.
        triggers = self.data.get("on", self.data.get(True))
        self.assertEqual(triggers["release"]["types"], ["published"])
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("push", triggers)
        self.assertNotIn("pull_request", triggers)
        dry_run = triggers["workflow_dispatch"]["inputs"]["dry_run"]
        self.assertEqual(str(dry_run["default"]).lower(), "true")

    def test_permissions_are_narrow_and_secrets_stay_out_of_untrusted_events(self):
        self.assertEqual(self.data["permissions"], {"contents": "read"})
        self.assertEqual(self.data["jobs"]["publish"]["permissions"],
                         {"contents": "read", "discussions": "write"})
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions/checkout@v", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_publish_policy_and_receipts_are_explicit(self):
        self.assertIn("PROMOTION_ENABLED", self.text)
        self.assertIn("release.published", self.text)
        self.assertIn("--submit", self.text)
        self.assertIn("promotion-receipt.json", self.text)
        self.assertIn("GITHUB_STEP_SUMMARY", self.text)
        self.assertIn("environment:", self.text)
        self.assertIn("release-promotion", self.text)
        self.assertGreaterEqual(self.text.count("set -o pipefail"), 2)

    def test_existing_ci_covers_the_new_tool_workflow_and_tests(self):
        text = PUBLIC_SITE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("tools/release_promotion.py", text)
        self.assertIn(".github/workflows/release-promotion.yml", text)
        self.assertIn("tests/test_release_promotion", text)
        checks = PUBLIC_SITE_CHECKS.read_text(encoding="utf-8")
        self.assertIn("tools/release_promotion.py", checks)
        self.assertIn("test_release_promotion", checks)
        self.assertIn("BSKY_APP_PASSWORD", checks)
        self.assertIn("MASTODON_ACCESS_TOKEN", checks)

    def test_operator_setup_and_evidence_boundaries_are_documented(self):
        self.assertTrue(DOCUMENTATION.is_file(), "release promotion operator guide is missing")
        text = DOCUMENTATION.read_text(encoding="utf-8")
        for phrase in (
            "PROMOTION_ENABLED", "PROMOTION_CHANNELS", "release-promotion",
            "BSKY_APP_PASSWORD", "MASTODON_ACCESS_TOKEN", "dry-run",
            "不代表搜索引擎已经收录", "不在每次提交后发布",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
