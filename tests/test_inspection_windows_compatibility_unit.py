"""Real byte boundaries and native CLI wire; anonymous temporary repositories."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location("compat_" + name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).strip()


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icode-compat-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Fixture")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "config", "core.autocrlf", "false")
        self.api = module("inspection_worklist")

    def put(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def report(self, path="nested/src/a.c", **kwargs):
        return self.api.build_worklist(self.root, [path], step="code", ticket_id="t", attempt=1, **kwargs)

    def test_default_nested_scopes_and_legacy_none(self):
        self.put("nested/src/a.c", b"int a;\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")
        report = self.report()
        self.assertEqual(report["scopes"], ["nested/src"])
        self.assertEqual(report, self.report(git_provider=None))
        for raw in ("nested\\src/a.c", "../a.c", str(self.root / "nested/src/a.c")):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.report(raw)

    def test_real_crlf_excerpt_and_deleted_blob_keep_exact_bytes(self):
        raw = b"int a;\r\n"
        source = self.put("src/a.c", raw)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")
        base = git(self.root, "rev-parse", "HEAD").decode("ascii")
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.root), "show", base + ":src/a.c"]), raw)
        report = self.report("src/a.c")
        entry = report["units"][0]["files"][0]
        self.assertEqual(entry["sha256"], hashlib.sha256(raw).hexdigest())
        finding = {"finding_id": "F", "verification_status": "confirmed", "locations": [{
            "path": "src/a.c", "start_line": 1, "end_line": 1,
            "source_sha256": entry["sha256"], "excerpt": raw.decode("utf-8")}]}
        self.assertEqual(self.api.validate_findings([finding], report, self.root), [])
        finding["locations"][0]["excerpt"] = "int a;\n"
        self.assertTrue(self.api.validate_findings([finding], report, self.root))
        source.unlink()
        git(self.root, "add", "-u")
        git(self.root, "commit", "-qm", "delete fixture")
        deleted = self.report("src/a.c", baselines={".": base})["units"][0]["files"][0]
        self.assertEqual(deleted["kind"], "deleted")
        self.assertEqual(deleted["sha256"], hashlib.sha256(raw).hexdigest())

    def test_cli_entry_utf8_help_and_error_with_cp1252_stdout(self):
        for name in ("icode_control", "icode_crosscheck"):
            tool = str(ROOT / "tools" / (name + ".py"))
            for args, expected in ((["--help"], 0), (["resolve-ticket", "--dir", str(self.root / "missing")], 1)
                                   if name == "icode_control" else (["validate", "--dir", str(self.root / "missing")], 1)):
                with self.subTest(name=name, args=args):
                    code = ("import sys,runpy;sys.stdout.reconfigure(encoding='cp1252');sys.argv="
                            + repr([tool, *args]) + ";runpy.run_path(" + repr(tool) + ",run_name='__main__')")
                    process = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=20)
                    self.assertEqual(process.returncode, expected, process.stderr.decode("utf-8", errors="replace"))
                    text = process.stdout.decode("utf-8", errors="strict")
                    self.assertTrue(any(ord(character) > 127 for character in text))
                    if expected:
                        self.assertIs(json.loads(text)["ok"], False)

    def test_import_and_imported_main_keep_text_capture(self):
        for name in ("icode_control", "icode_crosscheck"):
            with self.subTest(name=name):
                original = sys.stdout
                api = module(name)
                self.assertIs(sys.stdout, original)
                text = io.StringIO()
                with patch.object(sys, "argv", [name, "--help"]), contextlib.redirect_stdout(text):
                    with self.assertRaises(SystemExit) as caught:
                        api.main()
                self.assertEqual(caught.exception.code, 0)
                self.assertTrue(any(ord(character) > 127 for character in text.getvalue()))
                self.assertIs(sys.stdout, original)

    def test_atomic_write_and_failed_replace_preserve_destination(self):
        api = module("icode_crosscheck")
        target = self.root / "atomic.json"
        api.atomic_write(target, b"old\n")
        self.assertEqual(target.read_bytes(), b"old\n")
        with patch.object(api.os, "replace", side_effect=PermissionError("fixture replace denied")):
            with self.assertRaises(PermissionError):
                api.atomic_write(target, b"new\n")
        self.assertEqual(target.read_bytes(), b"old\n")
        self.assertEqual(list(self.root.glob("atomic.json.*.tmp")), [])

    def test_lock_entry_failure_closes_stream(self):
        api = module("icode_crosscheck")
        primitive_name = "_fcntl_mod" if hasattr(api, "_fcntl_mod") else "fcntl"
        stream = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stream.close)
        def reject(*args):
            raise PermissionError("fixture lock denied")
        primitive = SimpleNamespace(LOCK_EX=1, flock=reject)
        with patch.object(Path, "open", return_value=stream), patch.object(api, primitive_name, primitive):
            with self.assertRaises(PermissionError):
                with api.DirectoryLock(self.root):
                    self.fail("entry failure must never enter the critical section")
        self.assertTrue(stream.closed)

    def test_real_lock_exclusion_and_release_after_owner_termination(self):
        # Independent processes execute the actual platform lock, not a mocked API.
        tool = str(ROOT / "tools/icode_crosscheck.py")
        code = """import importlib.util,sys,pathlib
spec=importlib.util.spec_from_file_location('lock_worker',sys.argv[1])
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
root=pathlib.Path(sys.argv[2]);marker=root/sys.argv[3]
(marker.with_suffix('.attempt')).write_bytes(b'attempt')
with api.DirectoryLock(root):
 marker.write_bytes(b'entered')
 sys.stdin.buffer.read(1)
(marker.with_suffix('.exited')).write_bytes(b'exited')
"""
        processes = []
        def wait_file(path, process):
            deadline = time.monotonic() + 5
            while not path.exists():
                if process.poll() is not None:
                    self.fail("lock worker exited before barrier: " + repr(process.communicate()[1]))
                if time.monotonic() >= deadline:
                    self.fail("lock barrier timeout")
                time.sleep(0.02)
        try:
            owner = subprocess.Popen([sys.executable, "-B", "-c", code, tool, str(self.root), "owner"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(owner)
            wait_file(self.root / "owner", owner)
            waiter = subprocess.Popen([sys.executable, "-B", "-c", code, tool, str(self.root), "waiter"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(waiter)
            wait_file(self.root / "waiter.attempt", waiter)
            time.sleep(0.3)
            self.assertFalse((self.root / "waiter").exists())
            self.assertIsNone(waiter.poll())
            owner.terminate()
            owner.wait(timeout=5)
            wait_file(self.root / "waiter", waiter)
            waiter.stdin.write(b"x")
            waiter.stdin.flush()
            waiter.wait(timeout=5)
            self.assertEqual(waiter.returncode, 0, waiter.stderr.read())
            self.assertTrue((self.root / "waiter.exited").is_file())
            # A third acquisition proves normal __exit__ released the same lock.
            third = subprocess.Popen([sys.executable, "-B", "-c", code, tool, str(self.root), "third"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(third)
            wait_file(self.root / "third", third)
            third.stdin.write(b"x")
            third.stdin.flush()
            third.wait(timeout=5)
            self.assertEqual(third.returncode, 0, third.stderr.read())
            self.assertTrue((self.root / "third.exited").is_file())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
