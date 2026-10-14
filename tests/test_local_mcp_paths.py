from __future__ import annotations

import json
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp" / "_lib"))
from local_mcp_common import allowed_roots, expand_path, load_config, resolve_allowed_path


class LocalMcpPathsTest(unittest.TestCase):
    @staticmethod
    def load_server(name):
        spec = importlib.util.spec_from_file_location("path_test_" + name.replace("-", "_"), REPO / "mcp" / name / "server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_home_placeholders_without_home_environment_use_system_home(self):
        # Windows 通常只有 USERPROFILE；Linux 未设 HOME 时也可使用系统用户目录。
        with patch.dict(os.environ):
            os.environ.pop("HOME", None)
            expected = Path.home().resolve()
            for value in ("$HOME", "${HOME}", "%HOME%"):
                with self.subTest(value=value):
                    self.assertEqual(expand_path(value), expected)

    def test_empty_home_environment_uses_system_home(self):
        with patch.dict(os.environ, {"HOME": ""}):
            if os.name == "posix":
                import pwd
                expected = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
            else:
                expected = Path.home().resolve()
            self.assertEqual(expand_path("$HOME"), expected)

    @unittest.skipUnless(os.name == "posix", "POSIX empty HOME safety regression")
    def test_empty_home_does_not_allow_files_outside_system_home(self):
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        if home == Path("/"):
            self.skipTest("system account has filesystem root as its home")
        with patch.dict(os.environ, {"HOME": ""}):
            self.assertEqual(allowed_roots({}), [home])
            with self.assertRaises(PermissionError):
                resolve_allowed_path("/etc/passwd", {})

    @unittest.skipUnless(os.name == "posix", "POSIX empty HOME tilde regression")
    def test_empty_home_tilde_uses_system_home_instead_of_filesystem_root(self):
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        with patch.dict(os.environ, {"HOME": ""}):
            self.assertEqual(expand_path("~"), home)
            self.assertEqual(expand_path("~/docs"), home / "docs")

    def test_explicit_home_environment_keeps_configured_location(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"HOME": folder}):
                for value in ("$HOME/docs", "${HOME}/docs"):
                    with self.subTest(value=value):
                        self.assertEqual(expand_path(value), (Path(folder) / "docs").resolve())

    def test_other_variables_and_absolute_paths_keep_existing_behavior(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"HOME_BACKUP": folder}):
                os.environ.pop("HOME", None)
                self.assertEqual(expand_path("$HOME_BACKUP/docs"), (Path(folder) / "docs").resolve())
                self.assertEqual(expand_path(folder), Path(folder).resolve())
                self.assertEqual(expand_path("~/docs"), (Path.home() / "docs").resolve())

    def test_default_allowed_root_admits_home_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            home = root / "home"
            home.mkdir()
            (home / "inside.txt").write_text("inside", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            with patch.dict(os.environ), patch("local_mcp_common.Path.home", return_value=home):
                os.environ.pop("HOME", None)
                self.assertEqual(allowed_roots({}), [home])
                self.assertEqual(resolve_allowed_path("$HOME/inside.txt", {}), home / "inside.txt")
                with self.assertRaises(PermissionError):
                    resolve_allowed_path("$HOME/../outside.txt", {})
                with self.assertRaises(PermissionError):
                    resolve_allowed_path(str(outside), {})

    def test_config_environment_path_uses_same_home_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            (home / "config.json").write_text(json.dumps({"max_read_bytes": 42}), encoding="utf-8")
            with patch.dict(os.environ, {"ICODE_TEST_PATH_CONFIG": "${HOME}/config.json"}), patch(
                "local_mcp_common.Path.home", return_value=home
            ):
                os.environ.pop("HOME", None)
                self.assertEqual(load_config("ICODE_TEST_PATH_CONFIG", home, {"max_read_bytes": 1}), {"max_read_bytes": 42})

    def test_unavailable_system_home_fails_with_config_error(self):
        with patch.dict(os.environ), patch("local_mcp_common.Path.home", side_effect=RuntimeError("no home")):
            os.environ.pop("HOME", None)
            try:
                allowed_roots({})
            except Exception as exc:
                self.assertIsInstance(exc, ValueError)
            else:
                self.fail("unknown system home must not become an allowed directory")

    def test_relative_system_home_is_rejected_instead_of_allowing_working_directory(self):
        with patch.dict(os.environ), patch("local_mcp_common.Path.home", return_value=Path(".")):
            os.environ.pop("HOME", None)
            with self.assertRaises(ValueError):
                allowed_roots({})

    @unittest.skipUnless(os.name == "posix", "POSIX account lookup safety regression")
    def test_empty_home_with_unknown_system_account_fails_with_config_error(self):
        with patch.dict(os.environ, {"HOME": ""}), patch("pwd.getpwuid", side_effect=KeyError("unknown uid")):
            try:
                allowed_roots({})
            except Exception as exc:
                self.assertIsInstance(exc, ValueError)
            else:
                self.fail("unknown system account must not become an allowed directory")

    def test_default_index_database_directory_uses_system_home(self):
        module = self.load_server("icode-local-index")
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            with patch.dict(os.environ), patch("local_mcp_common.Path.home", return_value=home):
                os.environ.pop("HOME", None)
                target = module._db_path(home / "source", module.DEFAULTS)
                self.assertEqual(target.parent, home / ".claude" / "icode_data" / "local-index")

    def test_default_mail_attachment_directory_uses_system_home_without_network(self):
        from contextlib import nullcontext
        module = self.load_server("icode-mail-observe")
        config = {**module.DEFAULTS, "host": "imap.example.invalid", "username": "fixture"}
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            with patch.dict(os.environ), patch("local_mcp_common.Path.home", return_value=home), patch.object(
                module, "_config", return_value=config
            ), patch.object(module, "_connection", return_value=nullcontext(object())), patch.object(
                module, "_select"
            ), patch.object(module, "_fetch_raw", return_value=b"fixture"), patch.object(
                module.email_intake, "save_attachment", return_value={}
            ) as save:
                os.environ.pop("HOME", None)
                self.assertIn("answer", module.save_attachment("INBOX", "101", "part-1"))
                self.assertEqual(save.call_args.kwargs["allowed_root"], home / ".claude" / "icode_data" / "mail_attachments")

    def test_mail_credential_home_placeholder_keeps_file_permission_checks(self):
        module = self.load_server("icode-mail-observe")
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            secret = home / "mail.secret"
            secret.write_text("test-only-secret\n", encoding="utf-8")
            secret.chmod(0o600)
            with patch.dict(os.environ), patch("local_mcp_common.Path.home", return_value=home):
                os.environ.pop("HOME", None)
                os.environ.pop("ICODE_MAIL_OBSERVE_SECRET", None)
                try:
                    value = module._credential({"credential_file": "$HOME/mail.secret"})
                except module.MailObserveError:
                    self.fail("valid home credential path must be readable")
                self.assertEqual(value, "test-only-secret")

    def test_policy_home_placeholder_and_relative_path_keep_resolution_rules(self):
        module = self.load_server("icode-mcp-policy")
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            (home / "policy.json").write_text('{"version": 1}', encoding="utf-8")
            with patch.dict(os.environ), patch("local_mcp_common.Path.home", return_value=home), patch.object(
                module, "_config", return_value={"policy_file": "${HOME}/policy.json"}
            ):
                os.environ.pop("HOME", None)
                try:
                    path, data = module._load_policy()
                except FileNotFoundError:
                    self.fail("policy HOME placeholder must locate the configured file")
                self.assertEqual(path, home / "policy.json")
                self.assertEqual(data, {"version": 1})
            with patch.object(module, "SERVER_DIR", home), patch.object(module, "_config", return_value={"policy_file": "policy.json"}):
                self.assertEqual(module._load_policy()[0], home / "policy.json")


if __name__ == "__main__":
    unittest.main()
