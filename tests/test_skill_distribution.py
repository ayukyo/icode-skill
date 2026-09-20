"""Offline package tests; never install a host or touch user configuration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools/build_skill_distribution.py"


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icode-distribution-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.output = self.base / "icode"
        self.git("init", "-q")
        files = {
            "SKILL.md": "---\nname: icode\ndescription: Workflow\n---\n**版本**: v2.32.0\n",
            "LICENSE": "MIT\n",
            "README.md": "Readme\n",
            "README.zh-CN.md": "说明\n",
            ".gitignore": "__pycache__/\n",
            "install.sh": "#!/bin/sh\nexit 0\n",
            "steps/01_plan.md": "plan\n",
            "references/skill_routing.md": "routing\n",
            "schemas/metadata.schema.json": "{}\n",
            "templates/metadata.json": "{}\n",
            "tools/icode_control.py": "print('control')\n",
            "tools/docx/requirements.lock": "# locked dependencies\n",
            "scripts/sync-to-global.sh": "#!/bin/sh\nexit 0\n",
            "agent_runtime/README.md": "runtime\n",
            "agents/openai.yaml": "interface:\n  display_name: ICODE\n",
            "docs/example.md": "documentation\n",
            "integrations/codebuddy/commands/icode.md": "bridge\n",
            "mcp/workflow-gate/gates.json": "{}\n",
            "mcp/workflow-gate/skill-routes.json": json.dumps({
                "schema_version": 1, "routes": [{
                    "skill": "example", "steps": ["plan"], "triggers": ["bug"],
                    "input_contract": ["input"], "output_contract": ["output"],
                    "fallback": "report unavailable",
                }]}),
            "mcp/example/config.example.json": "{}\n",
            "skill-packs/manifest.json": json.dumps({"schema_version": 1, "skills": [{
                "name": "example", "path": "example", "entrypoint": "SKILL.md.template",
            }]}),
            "skill-packs/example/SKILL.md.template": (
                "---\nname: example\ndescription: Use when testing\n---\nRead reference.md\n"),
            "skill-packs/example/reference.md": "shared resource\n",
            "site/private.txt": "excluded site\n",
            "demo/private.txt": "excluded demo\n",
            "tests/private.txt": "excluded tests\n",
        }
        for name, body in files.items():
            self.put(name, body)
        for path in (ROOT / "integrations/discovery").iterdir():
            if path.name == "README.md" or path.name.endswith(".plugin.json.template"):
                self.put(f"integrations/discovery/{path.name}", path.read_text())
        (self.source / "install.sh").chmod(0o755)
        self.git("add", ".")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.source), *args],
                              check=True, capture_output=True)

    def put(self, name, body):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def build(self, output=None, source=None):
        return subprocess.run([
            sys.executable, "-B", str(BUILDER), "--source", str(source or self.source),
            "--output", str(output or self.output),
        ], capture_output=True, text=True)

    def assert_rejected(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("error", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_bundle_contains_workflow_and_rendered_shared_skill_with_resources(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        workflow = self.output / "skills/icode"
        for name in ("SKILL.md", "steps/01_plan.md", "references/skill_routing.md",
                     "schemas/metadata.schema.json", "tools/icode_control.py",
                     "mcp/workflow-gate/gates.json", "templates/metadata.json",
                     "agent_runtime/README.md", "scripts/sync-to-global.sh",
                     "agents/openai.yaml",
                     "tools/docx/requirements.lock", "mcp/example/config.example.json",
                     "integrations/codebuddy/commands/icode.md"):
            self.assertEqual((workflow / name).read_bytes(), (self.source / name).read_bytes())
        self.assertTrue(os.access(workflow / "install.sh", os.X_OK))
        self.assertEqual((self.output / "skills/example/SKILL.md").read_bytes(),
                         (self.source / "skill-packs/example/SKILL.md.template").read_bytes())
        self.assertEqual((self.output / "skills/example/reference.md").read_text(), "shared resource\n")
        self.assertEqual({p.parent.name for p in (self.output / "skills").glob("*/SKILL.md")},
                         {"icode", "example"})
        self.assertFalse(any((workflow / x).exists() for x in ("site", "demo", "tests", ".git")))
        for host in ("claude", "codex", "codebuddy"):
            manifest = json.loads((self.output / f".{host}-plugin/plugin.json").read_text())
            self.assertEqual(manifest["skills"], "./skills/")
            self.assertEqual(manifest["name"], "icode")
            self.assertEqual(manifest["version"], "2.32.0")
            self.assertTrue(manifest["description"])
            self.assertNotIn("mcpServers", manifest)
            self.assertNotIn("hooks", manifest)
        self.assertFalse((self.output / ".mcp.json").exists())

    def test_untracked_files_are_not_published(self):
        self.put("tools/accidental.py", "must not ship\n")
        self.put("skill-packs/example/accidental.md", "must not ship\n")
        self.assertEqual(self.build().returncode, 0)
        self.assertFalse(list(self.output.rglob("accidental.*")))

    def test_output_must_be_new_and_outside_source(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("keep")
        self.assert_rejected(self.build())
        self.assertEqual(marker.read_text(), "keep")
        self.assert_rejected(self.build(output=self.source / "icode"))
        self.assertFalse((self.source / "icode").exists())

    def test_rejects_symlink_source_file_and_directory(self):
        for relative in ("tools/icode_control.py", "tools/link"):
            with self.subTest(relative=relative):
                path = self.source / relative
                if path.exists():
                    path.unlink()
                path.symlink_to(self.source / "references")
                self.git("add", relative)
                self.assert_rejected(self.build())
                self.assertFalse(self.output.exists())
                path.unlink()
                self.git("rm", "--cached", relative)

    def test_rejects_symlink_output_parent_and_source_root(self):
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        self.assert_rejected(self.build(output=link / "icode"))
        self.assert_rejected(self.build(source=link / "source"))
        self.assertFalse(self.output.exists())

    def test_rejects_sensitive_tracked_paths_before_output(self):
        names = ("tools/tb/config.json", "tools/private/data.md", "tools/cookies.json",
                 "mcp/example/.env", "tools/secret.pem", "tools/tb/scripts/.tb_cookie",
                 "tools/private-data/data.md", "tools/private.py", "tools/config.yaml",
                 "tools/cookies/data.json", "tools/cookies.json.template")
        for name in names:
            with self.subTest(name=name):
                self.put(name, "DO_NOT_READ_OR_PUBLISH\n")
                self.git("add", "-f", name)
                result = self.build()
                self.assert_rejected(result)
                self.assertNotIn("DO_NOT_READ_OR_PUBLISH", result.stderr)
                self.assertFalse(self.output.exists())
                self.git("rm", "-f", name)

    def test_missing_workflow_resource_fails_before_output(self):
        self.git("rm", "-f", "mcp/workflow-gate/gates.json")
        self.assert_rejected(self.build())
        self.assertFalse(self.output.exists())

    def test_invalid_shared_manifest_is_rejected(self):
        self.put("skill-packs/manifest.json", "{broken")
        self.assert_rejected(self.build())
        self.assertFalse(self.output.exists())

    def test_repeat_build_is_byte_identical(self):
        self.assertEqual(self.build().returncode, 0)
        another_parent = self.base / "second"
        another_parent.mkdir()
        another = another_parent / "icode"
        self.assertEqual(self.build(output=another).returncode, 0)
        def digest(root):
            return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob("*") if p.is_file()}
        self.assertEqual(digest(self.output), digest(another))

    def test_manifest_metadata_comes_from_selected_source(self):
        name = "integrations/discovery/claude.plugin.json.template"
        manifest = json.loads((self.source / name).read_text())
        manifest["description"] = "selected source description"
        self.put(name, json.dumps(manifest))
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        built = json.loads((self.output / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(built["description"], "selected source description")

    def test_missing_shared_template_fails_before_output(self):
        self.git("rm", "-f", "skill-packs/example/SKILL.md.template")
        self.assert_rejected(self.build())
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
