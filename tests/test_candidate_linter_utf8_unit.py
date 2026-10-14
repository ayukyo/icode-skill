"""Standalone linter UTF-8 wire; real subprocesses and immutable ticket bytes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest import mock

import test_candidate_verification as fixtures


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/lint_workflow_contract.py"


class StandaloneLinterUtf8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.VerificationTests(methodName="runTest")
        try:
            cls.fixture.setUp()
            cls.fixture.record("fail", request="wire-physical-fail")
            cls.fixture.record("pass", "simulated", request="wire-simulated-pass")
            cls.before = cls.fixture.frozen()
        except BaseException:
            cls.fixture.doCleanups()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.fixture.doCleanups()

    def assert_unchanged(self):
        self.assertEqual(self.before, self.fixture.frozen())

    def arguments(self):
        return [str(SCRIPT), str(self.fixture.ticket),
                "--step", "audit-verified", "--strict", "--json"]

    def assert_blocked_report(self, report):
        self.assertIsInstance(report, dict)
        self.assertGreater(report["blocked"], 0)
        gates = [g for g in report["gates"] if g["gate"] == "delivery_evidence"]
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["status"], "blocked")
        self.assertTrue(any("outcome=fail" in issue for issue in gates[0]["issues"]), gates)

    def test_native_stdout_json_utf8_keeps_physical_failure(self):
        for encoding in ("cp1252:strict", "utf-8:strict"):
            with self.subTest(stdout_encoding=encoding):
                environment = dict(os.environ, PYTHONIOENCODING=encoding)
                result = subprocess.run([sys.executable, "-B", *self.arguments()],
                                        capture_output=True, timeout=30, env=environment)
                self.assertIsInstance(result.stdout, bytes)
                self.assertIsInstance(result.stderr, bytes)
                self.assertEqual(result.returncode, 1)
                self.assert_unchanged()
                # The old producer must fail an assertion here, not a JSON/decode error.
                self.assertTrue(result.stdout.strip(), "standalone JSON stdout is empty")
                try:
                    report = json.loads(result.stdout.decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self.fail("standalone stdout must be valid UTF-8 JSON: " + type(exc).__name__)
                self.assertEqual(result.stderr, b"")
                self.assert_blocked_report(report)
                self.assert_unchanged()

    def test_import_and_redirected_main_keep_text_stream(self):
        original_stdout = sys.stdout
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            spec = importlib.util.spec_from_file_location("wire_linter_import", SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertIs(sys.stdout, stream)
            self.assertEqual(stream.getvalue(), "")
            with mock.patch.object(sys, "argv", self.arguments()):
                result = module.main()
            self.assertIs(sys.stdout, stream)
            self.assertEqual(result, 1)
            self.assert_blocked_report(json.loads(stream.getvalue()))
            stream.seek(0)
            stream.truncate(0)
            with mock.patch.object(sys, "argv", self.arguments()):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(SCRIPT), run_name="__main__")
            self.assertEqual(stopped.exception.code, 1)
            self.assertIs(sys.stdout, stream)
        self.assertIs(sys.stdout, original_stdout)
        self.assert_blocked_report(json.loads(stream.getvalue()))
        self.assert_unchanged()

    def test_missing_arguments_keep_argparse_stderr_contract(self):
        environment = dict(os.environ, PYTHONIOENCODING="cp1252:strict")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)],
                                capture_output=True, timeout=30, env=environment)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"usage:", result.stderr)
        self.assertIn(b"out_dir", result.stderr)
        self.assertNotIn(b"UnicodeEncodeError", result.stderr)
        self.assert_unchanged()


if __name__ == "__main__":
    unittest.main()
