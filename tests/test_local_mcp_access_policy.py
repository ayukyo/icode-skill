from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp" / "_lib"))
common = importlib.import_module("local_mcp_common")


def load_server(name):
    spec = importlib.util.spec_from_file_location("policy_test_" + name.replace("-", "_"), REPO / "mcp" / name / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalMcpAccessPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drive_enumerator = staticmethod(common._windows_local_roots)
        cls.modules = {name: load_server(name) for name in (
            "icode-evidence", "icode-workspace", "icode-local-index", "icode-mcp-health", "icode-device-observe",
        )}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.disk = Path(self.temp.name).resolve()
        for name, value in (("_windows_platform", True), ("_windows_local_roots", [self.disk])):
            patcher = mock.patch.object(common, name, return_value=value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def config(self, name, payload):
        path = self.disk / (name + "-config.json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        env = "ICODE_" + name.removeprefix("icode-").replace("-", "_").upper() + "_CONFIG"
        patcher = mock.patch.dict(os.environ, {env: str(path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_windows_missing_and_null_roots_allow_requested_local_disk(self):
        requested = self.disk / "project"
        requested.mkdir()
        for config in ({}, {"allowed_roots": None}):
            with self.subTest(config=config):
                self.assertEqual(common.allowed_roots(config), [self.disk])
                self.assertEqual(common.resolve_allowed_path(requested, config), requested)

    def test_explicit_home_scope_is_respected_under_windows(self):
        home = self.disk / "home"
        home.mkdir()
        outside = self.disk / "project"
        outside.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertEqual(common.allowed_roots({"allowed_roots": ["$HOME"]}), [home])
            with self.assertRaises(PermissionError):
                common.resolve_allowed_path(outside, {"allowed_roots": ["$HOME"]})

    def test_linux_platform_default_stays_home_only(self):
        home = self.disk / "home"
        home.mkdir()
        with mock.patch.object(common, "_windows_platform", return_value=False), mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertEqual(common.allowed_roots({}), [home])
            self.assertEqual(common.allowed_roots({"allowed_roots": None}), [home])
            with self.assertRaises(PermissionError):
                common.resolve_allowed_path(self.disk, {"allowed_roots": None})

    def test_empty_or_malformed_explicit_roots_fail_closed(self):
        for roots in ([], "all", ["", None], [str(self.disk), None]):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                common.allowed_roots({"allowed_roots": roots})

    def test_no_local_drives_fail_closed(self):
        with mock.patch.object(common, "_windows_local_roots", return_value=[]):
            with self.assertRaises(ValueError):
                common.allowed_roots({"allowed_roots": None})

    def test_drive_enumeration_admits_local_types_without_scanning_directories(self):
        import ctypes
        kernel = mock.MagicMock()
        kernel.GetLogicalDrives.return_value = sum(1 << (ord(letter) - ord("A")) for letter in "CDEZ")
        types = {"C": 3, "D": 2, "E": 6, "Z": 4}
        kernel.GetDriveTypeW.side_effect = lambda path: types[path[0]]
        with mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True), mock.patch.object(
            Path, "iterdir", side_effect=AssertionError("drive enumeration must not scan")
        ):
            roots = self.drive_enumerator()
        self.assertEqual([PureWindowsPath(str(root)) for root in roots], [PureWindowsPath("C:/"), PureWindowsPath("D:/"), PureWindowsPath("E:/")])

    @unittest.skipUnless(os.name == "nt", "native Windows UNC anchors")
    def test_remote_unc_is_rejected_before_filesystem_or_network_resolution(self):
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("remote resolution forbidden")), mock.patch.object(
            Path, "exists", side_effect=AssertionError("remote stat forbidden")
        ):
            with self.assertRaises(PermissionError):
                common.resolve_allowed_path(r"\\fileserver\share\project", {})

    def test_default_windows_protects_precise_credential_paths(self):
        for relative in (
            ".ssh/id_ed25519", ".aws/credentials", ".gnupg/private-keys-v1.d/key",
            ".git-credentials", ".netrc", ".env", ".env.local", ".codex/auth.json",
            ".config/gh/hosts.yml", "AppData/Local/Google/Chrome/User Data/Default/Login Data",
        ):
            with self.subTest(path=relative):
                target = self.disk / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("private fixture", encoding="utf-8")
                with self.assertRaises(PermissionError) as caught:
                    common.resolve_allowed_path(target, {"allowed_roots": None})
                diagnostic = common.fail("read_failed", str(caught.exception), exc=caught.exception)["diagnostic"]
                self.assertEqual(diagnostic["kind"], "sensitive_path")

    def test_default_windows_does_not_reject_source_symbols_or_templates(self):
        for relative in ("secret.py", "token.cpp", "cookie.h", ".env.example", ".env.sample", "docs/credentials.md"):
            with self.subTest(path=relative):
                target = self.disk / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("secret token cookie symbols", encoding="utf-8")
                self.assertEqual(common.resolve_allowed_path(target, {}), target)

    def test_explicit_scope_keeps_existing_sensitive_file_semantics(self):
        target = self.disk / ".env"
        target.write_text("explicit fixture", encoding="utf-8")
        self.assertEqual(common.resolve_allowed_path(target, {"allowed_roots": [str(self.disk)]}), target)

    def test_scope_and_io_errors_have_distinct_diagnostics(self):
        allowed = self.disk / "allowed"
        allowed.mkdir()
        try:
            common.resolve_allowed_path(self.disk, {"allowed_roots": [str(allowed)]})
        except PermissionError as exc:
            response = common.fail("read_failed", str(exc), exc=exc)
        else:
            self.fail("outside path must be denied")
        self.assertEqual(response["diagnostic"]["kind"], "mcp_allowed_roots")
        response = common.fail("read_failed", "allowed_roots text", exc=PermissionError("allowed_roots text"))
        self.assertNotIn("diagnostic", response)
        with self.assertRaises(FileNotFoundError):
            common.resolve_allowed_path(allowed / "missing", {"allowed_roots": [str(allowed)]})

    def test_wsl_unc_helper_accepts_only_standard_local_views(self):
        helper = getattr(common, "_wsl_unc_root", None)
        self.assertTrue(callable(helper), "a pure WSL UNC classification helper is required")
        for path, expected in (
            (r"\\wsl.localhost\Distribution\home\project", r"\\wsl.localhost\Distribution" + "\\"),
            (r"\\wsl$\Distribution\home\project", r"\\wsl$\Distribution" + "\\"),
            (r"\\fileserver\share\project", None),
            ("D:/project", None),
        ):
            with self.subTest(path=path):
                self.assertEqual(helper(path), expected)

    @unittest.skipUnless(os.name == "nt", "native Windows drive/UNC path semantics")
    def test_windows_default_rejects_remote_unc_and_accepts_local_drive(self):
        with mock.patch.object(common, "_windows_local_roots", return_value=[Path("D:/")]):
            self.assertEqual(common.resolve_allowed_path("D:/project", {}, must_exist=False), Path("D:/project"))
            with self.assertRaises(PermissionError):
                common.resolve_allowed_path(r"\\fileserver\share\project", {}, must_exist=False)
            # Path resolution is mocked only to avoid requiring a running WSL distribution.
            with mock.patch.object(common, "expand_path", side_effect=lambda value, **_: Path(value)):
                path = Path(r"\\wsl.localhost\Distribution\home\project")
                self.assertEqual(common.resolve_allowed_path(path, {}, must_exist=False), path)

    def test_symlink_escape_uses_resolved_target_policy(self):
        allowed = self.disk / "allowed"
        allowed.mkdir()
        outside = self.disk / "outside"
        outside.mkdir()
        link = allowed / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest(f"symlink privilege unavailable: {exc}")
            raise
        with self.assertRaises(PermissionError):
            common.resolve_allowed_path(link, {"allowed_roots": [str(allowed)]})

    def test_bulk_evidence_and_index_skip_sensitive_files_with_visible_boundary(self):
        source = self.disk / "source"
        source.mkdir()
        (source / "public.txt").write_text("needle", encoding="utf-8")
        private = source / ".ssh" / "credential.txt"
        private.parent.mkdir()
        private.write_text("private needle", encoding="utf-8")
        evidence = self.modules["icode-evidence"]
        index = self.modules["icode-local-index"]
        self.config("icode-evidence", {"allowed_roots": None})
        self.config("icode-local-index", {"allowed_roots": None, "data_dir": str(self.disk / "index"), "extensions": [".txt"]})
        result = evidence.inspect_corpus(str(source), ["txt"])["answer"]
        self.assertEqual(result["file_count"], 1)
        self.assertGreaterEqual(result["skipped_sensitive"], 1)
        built = index.build_index(str(source))["answer"]
        self.assertEqual(built["file_count"], 1)
        self.assertTrue(built["sensitive_paths_excluded"])
        self.assertEqual(index.query_index(str(source), "private")["answer"]["count"], 0)

    def test_all_five_capabilities_report_platform_policy(self):
        for name, module in self.modules.items():
            with self.subTest(server=name):
                self.config(name, {"allowed_roots": None})
                answer = module.describe_capabilities()["answer"]
                self.assertEqual(answer["access_policy"]["scope"], "platform_default")
                self.assertEqual(answer["access_policy"]["allowed_roots"], [str(self.disk)])
                self.assertTrue(answer["access_policy"]["protect_sensitive_paths"])

    def test_cached_sensitive_index_rows_are_not_returned_under_new_default_policy(self):
        source = self.disk / "source"
        private = source / ".ssh" / "credential.txt"
        private.parent.mkdir(parents=True)
        private.write_text("cached_private_needle", encoding="utf-8")
        index = self.modules["icode-local-index"]
        payload = {"allowed_roots": [str(self.disk)], "data_dir": str(self.disk / "index"), "extensions": [".txt"]}
        self.config("icode-local-index", payload)
        self.assertEqual(index.build_index(str(source))["answer"]["file_count"], 1)
        self.config("icode-local-index", {**payload, "allowed_roots": None})
        result = index.query_index(str(source), "cached_private_needle")["answer"]
        self.assertEqual(result["count"], 0)

    def test_static_build_entrypoint_does_not_read_sensitive_directory(self):
        workspace = self.modules["icode-workspace"]
        source = self.disk / "source"
        private = source / ".ssh" / "build.sh"
        private.parent.mkdir(parents=True)
        private.write_text("secret fixture", encoding="utf-8")
        self.config("icode-workspace", {"allowed_roots": None})
        result = workspace._inspect_entrypoint(source, ".ssh/build.sh", 1000)
        self.assertFalse(result["readable"])
        self.assertEqual(result["reason"], "read_policy_denied")

    def test_derived_file_reads_recheck_resolved_sensitive_target(self):
        source = self.disk / "source"
        source.mkdir()
        private = source / ".ssh" / "private.json"
        private.parent.mkdir()
        private.write_text("{}", encoding="utf-8")
        original = Path.resolve
        for name, tool, filename, code in (
            ("icode-workspace", "inspect_build_inputs", "compile_commands.json", "build_inputs_failed"),
            ("icode-mcp-health", "validate_manifest", "tools_manifest.json", "manifest_failed"),
        ):
            with self.subTest(server=name):
                alias = source / filename
                alias.write_text("{}", encoding="utf-8")
                self.config(name, {"allowed_roots": None})

                def resolve(path, *args, **kwargs):
                    return original(private if path == alias else path, *args, **kwargs)

                with mock.patch.object(Path, "resolve", resolve):
                    result = getattr(self.modules[name], tool)(str(source))
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["diagnostic"]["kind"], "sensitive_path")

    def test_default_local_read_scope_does_not_allow_untrusted_server_execution(self):
        health = self.modules["icode-mcp-health"]
        server = self.disk / "untrusted"
        server.mkdir()
        sentinel = self.disk / "must-not-exist"
        (server / "server.py").write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n", encoding="utf-8")
        self.config("icode-mcp-health", {"allowed_roots": None, "trusted_server_roots": [str(self.disk / "trusted")]})
        import asyncio
        result = asyncio.run(health.probe_python_server(str(server)))
        self.assertEqual(result["error_code"], "untrusted_server")
        self.assertFalse(sentinel.exists())

    def test_default_local_read_scope_does_not_expand_device_remote_roots(self):
        device = self.modules["icode-device-observe"]
        fixture = self.disk / "fixture"
        fixture.mkdir()
        self.config("icode-device-observe", {
            "allowed_roots": None,
            "profiles": {"fixture": {"transport": "fixture", "root": str(fixture), "allowed_remote_roots": ["/logs"]}},
        })
        result = device.collect_log_window("fixture", "/private/credential.txt")
        self.assertEqual(result["error_code"], "log_failed")
        self.assertIn("allowed_remote_roots", result["error"])
        self.assertNotIn("diagnostic", result)

    def test_fixture_subfiles_use_local_sensitive_protection(self):
        device = self.modules["icode-device-observe"]
        fixture = self.disk / "fixture"
        private = fixture / ".ssh" / "credential.txt"
        private.parent.mkdir(parents=True)
        private.write_text("private fixture", encoding="utf-8")
        self.config("icode-device-observe", {
            "allowed_roots": None,
            "profiles": {"fixture": {"transport": "fixture", "root": str(fixture), "allowed_remote_roots": ["/.ssh"]}},
        })
        result = device.collect_log_window("fixture", "/.ssh/credential.txt")
        self.assertEqual(result.get("error_code"), "log_failed")
        self.assertEqual(result["diagnostic"]["kind"], "sensitive_path")


if __name__ == "__main__":
    unittest.main()
