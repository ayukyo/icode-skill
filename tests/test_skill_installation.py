"""Offline installation checks against a real Git fixture and distribution selector."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_skill_distribution as distribution_tests


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools/check_skill_installation.py"
sys.path.insert(0, str(ROOT / "tools"))
import build_skill_distribution as distribution


class InstallationTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing realistic fixture; do not invent a smaller selector.
        self.fixture = distribution_tests.DistributionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source = self.fixture.source
        self.base = self.fixture.base
        result = self.fixture.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.installed = self.fixture.output / "skills/icode"
        self.expected = {
            p.as_posix() for p in distribution.source_files(self.source)
            if p.as_posix() not in distribution.METADATA
        }

    def check(self, *, source=None, installed=None, code=0):
        result = subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--source",
             str(self.source if source is None else source), "--installed",
             str(self.installed if installed is None else installed)],
            capture_output=True, text=True, timeout=10, cwd=self.source,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip().startswith("{"), result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(report["ok"], code == 0)
        self.assertEqual(report["scope"], "root_workflow_resources")
        return report

    def test_complete_real_distribution_is_read_only_and_passes(self):
        def snapshot():
            return {
                str(p): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                for root in (self.source, self.fixture.output)
                for p in root.rglob("*") if p.is_file()
            }
        before = snapshot()
        report = self.check()
        self.assertEqual(report["expected_files"], len(self.expected))
        self.assertEqual(report["checked_files"], len(self.expected))
        for key in ("missing", "changed", "unsafe", "errors"):
            self.assertEqual(report[key], [])
        self.assertEqual(snapshot(), before)

    def test_truncated_skill_fails_even_with_same_total_file_count(self):
        for name in self.expected - {"SKILL.md"}:
            (self.installed / name).unlink()
        for index in range(len(self.expected) - 1):
            (self.installed / f"filler-{index}.txt").write_text("filler")
        report = self.check(code=1)
        self.assertEqual(set(report["missing"]), self.expected - {"SKILL.md"})

    def test_missing_workflow_directories_are_reported_by_relative_path(self):
        for directory in ("steps", "references", "tools"):
            shutil.rmtree(self.installed / directory)
        report = self.check(code=1)
        wanted = {p for p in self.expected if p.split("/")[0] in {"steps", "references", "tools"}}
        self.assertEqual(set(report["missing"]), wanted)

    def test_same_size_content_change_has_both_hashes(self):
        path = "steps/01_plan.md"
        (self.installed / path).write_bytes(b"xxxx\n")
        report = self.check(code=1)
        self.assertEqual(report["changed"], [{
            "path": path,
            "source_sha256": hashlib.sha256((self.source / path).read_bytes()).hexdigest(),
            "installed_sha256": hashlib.sha256(b"xxxx\n").hexdigest(),
        }])

    def test_extra_regular_files_do_not_change_expected_resources(self):
        (self.installed / "local-note.txt").write_text("not a source resource")
        self.check()

    def test_extra_installed_symlinks_are_rejected_without_following_them(self):
        extra = self.installed / "extra"
        extra.mkdir()
        (extra / "outside").symlink_to(self.base)
        (extra / "dangling").symlink_to(self.base / "absent")
        report = self.check(code=1)
        self.assertEqual({row["path"] for row in report["unsafe"]},
                         {"extra/outside", "extra/dangling"})

    def test_untracked_and_excluded_private_payloads_are_never_read(self):
        # A FIFO would hang if content were read, unlike a harmless sentinel file.
        for name in ("tools/untracked.py", "tools/private.txt", "tests/private.txt"):
            path = self.source / name
            if path.exists():
                path.unlink()
            os.mkfifo(path)
        (self.source / "tools/untracked-link").symlink_to(self.base / "absent")
        self.check()

    def test_selected_private_source_path_is_rejected_before_reading(self):
        self.fixture.put("tools/private.txt", "DO_NOT_READ")
        self.fixture.git("add", "tools/private.txt")
        path = self.source / "tools/private.txt"
        path.unlink()
        os.mkfifo(path)
        report = self.check(code=2)
        self.assertTrue(report["errors"])
        self.assertNotIn("DO_NOT_READ", json.dumps(report))

    def test_invalid_source_roots(self):
        for source in (self.base / "absent", self.base, self.source / "tools",
                       self.source / "SKILL.md"):
            with self.subTest(source=source):
                self.assertTrue(self.check(source=source, code=2)["errors"])

    def test_unknown_home_expansion_is_reported_as_json(self):
        absent_user = "~icode-installation-test-no-such-user-9f381d"
        for option, code in (("source", 2), ("installed", 1)):
            with self.subTest(option=option):
                self.assertTrue(self.check(**{option: absent_user}, code=code)["errors"])

    def test_empty_source_argument_does_not_silently_select_current_checkout(self):
        self.assertTrue(self.check(source="", code=2)["errors"])

    def test_empty_installed_argument_is_an_argument_error(self):
        self.assertTrue(self.check(installed="", code=2)["errors"])

    def test_missing_required_source_is_invalid(self):
        (self.source / "SKILL.md").unlink()
        self.assertTrue(self.check(code=2)["errors"])

    def test_empty_source_resource_directory_is_invalid(self):
        self.fixture.git("rm", "-f", "steps/01_plan.md")
        self.assertTrue(self.check(code=2)["errors"])

    def test_missing_or_non_directory_installation_fails(self):
        for installed in (self.base / "absent", self.installed / "SKILL.md"):
            with self.subTest(installed=installed):
                self.assertTrue(self.check(installed=installed, code=1)["errors"])

    def test_source_symlink_file_and_directory_are_invalid(self):
        for name in ("steps/01_plan.md", "references"):
            with self.subTest(name=name):
                path = self.source / name
                saved = path.with_name(path.name + "-saved")
                path.rename(saved)
                path.symlink_to(saved)
                self.assertTrue(self.check(code=2)["errors"])
                path.unlink()
                saved.rename(path)

    def test_source_root_and_ancestor_symlinks_are_invalid(self):
        root_link = self.base / "source-link"
        root_link.symlink_to(self.source)
        ancestor = self.base / "ancestor"
        ancestor.symlink_to(self.base)
        for source in (root_link, ancestor / "source"):
            with self.subTest(source=source):
                self.assertTrue(self.check(source=source, code=2)["errors"])

    def test_installed_root_and_ancestor_symlinks_are_unsafe(self):
        root_link = self.base / "installed-link"
        root_link.symlink_to(self.installed)
        ancestor = self.base / "ancestor"
        ancestor.symlink_to(self.base)
        for installed in (root_link, ancestor / "icode/skills/icode"):
            with self.subTest(installed=installed):
                self.assertTrue(self.check(installed=installed, code=1)["unsafe"])

    def test_installed_symlink_file_directory_and_dangling_link_are_unsafe(self):
        for name, target in (("steps/01_plan.md", self.source / "steps/01_plan.md"),
                             ("references", self.source / "references"),
                             ("tools/icode_control.py", self.base / "absent")):
            path = self.installed / name
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            path.symlink_to(target)
        unsafe = self.check(code=1)["unsafe"]
        self.assertEqual({item["path"] for item in unsafe},
                         {"steps/01_plan.md", "references/skill_routing.md", "tools/icode_control.py"})

    def test_parent_traversal_is_rejected_without_normalizing_it_away(self):
        self.assertTrue(self.check(source=self.source / ".." / "source", code=2)["errors"])
        self.assertTrue(self.check(installed=self.installed / ".." / "icode", code=1)["unsafe"])

    def test_non_regular_installed_payload_does_not_block(self):
        path = self.installed / "steps/01_plan.md"
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(self.check(code=1)["unsafe"][0]["path"], "steps/01_plan.md")

    def test_required_file_replaced_by_directory_is_unsafe(self):
        path = self.installed / "steps/01_plan.md"
        path.unlink()
        path.mkdir()
        self.assertEqual(self.check(code=1)["unsafe"][0]["path"], "steps/01_plan.md")

    def test_argument_errors_are_json_and_exit_two(self):
        result = subprocess.run([sys.executable, "-B", str(CHECKER)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stdout.strip().startswith("{"), result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
