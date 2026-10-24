"""Bounded, offline observations; never spawn or repair a registered command.

Only classifications and field names leave this module. Config values, arguments,
environment values and parse exception text can contain credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

MAX_BYTES = 4 * 1024 * 1024
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMPARE_KEYS = ("command", "args", "env", "cwd")
# A portable early warning, not a replacement for the full distribution checker.
CORE_RESOURCES = (
    "SKILL.md", "mcp/_lib/platform_entry.py", "mcp/_lib/claude_registry.py",
    "mcp/_lib/client_registry.py", "mcp/_lib/environment_diagnostics.py",
    "mcp/icode-mcp-health/server.py", "mcp/icode-mcp-health/tools_manifest.json",
    "mcp/icode-mcp-policy/policy.json", "tools/mcp_doctor.py",
    "tools/icode_delivery.py", "steps/status.md", "steps/verify.md",
    "steps/install.md", "steps/06_audit.md", "scripts/sync-to-global.sh",
)


def _remote_path(value, windows):
    return windows and value.lstrip('"').startswith(("\\\\", "//"))


def _search_command(command, search_path, windows, resolve_path=None):
    directories = search_path.split(";" if windows else os.pathsep)
    # Python's Windows which may also search cwd before the supplied PATH.
    checks = ([str(Path.cwd())] if windows else []) + directories
    for directory in checks:
        if _remote_path(directory, windows):
            return None, "network_path_uninspected"
        if resolve_path is not None:
            try:
                resolve_path(Path(directory or os.curdir).expanduser().absolute())
            except (OSError, ValueError):
                return None, "launch_path_uninspected"
    located = shutil.which(command, path=search_path)
    return located, "observed"


def runtime_inventory():
    """Observe this CLI's PATH without traversing a remote search directory."""
    rows = {}
    for name in ("python", "py", "git", "rg", "node", "wsl"):
        located, state = _search_command(name, os.environ.get("PATH", ""), os.name == "nt")
        rows[name] = {"available": bool(located) if state == "observed" else None, "status": state}
    return rows


def _read_bytes(path, resolve_path=None):
    path = Path(path).expanduser().absolute()
    if _remote_path(str(path), os.name == "nt"):
        raise ValueError("network_input_uninspected")
    if resolve_path is not None:
        path = resolve_path(path)
    # Refuse links and special files before opening. No runtime/config recursion.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("linked_input")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("non_regular_input")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("input_limit")
    return raw


def _object(path, resolve_path=None):
    value = json.loads(_read_bytes(path, resolve_path).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("object_required")
    return value


def _foreign(value, windows):
    if windows:
        return value.startswith("/") and not value.startswith("//") and "/" in value[1:]
    return bool(re.match(r"^[A-Za-z]:[\\/]", value)) or value.startswith("\\\\")


def _entry_status(entry, windows, resolve_path=None):
    if not isinstance(entry, dict):
        return "invalid_entry"
    if entry.get("disabled") is True:
        return "disabled"
    command = entry.get("command")
    if not command and isinstance(entry.get("url"), str) and entry["url"]:
        return "remote_unprobed"
    args, env, cwd = entry.get("args", []), entry.get("env", {}), entry.get("cwd")
    if (not isinstance(command, str) or not command.strip()
            or not isinstance(args, list) or not all(isinstance(a, str) for a in args)
            or not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
            or (cwd is not None and not isinstance(cwd, str))):
        return "invalid_entry"
    if any("\x00" in value for value in [command, *args, *([cwd] if cwd else [])]):
        return "invalid_entry"
    if any(_foreign(value, windows) for value in [command, *args, *([cwd] if cwd else [])]):
        return "foreign_os"
    # Do not access UNC/device targets in an offline diagnostic.
    if any(_remote_path(value, windows) for value in [command, *args, *([cwd] if cwd else [])]):
        return "network_path_uninspected"
    if cwd:
        try:
            directory = Path(cwd).expanduser().absolute()
            if resolve_path is not None:
                directory = resolve_path(directory)
        except (OSError, ValueError):
            return "launch_path_uninspected"
        if not directory.is_dir():
            return "cwd_not_found"
    if "/" in command or "\\" in command or re.match(r"^[A-Za-z]:", command):
        path = Path(command).expanduser()
        if not path.is_absolute():
            # Relative command paths depend on the host's launch directory.
            if not cwd:
                return "relative_command_unresolved"
            path = Path(cwd) / path
    else:
        launch_env = {k.upper(): v for k, v in env.items()} if windows else env
        located, state = _search_command(command, launch_env.get("PATH", os.environ.get("PATH", "")), windows, resolve_path)
        if state != "observed":
            return state
        if not located:
            return "command_not_found"
        path = Path(located)
    try:
        path = path.absolute()
        if resolve_path is not None:
            path = resolve_path(path)
    except (OSError, ValueError):
        return "launch_path_uninspected"
    if not path.is_file():
        return "command_not_found"
    if windows:
        try:
            with path.open("rb") as stream:
                if stream.read(128).startswith(b"#!"):
                    return "wrapper_shebang"
        except OSError:
            return "command_unreadable"
    if not windows and not os.access(path, os.X_OK):
        return "command_not_executable"
    for argument in args:
        # Only explicit local runner files; package names, switches, URLs and
        # filesystem-server allowed roots are not launch entrypoints.
        candidate = Path(argument)
        if candidate.is_absolute() and candidate.suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".cmd", ".exe"}:
            try:
                if resolve_path is not None:
                    candidate = resolve_path(candidate)
            except (OSError, ValueError):
                return "launch_path_uninspected"
            if not candidate.is_file():
                return "stale_entrypoint"
    return "static_ok"


def diagnose_registration(host_config, entries_dir=None, *, system=None, max_servers=100, resolve_path=None):
    """Read a JSON mcpServers config and optional exported entries under one policy.

    static_ok only covers the launch command and platform shape. It does not
    certify packages, remote services, a handshake or the current session.
    """
    report = {"schema_version": 1, "read_only": True, "status": "observed",
              "config": str(host_config), "servers": [],
              "session": {"status": "unverified", "reason": "static_check_only"},
              "summary": {"total": 0, "problems": 0}}
    try:
        config = _object(host_config, resolve_path)
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("servers_object_required")
    except (OSError, ValueError, UnicodeError):
        report.update(status="invalid_config", summary={"total": 0, "problems": 1})
        return report
    exports = {}
    try:
        if entries_dir is not None:
            directory = Path(entries_dir).expanduser().absolute()
            if _remote_path(str(entries_dir), (system or os.name).lower() in {"windows", "win32", "nt"}):
                report.update(status="entries_unreadable", summary={"total": 0, "problems": 1})
                return report
            if resolve_path is not None:
                directory = resolve_path(directory)
            if directory.exists():
                if directory.is_symlink() or not directory.is_dir():
                    raise ValueError("invalid_entries_directory")
                # Bound enumeration too, before opening any exported entry.
                for path in directory.glob("*.json"):
                    exports[path.stem] = path
                    if len(exports) > max_servers:
                        raise ValueError("inventory_limit")
        names = sorted(set(servers) | set(exports))
        if len(names) > max_servers:
            raise ValueError("inventory_limit")
    except (OSError, ValueError):
        report.update(status="limit_exceeded", summary={"total": 0, "problems": 1})
        return report
    windows = (system or os.name).lower() in {"windows", "win32", "nt"}
    for name in names:
        row = {"name": name if SERVER_NAME.fullmatch(name) else "<invalid-name>",
               "status": "missing", "drift": False, "different_fields": [], "expected_status": "absent"}
        if not SERVER_NAME.fullmatch(name):
            row["status"] = "invalid_name"
        else:
            if name in servers:
                row["status"] = _entry_status(servers[name], windows, resolve_path)
            if name in exports:
                try:
                    expected = _object(exports[name], resolve_path)
                    row["expected_status"] = _entry_status(expected, windows, resolve_path)
                    actual = servers.get(name)
                    if isinstance(actual, dict):
                        row["different_fields"] = [k for k in COMPARE_KEYS if actual.get(k) != expected.get(k)]
                        row["drift"] = bool(row["different_fields"])
                except (OSError, ValueError, UnicodeError):
                    row["expected_status"] = "unreadable"
        report["servers"].append(row)
    neutral = {"static_ok", "disabled", "remote_unprobed"}
    report["summary"] = {"total": len(names), "problems": sum(
        row["status"] not in neutral or row["drift"]
        or row["expected_status"] not in neutral | {"absent"} for row in report["servers"])}
    return report


def compare_installation(source, installed):
    """Compare only declared core files, without reading user configurations."""
    report = {"scope": "core_runtime_resources", "status": "match", "source": str(source), "installed": str(installed),
              "checked_files": 0, "changed": [], "missing": [], "unreadable": []}
    for relative in CORE_RESOURCES:
        try:
            wanted = hashlib.sha256(_read_bytes(Path(source) / relative)).hexdigest()
        except (OSError, ValueError):
            report["unreadable"].append({"path": relative, "side": "source"})
            continue
        try:
            actual = hashlib.sha256(_read_bytes(Path(installed) / relative)).hexdigest()
        except FileNotFoundError:
            report["missing"].append(relative)
            continue
        except (OSError, ValueError):
            report["unreadable"].append({"path": relative, "side": "installed"})
            continue
        report["checked_files"] += 1
        if actual != wanted:
            report["changed"].append({"path": relative, "source_sha256": wanted, "installed_sha256": actual})
    if any(report[key] for key in ("changed", "missing", "unreadable")):
        report["status"] = "drift"
    return report


def summarize_gates(out_dir):
    """Surface recorded degradation; this observation never changes a gate verdict."""
    root = Path(out_dir)
    report = {"schema_version": 1, "read_only": True, "status": "untracked",
              "thinking": {"total": 0, "success": 0, "degraded": 0, "blocked": 0},
              "mcp": {}, "errors": []}
    trace = root / ".thinking_gate_trace.jsonl"
    if trace.exists():
        report["status"] = "observed"
        try:
            raw = _read_bytes(trace).decode("utf-8-sig")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if (not isinstance(row, dict) or not isinstance(row.get("result"), str)
                        or row["result"] not in {"success", "degraded", "blocked"}):
                    raise ValueError("invalid_trace")
                report["thinking"]["total"] += 1
                report["thinking"][row["result"]] += 1
        except (OSError, ValueError, UnicodeError):
            report["errors"].append("thinking_trace_unreadable")
    mcp_report = root / ".mcp_gate_report.json"
    if mcp_report.exists():
        if report["status"] == "untracked":
            report["status"] = "observed"
        try:
            data = _object(mcp_report)
            for key in ("unavailable_before_call", "degraded_after_attempt", "missing_gate", "schema_errors"):
                value = data.get(key, 0)
                if type(value) is not int or value < 0:
                    raise ValueError("invalid_counter")
                report["mcp"][key] = value
        except (OSError, ValueError, UnicodeError):
            report["errors"].append("mcp_report_unreadable")
    if report["thinking"]["degraded"] or report["thinking"]["blocked"] or any(report["mcp"].values()):
        report["status"] = "attention"
    if report["errors"]:
        report["status"] = "partial"
    if report["status"] in {"attention", "partial"}:
        report["next_action"] = "Run tools/mcp_doctor.py --json; inspect the original gate trace locally; reload the host session after authorized repair."
    return report
