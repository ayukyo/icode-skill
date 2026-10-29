"""Offline package tests; never install a host or touch user configuration."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch


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
            "LICENSE.txt": "MIT\n",
            "README.md": "Readme\n",
            "README.zh-CN.md": "说明\n",
            ".gitignore": "__pycache__/\n",
            "install.sh": "#!/bin/sh\nexit 0\n",
            "steps/01_plan.md": "plan\n",
            "steps/docx.md": "docx authoring and revision\n",
            "steps/doc.md": "DOC worktree snapshots and exact binding\n",
            "references/doc_snapshot_contract.md": "DOC storage contract\n",
            "references/wsl_build.md": "WSL build contract\n",
            "references/mcp_path_access.md": "Local MCP path contract\n",
            "tools/test_result_summary.py": "# bounded JUnit report summary\n",
            "mcp/_lib/environment_diagnostics.py": "# offline observations\n",
            "tools/mcp_doctor.py": "# offline environment CLI\n",
            "references/environment_diagnostics.md": "Environment observation boundaries\n",
            "tools/doc_snapshot.py": "# DOC capture/publish/resolve runtime\n",
            "references/skill_routing.md": "routing\n",
            "references/docx_authoring.md": "authoring\n",
            "references/docx_revision.md": "revision\n",
            "schemas/metadata.schema.json": "{}\n",
            "templates/metadata.json": "{}\n",
            "tools/icode_control.py": "print('control')\n",
            "tools/docx/requirements.lock": "# locked dependencies\n",
            "tools/docx/bootstrap_renderer.py": "# renderer bootstrap\n",
            "tools/docx/renderer_support.py": "# renderer validation\n",
            "tools/docx/renderer_launch.py": "# owned launch wrapper\n",
            "tools/docx/renderer_packages.lock.json": "{\"schema_version\":1,\"packages\":[]}\n",
            "tools/docx/build_docx.py": "# docx builder\n",
            "tools/docx/inspect_docx.py": "# integrity checks\n",
            "tools/docx/render_docx.py": "# page renderer\n",
            "tools/docx/compare_docx.py": "# scoped revision checks\n",
            "tools/docx/verify_sources.py": "# source anchors\n",
            "tools/docx/draw_sequence.py": "# palette-bound sequence diagrams\n",
            "tools/docx/diagram_svg.py": "# bounded SVG runtime\n",
            "tools/docx/diagram_ooxml.py": "# shared SVG fallback relationships\n",
            "tools/docx/diagram.schema.json": "{}\n",
            "tools/docx/qualify_svg.py": "# Chinese SVG fixtures\n",
            "tools/docx/check_svg_release.py": "# owned open-source qualification\n",
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
        self.put("assets/icode-ticket-hex-128.png", "small icon\n")
        self.put("assets/icode-ticket-hex-512.png", "large icon\n")
        self.put("tools/ppt/templates/example/template.pptx", "template payload\n")
        for name in ("install_skill_pack.py", "validate_skill_pack.py", "build_skill_distribution.py"):
            self.put(f"tools/{name}", (ROOT / "tools" / name).read_text(encoding="utf-8"))
        for path in (ROOT / "integrations/discovery").iterdir():
            if path.name == "README.md" or path.name.endswith(".plugin.template.json"):
                self.put(f"integrations/discovery/{path.name}", path.read_text(encoding="utf-8"))
        (self.source / "install.sh").chmod(0o755)
        self.git("add", ".")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.source), *args],
                              check=True, capture_output=True)

    def put(self, name, body):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def symlink_or_skip(self, path, target, **kwargs):
        try:
            path.symlink_to(target, **kwargs)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows account cannot create symbolic links; verify on Linux")
            raise

    def build(self, output=None, source=None):
        return subprocess.run([
            sys.executable, "-B", str(BUILDER), "--source", str(source or self.source),
            "--output", str(output or self.output),
        ], capture_output=True, text=True)

    def build_core(self):
        return subprocess.run([
            sys.executable, "-B", str(BUILDER), "--source", str(self.source),
            "--output", str(self.output), "--profile", "core",
            "--components-output", str(self.base / "components"),
        ], capture_output=True, text=True, encoding="utf-8")

    def component_install(self, component="docx", dry_run=False):
        skill = self.output / "skills/icode"
        manifest = json.loads((skill / "components.json").read_text(encoding="utf-8"))
        archive = self.base / "components" / manifest["components"][component]["archive"]
        args = [sys.executable, "-B", str(skill / "tools/install_skill_pack.py"),
                "--component-root", str(skill), "--component", component,
                "--component-archive", str(archive), "--component-cache", str(self.base / "cache")]
        if dry_run:
            args.append("--dry-run")
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")

    def file_snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()}

    def component_module(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            spec = importlib.util.spec_from_file_location("component_test_installer", ROOT / "tools/install_skill_pack.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            sys.path.pop(0)

    def rewrite_component(self, members, declaration_change=None):
        skill = self.output / "skills/icode"
        path = skill / "components.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        spec = metadata["components"]["docx"]
        archive = self.base / "components" / spec["archive"]
        rows = []
        with zipfile.ZipFile(archive, "w") as zipped:
            for name, data, mode in members:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                zipped.writestr(info, data)
                rows.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "mode": 0o644})
        if declaration_change:
            declaration_change(rows)
        spec.update(files=rows, size=archive.stat().st_size, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        path.write_text(json.dumps(metadata), encoding="utf-8")

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
                     "steps/docx.md", "references/docx_authoring.md", "references/docx_revision.md",
                     "steps/doc.md", "references/doc_snapshot_contract.md", "tools/doc_snapshot.py",
                     "references/wsl_build.md", "references/mcp_path_access.md", "tools/test_result_summary.py",
                     "mcp/_lib/environment_diagnostics.py", "tools/mcp_doctor.py", "references/environment_diagnostics.md",
                     "tools/docx/compare_docx.py", "tools/docx/verify_sources.py", "tools/docx/draw_sequence.py",
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
            manifest = json.loads((self.output / f".{host}-plugin/plugin.json").read_text(encoding="utf-8"))
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
                self.symlink_or_skip(path, self.source / "references")
                self.git("add", relative)
                self.assert_rejected(self.build())
                self.assertFalse(self.output.exists())
                path.unlink()
                self.git("rm", "--cached", relative)

    def test_rejects_symlink_output_parent_and_source_root(self):
        link = self.base / "link"
        self.symlink_or_skip(link, self.base, target_is_directory=True)
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

    def test_doc_runtime_must_be_tracked_before_packaging(self):
        baseline_parent = self.base / "baseline"
        baseline_parent.mkdir()
        baseline = self.build(output=baseline_parent / "icode")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.git("rm", "--cached", "tools/doc_snapshot.py")
        result = self.build()
        self.assert_rejected(result)
        self.assertIn("tools/doc_snapshot.py", result.stderr)
        self.assertFalse(self.output.exists())

    def test_generic_verification_resources_must_be_tracked(self):
        for name in ("tools/test_result_summary.py", "references/wsl_build.md", "references/mcp_path_access.md",
                     "mcp/_lib/environment_diagnostics.py", "tools/mcp_doctor.py", "references/environment_diagnostics.md"):
            with self.subTest(resource=name):
                self.git("rm", "--cached", name)
                result = self.build()
                self.assert_rejected(result)
                self.assertIn(name, result.stderr)
                self.assertFalse(self.output.exists())
                self.git("add", name)

    def test_missing_doc_contract_fails_before_output(self):
        self.git("rm", "-f", "references/doc_snapshot_contract.md")
        result = self.build()
        self.assert_rejected(result)
        self.assertIn("references/doc_snapshot_contract.md", result.stderr)
        self.assertFalse(self.output.exists())

    def test_bundle_text_is_utf8_when_python_utf8_mode_is_disabled(self):
        result = subprocess.run([
            sys.executable, "-B", "-X", "utf8=0", str(BUILDER), "--source", str(self.source),
            "--output", str(self.output),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for host in ("claude", "codex", "codebuddy"):
            json.loads((self.output / f".{host}-plugin/plugin.json").read_text(encoding="utf-8"))

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
        name = "integrations/discovery/claude.plugin.template.json"
        manifest = json.loads((self.source / name).read_text(encoding="utf-8"))
        manifest["description"] = "selected source description"
        self.put(name, json.dumps(manifest))
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        built = json.loads((self.output / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(built["description"], "selected source description")

    def test_missing_shared_template_fails_before_output(self):
        self.git("rm", "-f", "skill-packs/example/SKILL.md.template")
        self.assert_rejected(self.build())
        self.assertFalse(self.output.exists())

    def test_core_has_small_entry_complete_gates_icons_and_bounded_file_count(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        self.assertLessEqual(len(self.file_snapshot(self.output)), 200)
        self.assertLessEqual((skill / "SKILL.md").stat().st_size, 8192)
        self.assertEqual((skill / "WORKFLOW.md").read_bytes(), (self.source / "SKILL.md").read_bytes())
        for name in ("tools/icode_control.py", "schemas/metadata.schema.json",
                     "mcp/workflow-gate/gates.json", "mcp/workflow-gate/skill-routes.json",
                     "assets/icode-ticket-hex-128.png", "tools/install_skill_pack.py"):
            self.assertTrue((skill / name).is_file(), name)
        for name in ("tools/docx/build_docx.py", "tools/ppt/templates/example/template.pptx",
                     "agent_runtime/README.md", "docs/example.md", "mcp/example/config.example.json"):
            self.assertFalse((skill / name).exists(), name)
        self.assertTrue((self.output / "skills/example/SKILL.md").is_file())

    def test_core_and_component_archives_preserve_all_source_payload(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        metadata = json.loads((skill / "components.json").read_text(encoding="utf-8"))
        self.assertEqual(set(metadata["components"]), {"ppt", "docx", "mcp", "ui", "support"})
        restored = self.file_snapshot(skill)
        for component in metadata["components"].values():
            archive = self.base / "components" / component["archive"]
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), component["sha256"])
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()), {f["path"] for f in component["files"]})
                for row in component["files"]:
                    data = z.read(row["path"])
                    self.assertNotIn(row["path"], restored)
                    self.assertEqual(len(data), row["size"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
                    restored[row["path"]] = data
        full_parent = self.base / "full"
        full_parent.mkdir()
        result = self.build(output=full_parent / "icode")
        self.assertEqual(result.returncode, 0, result.stderr)
        full = self.file_snapshot(full_parent / "icode/skills/icode")
        self.assertEqual(restored.pop("WORKFLOW.md"), full.pop("SKILL.md"))
        restored.pop("SKILL.md")
        restored.pop("components.json")
        self.assertEqual(restored, full)

    def test_core_budget_failure_leaves_both_outputs_absent(self):
        for i in range(201):
            self.put(f"tools/extra_{i}.py", "# extra core resource\n")
        self.git("add", ".")
        result = self.build_core()
        self.assert_rejected(result)
        self.assertIn("budget", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.base / "components").exists())

    def test_component_install_dry_run_exact_restore_and_idempotency(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        before = self.file_snapshot(skill)
        result = self.component_install(dry_run=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.file_snapshot(skill), before)
        result = self.component_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = Path(json.loads(result.stdout)["runtime_root"])
        self.assertFalse(runtime.is_relative_to(self.output))
        self.assertEqual((runtime / "tools/docx/build_docx.py").read_bytes(),
                         (self.source / "tools/docx/build_docx.py").read_bytes())
        self.assertEqual(self.file_snapshot(skill), before)
        installed = self.file_snapshot(runtime)
        result = self.component_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.file_snapshot(runtime), installed)
        self.assertEqual(self.file_snapshot(skill), before)

    def test_component_conflict_is_rejected_before_any_new_file(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        conflict = skill / "tools/docx/build_docx.py"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("user content", encoding="utf-8")
        before = self.file_snapshot(skill)
        result = self.component_install()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.file_snapshot(skill), before)

    def test_component_cache_cannot_expand_inside_the_discovery_package(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        spec = json.loads((skill / "components.json").read_text(encoding="utf-8"))["components"]["docx"]
        archive = self.base / "components" / spec["archive"]
        module = self.component_module()
        for cache in (skill / "local-cache", self.output / "local-cache"):
            with self.subTest(cache=str(cache)):
                before = self.file_snapshot(self.output)
                with self.assertRaises(module.InstallConflict):
                    module.install_component(skill, "docx", archive=archive, cache=cache)
                self.assertFalse(cache.exists())
                self.assertEqual(self.file_snapshot(self.output), before)

    def test_corrupt_component_and_version_mismatch_are_rejected_without_writes(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        metadata = json.loads((skill / "components.json").read_text(encoding="utf-8"))
        archive = self.base / "components" / metadata["components"]["docx"]["archive"]
        old = archive.read_bytes()
        archive.write_bytes(old + b"corrupt")
        before = self.file_snapshot(skill)
        self.assertEqual(self.component_install().returncode, 1)
        self.assertEqual(self.file_snapshot(skill), before)
        archive.write_bytes(old)
        entry = skill / "SKILL.md"
        entry.write_text(entry.read_text(encoding="utf-8").replace("v2.32.0", "v9.0.0"), encoding="utf-8")
        before = self.file_snapshot(skill)
        self.assertEqual(self.component_install().returncode, 1)
        self.assertEqual(self.file_snapshot(skill), before)

    def test_automatic_download_is_pinned_and_cache_is_reused(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        second = self.base / "second-skill"
        shutil.copytree(skill, second)
        metadata = json.loads((skill / "components.json").read_text(encoding="utf-8"))
        spec = metadata["components"]["docx"]
        data = (self.base / "components" / spec["archive"]).read_bytes()
        calls = []
        class Reply(io.BytesIO):
            def geturl(self):
                return spec["url"]
        class Opener:
            def open(self, request, timeout):
                calls.append(request.full_url)
                return Reply(data)
        module = self.component_module()
        cache = self.base / "cache"
        before = self.file_snapshot(skill)
        with patch.object(module, "build_opener", return_value=Opener()):
            result = module.install_component(skill, "docx", cache=cache)
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [spec["url"]])
        with patch.object(module, "build_opener", side_effect=AssertionError("cache must avoid network")):
            second_result = module.install_component(second, "docx", cache=cache)
            self.assertEqual(module.obtain_component(spec, cache / "archives").read_bytes(), data)
        runtime = Path(second_result["runtime_root"])
        self.assertEqual((runtime / "tools/docx/build_docx.py").read_bytes(),
                         (self.source / "tools/docx/build_docx.py").read_bytes())
        self.assertEqual(self.file_snapshot(skill), before)

    def test_automatic_dry_run_never_downloads_or_creates_cache(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        module = self.component_module()
        skill = self.output / "skills/icode"
        before = self.file_snapshot(skill)
        cache = self.base / "cache"
        with patch.object(module, "build_opener", side_effect=AssertionError("dry-run must stay offline")):
            report = module.install_component(skill, "docx", dry_run=True, cache=cache)
        self.assertTrue(report["download_required"])
        self.assertFalse(cache.exists())
        self.assertEqual(self.file_snapshot(skill), before)

    def test_download_hash_failure_does_not_publish_a_cache_archive(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        spec = json.loads((skill / "components.json").read_text(encoding="utf-8"))["components"]["docx"]
        class Reply(io.BytesIO):
            def geturl(self):
                return spec["url"]
        class Opener:
            def open(self, request, timeout):
                return Reply(b"x" * spec["size"])
        module = self.component_module()
        cache = self.base / "download-cache"
        with patch.object(module, "build_opener", return_value=Opener()):
            with self.assertRaises(module.InstallConflict):
                module.obtain_component(spec, cache)
        self.assertFalse(list(cache.glob("*.zip")))
        self.assertFalse(list(cache.glob("component-*")))
        with patch.object(module, "build_opener", return_value=Opener()):
            with self.assertRaises(module.InstallConflict):
                module.install_component(skill, "docx", cache=cache)
        self.assertFalse((cache / "runtimes").exists())

    def test_component_download_urls_and_redirects_reject_untrusted_hosts(self):
        module = self.component_module()
        for url in (None, "http://github.com/owner/repo", "https://private.invalid/a.zip",
                    "https://user:pass@github.com/owner/repo", "file:///tmp/a.zip"):
            with self.subTest(url=url):
                with self.assertRaises(module.InstallConflict):
                    module.check_component_url(url)
        with self.assertRaises(module.InstallConflict):
            module.ComponentRedirects().redirect_request(
                module.Request("https://github.com/owner/repo"), None, 302, "redirect", {}, "http://private.invalid/a.zip")

    def test_unsafe_member_and_malformed_mode_are_rejected_without_writes(self):
        result = self.build_core()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.output / "skills/icode"
        scenarios = [
            ([("../escape.txt", b"bad", 0o100644)], None),
            ([("tools/docx/link.py", b"outside", 0o120777)], None),
            ([("tools/docx/a.py", b"valid", 0o100644)], lambda rows: rows[0].update(mode=[])),
            ([("tools/docx", b"file", 0o100644), ("tools/docx/a.py", b"child", 0o100644)], None),
            ([("tools/docx/A.py", b"upper", 0o100644), ("tools/docx/a.py", b"lower", 0o100644)], None),
        ]
        for members, change in scenarios:
            with self.subTest(members=[row[0] for row in members]):
                self.rewrite_component(members, change)
                before = self.file_snapshot(skill)
                result = self.component_install()
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.file_snapshot(skill), before)


if __name__ == "__main__":
    unittest.main()
