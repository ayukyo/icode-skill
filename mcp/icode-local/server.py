"""Same-process local MCP entry point with lazy legacy module loading."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import (READ_ONLY_ANNOTATIONS, MANAGED_WRITE_ANNOTATIONS,
                              fail, module_config_profile, ok)
from mcp.server.fastmcp import FastMCP

SERVER_NAME = "icode-local"
MEMBERS = {'workspace': 'icode-workspace', 'evidence': 'icode-evidence', 'index': 'icode-local-index', 'health': 'icode-mcp-health', 'policy': 'icode-mcp-policy'}
_MODULES: dict[str, ModuleType] = {}
mcp = FastMCP(SERVER_NAME)


def _module(prefix: str) -> ModuleType:
    # Each legacy module keeps its original SERVER_DIR, defaults and FastMCP;
    # importing it never executes its guarded mcp.run() entry point.
    if prefix not in _MODULES:
        path = SERVER_DIR.parent / MEMBERS[prefix] / "server.py"
        spec = importlib.util.spec_from_file_location("_icode_local_" + prefix, path)
        if spec is None or spec.loader is None:
            raise ImportError("local MCP implementation is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[prefix] = module
    return _MODULES[prefix]


def _profile() -> Path:
    return Path(os.path.expandvars(os.path.expanduser(os.environ.get("ICODE_LOCAL_CONFIG", str(SERVER_DIR / "config.json")))))


def _invoke(prefix: str, tool: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    module = _module(prefix)
    with module_config_profile(_profile(), required="ICODE_LOCAL_CONFIG" in os.environ):
        # These two legacy tools never read configuration themselves. Validate
        # their profile here; other tools retain their existing error codes.
        if (prefix, tool) in {("health", "scan_sensitive_payload"), ("policy", "describe_capabilities")}:
            try:
                module._config()
            except (OSError, ValueError):
                return fail("config_failed", "Local module configuration is unavailable or invalid.")
        return getattr(module, tool)(*args, **kwargs)


async def _invoke_async(prefix: str, tool: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    module = _module(prefix)
    with module_config_profile(_profile(), required="ICODE_LOCAL_CONFIG" in os.environ):
        return await getattr(module, tool)(*args, **kwargs)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    """Static capabilities without imports, scans, databases or network access."""
    return ok({"server": SERVER_NAME, "members": MEMBERS,
               "tools": TOOLS, "api_key_required": False,
               "lazy_modules": True, "network": False,
               "decision_authority": "none",
               "configuration": "module_configs references independent legacy configuration paths"})


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_describe_capabilities() -> dict[str, Any]:
    return _invoke('workspace', 'describe_capabilities')


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_inspect_workspace(path: str) -> dict[str, Any]:
    return _invoke('workspace', 'inspect_workspace', path=path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_resolve_project(path: str, max_depth: int=2) -> dict[str, Any]:
    '从当前目录或唯一嵌套仓解析工程根；歧义和预算耗尽均 fail-closed。'
    return _invoke('workspace', 'resolve_project', path=path, max_depth=max_depth)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_inspect_project_profile(path: str, max_depth: int=2) -> dict[str, Any]:
    '静态生成仓库规模、平台层、构建入口风险和扫描策略；不运行仓内命令。'
    return _invoke('workspace', 'inspect_project_profile', path=path, max_depth=max_depth)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_inspect_repo_matrix(root: str, max_depth: int=4) -> dict[str, Any]:
    return _invoke('workspace', 'inspect_repo_matrix', root=root, max_depth=max_depth)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_inspect_build_inputs(path: str) -> dict[str, Any]:
    return _invoke('workspace', 'inspect_build_inputs', path=path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def workspace_inspect_artifact(path: str, include_symbols: bool=False, include_strings: bool=False) -> dict[str, Any]:
    return _invoke('workspace', 'inspect_artifact', path=path, include_symbols=include_symbols, include_strings=include_strings)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evidence_describe_capabilities() -> dict[str, Any]:
    return _invoke('evidence', 'describe_capabilities')


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evidence_inspect_file(path: str) -> dict[str, Any]:
    return _invoke('evidence', 'inspect_file', path=path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evidence_read_evidence(path: str, start_line: int=1, max_lines: int=200) -> dict[str, Any]:
    return _invoke('evidence', 'read_evidence', path=path, start_line=start_line, max_lines=max_lines)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evidence_verify_digest(path: str, sha256: str) -> dict[str, Any]:
    return _invoke('evidence', 'verify_digest', path=path, sha256=sha256)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evidence_build_timeline(paths: list[str], timestamp_regex: str='(?P<timestamp>\\d{4}-\\d{2}-\\d{2}[T ][0-9:.+-]+)', max_events: int=1000) -> dict[str, Any]:
    return _invoke('evidence', 'build_timeline', paths=paths, timestamp_regex=timestamp_regex, max_events=max_events)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def evidence_inspect_corpus(root: str, extensions: list[str] | None=None, max_files: int=500) -> dict[str, Any]:
    return _invoke('evidence', 'inspect_corpus', root=root, extensions=extensions, max_files=max_files)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def index_describe_capabilities() -> dict[str, Any]:
    return _invoke('index', 'describe_capabilities')


@mcp.tool(annotations=MANAGED_WRITE_ANNOTATIONS)
def index_build_index(root: str, max_files: int=2000) -> dict[str, Any]:
    return _invoke('index', 'build_index', root=root, max_files=max_files)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def index_index_status(root: str) -> dict[str, Any]:
    return _invoke('index', 'index_status', root=root)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def index_query_index(root: str, query: str, limit: int=20) -> dict[str, Any]:
    return _invoke('index', 'query_index', root=root, query=query, limit=limit)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def health_describe_capabilities() -> dict[str, Any]:
    return _invoke('health', 'describe_capabilities')


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def health_inventory_servers(mcp_root: str) -> dict[str, Any]:
    return _invoke('health', 'inventory_servers', mcp_root=mcp_root)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def health_diagnose_registration(host_config: str, entries_dir: str | None=None) -> dict[str, Any]:
    'Offline JSON/TOML registration observations; never execute or repair entries.'
    return _invoke('health', 'diagnose_registration', host_config=host_config, entries_dir=entries_dir)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def health_validate_manifest(path: str) -> dict[str, Any]:
    return _invoke('health', 'validate_manifest', path=path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def health_probe_python_server(server_dir: str) -> dict[str, Any]:
    return await _invoke_async('health', 'probe_python_server', server_dir=server_dir)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def health_scan_sensitive_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _invoke('health', 'scan_sensitive_payload', payload=payload)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def policy_describe_capabilities() -> dict[str, Any]:
    return _invoke('policy', 'describe_capabilities')


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def policy_validate_policy() -> dict[str, Any]:
    return _invoke('policy', 'validate_policy')


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def policy_list_step_policy(step: str) -> dict[str, Any]:
    return _invoke('policy', 'list_step_policy', step=step)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def policy_evaluate_call(step: str, server: str, tool: str, operation: str='read') -> dict[str, Any]:
    return _invoke('policy', 'evaluate_call', step=step, server=server, tool=tool, operation=operation)


TOOLS = ['describe_capabilities', 'workspace_describe_capabilities', 'workspace_inspect_workspace', 'workspace_resolve_project', 'workspace_inspect_project_profile', 'workspace_inspect_repo_matrix', 'workspace_inspect_build_inputs', 'workspace_inspect_artifact', 'evidence_describe_capabilities', 'evidence_inspect_file', 'evidence_read_evidence', 'evidence_verify_digest', 'evidence_build_timeline', 'evidence_inspect_corpus', 'index_describe_capabilities', 'index_build_index', 'index_index_status', 'index_query_index', 'health_describe_capabilities', 'health_inventory_servers', 'health_diagnose_registration', 'health_validate_manifest', 'health_probe_python_server', 'health_scan_sensitive_payload', 'policy_describe_capabilities', 'policy_validate_policy', 'policy_list_step_policy', 'policy_evaluate_call']

# Prefixing a transport name must not rename the original input model schema.
# FastMCP exposes no decorator argument for the generated Arguments model title.
for _tool in mcp._tool_manager.list_tools():
    if _tool.name != "describe_capabilities":
        _tool.parameters["title"] = _tool.name.split("_", 1)[1] + "Arguments"
        if _tool.output_schema is not None:
            _tool.output_schema["title"] = _tool.name.split("_", 1)[1] + "DictOutput"

if __name__ == "__main__":
    mcp.run()
