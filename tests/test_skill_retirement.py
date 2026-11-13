"""Shared Skill retirement contracts; all installs use private temporary roots."""

import contextlib
import io
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import install_skill_pack as installer

PAYLOAD_RECEIPT = ".icode-skill-payload.json"


class SkillRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.packs = self.base / "packs"
        self.packs.mkdir()
        self.root = self.base / "host" / "skills"
        self.manifest = self.packs / "manifest.json"

    def manifest_for(self, *names):
        for name in names:
            source = self.packs / name
            source.mkdir(exist_ok=True)
            (source / "SKILL.md.template").write_text(
                f"---\nname: {name}\ndescription: Use when testing retirement.\n---\n{name}\n",
                encoding="utf-8")
        self.manifest.write_text(json.dumps({"schema_version": 1, "skills": [
            {"name": name, "path": name, "entrypoint": "SKILL.md.template"}
            for name in names]}), encoding="utf-8")
        return self.manifest

    def install(self, *names, dry_run=False, keep_extra=False):
        return installer.install(self.manifest_for(*names), [str(self.root)], dry_run, keep_extra)

    def snapshot(self):
        return {str(path.relative_to(self.base)): path.read_bytes()
                for path in self.base.rglob("*") if path.is_file()}

    def install_deferred(self, manifest):
        self.assertIn("defer_retirement", inspect.signature(installer.install).parameters)
        return installer.install(manifest, [str(self.root)], False, False,
                                 defer_retirement=True)

    def test_reinstall_archives_retired_skill_and_is_idempotent(self):
        self.install("fixture-a", "fixture-b")
        (self.root / "fixture-a" / "config.json").write_text("private local runtime")
        actions = self.install("fixture-b", "fixture-c")
        retirements = [a for a in actions if a["action"] == "retire"]
        self.assertEqual(len(retirements), 1)
        retirement = retirements[0]
        archive = retirement["archive"]
        self.assertFalse((self.root / "fixture-a").exists())
        self.assertFalse(archive.is_relative_to(self.root))
        self.assertEqual((archive / "config.json").read_text(), "private local runtime")
        before = self.snapshot()
        self.assertTrue(all(a["action"] == "current" for a in self.install("fixture-b", "fixture-c")))
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_has_no_writes_for_create_or_retire(self):
        self.install("fixture-a")
        manifest = self.manifest_for("fixture-b")
        before = self.snapshot()
        actions = installer.install(manifest, [str(self.root)], True, False)
        self.assertEqual({a["action"] for a in actions}, {"create", "retire"})
        self.assertEqual(self.snapshot(), before)
        missing = self.base / "missing" / "skills"
        installer.install(manifest, [str(missing)], True, False)
        self.assertFalse(missing.parent.exists())

    def test_modified_and_legacy_retired_skills_are_conflicts(self):
        self.install("fixture-a", "fixture-b")
        (self.root / "fixture-a" / "SKILL.md").write_text("user modification")
        receipt = self.root / "fixture-b" / PAYLOAD_RECEIPT
        if receipt.exists():
            receipt.unlink()
        actions = self.install()
        self.assertEqual([a["action"] for a in actions], ["retained_conflict"] * 2)
        self.assertTrue((self.root / "fixture-a").is_dir())
        self.assertTrue((self.root / "fixture-b").is_dir())

    def test_foreign_markerless_and_main_icode_are_preserved(self):
        self.install("fixture-a")
        for name in ("custom", "foreign", "icode"):
            folder = self.root / name
            folder.mkdir()
            (folder / "SKILL.md").write_text("local")
        (self.root / "foreign" / installer.OWNER_MARKER).write_text(json.dumps(
            {"schema_version": 1, "owner": "other", "skill": "foreign"}))
        (self.root / "icode" / ".icode-install-owner.json").write_text("main")
        self.install()
        self.assertTrue(all((self.root / n / "SKILL.md").exists()
                            for n in ("custom", "foreign", "icode")))

    def test_keep_extra_does_not_retire(self):
        self.install("fixture-a")
        actions = self.install("fixture-b", keep_extra=True)
        self.assertIn("retained", [a["action"] for a in actions])
        self.assertTrue((self.root / "fixture-a" / "SKILL.md").exists())

    def test_deferred_retirement_updates_active_payload_then_finalizes(self):
        self.install("fixture-a", "fixture-b")
        (self.root / "fixture-b" / "stale.txt").write_text("old payload")
        manifest = self.manifest_for("fixture-b", "fixture-c")
        (self.packs / "fixture-b" / "reference.md").write_text("new payload")
        actions = self.install_deferred(manifest)
        retained = [a for a in actions if a["action"] == "retained"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["reason"], "defer-retirement")
        self.assertTrue((self.root / "fixture-a" / "SKILL.md").is_file())
        self.assertTrue((self.root / "fixture-c" / "SKILL.md").is_file())
        self.assertEqual((self.root / "fixture-b" / "reference.md").read_text(), "new payload")
        self.assertFalse((self.root / "fixture-b" / "stale.txt").exists())
        self.assertFalse((self.root.parent / ".icode-skill-archive").exists())
        actions = installer.install(manifest, [str(self.root)], False, False)
        self.assertEqual([a["action"] for a in actions], ["current", "current", "retire"])
        self.assertFalse((self.root / "fixture-a").exists())

    def test_deferred_retirement_preserves_legacy_and_user_edits_until_final_check(self):
        self.install("fixture-a", "fixture-b")
        (self.root / "fixture-a" / PAYLOAD_RECEIPT).unlink()
        (self.root / "fixture-b" / "SKILL.md").write_text("user modified")
        manifest = self.manifest_for("fixture-c")
        actions = self.install_deferred(manifest)
        self.assertEqual([a["action"] for a in actions], ["create", "retained", "retained"])
        self.assertTrue((self.root / "fixture-a" / "SKILL.md").is_file())
        self.assertEqual((self.root / "fixture-b" / "SKILL.md").read_text(), "user modified")
        actions = installer.install(manifest, [str(self.root)], False, False)
        self.assertEqual([a["action"] for a in actions],
                         ["current", "retained_conflict", "retained_conflict"])

    def test_deferred_cli_reports_incomplete_convergence_without_failure(self):
        self.install("fixture-a")
        self.manifest_for("fixture-b")
        output = io.StringIO()
        with patch.object(sys, "argv", ["installer", "--manifest", str(self.manifest),
                                        "--target-root", str(self.root), "--defer-retirement"]), \
                contextlib.redirect_stdout(output):
            try:
                result = installer.main()
            except SystemExit as error:
                self.fail(f"deferred shared CLI should succeed, exited {error.code}")
            self.assertEqual(result, 0)
        report = json.loads(output.getvalue().splitlines()[-1])
        self.assertTrue(report["ok"])
        self.assertFalse(report["converged"])
        self.assertIn("defer-retirement", output.getvalue())

    def test_component_mode_rejects_deferred_retirement_option(self):
        errors = io.StringIO()
        with patch.object(sys, "argv", ["installer", "--component-root", str(self.root),
                                        "--component", "example", "--defer-retirement"]), \
                contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as error:
                installer.main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn("shared-skill options cannot be mixed", errors.getvalue())

    def test_active_legacy_marker_refreshes_without_changing_marker_contract(self):
        self.install("fixture-a")
        folder = self.root / "fixture-a"
        receipt = folder / PAYLOAD_RECEIPT
        if receipt.exists():
            receipt.unlink()
        marker = (folder / installer.OWNER_MARKER).read_bytes()
        self.install("fixture-a")
        self.assertEqual((folder / installer.OWNER_MARKER).read_bytes(), marker)
        self.assertTrue(receipt.is_file())
        self.assertEqual(self.install()[0]["action"], "retire")

    def test_replacement_stage_failure_keeps_old_skills(self):
        self.install("fixture-a")
        manifest = self.manifest_for("fixture-b")
        before = self.snapshot()
        with patch.object(installer, "prepare_stage", side_effect=OSError("stage failure")):
            with self.assertRaises(OSError):
                installer.install(manifest, [str(self.root)], False, False)
        self.assertEqual(self.snapshot(), before)

    def test_archive_failure_rolls_back_new_updates_and_retirements(self):
        self.install("fixture-a", "fixture-b", "fixture-d")
        manifest = self.manifest_for("fixture-b", "fixture-c")
        (self.packs / "fixture-b" / "reference.md").write_text("updated")
        before = self.snapshot()
        real_rename = Path.rename

        def fail_second_retirement(path, target):
            if path == self.root / "fixture-d":
                raise OSError("archive failure")
            return real_rename(path, target)

        with patch.object(Path, "rename", fail_second_retirement):
            with self.assertRaises(OSError):
                installer.install(manifest, [str(self.root)], False, False)
        self.assertEqual(self.snapshot(), before)
        # A failed transaction leaves the original receipts valid for retry.
        actions = installer.install(manifest, [str(self.root)], False, False)
        self.assertEqual(sum(a["action"] == "retire" for a in actions), 2)

    def test_corrupt_receipts_and_markers_are_preserved_with_conflicts(self):
        self.install("fixture-a", "fixture-b", "fixture-c")
        (self.root / "fixture-a" / PAYLOAD_RECEIPT).write_text("{")
        (self.root / "fixture-b" / installer.OWNER_MARKER).write_text("[]")
        receipt = self.root / "fixture-c" / PAYLOAD_RECEIPT
        data = json.loads(receipt.read_text())
        data["skill"] = "../outside"
        receipt.write_text(json.dumps(data))
        self.manifest_for()
        before = self.snapshot()
        actions = installer.install(self.manifest, [str(self.root)], False, False)
        self.assertEqual([a["action"] for a in actions], ["retained_conflict"] * 3)
        self.assertEqual(self.snapshot(), before)

    def test_nested_roots_are_rejected_before_any_write(self):
        manifest = self.manifest_for("fixture-a")
        with self.assertRaises(installer.InstallConflict):
            installer.install(manifest, [str(self.root), str(self.root / "nested")], False, False)
        self.assertFalse(self.root.exists())

    def test_archive_symlink_is_a_conflict_without_writes(self):
        self.install("fixture-a")
        outside = self.base / "outside"
        outside.mkdir()
        try:
            (self.root.parent / ".icode-skill-archive").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.manifest_for()
        before = self.snapshot()
        actions = installer.install(self.manifest, [str(self.root)], False, False)
        self.assertEqual(actions[0]["action"], "retained_conflict")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_linked_root_is_rejected_even_when_link_points_to_directory(self):
        self.root.parent.mkdir(parents=True)
        outside = self.base / "outside"
        outside.mkdir()
        try:
            self.root.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(installer.InstallConflict):
            self.install("fixture-a")
        self.assertEqual(list(outside.iterdir()), [])

    def test_conflicts_are_nonzero_in_cli(self):
        self.install("fixture-a")
        (self.root / "fixture-a" / "SKILL.md").write_text("modified")
        self.manifest_for()
        output = io.StringIO()
        with patch.object(sys, "argv", ["installer", "--manifest", str(self.manifest),
                                        "--target-root", str(self.root)]), contextlib.redirect_stdout(output):
            self.assertNotEqual(installer.main(), 0)
        self.assertIn("retained_conflict", output.getvalue())

    def test_symlink_payload_is_never_retired(self):
        self.install("fixture-a")
        outside = self.base / "outside"
        outside.write_text("do not touch")
        try:
            (self.root / "fixture-a" / "linked").symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        actions = self.install()
        self.assertEqual(actions[0]["action"], "retained_conflict")
        self.assertEqual(outside.read_text(), "do not touch")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX named pipes")
    def test_special_files_are_conflicts_before_hashing_payload(self):
        self.install("fixture-a")
        os.mkfifo(self.root / "fixture-a" / "pipe")
        with patch.object(installer, "installed_digest", side_effect=AssertionError("must not read pipes")):
            actions = self.install()
        self.assertEqual(actions[0]["action"], "retained_conflict")


if __name__ == "__main__":
    unittest.main()
