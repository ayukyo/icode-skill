from __future__ import annotations

import hashlib
import asyncio
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SERVERS = [
    "icode-evidence",
    "icode-workspace",
    "icode-device-observe",
    "icode-mail-observe",
    "icode-mcp-health",
    "icode-mcp-policy",
    "icode-local-index",
]


def load_server(name: str):
    path = REPO / "mcp" / name / "server.py"
    spec = importlib.util.spec_from_file_location("test_" + name.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalMcpSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = {name: load_server(name) for name in SERVERS}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        for key in (
            "ICODE_EVIDENCE_CONFIG",
            "ICODE_WORKSPACE_CONFIG",
            "ICODE_DEVICE_OBSERVE_CONFIG",
            "ICODE_MAIL_OBSERVE_CONFIG",
            "ICODE_MAIL_OBSERVE_SECRET",
            "ICODE_MCP_HEALTH_CONFIG",
            "ICODE_MCP_POLICY_CONFIG",
            "ICODE_LOCAL_INDEX_CONFIG",
        ):
            os.environ.pop(key, None)
        self.temp.cleanup()

    def write_config(self, env_name: str, payload: dict) -> Path:
        path = self.root / f"{env_name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.environ[env_name] = str(path)
        return path

    def test_every_manifest_matches_declared_tools_and_is_keyless(self):
        for name, module in self.modules.items():
            manifest = json.loads((REPO / "mcp" / name / "tools_manifest.json").read_text())
            self.assertFalse(manifest["api_key_required"], name)
            self.assertEqual(sorted(item["name"] for item in manifest["tools"]), sorted(module.TOOLS), name)

    def test_evidence_hash_read_timeline_and_corpus(self):
        module = self.modules["icode-evidence"]
        log = self.root / "sample.log"
        log.write_text("2026-09-09T10:00:02 second\n2026-09-09T10:00:01 first\n", encoding="utf-8")
        self.write_config("ICODE_EVIDENCE_CONFIG", {"allowed_roots": [str(self.root)], "max_read_bytes": 10000, "max_corpus_files": 20})
        inspected = module.inspect_file(str(log))["answer"]
        self.assertEqual(inspected["sha256"], hashlib.sha256(log.read_bytes()).hexdigest())
        self.assertIn("first", module.read_evidence(str(log))["answer"]["text"])
        timeline = module.build_timeline([str(log)])["answer"]["events"]
        self.assertEqual([item["text"].split()[-1] for item in timeline], ["first", "second"])
        corpus = module.inspect_corpus(str(self.root), ["log"], max_files=1)["answer"]
        self.assertEqual(corpus["file_count"], 1)
        self.assertFalse(corpus["truncated"])
        second_log = self.root / "second.log"
        second_log.write_text("another\n", encoding="utf-8")
        self.assertTrue(module.inspect_corpus(str(self.root), ["log"], max_files=1)["answer"]["truncated"])
        outside = Path(self.temp.name).parent / f"outside-{Path(self.temp.name).name}.log"
        try:
            outside.write_text("outside", encoding="utf-8")
            (self.root / "escape.log").symlink_to(outside)
            corpus = module.inspect_corpus(str(self.root), ["log"])["answer"]
            self.assertEqual(corpus["file_count"], 2)
        finally:
            outside.unlink(missing_ok=True)

    def test_evidence_timeline_orders_offsets_and_reports_source_truncation(self):
        module = self.modules["icode-evidence"]
        log = self.root / "offsets.log"
        log.write_text(
            "2026-09-09T10:00:00+02:00 early\n"
            "2026-09-09T09:00:00+00:00 late\n"
            "2026-09-09T10:00:00+00:00 outside-window\n",
            encoding="utf-8",
        )
        self.write_config("ICODE_EVIDENCE_CONFIG", {
            "allowed_roots": [str(self.root)],
            "max_read_bytes": 70,
            "max_corpus_files": 20,
        })
        answer = module.build_timeline([str(log)])["answer"]
        self.assertEqual([item["text"].split()[-1] for item in answer["events"]], ["early", "late"])
        self.assertTrue(answer["source_truncated"][str(log)])
        self.assertTrue(answer["truncated"])

    def test_workspace_reads_git_and_compile_database(self):
        module = self.modules["icode-workspace"]
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        source = repo / "main.c"
        source.write_text("int main(void){return 0;}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "main.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=ICODE Test", "-c", "user.email=icode@example.invalid", "commit", "-qm", "init"], check=True)
        compile_db = repo / "compile_commands.json"
        compile_db.write_text(json.dumps([{"directory": str(repo), "file": str(source), "command": "cc main.c"}]), encoding="utf-8")
        self.write_config("ICODE_WORKSPACE_CONFIG", {"allowed_roots": [str(self.root)], "max_repo_depth": 4, "max_repos": 10, "max_command_chars": 10000})
        snapshot = module.inspect_workspace(str(repo))["answer"]
        self.assertTrue(snapshot["is_git"])
        self.assertEqual(len(snapshot["head"]), 40)
        self.assertEqual(module.inspect_repo_matrix(str(self.root))["answer"]["count"], 1)
        self.assertEqual(module.inspect_build_inputs(str(repo))["answer"]["source_count"], 1)
        self.assertIn("sha256", module.inspect_artifact(str(source))["answer"])

    def test_workspace_does_not_escape_allowed_root_via_git_toplevel(self):
        module = self.modules["icode-workspace"]
        repo = self.root / "repo"
        child = repo / "allowed"
        child.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.write_config("ICODE_WORKSPACE_CONFIG", {
            "allowed_roots": [str(child)],
            "max_repo_depth": 4,
            "max_repos": 10,
            "max_command_chars": 10000,
        })
        result = module.inspect_workspace(str(child))
        self.assertEqual(result["error_code"], "workspace_failed")
        self.assertIn("allowed_roots", result["error"])

    def test_device_fixture_is_profile_bound_and_read_only(self):
        module = self.modules["icode-device-observe"]
        fixture = self.root / "fixture"
        (fixture / "logs").mkdir(parents=True)
        (fixture / "artifacts").mkdir()
        (fixture / "device.json").write_text(json.dumps({"kernel": "fixture-kernel", "uptime": "1 day"}), encoding="utf-8")
        log = fixture / "logs" / "device.log"
        log.write_text("INFO boot\nERROR camera\n", encoding="utf-8")
        artifact = fixture / "artifacts" / "app.bin"
        artifact.write_bytes(b"same")
        local = self.root / "app.bin"
        local.write_bytes(b"same")
        self.write_config("ICODE_DEVICE_OBSERVE_CONFIG", {
            "allowed_roots": [str(self.root)],
            "profiles": {"fixture": {"transport": "fixture", "root": str(fixture), "allowed_remote_roots": ["/logs", "/artifacts"]}},
            "timeout_seconds": 2,
            "max_output_chars": 10000,
        })
        self.assertEqual(module.observe_device("fixture", ["kernel"])["answer"]["checks"]["kernel"], "fixture-kernel")
        lines = module.collect_log_window("fixture", "/logs/device.log", ["ERROR"])["answer"]["lines"]
        self.assertEqual(lines, ["ERROR camera"])
        exact = module.collect_log_window("fixture", "/logs/device.log", max_lines=2)["answer"]
        self.assertFalse(exact["truncated"])
        log.write_text("INFO preboot\nINFO boot\nERROR camera\n", encoding="utf-8")
        overflow = module.collect_log_window("fixture", "/logs/device.log", max_lines=2)["answer"]
        self.assertTrue(overflow["truncated"])
        self.assertEqual(overflow["lines"], ["INFO boot", "ERROR camera"])
        self.assertTrue(module.compare_artifact("fixture", "/artifacts/app.bin", str(local))["answer"]["matches"])
        self.assertEqual(module.collect_log_window("fixture", "/etc/passwd")["error_code"], "log_failed")

    def test_health_manifest_probe_and_secret_scan(self):
        module = self.modules["icode-mcp-health"]
        self.write_config("ICODE_MCP_HEALTH_CONFIG", {"allowed_roots": [str(REPO)], "trusted_server_roots": [str(REPO / "mcp")], "max_servers": 100, "probe_timeout_seconds": 10})
        for name in SERVERS:
            manifest = module.validate_manifest(str(REPO / "mcp" / name))["answer"]
            self.assertTrue(manifest["valid"], name)
            probe = asyncio.run(module.probe_python_server(str(REPO / "mcp" / name)))["answer"]
            self.assertTrue(probe["syntax_ok"], name)
            self.assertTrue(probe["tools_match"], name)
            self.assertFalse(probe["contract_errors"], name)
            self.assertTrue(all("outputSchema" in tool for tool in probe["protocol_tools"]), name)
            self.assertTrue(all(tool["annotations"]["destructiveHint"] is False for tool in probe["protocol_tools"]), name)
            if name == "icode-device-observe":
                remote = {tool["name"]: tool["annotations"] for tool in probe["protocol_tools"]}
                self.assertTrue(remote["observe_device"]["openWorldHint"])
            if name == "icode-local-index":
                indexed = {tool["name"]: tool["annotations"] for tool in probe["protocol_tools"]}
                self.assertFalse(indexed["build_index"]["readOnlyHint"])
            if name == "icode-mail-observe":
                mail = {tool["name"]: tool["annotations"] for tool in probe["protocol_tools"]}
                self.assertFalse(mail["save_attachment"]["readOnlyHint"])
                self.assertTrue(mail["save_attachment"]["openWorldHint"])
        self.assertFalse(module.scan_sensitive_payload({"api_key": "secret-value"})["answer"]["safe"])
        self.assertTrue(module.scan_sensitive_payload({"api_key": "<redacted>"})["answer"]["safe"])

        broken = self.root / "broken-server"
        broken.mkdir()
        (broken / "server.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        self.write_config("ICODE_MCP_HEALTH_CONFIG", {
            "allowed_roots": [str(self.root)],
            "trusted_server_roots": [str(self.root)],
            "probe_timeout_seconds": 2,
        })
        failed = asyncio.run(module.probe_python_server(str(broken)))
        self.assertEqual(failed["error_code"], "probe_failed")

    def test_policy_validates_and_denies_unrouted_calls(self):
        module = self.modules["icode-mcp-policy"]
        policy = REPO / "mcp" / "icode-mcp-policy" / "policy.json"
        self.write_config("ICODE_MCP_POLICY_CONFIG", {"policy_file": str(policy)})
        self.assertTrue(module.validate_policy()["answer"]["valid"])
        self.assertTrue(module.evaluate_call("log", "icode-evidence", "build_timeline")["answer"]["allowed"])
        self.assertFalse(module.evaluate_call("list", "icode-device-observe", "observe_device")["answer"]["allowed"])
        self.assertTrue(module.evaluate_call("plan", "icode-mail-observe", "get_message", "read")["answer"]["allowed"])
        self.assertTrue(module.evaluate_call("plan", "icode-mail-observe", "save_attachment", "managed_evidence_write")["answer"]["allowed"])
        self.assertFalse(module.evaluate_call("plan", "icode-mail-observe", "get_message", "managed_evidence_write")["answer"]["allowed"])
        self.assertFalse(module.evaluate_call("plan", "icode-mail-observe", "save_attachment", "read")["answer"]["allowed"])

    def test_policy_rejects_malformed_permissions_and_routes(self):
        module = self.modules["icode-mcp-policy"]
        invalid = self.root / "policy.json"
        invalid.write_text(json.dumps({
            "version": 1,
            "default": "allow",
            "servers": {
                "bad": {"tools": ["", "dup", "dup"], "operations": ["read", 7]},
            },
            "steps": {
                "plan": [
                    {"server": "bad", "condition": "", "required": "yes"},
                    {"server": "bad", "condition": "again", "required": False},
                ],
            },
        }), encoding="utf-8")
        self.write_config("ICODE_MCP_POLICY_CONFIG", {"policy_file": str(invalid)})
        answer = module.validate_policy()["answer"]
        self.assertFalse(answer["valid"])
        joined = "; ".join(answer["errors"])
        for marker in ("default", "工具名", "operations", "condition", "required", "重复 server"):
            self.assertIn(marker, joined)
        self.assertEqual(module.list_step_policy("plan")["error_code"], "policy_invalid")

    def test_local_index_build_query_and_staleness(self):
        module = self.modules["icode-local-index"]
        docs = self.root / "docs"
        docs.mkdir()
        source = docs / "note.md"
        source.write_text("camera pipeline unique_needle contract\n", encoding="utf-8")
        data = self.root / "index"
        self.write_config("ICODE_LOCAL_INDEX_CONFIG", {
            "allowed_roots": [str(self.root)],
            "data_dir": str(data),
            "max_files": 100,
            "max_file_bytes": 10000,
            "max_total_bytes": 100000,
            "extensions": [".md"],
            "exclude_dirs": [".git"],
        })
        built = module.build_index(str(docs))["answer"]
        self.assertEqual(built["file_count"], 1)
        query = module.query_index(str(docs), "unique_needle")["answer"]
        self.assertEqual(query["count"], 1)
        self.assertFalse(query["stale"])
        added = docs / "added.md"
        added.write_text("new eligible file\n", encoding="utf-8")
        status = module.index_status(str(docs))["answer"]
        self.assertTrue(status["stale"])
        self.assertEqual(status["new"], 1)
        module.build_index(str(docs))
        source.write_text("changed unique_needle\n", encoding="utf-8")
        self.assertTrue(module.index_status(str(docs))["answer"]["stale"])

    def test_local_index_honors_total_byte_limit_before_insert(self):
        module = self.modules["icode-local-index"]
        docs = self.root / "limited"
        docs.mkdir()
        (docs / "a.md").write_text("123456", encoding="utf-8")
        (docs / "b.md").write_text("abcdef", encoding="utf-8")
        self.write_config("ICODE_LOCAL_INDEX_CONFIG", {
            "allowed_roots": [str(self.root)],
            "data_dir": str(self.root / "limited-index"),
            "max_files": 100,
            "max_file_bytes": 10000,
            "max_total_bytes": 10,
            "extensions": [".md"],
            "exclude_dirs": [".git"],
        })
        answer = module.build_index(str(docs))["answer"]
        self.assertLessEqual(answer["total_bytes"], 10)
        self.assertEqual(answer["file_count"], 1)
        self.assertTrue(answer["truncated"])
        status = module.index_status(str(docs))["answer"]
        self.assertTrue(status["incomplete"])


if __name__ == "__main__":
    unittest.main()
