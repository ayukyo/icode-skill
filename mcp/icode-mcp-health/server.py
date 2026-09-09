"""ICODE MCP 安装面、manifest、Python 入口和敏感信息健康检查。"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import anyio

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import READ_ONLY_ANNOTATIONS, describe, expand_path, fail, load_config, ok, resolve_allowed_path  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-mcp-health"
TOOLS = ["describe_capabilities", "inventory_servers", "validate_manifest", "probe_python_server", "scan_sensitive_payload"]
DEFAULTS = {
    "allowed_roots": ["$HOME"],
    "trusted_server_roots": ["$HOME/.claude/skills/icode/mcp"],
    "max_servers": 100,
    "probe_timeout_seconds": 10,
}
SENSITIVE_KEY = re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|private[_-]?key|cookie|authorization)")
SENSITIVE_VALUE = re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|bearer\s+[A-Za-z0-9._~+/=-]{12,})")
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_MCP_HEALTH_CONFIG", SERVER_DIR, DEFAULTS)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return describe(SERVER_NAME, TOOLS, "MCP 安装清单、工具 manifest、入口语法与敏感数据静态健康检查")


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inventory_servers(mcp_root: str) -> dict[str, Any]:
    try:
        root = resolve_allowed_path(mcp_root, _config())
        servers = []
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            servers.append({
                "name": child.name,
                "installable": (child / "install.sh").is_file(),
                "uninstallable": (child / "uninstall.sh").is_file(),
                "manifest": (child / "tools_manifest.json").is_file(),
                "python_server": (child / "server.py").is_file(),
            })
            if len(servers) >= int(_config()["max_servers"]):
                break
        return ok({"root": str(root), "servers": servers, "count": len(servers)})
    except (OSError, ValueError, PermissionError) as exc:
        return fail("inventory_failed", str(exc))


def _validate_manifest_file(path: Path) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [str(exc)], None
    if not isinstance(data, dict):
        return ["manifest 顶层必须是 object"], None
    if not isinstance(data.get("server"), str) or not data["server"]:
        errors.append("缺少 server")
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools 必须是非空数组")
    else:
        names = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                errors.append(f"tools[{index}] 缺少 name")
                continue
            names.append(tool["name"])
            for key in ("read_only", "deterministic", "network"):
                if not isinstance(tool.get(key), bool):
                    errors.append(f"{tool['name']} 缺少布尔字段 {key}")
        if len(names) != len(set(names)):
            errors.append("工具名重复")
    if data.get("api_key_required") is not False:
        errors.append("本地 MCP 必须显式 api_key_required=false")
    return errors, data


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def validate_manifest(path: str) -> dict[str, Any]:
    try:
        target = resolve_allowed_path(path, _config())
        manifest = target / "tools_manifest.json" if target.is_dir() else target
        errors, data = _validate_manifest_file(manifest)
        return ok({"path": str(manifest), "valid": not errors, "errors": errors, "server": data.get("server") if data else None})
    except (OSError, ValueError, PermissionError) as exc:
        return fail("manifest_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def probe_python_server(server_dir: str) -> dict[str, Any]:
    try:
        config = _config()
        root = resolve_allowed_path(server_dir, config)
        server = root / "server.py" if root.is_dir() else root
        server = server.resolve()
        trusted = [expand_path(item) for item in config.get("trusted_server_roots", [])]
        if not trusted or not any(server == item or item in server.parents for item in trusted):
            return fail("untrusted_server", f"动态探针只允许 trusted_server_roots 内的入口: {server}")
        source = server.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(server))
        tool_names = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool" for dec in node.decorator_list):
                tool_names.append(node.name)
        compile(source, str(server), "exec")
        manifest_path = server.parent / "tools_manifest.json"
        manifest_tools: list[str] = []
        manifest_errors: list[str] = []
        if manifest_path.exists():
            manifest_errors, data = _validate_manifest_file(manifest_path)
            if data and isinstance(data.get("tools"), list):
                manifest_tools = [tool["name"] for tool in data["tools"] if isinstance(tool, dict) and "name" in tool]
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        params = StdioServerParameters(command=sys.executable, args=[str(server)], env=env)
        with anyio.fail_after(int(config["probe_timeout_seconds"])):
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    listed = await session.list_tools()
        protocol_tools = [tool.model_dump(by_alias=True, exclude_none=True) for tool in listed.tools]
        protocol_names = sorted(tool["name"] for tool in protocol_tools)
        contract_errors = []
        for tool in protocol_tools:
            if not isinstance(tool.get("inputSchema"), dict):
                contract_errors.append(f"{tool['name']}: missing inputSchema")
            if not isinstance(tool.get("outputSchema"), dict):
                contract_errors.append(f"{tool['name']}: missing outputSchema")
            if not isinstance(tool.get("annotations"), dict):
                contract_errors.append(f"{tool['name']}: missing annotations")
        return ok({
            "server": str(server),
            "syntax_ok": True,
            "compile_stderr": "",
            "decorated_tools": sorted(tool_names),
            "protocol_tools": protocol_tools,
            "manifest_tools": sorted(manifest_tools),
            "manifest_errors": manifest_errors,
            "contract_errors": contract_errors,
            "tools_match": sorted(tool_names) == sorted(manifest_tools) == protocol_names,
        })
    except (OSError, ValueError, PermissionError, SyntaxError, TimeoutError) as exc:
        return fail("probe_failed", str(exc))
    except Exception as exc:
        # stdio_client 进程提前退出时 anyio 会包装为 ExceptionGroup；MCP 工具应返回结构化失败。
        return fail("probe_failed", str(exc))


def _scan(value: Any, path: str, findings: list[dict[str, str]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if SENSITIVE_KEY.search(str(key)) and child not in (None, "", "${REDACTED}", "<redacted>"):
                findings.append({"path": child_path, "reason": "sensitive_key_with_value"})
            _scan(child, child_path, findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan(child, f"{path}[{index}]", findings)
    elif isinstance(value, str) and SENSITIVE_VALUE.search(value):
        findings.append({"path": path, "reason": "sensitive_value_pattern"})


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def scan_sensitive_payload(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    _scan(payload, "", findings)
    return ok({"safe": not findings, "findings": findings})


if __name__ == "__main__":
    mcp.run()
