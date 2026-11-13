"""ICODE MCP 路由策略查询与调用前校验。policy.json 是机器真源。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import READ_ONLY_ANNOTATIONS, describe, expand_path, fail, load_config, ok  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-mcp-policy"
TOOLS = ["describe_capabilities", "list_step_policy", "evaluate_call", "validate_policy"]
DEFAULTS = {"policy_file": str(SERVER_DIR / "policy.json")}
# Transport aliases identify existing logical policies; they never grant operations.
TRANSPORT_MEMBERS = {
    "workspace": "icode-workspace", "evidence": "icode-evidence",
    "index": "icode-local-index", "health": "icode-mcp-health",
    "policy": "icode-mcp-policy",
}
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_MCP_POLICY_CONFIG", SERVER_DIR, DEFAULTS)


def _load_policy() -> tuple[Path, dict[str, Any]]:
    raw = str(_config().get("policy_file", SERVER_DIR / "policy.json"))
    path = expand_path(raw, base_dir=SERVER_DIR)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy 顶层必须是 object")
    return path, data


def _policy_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append("version 必须为 1")
    if data.get("default") != "deny":
        errors.append("default 必须为 deny")
    servers = data.get("servers")
    steps = data.get("steps")
    if not isinstance(servers, dict) or not servers:
        errors.append("servers 必须是非空 object")
        servers = {}
    if not isinstance(steps, dict) or not steps:
        errors.append("steps 必须是非空 object")
        steps = {}
    for server, spec in servers.items():
        if not isinstance(server, str) or not server:
            errors.append("server 名必须是非空字符串")
        if not isinstance(spec, dict):
            errors.append(f"servers.{server} 必须是 object")
            continue
        tools = spec.get("tools")
        if not isinstance(tools, list) or not tools:
            errors.append(f"servers.{server}.tools 必须是数组")
        elif any(not isinstance(tool, str) or not tool for tool in tools):
            errors.append(f"servers.{server}.工具名必须是非空字符串")
        elif len(tools) != len(set(tools)):
            errors.append(f"servers.{server}.工具名重复")
        operations = spec.get("operations")
        if not isinstance(operations, list) or not operations or any(
                not isinstance(operation, str) or not operation for operation in operations):
            errors.append(f"servers.{server}.operations 必须是非空字符串数组")
        elif len(operations) != len(set(operations)):
            errors.append(f"servers.{server}.operations 重复")
        tool_operations = spec.get("tool_operations")
        if tool_operations is not None:
            if not isinstance(tool_operations, dict):
                errors.append(f"servers.{server}.tool_operations 必须是 object")
            elif isinstance(tools, list) and set(tool_operations) != set(tools):
                errors.append(f"servers.{server}.tool_operations 必须逐工具完整声明")
            else:
                declared_operations = set(operations) if isinstance(operations, list) else set()
                for tool, allowed in tool_operations.items():
                    if not isinstance(allowed, list) or not allowed or any(
                            not isinstance(operation, str) or not operation for operation in allowed):
                        errors.append(f"servers.{server}.tool_operations.{tool} 必须是非空字符串数组")
                    elif len(allowed) != len(set(allowed)):
                        errors.append(f"servers.{server}.tool_operations.{tool} 重复")
                    elif not set(allowed) <= declared_operations:
                        errors.append(f"servers.{server}.tool_operations.{tool} 含未声明 operation")
    for step, routes in steps.items():
        if not isinstance(routes, list):
            errors.append(f"steps.{step} 必须是数组")
            continue
        routed_servers: set[str] = set()
        for index, route in enumerate(routes):
            if not isinstance(route, dict) or route.get("server") not in servers:
                errors.append(f"steps.{step}[{index}] 引用未知 server")
                continue
            server = route["server"]
            if server in routed_servers:
                errors.append(f"steps.{step} 重复 server: {server}")
            routed_servers.add(server)
            if not isinstance(route.get("condition"), str) or not route["condition"].strip():
                errors.append(f"steps.{step}[{index}].condition 必须是非空字符串")
            if not isinstance(route.get("required"), bool):
                errors.append(f"steps.{step}[{index}].required 必须是布尔值")
            if "operations" in route:
                allowed = route["operations"]
                server_spec = servers[server]
                declared = server_spec.get("operations", []) if isinstance(server_spec, dict) else []
                if not isinstance(declared, list):
                    declared = []
                if not isinstance(allowed, list) or not allowed or any(
                        not isinstance(op, str) or op not in declared for op in allowed):
                    errors.append(f"steps.{step}[{index}].operations 必须是 server 操作的非空子集")
    errors.extend(_transport_errors(data, servers))
    return errors


def _transport_errors(data: dict[str, Any], servers: dict[str, Any]) -> list[str]:
    # Policies predating the consolidated transport remain supported unchanged.
    if "transport" not in data:
        return []
    transport = data["transport"]
    if not isinstance(transport, dict) or set(transport) != {"server", "members", "tools"}:
        return ["transport 必须只声明 server、members、tools"]
    if transport["server"] != "icode-local" or transport["members"] != TRANSPORT_MEMBERS:
        return ["transport server 或 members 不匹配既有逻辑服务"]
    expected: dict[str, Any] = {}
    for prefix, server in TRANSPORT_MEMBERS.items():
        spec = servers.get(server)
        tools = spec.get("tools") if isinstance(spec, dict) else None
        if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
            return [f"transport 缺少有效逻辑工具: {server}"]
        expected.update({f"{prefix}_{tool}": {"server": server, "tool": tool} for tool in tools})
    if len(expected) != 27:
        return ["transport 必须覆盖 27 个既有逻辑工具"]
    expected["describe_capabilities"] = {"routes": [
        {"server": server, "tool": "describe_capabilities"} for server in TRANSPORT_MEMBERS.values()
    ]}
    if transport["tools"] != expected:
        return ["transport.tools 必须完整且精确映射既有工具，不得声明权限或别名"]
    return []


def _effective_transport(data: dict[str, Any]) -> dict[str, Any]:
    if "transport" in data:
        return data["transport"]
    # Old external policies need no rewrite. Only packaged tools have physical
    # wrappers; intersect their static manifests with the user's logical tools.
    tools: dict[str, Any] = {}
    for prefix, server in TRANSPORT_MEMBERS.items():
        spec = data["servers"].get(server)
        if spec is None:
            continue
        manifest_path = SERVER_DIR.parent / server / "tools_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # Standalone legacy installs need not ship optional sibling tools.
            continue
        if not isinstance(manifest, dict) or manifest.get("server") != server or not isinstance(manifest.get("tools"), list):
            raise ValueError(f"Invalid packaged tool manifest: {server}")
        for item in manifest["tools"]:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name in spec["tools"]:
                tools[f"{prefix}_{name}"] = {"server": server, "tool": name}
    # Aggregate only available describe wrappers and retain their logical read
    # checks. No operation or condition is supplied by this adapter.
    describe_routes = [target for target in tools.values() if target["tool"] == "describe_capabilities"]
    if describe_routes:
        tools["describe_capabilities"] = {"routes": describe_routes}
    return {"server": "icode-local", "members": TRANSPORT_MEMBERS, "tools": tools}


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return describe(SERVER_NAME, TOOLS, "按现有 /icode 步骤查询 MCP 路由并做调用前最小权限校验")


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def validate_policy() -> dict[str, Any]:
    try:
        path, data = _load_policy()
        errors = _policy_errors(data)
        return ok({"path": str(path), "valid": not errors, "errors": errors, "server_count": len(data.get("servers", {})), "step_count": len(data.get("steps", {}))})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return fail("policy_invalid", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def list_step_policy(step: str) -> dict[str, Any]:
    try:
        path, data = _load_policy()
        errors = _policy_errors(data)
        if errors:
            return fail("policy_invalid", "; ".join(errors))
        routes = data.get("steps", {}).get(step)
        if routes is None:
            return fail("unknown_step", f"策略中没有步骤: {step}")
        transport = _effective_transport(data)
        if transport:
            routes = [dict(route) for route in routes]
            for route in routes:
                mapped = {target["tool"]: name for name, target in transport["tools"].items()
                          if target.get("server") == route["server"]}
                if mapped:
                    route.update(transport_server=transport["server"], transport_tools=mapped)
        return ok({"policy": str(path), "step": step, "routes": routes})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return fail("policy_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evaluate_call(step: str, server: str, tool: str, operation: str = "read") -> dict[str, Any]:
    try:
        path, data = _load_policy()
        errors = _policy_errors(data)
        if errors:
            return fail("policy_invalid", "; ".join(errors))
        transport = _effective_transport(data) if server == "icode-local" else data.get("transport")
        if transport and server == transport["server"]:
            target = transport["tools"].get(tool)
            if target is None:
                return ok({"allowed": False, "reason": "unknown_tool", "step": step,
                           "server": server, "tool": tool, "policy": str(path)})
            if "routes" in target:
                # Global describe is static metadata. Only original describe/read
                # routes participate, so it cannot borrow a module's write grant.
                if operation != "read":
                    return ok({"allowed": False, "reason": "operation_denied", "step": step,
                               "server": server, "tool": tool, "policy": str(path)})
                candidates = [_evaluate_logical(data, path, step, item["server"], item["tool"], operation)
                              for item in target["routes"]]
                allowed = [item for item in candidates if item["allowed"]]
                result = dict((allowed or candidates)[0])
                if allowed:
                    result["condition"] = " OR ".join(item["condition"] for item in allowed)
                    result["required"] = False
                    result["logical_routes"] = [{key: item[key] for key in ("server", "tool", "condition", "required")}
                                                for item in allowed]
            else:
                result = _evaluate_logical(data, path, step, target["server"], target["tool"], operation)
                result.update(logical_server=target["server"], logical_tool=target["tool"])
            result.update(server=server, tool=tool)
            return ok(result)
        return ok(_evaluate_logical(data, path, step, server, tool, operation))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return fail("evaluation_failed", str(exc))


def _evaluate_logical(data: dict[str, Any], path: Path, step: str, server: str,
                      tool: str, operation: str) -> dict[str, Any]:
    server_spec = data["servers"].get(server)
    if server_spec is None:
        return {"allowed": False, "reason": "unknown_server", "step": step, "server": server, "tool": tool, "policy": str(path)}
    if tool not in server_spec["tools"]:
        return {"allowed": False, "reason": "unknown_tool", "step": step, "server": server, "tool": tool, "policy": str(path)}
    allowed_operations = server_spec.get("tool_operations", {}).get(
        tool,
        server_spec.get("operations", ["read"]),
    )
    if operation not in allowed_operations:
        return {"allowed": False, "reason": "operation_denied", "step": step, "server": server, "tool": tool, "policy": str(path)}
    route = next((item for item in data["steps"].get(step, []) if item.get("server") == server), None)
    if route is None:
        return {"allowed": False, "reason": "not_routed_for_step", "step": step, "server": server, "tool": tool, "policy": str(path)}
    if "operations" in route and operation not in route["operations"]:
        return {"allowed": False, "reason": "step_operation_denied", "step": step,
                "server": server, "tool": tool, "policy": str(path)}
    return {"allowed": True, "reason": "routed", "step": step, "server": server, "tool": tool, "condition": route.get("condition", "always"), "required": bool(route.get("required", False)), "policy": str(path)}


if __name__ == "__main__":
    mcp.run()
