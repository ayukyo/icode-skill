"""ICODE 嵌入式设备只读观测 MCP。无任意命令、无部署/重启/刷写能力。"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import REMOTE_READ_ONLY_ANNOTATIONS, READ_ONLY_ANNOTATIONS, describe, fail, load_config, ok, resolve_allowed_path, run_command, sha256_file  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-device-observe"
TOOLS = ["describe_capabilities", "list_profiles", "observe_device", "collect_log_window", "compare_artifact"]
DEFAULTS = {"allowed_roots": ["$HOME"], "profiles": {}, "timeout_seconds": 10, "max_output_chars": 30000}
CHECKS = {
    "kernel": ["uname", "-a"],
    "uptime": ["uptime"],
    "disk": ["df", "-h"],
    "memory": ["free", "-m"],
    "os_release": ["cat", "/etc/os-release"],
    "device_model": ["cat", "/proc/device-tree/model"],
    "cpu_info": ["cat", "/proc/cpuinfo"],
    "network_links": ["ip", "-o", "link"],
    "processes": ["ps", "-eo", "pid,comm,args"],
    "kernel_modules": ["cat", "/proc/modules"],
}
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_DEVICE_OBSERVE_CONFIG", SERVER_DIR, DEFAULTS)


def _profile(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _config()
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or name not in profiles or not isinstance(profiles[name], dict):
        raise ValueError(f"未知设备 profile: {name}")
    profile = profiles[name]
    transport = profile.get("transport")
    if transport not in {"fixture", "ssh", "adb"}:
        raise ValueError("transport 仅支持 fixture/ssh/adb")
    return config, profile


def _safe_remote_path(value: str, profile: dict[str, Any]) -> str:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise ValueError("远端路径必须是无空格、无 .. 的绝对路径")
    roots = profile.get("allowed_remote_roots", [])
    if not isinstance(roots, list) or not any(value == root or value.startswith(str(root).rstrip("/") + "/") for root in roots):
        raise PermissionError(f"远端路径不在 profile.allowed_remote_roots: {value}")
    return value


def _transport_prefix(profile: dict[str, Any]) -> list[str]:
    transport = profile["transport"]
    if transport == "ssh":
        target = str(profile.get("target", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", target):
            raise ValueError("SSH target 非法")
        port = int(profile.get("port", 22))
        if port < 1 or port > 65535:
            raise ValueError("SSH port 非法")
        return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-p", str(port), target]
    if transport == "adb":
        serial = str(profile.get("serial", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", serial):
            raise ValueError("ADB serial 非法")
        return ["adb", "-s", serial, "shell"]
    raise ValueError("fixture 不使用远端命令")


def _remote(profile: dict[str, Any], command: list[str], config: dict[str, Any]) -> dict[str, Any]:
    # SSH/ADB 最终都可能经过设备 shell，因此参数只来自固定命令或 _safe_remote_path。
    remote_command = " ".join(shlex.quote(item) for item in command)
    return run_command(
        [*_transport_prefix(profile), remote_command],
        timeout=int(config["timeout_seconds"]),
        max_chars=int(config["max_output_chars"]),
    )


def _fixture_path(profile: dict[str, Any], remote_path: str, config: dict[str, Any]) -> Path:
    root = resolve_allowed_path(str(profile.get("root", "")), config)
    safe = _safe_remote_path(remote_path, profile)
    relative = safe.lstrip("/")
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise PermissionError("fixture 路径越界")
    if not target.exists():
        raise FileNotFoundError(str(target))
    return target


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    result = describe(SERVER_NAME, TOOLS, "命名设备 profile 的固定只读检查、日志窗口与制品哈希对比")
    result["answer"].update({"transports": ["fixture", "ssh", "adb"], "forbidden": ["arbitrary_command", "deploy", "flash", "reboot", "kill", "write"]})
    return result


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def list_profiles() -> dict[str, Any]:
    try:
        profiles = _config().get("profiles", {})
        return ok({"profiles": [{"name": name, "transport": value.get("transport")} for name, value in profiles.items() if isinstance(value, dict)]})
    except (OSError, ValueError) as exc:
        return fail("config_failed", str(exc))


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def observe_device(profile: str, checks: list[str] | None = None) -> dict[str, Any]:
    try:
        config, selected = _profile(profile)
        requested = checks or ["kernel", "uptime", "disk", "memory"]
        if not requested or any(item not in CHECKS for item in requested):
            return fail("invalid_check", f"checks 仅允许: {sorted(CHECKS)}")
        if selected["transport"] == "fixture":
            root = resolve_allowed_path(str(selected.get("root", "")), config)
            fixture = root / "device.json"
            if not fixture.is_file():
                return fail("fixture_missing", f"fixture profile 缺少 {fixture}")
            import json
            data = json.loads(fixture.read_text(encoding="utf-8"))
            return ok({"profile": profile, "transport": "fixture", "checks": {item: data.get(item) for item in requested}})
        results = {item: _remote(selected, CHECKS[item], config) for item in requested}
        return ok({"profile": profile, "transport": selected["transport"], "checks": results})
    except (OSError, ValueError, PermissionError, subprocess.TimeoutExpired) as exc:
        return fail("observe_failed", str(exc))


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def collect_log_window(profile: str, path: str, patterns: list[str] | None = None, max_lines: int = 500) -> dict[str, Any]:
    try:
        if max_lines < 1 or max_lines > 5000:
            return fail("invalid_limit", "max_lines 必须为 1..5000")
        config, selected = _profile(profile)
        safe_path = _safe_remote_path(path, selected)
        if selected["transport"] == "fixture":
            target = _fixture_path(selected, safe_path, config)
            raw_window = target.read_text(encoding="utf-8", errors="replace").splitlines()[-(max_lines + 1):]
            truncated = len(raw_window) > max_lines
            raw = raw_window[-max_lines:]
            digest = sha256_file(target)[0]
        else:
            result = _remote(selected, ["tail", "-n", str(max_lines + 1), "--", safe_path], config)
            if result["returncode"] != 0:
                return fail("remote_read_failed", result["stderr"] or result["stdout"])
            raw_window = result["stdout"].splitlines()
            truncated = len(raw_window) > max_lines or bool(result["truncated"])
            raw = raw_window[-max_lines:]
            digest = None
        compiled = [re.compile(item) for item in (patterns or [])]
        selected_lines = [line for line in raw if not compiled or any(pattern.search(line) for pattern in compiled)]
        return ok({"profile": profile, "path": safe_path, "lines": selected_lines, "line_count": len(selected_lines), "source_sha256": digest, "truncated": truncated})
    except (OSError, ValueError, PermissionError, re.error, subprocess.TimeoutExpired) as exc:
        return fail("log_failed", str(exc))


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def compare_artifact(profile: str, remote_path: str, local_path: str) -> dict[str, Any]:
    try:
        config, selected = _profile(profile)
        local = resolve_allowed_path(local_path, config)
        local_digest = sha256_file(local)[0]
        safe_path = _safe_remote_path(remote_path, selected)
        if selected["transport"] == "fixture":
            remote_digest = sha256_file(_fixture_path(selected, safe_path, config))[0]
        else:
            result = _remote(selected, ["sha256sum", "--", safe_path], config)
            if result["returncode"] != 0:
                return fail("remote_hash_failed", result["stderr"] or result["stdout"])
            match = re.match(r"^([0-9a-fA-F]{64})\b", result["stdout"].strip())
            if not match:
                return fail("invalid_remote_hash", "设备未返回合法 SHA-256")
            remote_digest = match.group(1).lower()
        return ok({"profile": profile, "remote_path": safe_path, "local_path": str(local), "remote_sha256": remote_digest, "local_sha256": local_digest, "matches": remote_digest == local_digest})
    except (OSError, ValueError, PermissionError, subprocess.TimeoutExpired) as exc:
        return fail("compare_failed", str(exc))


if __name__ == "__main__":
    mcp.run()
