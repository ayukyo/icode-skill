"""Release attachments form one offline, version-pinned distribution batch."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest
import zipfile
from unittest.mock import patch

import test_skill_distribution as distribution

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/build_release_assets.py"


class ReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = distribution.DistributionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.output = self.fixture.base / "release"

    def build(self, output=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--source",
            str(self.fixture.source), "--output", str(output or self.output)],
            capture_output=True, text=True, encoding="utf-8")

    def success(self, output=None):
        result = self.build(output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_every_component_url_has_a_matching_published_attachment(self):
        result = self.success()
        names = {f"icode-v2.32.0-{name}.zip" for name in
                 ("core", "plugin-core", "skillhub", "ppt", "docx", "mcp", "ui", "support")}
        names.add("icode-v2.32.0.tar.gz")
        self.assertEqual({row["name"] for row in result["assets"]}, names)
        for line in (self.output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(len(list(self.output.iterdir())), 12)
        with zipfile.ZipFile(self.output / "icode-v2.32.0-core.zip") as core:
            self.assertTrue(core.read("icode/SKILL.md").startswith(b"---\n"))
            self.assertNotIn(b"\r", core.read("icode/SKILL.md"))
            manifest = json.loads(core.read("icode/components.json"))
            for component in manifest["components"].values():
                path = self.output / component["archive"]
                self.assertEqual(path.stat().st_size, component["size"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), component["sha256"])
                self.assertTrue(component["url"].endswith("/v2.32.0/" + path.name))
        legacy = (self.output / "icode-v2.32.0.tar.gz.sha256").read_text(encoding="utf-8")
        self.assertIn("  icode-v2.32.0.tar.gz\n", legacy)

    def test_skillhub_archive_preserves_resources_license_and_pinned_components(self):
        template = '{"project_id":"example"}\n'
        self.fixture.put("templates/device_config.template.json", template)
        self.fixture.git("add", "templates/device_config.template.json")
        self.success()
        target = self.output / "icode-v2.32.0-skillhub.zip"
        self.assertTrue(target.is_file(), "Release must build the upload package from source")
        with zipfile.ZipFile(target) as archive:
            payload = {name.removeprefix("icode/"): archive.read(name)
                       for name in archive.namelist()}
        self.assertLessEqual(len(payload), 200)
        self.assertLessEqual(sum(map(len, payload.values())), 5 * 1024 * 1024)
        self.assertEqual(payload["LICENSE.txt"], (self.fixture.source / "LICENSE.txt").read_bytes())
        self.assertEqual(payload["templates/device_config.template.json"],
                         (self.fixture.source / "templates/device_config.template.json").read_bytes())
        self.assertNotIn(".gitignore", payload)
        self.assertNotIn("agents/openai.yaml", payload)
        entry = payload["SKILL.md"].decode("utf-8")
        for field in ('slug: "icode"', 'version: "2.32.0"',
                      'displayName: "ICODE AI Coding Workflow"'):
            self.assertIn(field, entry)
        for name, data in payload.items():
            self.assertIn(Path(name).suffix, {".md", ".txt", ".json", ".py", ".sh", ".svg"})
            data.decode("utf-8", errors="strict")
        manifest = json.loads(payload["components.json"])
        with zipfile.ZipFile(self.output / "icode-v2.32.0-core.zip") as core:
            original = json.loads(core.read("icode/components.json"))
        self.assertEqual(manifest["components"], original["components"])
        declared = set()
        for row in manifest["core_files"]:
            declared.add(row["path"])
            data = payload[row["path"]]
            self.assertEqual(row["size"], len(data))
            self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(declared, payload.keys() - {"components.json"})

    def test_skillhub_rejects_unhandled_source_file_types_before_release_output(self):
        self.fixture.put("references/unsupported.blob", "text with unsupported extension\n")
        self.fixture.git("add", "references/unsupported.blob")
        result = self.build()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SkillHub", result.stderr)
        self.assertFalse(self.output.exists())

    def test_import_archive_accepts_200_files_and_rejects_201(self):
        baseline = self.success()["profiles"]["core"]["files"]
        for index in range(200 - baseline):
            self.fixture.put(f"references/budget-{index}.md", "budget fixture\n")
        self.fixture.git("add", "references")
        at_limit = self.fixture.base / "at-limit"
        result = self.success(at_limit)
        self.assertEqual(result["profiles"]["core"]["files"], 200)
        with zipfile.ZipFile(at_limit / "icode-v2.32.0-plugin-core.zip") as archive:
            self.assertEqual(len(archive.namelist()), 200)
        for profile in ("core", "skillhub"):
            with zipfile.ZipFile(at_limit / f"icode-v2.32.0-{profile}.zip") as archive:
                self.assertLessEqual(len(archive.namelist()), 200)
        self.fixture.put("references/over-limit.md", "one file too many\n")
        self.fixture.git("add", "references/over-limit.md")
        overflow = self.fixture.base / "over-limit"
        result = self.build(overflow)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("core budget exceeded", result.stderr)
        self.assertFalse(overflow.exists())

    def test_core_plus_all_components_restores_full_workflow(self):
        self.success()
        with tarfile.open(self.output / "icode-v2.32.0.tar.gz") as full:
            expected = {m.name.removeprefix("icode/skills/icode/"): full.extractfile(m).read()
                        for m in full if m.isfile() and m.name.startswith("icode/skills/icode/")}
        with zipfile.ZipFile(self.output / "icode-v2.32.0-core.zip") as core:
            restored = {name.removeprefix("icode/"): core.read(name) for name in core.namelist()}
        manifest = json.loads(restored.pop("components.json"))
        restored.pop("SKILL.md")
        restored["SKILL.md"] = restored.pop("WORKFLOW.md")
        for component in manifest["components"].values():
            with zipfile.ZipFile(self.output / component["archive"]) as archive:
                for name in archive.namelist():
                    self.assertNotIn(name, restored)
                    restored[name] = archive.read(name)
        self.assertEqual(restored, expected)

    def test_archives_and_receipts_are_repeatable(self):
        self.success()
        other = self.fixture.base / "second-release"
        self.success(other)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()},
                         {p.name: p.read_bytes() for p in other.iterdir()})

    def test_existing_or_source_output_is_rejected_without_overwrite(self):
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_bytes(b"user data")
        for path in [self.output, self.fixture.source / "release"]:
            with self.subTest(path=path):
                result = self.build(path)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(marker.read_bytes(), b"user data")
        self.assertEqual(list(self.output.iterdir()), [marker])
        self.assertFalse((self.fixture.source / "release").exists())

    def test_core_budget_failure_does_not_publish_partial_release(self):
        self.fixture.put("references/oversized.md", "x" * (6 * 1024 * 1024))
        self.fixture.git("add", "references/oversized.md")
        result = self.build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("core budget exceeded", result.stderr)
        self.assertFalse(self.output.exists())

    def test_changed_plugin_metadata_rejects_mixed_release_batch(self):
        spec = importlib.util.spec_from_file_location("release_assets_review", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
            spec.loader.exec_module(module)
        original = module.build

        def change_after_full(*args, **kwargs):
            report = original(*args, **kwargs)
            if report["profile"] == "full":
                path = self.fixture.source / "integrations/discovery/claude.plugin.template.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                metadata["description"] = "Changed between profile builds"
                path.write_text(json.dumps(metadata), encoding="utf-8")
            return report

        with patch.object(module, "build", side_effect=change_after_full):
            with self.assertRaisesRegex(ValueError, "plugin resources"):
                module.build_assets(self.fixture.source, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction contract")
    def test_junction_cannot_redirect_release_output_into_source(self):
        junction = self.fixture.base / "source-junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction),
                                 str(self.fixture.source)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            result = self.build(junction / "unexpected-release")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("reparse", result.stderr)
            self.assertFalse((self.fixture.source / "unexpected-release").exists())
        finally:
            os.rmdir(junction)  # Remove only this test's junction, never its target.


if __name__ == "__main__":
    unittest.main()
