#!/usr/bin/env python3
"""Contract tests for the DOCX runtime package-index recovery policy."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = ROOT / "tools" / "docx" / "bootstrap_runtime.py"
SPEC = importlib.util.spec_from_file_location("icode_docx_bootstrap", BOOTSTRAP_PATH)
assert SPEC and SPEC.loader
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BOOTSTRAP
SPEC.loader.exec_module(BOOTSTRAP)


class PackageIndexPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_pip_env = {
            name: os.environ.pop(name)
            for name in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_NO_INDEX")
            if name in os.environ
        }

    def tearDown(self) -> None:
        os.environ.update(self.saved_pip_env)

    def test_default_order_is_pypi_then_trusted_mirrors(self) -> None:
        attempts = BOOTSTRAP.package_index_attempts()
        self.assertEqual([item.label for item in attempts], ["pypi", "tsinghua", "aliyun"])
        self.assertEqual(attempts[0].strategy, "default")
        self.assertTrue(all(item.url.startswith("https://") for item in attempts))

    def test_user_configured_index_is_not_overridden(self) -> None:
        os.environ["PIP_INDEX_URL"] = "https://packages.example.invalid/simple"
        attempts = BOOTSTRAP.package_index_attempts()
        self.assertEqual(attempts, (BOOTSTRAP.PackageIndexAttempt("user-configured", None, "user_configured"),))

    def test_network_timeout_retries_next_mirror(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 1, "", "ERROR: Read timed out while downloading Pillow"),
            subprocess.CompletedProcess([], 0, "installed", ""),
        ]
        with patch.object(BOOTSTRAP.subprocess, "run", side_effect=responses) as run:
            installed, strategy, error = BOOTSTRAP.install_locked_dependencies(Path("/tmp/icode-python"))
        self.assertTrue(installed)
        self.assertEqual(strategy, "fallback")
        self.assertEqual(error, "")
        self.assertEqual(run.call_count, 2)
        self.assertIn("https://pypi.org/simple", run.call_args_list[0].args[0])
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", run.call_args_list[1].args[0])

    def test_lock_or_resolver_failure_does_not_switch_index(self) -> None:
        response = subprocess.CompletedProcess([], 1, "", "ERROR: Cannot install python-docx==1.2.0 because requirements conflict")
        with patch.object(BOOTSTRAP.subprocess, "run", return_value=response) as run:
            installed, strategy, error = BOOTSTRAP.install_locked_dependencies(Path("/tmp/icode-python"))
        self.assertFalse(installed)
        self.assertEqual(strategy, "default")
        self.assertIn("requirements conflict", error)
        self.assertEqual(run.call_count, 1)

    def test_user_configured_network_failure_does_not_fallback(self) -> None:
        os.environ["PIP_INDEX_URL"] = "https://packages.example.invalid/simple"
        response = subprocess.CompletedProcess([], 1, "", "ERROR: Read timed out")
        with patch.object(BOOTSTRAP.subprocess, "run", return_value=response) as run:
            installed, strategy, error = BOOTSTRAP.install_locked_dependencies(Path("/tmp/icode-python"))
        self.assertFalse(installed)
        self.assertEqual(strategy, "user_configured")
        self.assertIn("Read timed out", error)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("--index-url", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
