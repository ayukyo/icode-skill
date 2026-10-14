from __future__ import annotations

import errno
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CONFIG_ENV = "ICODE_WORKSPACE_CONFIG"
ENTRYPOINTS = (
    ("inspect_workspace", "workspace_failed"),
    ("resolve_project", "project_resolution_failed"),
    ("inspect_project_profile", "project_profile_failed"),
    ("inspect_repo_matrix", "repo_matrix_failed"),
    ("inspect_build_inputs", "build_inputs_failed"),
    ("inspect_artifact", "artifact_failed"),
)


class WorkspaceAccessPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = REPO / "mcp" / "icode-workspace" / "server.py"
        spec = importlib.util.spec_from_file_location("test_workspace_access_policy_server", path)
        assert spec and spec.loader
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.config = self.root / "workspace-config.json"
        self.write_config({"allowed_roots": [str(self.allowed)]})
        environment = mock.patch.dict(os.environ, {CONFIG_ENV: str(self.config)})
        environment.start()
        self.addCleanup(environment.stop)

    def write_config(self, payload):
        self.config.write_text(json.dumps(payload), encoding="utf-8")

    def assert_scope_diagnostic(self, result, denied_path, allowed_root=None):
        diagnostic = result.get("diagnostic")
        self.assertIsInstance(diagnostic, dict, "scope rejection must have a typed diagnostic")
        self.assertEqual(diagnostic["kind"], "mcp_allowed_roots")
        self.assertEqual(diagnostic["resolved_path"], str(denied_path))
        self.assertEqual(diagnostic["allowed_roots"], [str(allowed_root or self.allowed)])
        self.assertEqual(diagnostic["config_env"], CONFIG_ENV)
        self.assertEqual(diagnostic["scope"], "explicit")
        self.assertFalse(diagnostic["auto_expand_allowed_roots"])
        self.assertIn("allowed_roots", diagnostic["remediation"])

    def test_capabilities_describe_effective_policy_without_expanding_scope(self):
        answer = self.module.describe_capabilities()["answer"]
        policy = answer.get("access_policy")
        self.assertIsInstance(policy, dict, "capabilities must expose the effective access policy")
        self.assertEqual(policy["configured_allowed_roots"], [str(self.allowed)])
        self.assertEqual(policy["allowed_roots"], [str(self.allowed)])
        self.assertEqual(policy["config_env"], CONFIG_ENV)
        self.assertEqual(policy["scope"], "explicit")
        self.assertFalse(policy["auto_expand_allowed_roots"])
        self.assertTrue(answer["source_read_only"])
        self.assertEqual(answer["tools"], self.module.TOOLS)
        self.assertEqual(json.loads(self.config.read_text())["allowed_roots"], [str(self.allowed)])

    def test_explicit_home_policy_remains_narrow_and_resolved(self):
        home = self.root / "home"
        home.mkdir()
        self.write_config({"allowed_roots": ["$HOME"]})
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            answer = self.module.describe_capabilities()["answer"]
        policy = answer.get("access_policy")
        self.assertIsInstance(policy, dict)
        self.assertEqual(policy["configured_allowed_roots"], ["$HOME"])
        self.assertEqual(policy["allowed_roots"], [str(home)])
        self.assertFalse(policy["auto_expand_allowed_roots"])
        self.assertIsNone(self.module.DEFAULTS["allowed_roots"])

    def test_allowed_directory_remains_readable_without_scope_diagnostic(self):
        result = self.module.inspect_workspace(str(self.allowed))
        self.assertIn("answer", result)
        self.assertEqual(result["answer"]["path"], str(self.allowed))
        self.assertNotIn("diagnostic", result)
        resolution = self.module.resolve_project(str(self.allowed))
        self.assertEqual(resolution["answer"]["project_root"], str(self.allowed))
        self.assertNotIn("diagnostic", resolution)

    def test_outside_path_retains_each_error_code_and_reports_policy_denial(self):
        previous = self.config.read_bytes()
        with mock.patch.object(self.module, "_git", side_effect=AssertionError("must not run git")), \
             mock.patch.object(self.module, "_discover_project_roots", side_effect=AssertionError("must not scan")), \
             mock.patch.object(self.module, "sha256_file", side_effect=AssertionError("must not read")):
            for name, code in ENTRYPOINTS:
                with self.subTest(tool=name):
                    result = getattr(self.module, name)(str(self.outside))
                    self.assertEqual(result["error_code"], code)
                    self.assertEqual(result["error"], f"路径不在 allowed_roots 内: {self.outside}")
                    self.assertIsNone(result["confidence"])
                    self.assert_scope_diagnostic(result, self.outside)
        self.assertEqual(self.config.read_bytes(), previous)

    def test_missing_outside_path_is_policy_denial_without_existence_claim(self):
        missing = self.outside / "missing"
        self.assertFalse(missing.exists())
        result = self.module.inspect_workspace(str(missing))
        self.assertEqual(result["error_code"], "workspace_failed")
        self.assert_scope_diagnostic(result, missing)
        self.assertNotIn("exists", result["diagnostic"])

    def test_real_missing_allowed_path_is_not_mislabeled_as_scope_denial(self):
        missing = self.allowed / "missing"
        for name, code in ENTRYPOINTS:
            with self.subTest(tool=name):
                result = getattr(self.module, name)(str(missing))
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["error"], str(missing))
                self.assertNotIn("diagnostic", result)

    def test_git_parent_scope_rejection_is_diagnosed_at_resolved_parent(self):
        repo = self.root / "repository"
        child = repo / "allowed-child"
        child.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.write_config({"allowed_roots": [str(child)]})
        for name, code in ENTRYPOINTS[:3]:
            with self.subTest(tool=name):
                result = getattr(self.module, name)(str(child))
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["error"], f"路径不在 allowed_roots 内: {repo}")
                self.assert_scope_diagnostic(result, repo, child)

    def test_permission_error_with_policy_words_is_not_treated_as_scope_denial(self):
        error = PermissionError(errno.EACCES, "路径不在 allowed_roots 内: unrelated I/O")
        with mock.patch.object(self.module, "_repo_snapshot", side_effect=error):
            result = self.module.inspect_workspace(str(self.allowed))
        self.assertEqual(result["error_code"], "workspace_failed")
        self.assertEqual(result["error"], str(error))
        self.assertNotIn("diagnostic", result)

    def test_file_permission_error_preserves_artifact_error_without_scope_diagnostic(self):
        artifact = self.allowed / "artifact.bin"
        artifact.write_bytes(b"fixture")
        error = PermissionError(errno.EACCES, "read access denied")
        with mock.patch.object(self.module, "sha256_file", side_effect=error):
            result = self.module.inspect_artifact(str(artifact))
        self.assertEqual(result["error_code"], "artifact_failed")
        self.assertEqual(result["error"], str(error))
        self.assertNotIn("diagnostic", result)

    def test_invalid_json_config_fails_closed_without_scope_diagnostic(self):
        self.config.write_text("{ invalid JSON", encoding="utf-8")
        for name, code in ENTRYPOINTS:
            with self.subTest(tool=name):
                result = getattr(self.module, name)(str(self.allowed))
                self.assertEqual(result["error_code"], code)
                self.assertNotIn("diagnostic", result)
        capabilities = self.module.describe_capabilities()
        self.assertEqual(capabilities.get("error_code"), "workspace_failed")
        self.assertNotIn("diagnostic", capabilities)

    def test_invalid_allowed_roots_fail_closed_without_scope_diagnostic(self):
        for roots in ([], "all", ["", None]):
            with self.subTest(allowed_roots=roots):
                self.write_config({"allowed_roots": roots})
                result = self.module.inspect_workspace(str(self.allowed))
                self.assertEqual(result["error_code"], "workspace_failed")
                self.assertNotIn("diagnostic", result)
                capabilities = self.module.describe_capabilities()
                self.assertEqual(capabilities.get("error_code"), "workspace_failed")
                self.assertNotIn("diagnostic", capabilities)

    def test_nonobject_config_is_not_mislabeled_as_scope_denial(self):
        self.write_config([])
        result = self.module.inspect_workspace(str(self.allowed))
        self.assertEqual(result["error_code"], "workspace_failed")
        self.assertNotIn("diagnostic", result)
        capabilities = self.module.describe_capabilities()
        self.assertEqual(capabilities.get("error_code"), "workspace_failed")
        self.assertNotIn("diagnostic", capabilities)


if __name__ == "__main__":
    unittest.main()
