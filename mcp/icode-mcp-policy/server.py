"""ICODE MCP 路由策略查询与调用前校验。policy.json 是机器真源。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import READ_ONLY_ANNOTATIONS, describe, fail, load_config, ok  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-mcp-policy"
TOOLS = ["describe_capabilities", "list_step_policy", "evaluate_call", "validate_policy"]
DEFAULTS = {"policy_file": str(SERVER_DIR / "policy.json")}
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_MCP_POLICY_CONFIG", SERVER_DIR, DEFAULTS)


def _load_policy() -> tuple[Path, dict[str, Any]]:
    raw = str(_config().get("policy_file", SERVER_DIR / "policy.json"))
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = SERVER_DIR / path
    path = path.resolve()
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
    return errors


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
        server_spec = data["servers"].get(server)
        if server_spec is None:
            return ok({"allowed": False, "reason": "unknown_server", "step": step, "server": server, "tool": tool, "policy": str(path)})
        if tool not in server_spec["tools"]:
            return ok({"allowed": False, "reason": "unknown_tool", "step": step, "server": server, "tool": tool, "policy": str(path)})
        if operation not in server_spec.get("operations", ["read"]):
            return ok({"allowed": False, "reason": "operation_denied", "step": step, "server": server, "tool": tool, "policy": str(path)})
        route = next((item for item in data["steps"].get(step, []) if item.get("server") == server), None)
        if route is None:
            return ok({"allowed": False, "reason": "not_routed_for_step", "step": step, "server": server, "tool": tool, "policy": str(path)})
        return ok({"allowed": True, "reason": "routed", "step": step, "server": server, "tool": tool, "condition": route.get("condition", "always"), "required": bool(route.get("required", False)), "policy": str(path)})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return fail("evaluation_failed", str(exc))


if __name__ == "__main__":
    mcp.run()
