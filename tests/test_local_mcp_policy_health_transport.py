from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
MEMBERS = {
    "workspace": "icode-workspace", "evidence": "icode-evidence",
    "index": "icode-local-index", "health": "icode-mcp-health",
    "policy": "icode-mcp-policy",
}
DEFAULT_SERVERS = ["icode-local", "icode-device-observe", "icode-mail-observe",
                   "cheap-research", "vision-bridge", "context7", "memory",
                   "playwright", "sequential-thinking"]


def load_server(name):
    spec = importlib.util.spec_from_file_location("transport_test_" + name.replace("-", "_"),
                                                REPO / "mcp" / name / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_in_child(server: Path, config: dict, home: Path):
    # Keep OS event-loop/stdio failures bounded even when cancellation cannot run.
    script = ("import asyncio, importlib.util, json, sys; "
              "spec=importlib.util.spec_from_file_location('probe_health', sys.argv[1]); "
              "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
              "config=json.loads(sys.argv[3]); module._config=lambda: config; "
              "print(json.dumps(asyncio.run(module.probe_python_server(sys.argv[2]))))")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-c", script,
                             str(REPO / "mcp/icode-mcp-health/server.py"), str(server), json.dumps(config)],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=True)
    return json.loads(result.stdout)


class PolicyTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_server("icode-mcp-policy")
        cls.policy = json.loads((REPO / "mcp/icode-mcp-policy/policy.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "policy.json"
        self.data = copy.deepcopy(self.policy)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        patcher = mock.patch.object(self.module, "_config", return_value={"policy_file": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_transport_covers_all_original_tools_without_new_logical_permissions(self):
        transport = self.data.get("transport", {})
        self.assertEqual(transport.get("server"), "icode-local")
        self.assertEqual(transport.get("members"), MEMBERS)
        expected = {f"{prefix}_{tool}": {"server": server, "tool": tool}
                    for prefix, server in MEMBERS.items()
                    for tool in self.data["servers"][server]["tools"]}
        self.assertEqual(len(expected), 27)
        actual = transport["tools"].copy()
        describe = actual.pop("describe_capabilities")
        self.assertEqual(actual, expected)
        self.assertEqual(describe, {"routes": [{"server": server, "tool": "describe_capabilities"}
                                               for server in MEMBERS.values()]})
        self.assertNotIn("icode-local", self.data["servers"])

    def test_prefixed_calls_preserve_each_logical_decision_and_conditions(self):
        for prefix, server in MEMBERS.items():
            for tool in self.data["servers"][server]["tools"]:
                for step in [*self.data["steps"], "unknown"]:
                    for operation in ("read", "managed_index_write", "managed_evidence_write", "unknown"):
                        with self.subTest(step=step, server=server, tool=tool, operation=operation):
                            old = self.module.evaluate_call(step, server, tool, operation)["answer"]
                            new = self.module.evaluate_call(step, "icode-local", f"{prefix}_{tool}", operation)["answer"]
                            self.assertEqual(new["allowed"], old["allowed"])
                            self.assertEqual(new["reason"], old["reason"])
                            self.assertEqual(new.get("condition"), old.get("condition"))
                            self.assertEqual(new.get("required"), old.get("required"))
                            self.assertEqual(new.get("logical_server"), server)
                            self.assertEqual(new.get("logical_tool"), tool)

    def test_legacy_result_is_identical_when_transport_is_absent(self):
        with_transport = self.module.evaluate_call("log", "icode-evidence", "inspect_file")
        self.data.pop("transport", None)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertEqual(self.module.evaluate_call("log", "icode-evidence", "inspect_file"), with_transport)

    def test_static_describe_uses_only_original_read_routes(self):
        for step in [*self.data["steps"], "unknown"]:
            expected = any(self.module.evaluate_call(step, server, "describe_capabilities")["answer"]["allowed"]
                           for server in MEMBERS.values())
            actual = self.module.evaluate_call(step, "icode-local", "describe_capabilities")["answer"]
            self.assertEqual(actual["allowed"], expected, step)
        for operation in ("unknown", "managed_index_write"):
            self.assertFalse(self.module.evaluate_call("install", "icode-local", "describe_capabilities", operation)["answer"]["allowed"])
        for tool in ("build_index", "index_missing", "workspace_inspect_file", "workspace__inspect_workspace"):
            self.assertEqual(self.module.evaluate_call("init", "icode-local", tool)["answer"]["reason"], "unknown_tool")

    def test_static_describe_cannot_inherit_custom_module_write_permission(self):
        self.data["servers"]["icode-workspace"]["operations"].append("managed_index_write")
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertTrue(self.module.evaluate_call("plan", "icode-workspace", "describe_capabilities", "managed_index_write")["answer"]["allowed"])
        result = self.module.evaluate_call("plan", "icode-local", "describe_capabilities", "managed_index_write")
        self.assertFalse(result["answer"]["allowed"])

    def test_external_custom_policy_without_transport_keeps_restrictions(self):
        self.data.pop("transport", None)
        self.data["steps"]["init"] = []
        self.data["steps"]["plan"] = [route for route in self.data["steps"]["plan"]
                                         if route["server"] != "icode-workspace"]
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertTrue(self.module.validate_policy()["answer"]["valid"])
        self.assertFalse(self.module.evaluate_call("init", "icode-local-index", "build_index", "managed_index_write")["answer"]["allowed"])
        self.assertFalse(self.module.evaluate_call("plan", "icode-workspace", "inspect_workspace")["answer"]["allowed"])
        self.assertFalse(self.module.evaluate_call("init", "icode-local", "index_build_index", "managed_index_write")["answer"]["allowed"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.data)

    def test_legacy_physical_calls_preserve_all_logical_decisions_without_rewriting_policy(self):
        self.data.pop("transport")
        self.path.write_text(json.dumps(self.data, indent=3) + "\n", encoding="utf-8")
        before = self.path.read_bytes()
        self.assertTrue(self.module.validate_policy()["answer"]["valid"])
        for prefix, server in MEMBERS.items():
            for tool in self.data["servers"][server]["tools"]:
                for step in [*self.data["steps"], "unknown"]:
                    for operation in ("read", "managed_index_write", "managed_evidence_write", "unknown"):
                        with self.subTest(step=step, server=server, tool=tool, operation=operation):
                            old = self.module.evaluate_call(step, server, tool, operation)["answer"]
                            new = self.module.evaluate_call(step, "icode-local", f"{prefix}_{tool}", operation)["answer"]
                            for key in ("allowed", "reason", "condition", "required"):
                                self.assertEqual(new.get(key), old.get(key))
        self.module.list_step_policy("log")
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_legacy_policy_exposes_only_existing_packaged_tools(self):
        self.data = {
            "version": 1, "default": "deny",
            "servers": {
                "icode-evidence": {"operations": ["read", "custom_write"],
                                   "tools": ["inspect_file", "custom_tool"],
                                   "tool_operations": {"inspect_file": ["read"], "custom_tool": ["custom_write"]}},
                "custom-server": {"operations": ["read"], "tools": ["inspect_file"]},
            },
            "steps": {"log": [{"server": "icode-evidence", "condition": "user condition", "required": True,
                                "operations": ["read"]},
                               {"server": "custom-server", "condition": "custom condition", "required": False}],
                      "none": []},
        }
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        before = self.path.read_bytes()
        self.assertTrue(self.module.validate_policy()["answer"]["valid"])
        allowed = self.module.evaluate_call("log", "icode-local", "evidence_inspect_file")["answer"]
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["condition"], "user condition")
        self.assertTrue(allowed["required"])
        for step, tool, operation in (("none", "evidence_inspect_file", "read"),
                                      ("log", "evidence_inspect_file", "custom_write"),
                                      ("log", "evidence_custom_tool", "custom_write"),
                                      ("log", "evidence_read_evidence", "read"),
                                      ("log", "workspace_inspect_workspace", "read"),
                                      ("log", "custom_inspect_file", "read"),
                                      ("log", "describe_capabilities", "read")):
            with self.subTest(step=step, tool=tool, operation=operation):
                self.assertFalse(self.module.evaluate_call(step, "icode-local", tool, operation)["answer"]["allowed"])
        routes = self.module.list_step_policy("log")["answer"]["routes"]
        self.assertEqual(routes[0]["transport_server"], "icode-local")
        self.assertEqual(routes[0]["transport_tools"], {"inspect_file": "evidence_inspect_file"})
        self.assertEqual(routes[0]["operations"], ["read"])
        self.assertNotIn("transport_server", routes[1])
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_describe_preserves_only_authorized_read_routes_and_or_conditions(self):
        self.data.pop("transport")
        self.data["servers"] = {server: {"operations": ["read", "custom_write"],
                                       "tools": ["describe_capabilities"],
                                       "tool_operations": {"describe_capabilities": ["read", "custom_write"]}}
                                for server in ("icode-workspace", "icode-evidence")}
        self.data["steps"] = {"log": [
            {"server": "icode-workspace", "condition": "workspace condition", "required": True},
            {"server": "icode-evidence", "condition": "evidence condition", "required": True},
        ]}
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        result = self.module.evaluate_call("log", "icode-local", "describe_capabilities")["answer"]
        self.assertTrue(result["allowed"])
        self.assertEqual(result["condition"], "workspace condition OR evidence condition")
        self.assertFalse(result["required"])
        self.assertEqual(len(result["logical_routes"]), 2)
        self.assertFalse(self.module.evaluate_call("log", "icode-local", "describe_capabilities", "custom_write")["answer"]["allowed"])
        self.data["servers"]["icode-workspace"]["tool_operations"]["describe_capabilities"] = ["custom_write"]
        self.data["steps"]["log"][1]["operations"] = ["custom_write"]
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertFalse(self.module.evaluate_call("log", "icode-local", "describe_capabilities")["answer"]["allowed"])

    def test_standalone_legacy_routes_work_without_optional_member_manifests(self):
        self.data.pop("transport")
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        before = self.path.read_bytes()
        standalone = Path(self.temp.name) / "standalone/mcp/icode-mcp-policy"
        standalone.mkdir(parents=True)
        with mock.patch.object(self.module, "SERVER_DIR", standalone):
            self.assertTrue(self.module.validate_policy()["answer"]["valid"])
            self.assertTrue(self.module.evaluate_call("log", "icode-evidence", "inspect_file")["answer"]["allowed"])
            result = self.module.list_step_policy("log")
            self.assertIn("answer", result, result)
            routes = result["answer"]["routes"]
            self.assertEqual(routes, self.data["steps"]["log"])
            for tool in ("evidence_inspect_file", "workspace_describe_capabilities", "describe_capabilities"):
                with self.subTest(tool=tool):
                    self.assertFalse(self.module.evaluate_call("log", "icode-local", tool)["answer"]["allowed"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_member_manifests_expose_only_available_physical_routes(self):
        self.data.pop("transport")
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        standalone = Path(self.temp.name) / "standalone/mcp/icode-mcp-policy"
        standalone.mkdir(parents=True)
        evidence = standalone.parent / "icode-evidence"
        evidence.mkdir()
        manifest = evidence / "tools_manifest.json"
        manifest.write_bytes((REPO / "mcp/icode-evidence/tools_manifest.json").read_bytes())
        with mock.patch.object(self.module, "SERVER_DIR", standalone):
            result = self.module.list_step_policy("plan")
            self.assertIn("answer", result, result)
            routes = result["answer"]["routes"]
            self.assertNotIn("transport_server", routes[0])
            self.assertEqual(routes[1]["transport_tools"]["inspect_file"], "evidence_inspect_file")
            self.assertTrue(self.module.evaluate_call("plan", "icode-local", "evidence_inspect_file")["answer"]["allowed"])
            self.assertFalse(self.module.evaluate_call("plan", "icode-local", "workspace_inspect_workspace")["answer"]["allowed"])
            describe = self.module.evaluate_call("plan", "icode-local", "describe_capabilities")["answer"]
            self.assertTrue(describe["allowed"])
            self.assertEqual([route["server"] for route in describe["logical_routes"]], ["icode-evidence"])
            # A present damaged package is not an optional absent member.
            for damaged in ("{", "[]", '{"server": "wrong", "tools": []}'):
                with self.subTest(damaged=damaged):
                    manifest.write_text(damaged, encoding="utf-8")
                    self.assertTrue(self.module.evaluate_call("plan", "icode-evidence", "inspect_file")["answer"]["allowed"])
                    self.assertEqual(self.module.list_step_policy("plan")["error_code"], "policy_failed")
                    self.assertEqual(self.module.evaluate_call("plan", "icode-local", "evidence_inspect_file")["error_code"], "evaluation_failed")

    def test_routes_keep_logical_conditions_and_expose_physical_tools(self):
        answer = self.module.list_step_policy("learn")["answer"]
        route = answer["routes"][0]
        self.assertEqual({key: route[key] for key in self.data["steps"]["learn"][0]}, self.data["steps"]["learn"][0])
        self.assertEqual(route["transport_server"], "icode-local")
        self.assertEqual(route["transport_tools"]["query_index"], "index_query_index")
        self.assertEqual(route["operations"], ["read"])

    def test_malformed_transport_is_rejected_without_permission_fallback(self):
        base = self.data.get("transport", {"server": "icode-local", "members": MEMBERS, "tools": {}})
        variants = [None, [], {**base, "server": "typo"}, {**base, "operations": ["write"]},
                    {**base, "members": {**MEMBERS, "ws": "icode-workspace"}},
                    {**base, "tools": {}},
                    {**base, "tools": {**base["tools"], "index_build_index": {"server": "icode-local-index", "tool": "query_index"}}},
                    {**base, "tools": {**base["tools"], "workspace_unknown": {"server": "icode-workspace", "tool": "unknown"}}},
                    {**base, "tools": {**base["tools"], "index_build_index": {"server": "icode-local-index", "tool": "build_index", "operations": ["read"]}}}]
        for transport in variants:
            with self.subTest(transport=transport):
                self.data["transport"] = transport
                self.path.write_text(json.dumps(self.data), encoding="utf-8")
                self.assertFalse(self.module.validate_policy()["answer"]["valid"])
                self.assertEqual(self.module.evaluate_call("init", "icode-local", "index_build_index", "read")["error_code"], "policy_invalid")
                self.assertEqual(self.module.list_step_policy("init")["error_code"], "policy_invalid")


class HealthCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_server("icode-mcp-health")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = {**self.module.DEFAULTS, "allowed_roots": [str(self.root)]}
        patcher = mock.patch.object(self.module, "_config", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.catalog = {"schema_version": 1, "servers": DEFAULT_SERVERS,
                        "replacements": {"icode-local": {"members": MEMBERS, "retain_payload": True}}}

    def fixture(self):
        for name in [*DEFAULT_SERVERS, *MEMBERS.values(), "routing-rules"]:
            (self.root / name).mkdir()
        (self.root / "servers.json").write_text(json.dumps(self.catalog), encoding="utf-8")

    def test_catalog_distinguishes_default_services_compatibility_modules_and_rules(self):
        self.fixture()
        answer = self.module.inventory_servers(str(self.root))["answer"]
        self.assertEqual(answer["count"], 9)
        self.assertEqual({item["name"] for item in answer["servers"]}, set(DEFAULT_SERVERS))
        self.assertEqual({item["name"] for item in answer["compatibility_modules"]}, set(MEMBERS.values()))
        self.assertEqual({item["name"] for item in answer["rule_directories"]}, {"routing-rules"})

    def test_missing_catalog_preserves_original_inventory_shape(self):
        (self.root / "legacy").mkdir()
        answer = self.module.inventory_servers(str(self.root))["answer"]
        self.assertEqual(set(answer), {"root", "servers", "count"})
        self.assertEqual(answer["count"], 1)

    def test_invalid_catalog_does_not_fall_back_to_directory_registration(self):
        self.fixture()
        variants = [[], {**self.catalog, "schema_version": 2},
                    {**self.catalog, "servers": ["../outside"]},
                    {**self.catalog, "servers": [*DEFAULT_SERVERS, "icode-workspace"]},
                    {**self.catalog, "replacements": {"icode-local": {"members": MEMBERS, "retain_payload": False}}}]
        for data in variants:
            with self.subTest(data=data):
                (self.root / "servers.json").write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(self.module.inventory_servers(str(self.root))["error_code"], "inventory_failed")

    def test_catalog_path_is_rechecked_through_access_policy(self):
        self.fixture()
        original = self.module.resolve_allowed_path
        def resolve(path, config):
            if Path(path).name == "servers.json":
                raise PermissionError("catalog path denied")
            return original(path, config)
        with mock.patch.object(self.module, "resolve_allowed_path", side_effect=resolve):
            self.assertEqual(self.module.inventory_servers(str(self.root))["error_code"], "inventory_failed")

    def test_stdio_probe_detects_manifest_annotation_mismatch(self):
        server = self.root / "fixture"
        server.mkdir()
        (server / "server.py").write_text(
            "from mcp.server.fastmcp import FastMCP\n"
            "from mcp.types import ToolAnnotations\n"
            "mcp = FastMCP('fixture')\n"
            "@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))\n"
            "def sample(value: str) -> dict[str, str]:\n"
            "    return {'value': value}\n"
            "if __name__ == '__main__':\n"
            "    mcp.run()\n", encoding="utf-8")
        (server / "tools_manifest.json").write_text(json.dumps({
            "server": "fixture", "api_key_required": False,
            "tools": [{"name": "sample", "read_only": True, "deterministic": True, "network": False}],
        }), encoding="utf-8")
        self.config["trusted_server_roots"] = [str(self.root)]
        answer = probe_in_child(server, self.config, self.root)["answer"]
        self.assertTrue(answer["tools_match"])
        self.assertIn("sample: readOnlyHint does not match manifest", answer["contract_errors"])

    def test_stdio_unified_server_retains_all_contract_checks(self):
        server = Path(os.environ.get("ICODE_LOCAL_TEST_SERVER_DIR", str(REPO / "mcp/icode-local")))
        self.assertTrue((server / "server.py").is_file())
        config = {**self.config, "allowed_roots": [str(REPO), str(server.parent)],
                  "trusted_server_roots": [str(server)], "probe_timeout_seconds": 10}
        answer = probe_in_child(server, config, self.root)["answer"]
        self.assertTrue(answer["syntax_ok"])
        self.assertTrue(answer["tools_match"])
        self.assertEqual(answer["manifest_errors"], [])
        self.assertEqual(answer["contract_errors"], [])
        policy = json.loads((REPO / "mcp/icode-mcp-policy/policy.json").read_text(encoding="utf-8"))
        self.assertEqual(set(answer["decorated_tools"]), set(policy["transport"]["tools"]))
        self.assertEqual(len(answer["protocol_tools"]), 28)
        tools = {tool["name"]: tool for tool in answer["protocol_tools"]}
        self.assertFalse(tools["index_build_index"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["evidence_inspect_file"]["annotations"]["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
