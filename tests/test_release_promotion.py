"""Offline contracts for release-driven promotion planning and publishing."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release_promotion.py"
REPOSITORY = "ayukyo/icode-skill"
SITE_URL = "https://ayukyo.github.io/icode-skill/"


class ReleasePromotionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "release promotion tool has not been implemented")
        spec = importlib.util.spec_from_file_location("release_promotion", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.release = {
            "tag_name": "v2.31.0",
            "name": "ICODE v2.31.0",
            "html_url": f"https://github.com/{REPOSITORY}/releases/tag/v2.31.0",
            "published_at": "2026-09-21T12:00:00Z",
            "draft": False,
            "prerelease": False,
        }

    def test_plan_is_deterministic_bounded_and_does_not_copy_release_body(self):
        self.release["body"] = "PRIVATE-NOTES-SHOULD-NOT-BE-PUBLISHED"
        first = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        second = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["campaign_id"], second["campaign_id"])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn(self.release["body"], serialized)
        self.assertLessEqual(len(first["channels"]["bluesky"]["text"]), 300)
        self.assertLessEqual(len(first["channels"]["mastodon"]["status"]), 500)
        self.assertIn(SITE_URL, first["channels"]["bluesky"]["text"])
        self.assertIn("v2.31.0", first["channels"]["github"]["title"])

    def test_plan_rejects_untrusted_or_ambiguous_release_inputs(self):
        mutations = {
            "draft": True,
            "prerelease": True,
            "tag_name": "latest release",
            "html_url": "https://attacker.example/release",
            "published_at": "not-a-time",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                release = dict(self.release)
                release[field] = value
                with self.assertRaises(self.module.InputError):
                    self.module.build_plan(release, REPOSITORY, SITE_URL)
        for repository in ("", "owner", "owner/repo/extra", "../repo", "owner/re po"):
            with self.subTest(repository=repository), self.assertRaises(self.module.InputError):
                self.module.build_plan(self.release, repository, SITE_URL)
        for site_url in ("http://example.com/", "https://user:pass@example.com/",
                         "https://localhost/", "https://example.com/path"):
            with self.subTest(site_url=site_url), self.assertRaises(self.module.InputError):
                self.module.build_plan(self.release, REPOSITORY, site_url)

    def test_default_cli_plan_is_offline_and_atomic(self):
        with tempfile.TemporaryDirectory(prefix="promotion-test-", dir=ROOT) as directory:
            event = Path(directory) / "event.json"
            output = Path(directory) / "plan.json"
            event.write_text(json.dumps({"release": self.release}), encoding="utf-8")
            with patch.object(self.module, "http_json", side_effect=AssertionError("network forbidden")):
                code = self.module.main([
                    "plan", "--event", str(event), "--repository", REPOSITORY,
                    "--site-url", SITE_URL, "--output", str(output),
                ])
            self.assertEqual(code, 0)
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(plan["release"]["tag"], "v2.31.0")
            self.assertFalse((output.parent / (output.name + ".tmp")).exists())

    def test_publish_defaults_to_dry_run_with_zero_network(self):
        plan = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        with patch.object(self.module, "http_json", side_effect=AssertionError("network forbidden")):
            code, receipt = self.module.publish(plan, ["github", "bluesky", "mastodon"], submit=False)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "dry-run")
        self.assertEqual({item["status"] for item in receipt["channels"]}, {"dry-run"})

    def test_submit_without_credentials_skips_external_channels_without_network(self):
        plan = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        names = ["GITHUB_TOKEN", "BSKY_HANDLE", "BSKY_APP_PASSWORD",
                 "MASTODON_BASE_URL", "MASTODON_ACCESS_TOKEN"]
        clean = {name: "" for name in names}
        with patch.dict(os.environ, clean, clear=False):
            with patch.object(self.module, "http_json", side_effect=AssertionError("network forbidden")):
                code, receipt = self.module.publish(
                    plan, ["github", "bluesky", "mastodon"], submit=True
                )
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "skipped")
        self.assertEqual({item["status"] for item in receipt["channels"]}, {"skipped"})
        self.assertEqual({item["reason"] for item in receipt["channels"]}, {"missing_credentials"})

    def test_bluesky_uses_deterministic_put_record_and_mastodon_idempotency_header(self):
        plan = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        calls = []

        def fake_http(method, url, *, headers=None, payload=None):
            calls.append((method, url, headers or {}, payload))
            if url.endswith("com.atproto.server.createSession"):
                return 200, {"accessJwt": "session-token", "did": "did:plc:icode"}
            if url.endswith("com.atproto.repo.putRecord"):
                return 200, {"uri": "at://did:plc:icode/app.bsky.feed.post/record"}
            if url.endswith("/api/v1/statuses"):
                return 200, {"id": "mastodon-post-1", "url": "https://social.example/@icode/1"}
            raise AssertionError(url)

        env = {
            "BSKY_HANDLE": "icode.example.com",
            "BSKY_APP_PASSWORD": "secret-app-password",
            "MASTODON_BASE_URL": "https://social.example/",
            "MASTODON_ACCESS_TOKEN": "secret-access-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(self.module, "http_json", fake_http):
            code, receipt = self.module.publish(plan, ["bluesky", "mastodon"], submit=True)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "published")
        put = next(call for call in calls if call[1].endswith("com.atproto.repo.putRecord"))
        self.assertEqual(put[3]["rkey"], plan["channels"]["bluesky"]["rkey"])
        mastodon = next(call for call in calls if call[1].endswith("/api/v1/statuses"))
        self.assertEqual(mastodon[2]["Idempotency-Key"],
                         plan["channels"]["mastodon"]["idempotency_key"])
        self.assertNotIn("secret", json.dumps(receipt))

    def test_github_discussion_reuses_marker_or_creates_in_configured_category(self):
        plan = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        marker = plan["channels"]["github"]["marker"]
        headers_seen = []

        def existing_http(method, url, *, headers=None, payload=None):
            headers_seen.append(headers or {})
            return 200, {"data": {"repository": {
                "id": "repo-id",
                "discussionCategories": {"nodes": [{"id": "cat-id", "name": "Announcements"}]},
                "discussions": {"nodes": [{"url": "https://github.com/example/discussions/1",
                                              "title": "old", "body": marker}]},
            }}}

        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret-github-token"}, clear=False):
            with patch.object(self.module, "http_json", existing_http):
                code, receipt = self.module.publish(plan, ["github"], submit=True)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["channels"][0]["status"], "existing")
        self.assertEqual(len(headers_seen), 1)
        self.assertEqual(headers_seen[0]["Authorization"], "Bearer secret-github-token")
        self.assertNotIn("secret", json.dumps(receipt))

        calls = []

        def create_http(method, url, *, headers=None, payload=None):
            calls.append(payload)
            if len(calls) == 1:
                return 200, {"data": {"repository": {
                    "id": "repo-id",
                    "discussionCategories": {"nodes": [{"id": "cat-id", "name": "News"}]},
                    "discussions": {"nodes": []},
                }}}
            return 200, {"data": {"createDiscussion": {"discussion": {
                "url": "https://github.com/example/discussions/2"
            }}}}

        env = {"GITHUB_TOKEN": "secret-github-token", "GITHUB_DISCUSSION_CATEGORY": "News"}
        with patch.dict(os.environ, env, clear=False), patch.object(self.module, "http_json", create_http):
            code, receipt = self.module.publish(plan, ["github"], submit=True)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["channels"][0]["status"], "published")
        self.assertEqual(calls[1]["variables"]["categoryId"], "cat-id")
        self.assertIn(marker, calls[1]["variables"]["body"])

    def test_tampered_plan_and_invalid_channel_fail_before_network(self):
        plan = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        plan["channels"]["mastodon"]["status"] += " tampered"
        with patch.object(self.module, "http_json", side_effect=AssertionError("network forbidden")):
            with self.assertRaises(self.module.InputError):
                self.module.publish(plan, ["mastodon"], submit=True)
        clean = self.module.build_plan(self.release, REPOSITORY, SITE_URL)
        for channels in ([], ["github", "github"], ["unknown"]):
            with self.subTest(channels=channels), self.assertRaises(self.module.InputError):
                self.module.publish(clean, channels, submit=False)


if __name__ == "__main__":
    unittest.main()
