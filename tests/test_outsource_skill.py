"""Exercise source-pack publication and the installed outsource routing entry.

All writes target TemporaryDirectory; no host/global skill installation is used.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skill-packs/manifest.json"
ROUTES = ROOT / "mcp/workflow-gate/skill-routes.json"


class OutsourceSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="icode-outsource-skill-")
        # Windows CI may supply an 8.3 TEMP alias (RUNNER~1); compare the
        # canonical path identity returned by the installed resolver.
        cls.base = Path(cls.temporary.name).resolve(strict=True)
        cls.target = cls.base / "installed-skills"
        cls.run_cli("tools/install_skill_pack.py", "--manifest", MANIFEST,
                    "--target-root", cls.target, "--dry-run")
        if cls.target.exists():
            raise AssertionError("dry run wrote the target skill root")
        cls.run_cli("tools/install_skill_pack.py", "--manifest", MANIFEST,
                    "--target-root", cls.target)
        cls.entry = cls.target / "icode-outsource/SKILL.md"
        cls.resolver = re.search(r"```python\n(.*?)\n```", cls.entry.read_text(encoding="utf-8"), re.S).group(1)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def run_cli(cls, script, *arguments):
        result = subprocess.run([sys.executable, "-B", str(ROOT / script),
                                 *map(str, arguments)], cwd=cls.base,
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout

    def make_runtime(self, name):
        runtime = self.base / name
        runtime.mkdir(parents=True, exist_ok=True)
        for relative in ("SKILL.md", "steps/outsource.md", "references/outsource_contract.md",
                         "tools/icode_outsource.py", "tools/outsource/common.py",
                         "tools/outsource/store.py", "tools/outsource/workspace.py", "tools/outsource/cli.py"):
            target = runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = ROOT / relative
            # Source files must actually exist; this catches an incomplete delivery.
            target.write_bytes(source.read_bytes())
        return runtime

    def resolve(self, declared=None, environment=None):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), contextlib.redirect_stdout(output):
            os.environ.pop("ICODE_SKILL_ROOT", None)
            if environment:
                os.environ["ICODE_SKILL_ROOT"] = str(environment)
            exec(compile(self.resolver, str(self.entry), "exec"),
                 {"skill_entry": str(self.entry), "host_icode_root": declared})
        return json.loads(output.getvalue())

    def test_rendered_pack_is_publishable_and_all_installed_hashes_match(self):
        self.run_cli("tools/validate_skill_pack.py", "--manifest", MANIFEST,
                     "--routes", ROUTES, "--target-root", self.target)
        self.assertEqual(self.entry.read_bytes(),
                         (ROOT / "skill-packs/icode-outsource/SKILL.md.template").read_bytes())
        self.assertFalse((ROOT / "skill-packs/icode-outsource/SKILL.md").exists())

    def test_installed_entry_routes_to_actual_sibling_runtime(self):
        runtime = self.make_runtime("installed-skills/icode")
        result = self.resolve()
        self.assertEqual(Path(result["skill_root"]), runtime)
        self.assertEqual(Path(result["step"]), runtime / "steps/outsource.md")
        help_result = subprocess.run([sys.executable, "-B", result["cli"], "--help"],
                                     cwd=self.base, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        # Follow the step's actual relative link in its installed location.
        step = Path(result["step"])
        links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", step.read_text(encoding="utf-8"))
        self.assertTrue(links)
        for link in links:
            self.assertTrue((step.parent / link).resolve().is_file(), link)

    def test_resolved_installed_runtime_can_publish_and_resolve_handoff(self):
        runtime = self.make_runtime("installed-working-runtime")
        resolved = self.resolve(str(runtime))
        source = self.base / "installed-source"
        source.mkdir()
        (source / "file.c").write_text("original", encoding="utf-8")
        contract = self.base / "installed-contract.json"
        contract.write_text(json.dumps({
            "schema_version": 1, "title": "安装闭环", "goal": "只验证隔离发布",
            "requester": "installed-owner", "constraints": [],
            "projects": [{"root": str(source), "read_paths": ["file.c"], "write_paths": ["file.c"]}],
            "acceptance": [{"id": "A", "check": "真实文件"}],
        }, ensure_ascii=False), encoding="utf-8")
        board = self.base / "installed-board"

        def run(*args):
            result = subprocess.run([sys.executable, "-B", resolved["cli"], *map(str, args)],
                                    cwd=self.base, capture_output=True, text=True,
                                    encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        run("--board", board, "init")
        published = run("--board", board, "publish", "--contract", contract,
                        "--session", "installed-session", "--request-id", "installed-publish")
        shown = run("show", published["request_path"])
        self.assertEqual(shown["ticket_id"], published["ticket_id"])
        self.assertEqual((source / "file.c").read_text(encoding="utf-8"), "original")

    def test_host_declared_actual_root_wins_environment(self):
        runtime = self.make_runtime("host-declared-runtime")
        result = self.resolve(str(runtime), self.base / "stale-global")
        self.assertEqual(Path(result["skill_root"]), runtime)

    def test_explicit_missing_dependency_does_not_fall_back(self):
        self.make_runtime("installed-skills/icode")
        missing = self.base / "missing-runtime"
        with self.assertRaisesRegex(RuntimeError, "dependency unavailable"):
            self.resolve(str(missing))
        self.assertFalse(missing.exists())

    def test_environment_root_resolves_and_core_missing_capability_stops(self):
        runtime = self.make_runtime("environment-runtime")
        self.assertEqual(Path(self.resolve(environment=runtime)["skill_root"]), runtime)
        (runtime / "tools/outsource/workspace.py").unlink()
        before = sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*"))
        with self.assertRaisesRegex(RuntimeError, "workspace.py"):
            self.resolve(environment=runtime)
        self.assertEqual(before, sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*")))

    def test_machine_route_dispatches_only_the_independent_step(self):
        routes = json.loads(ROUTES.read_text(encoding="utf-8"))["routes"]
        outsource = [row for row in routes if row["skill"] == "icode-outsource"]
        self.assertEqual(len(outsource), 1)
        route = outsource[0]
        self.assertEqual(route["steps"], ["outsource"])
        cases = [("outsource", "exact_request_md_handoff", True),
                 ("outsource", "requester_parent_delivery_recovery", True),
                 ("plan", "exact_request_md_handoff", False),
                 ("code", "same_machine_ai_outsource_publish", False),
                 ("outsource", "ordinary_code_change", False)]
        for step, trigger, expected in cases:
            selected = [row["skill"] for row in routes
                        if step in row["steps"] and trigger in row["triggers"]]
            self.assertEqual("icode-outsource" in selected, expected)
        # Root command and natural-language tables must lead to a real step.
        for source in (ROOT / "SKILL.md", ROOT / "references/natural_language_entry.md"):
            links = re.findall(r"\]\(([^)#]*steps/outsource\.md)\)", source.read_text(encoding="utf-8"))
            self.assertTrue(links, source)
            for link in links:
                self.assertEqual((source.parent / link).resolve(), ROOT / "steps/outsource.md")

    def test_documented_commands_are_accepted_by_actual_cli_parser(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            from outsource.cli import parser
            content = (ROOT / "steps/outsource.md").read_text(encoding="utf-8")
            commands = [line for block in re.findall(r"```bash\n(.*?)\n```", content, re.S)
                        for line in block.splitlines() if line.startswith("python3 ")]
            self.assertGreaterEqual(len(commands), 10)
            for command in commands:
                arguments = shlex.split(command)[2:]
                arguments = ["1" if value == "<returned_attempt>" else value for value in arguments]
                parsed = parser().parse_args(arguments)
                self.assertIsNotNone(parsed.command)
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
