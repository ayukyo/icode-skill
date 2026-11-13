"""Register locked Node packages without copying one host's user settings.

Only recognized literal default npm launch chains are migrated. Saved receipts
contain digests and package identity, never arguments or environment values.
"""
import argparse
import copy
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import claude_registry
import client_registry

SERVERS = ("context7", "memory", "playwright", "sequential-thinking")
HOSTS = ("claude", "codex", "codebuddy", "workbuddy")


def _basename(value):
    return ntpath.basename(value).lower()


def legacy_suffix(entry, spec):
    """Return CLI suffix only for literal npx default-package invocations."""
    if not isinstance(entry, dict):
        return None
    command, args = entry.get("command"), entry.get("args", [])
    if not isinstance(command, str) or not isinstance(args, list) or not all(isinstance(x, str) for x in args):
        return None
    # cmd /c can execute shell metacharacters even when supplied as arguments.
    wrapped = _basename(command) in ("cmd", "cmd.exe")
    if wrapped:
        if len(args) < 3 or args[0].lower() != "/c" or any(re.search(r'[&|<>^%\r\n"]', x) for x in args[1:]):
            return None
        command, args = args[1], args[2:]
    if _basename(command) in ("node", "node.exe"):
        if not args or _basename(args[0]) != "npx-cli.js":
            return None
        args = args[1:]
    elif wrapped or _basename(command) not in ("npx", "npx.cmd", "npx.exe"):
        return None
    if len(args) < 2 or args[0] not in ("-y", "--yes"):
        return None
    if args[1] not in (spec["package"], spec["package"] + "@" + spec["version"]):
        return None
    return list(args[2:])


def codex_representable(node):
    """Official add resets metadata: only its default envelope is safe."""
    defaults = {"name": node.get("name"), "enabled": True, "disabled_reason": None,
                "startup_timeout_sec": None, "tool_timeout_sec": None,
                "enabled_tools": None, "disabled_tools": None}
    if any(key not in defaults or value != defaults[key] for key, value in node.items() if key != "transport"):
        return False
    transport = node.get("transport", {})
    allowed = {"type", "command", "args", "env", "env_vars", "cwd"}
    return (isinstance(transport, dict) and not (set(transport) - allowed)
            and transport.get("type", "stdio") == "stdio"
            and not transport.get("env_vars") and transport.get("cwd") is None)


def _packages():
    import node_packages
    return node_packages


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _config(host):
    return {"claude": claude_registry.CLAUDE_JSON, "codebuddy": client_registry.CODEBUDDY_CFG,
            "workbuddy": client_registry.WORKBUDDY_CFG}[host]


def _read_config(path):
    claude_registry._check_registry_path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError("cannot read host configuration") from exc
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise RuntimeError("invalid host configuration")
    return data


def _actual(host, name):
    # Reuse the strict official-CLI reader, which distinguishes absent/error.
    if host == "codex":
        import install_reconcile
        return install_reconcile._actual(host, name)
    value = _read_config(_config(host)).get("mcpServers", {}).get(name)
    if value is not None and not isinstance(value, dict):
        raise RuntimeError("invalid host configuration node")
    return value


def _launch(host, actual):
    return actual.get("transport", {}) if host == "codex" and actual else actual


def _receipt_path(server):
    if server not in SERVERS:
        raise RuntimeError("unknown managed Node server")
    return _packages().default_runtime_root() / "registrations" / f"{server}.json"


def _read_receipt(server):
    path = _receipt_path(server)
    data = _read_config(path) if path.exists() else {"version": 1, "server": server, "hosts": {}}
    if data.get("version") != 1 or data.get("server") != server or not isinstance(data.get("hosts"), dict):
        raise RuntimeError("invalid managed Node registration receipt")
    for host, value in data["hosts"].items():
        if host not in HOSTS:
            raise RuntimeError("invalid managed Node registration ownership")
        _validate_record(value)
    return data


def _validate_record(value, *, nested=False):
    if not isinstance(value, dict) or value.get("status") not in (("migrated",) if nested else ("migrated", "retained", "pending")):
        raise RuntimeError("invalid managed Node registration ownership")
    fields = {"digest", "status", "before_digest"}
    if value["status"] in ("migrated", "pending"):
        fields.update(("runtime_root", "lock_sha256", "package", "package_version", "node"))
        if "entry" in value or value["status"] == "pending":
            fields.add("entry")
    if value["status"] == "pending":
        fields.add("source")
        if value.get("source") not in ("fresh", "legacy", "adopt", "managed"):
            raise RuntimeError("invalid managed Node pending source")
        if "old_record" in value:
            fields.add("old_record")
            _validate_record(value["old_record"], nested=True)
    if set(value) != fields or any(not isinstance(value[key], str) for key in fields - {"old_record"}):
        raise RuntimeError("invalid managed Node registration ownership")
    if any(not re.fullmatch(r"[0-9a-f]{64}", value[key]) for key in ("digest", "before_digest")):
        raise RuntimeError("invalid managed Node registration ownership")


def _write_receipt(server, data):
    path = _receipt_path(server)
    claude_registry._check_registry_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    claude_registry._write_cfg(path, data)


def _write_host(host, name, expected, *, before_digest):
    if host == "codex":
        import install_reconcile
        if _digest(_actual(host, name)) != before_digest:
            raise RuntimeError("concurrent host configuration change")
        command = ["add", name]
        for key, value in (expected.get("env") or {}).items():
            command += ["--env", f"{key}={value}"]
        command += ["--", expected["command"], *expected.get("args", [])]
        result = install_reconcile._codex_command(command)
        if result.returncode:
            raise RuntimeError("Codex CLI registration failed")
        return
    path = _config(host)
    data = _read_config(path)
    if _digest(data.get("mcpServers", {}).get(name)) != before_digest:
        raise RuntimeError("concurrent host configuration change")
    data.setdefault("mcpServers", {})[name] = expected
    # Existing writer provides private same-directory staging + fsync/replace.
    claude_registry._write_cfg(path, data)


def _host_selection(hosts):
    if hosts is None:
        raw = os.environ.get("ICODE_MCP_SELECTED_HOSTS")
        hosts = raw.split(",") if raw is not None else ["claude"]
    selected = list(dict.fromkeys(host for host in hosts if host))
    if not selected or any(host not in HOSTS for host in selected):
        raise RuntimeError("invalid frozen host selection")
    return selected


def _adopt_digest(host, server, actual):
    """An explicit one-shot authorization is bound to the complete old node."""
    token = os.environ.get("ICODE_MCP_NODE_ADOPT", "")
    if not token:
        return False
    if not re.fullmatch(r"(?:claude|codex|codebuddy|workbuddy)/[a-z0-9-]+:[0-9a-f]{64}", token):
        raise RuntimeError("invalid explicit Node adoption authorization")
    target, digest = token.split(":", 1)
    if target != f"{host}/{server}":
        return False
    if actual is None or _digest(actual) != digest:
        raise RuntimeError("explicit Node adoption digest conflict")
    return True


def _verify_identity(server, record, entry):
    return _packages().verify_owned_runtime(server, record["runtime_root"],
        package=record["package"], version=record["package_version"],
        lock_sha256=record["lock_sha256"], entry=entry)


def _verify_migrated(server, host, actual, record, *, require_node, require_current):
    if actual is None or _digest(actual) != record["digest"]:
        return False
    launch = _launch(host, actual)
    if not launch.get("args"):
        return False
    entry = record.get("entry", launch["args"][0])
    runtime = _verify_identity(server, record, entry)
    node = Path(record["node"])
    claude_registry._check_registry_path(node)
    if (not node.is_absolute() or (require_node and not node.is_file()) or launch.get("command") != str(node)
            or launch["args"][0] != runtime["entry"]):
        return False
    if require_current:
        spec = _packages().load_spec(server)
        if (record["package"] != spec["package"] or record["package_version"] != spec["version"]
                or record["lock_sha256"] != spec.get("lock_sha256", record["lock_sha256"])):
            return False
    return True


def verify_host(server, host, actual=None, *, require_node=True, require_current=True):
    """Verify host-specific digest, including deliberately retained custom nodes."""
    if server not in SERVERS or host not in HOSTS:
        return False
    receipt = _read_receipt(server)
    record = receipt["hosts"].get(host)
    if record is None or record["status"] == "pending":
        return False
    if actual is None:
        actual = _actual(host, server)
    if actual is None or _digest(actual) != record["digest"]:
        return False
    if record["status"] == "migrated":
        return _verify_migrated(server, host, actual, record, require_node=require_node, require_current=require_current)
    return True


def _completed(record):
    return {key: ("migrated" if key == "status" else value) for key, value in record.items()
            if key not in ("source", "old_record")}


def _pending_state(server, host, actual, record):
    # Validate the intended immutable runtime even if configuration was never
    # written. A durable intent is recovery evidence, not successful delivery.
    _verify_identity(server, record, record["entry"])
    digest = _digest(actual)
    if digest == record["digest"]:
        if _verify_migrated(server, host, actual, record, require_node=False, require_current=False):
            return "intended"
    elif digest == record["before_digest"]:
        old = record.get("old_record")
        if record["source"] != "managed" or (old and _verify_migrated(server, host, actual, old, require_node=False, require_current=False)):
            return "old"
    raise RuntimeError(f"managed Node pending ownership conflict: {host}/{server}")


def can_resume_host(server, host, actual):
    """Read-only ownership preflight; pending never passes verify_host/finish."""
    record = _read_receipt(server)["hosts"].get(host)
    if record is None or record["status"] == "retained":
        return True
    if record["status"] == "pending":
        _pending_state(server, host, actual, record)
        return True
    return _verify_migrated(server, host, actual, record, require_node=False, require_current=False)


def _expected_actual(host, server, launch):
    if host != "codex":
        return launch
    # Official CLI defaults were verified in an isolated CODEX_HOME. Include
    # the complete envelope in the intent digest, not only launch fields.
    return {"name": server, "enabled": True, "disabled_reason": None,
        "transport": {"type": "stdio", "command": launch["command"], "args": launch.get("args", []),
                      "env": launch.get("env") or None, "env_vars": [], "cwd": None},
        "enabled_tools": None, "disabled_tools": None, "startup_timeout_sec": None, "tool_timeout_sec": None}


def _host_context(old_node):
    launch = (old_node.get("transport") or old_node) if isinstance(old_node, dict) else {}
    explicit = launch.get("env") or {}
    if not isinstance(explicit, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in explicit.items()):
        raise RuntimeError("invalid old host environment")
    env = dict(os.environ)
    for key, value in explicit.items():
        if os.name == "nt":
            for existing in list(env):
                if existing.lower() == key.lower():
                    del env[existing]
        env[key] = value
    cwd = launch.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir()):
        raise RuntimeError("cannot prove original host cwd")
    return launch, env, cwd


def _old_npm(node_path, launch, env, cwd):
    command, args = launch.get("command", ""), launch.get("args", [])
    if _basename(command) in ("cmd", "cmd.exe"):
        if len(args) < 3 or args[0].lower() != "/c":
            raise RuntimeError("cannot prove original npm executable")
        command, args = args[1], args[2:]
    def executable(value):
        path = Path(value)
        if path.is_absolute():
            resolved = path
        elif path.name != value:
            resolved = Path(cwd) / path
        else:
            found = shutil.which(value, path=next((v for k, v in env.items() if k.lower() == "path"), ""))
            if not found:
                raise RuntimeError("cannot prove original npm executable")
            resolved = Path(found)
        claude_registry._check_registry_path(resolved.parent)
        if not resolved.is_file():
            raise RuntimeError("cannot prove original npm executable")
        return resolved.resolve()
    if _basename(command) in ("node", "node.exe") and args and _basename(args[0]) == "npx-cli.js":
        old_node = executable(command)
        npx = executable(args[0])
    elif _basename(command) in ("npx", "npx.cmd", "npx.exe"):
        npx = executable(command)
        old_node = executable("node.exe" if os.name == "nt" else "node")
        # npm's official Unix npx symlink resolves to its npx-cli.js. Opaque
        # shell/cmd wrappers cannot establish which npm implementation ran.
        if npx.name != "npx-cli.js":
            raise RuntimeError("cannot prove original npm executable")
    else:
        raise RuntimeError("cannot prove original npm executable")
    npm = npx.with_name("npm-cli.js")
    claude_registry._check_registry_path(npm)
    if not npm.is_file():
        raise RuntimeError("cannot prove original npm executable")
    return old_node, npm


def _npm_cache(node_path, old_node=None):
    launch, env, cwd = _host_context(old_node)
    if not cwd:
        raise RuntimeError("cannot prove original host cwd; explicit CLI evidence required")
    # npx can prefer a local package over the cache. Do not guess its dispatch.
    for directory in (Path(cwd), *Path(cwd).parents):
        if (directory / "node_modules/@modelcontextprotocol/server-memory").exists():
            raise RuntimeError("local memory package may override npx cache")
    node, npm = _old_npm(node_path, launch, env, cwd)
    try:
        result = subprocess.run([str(node), str(npm), "config", "get", "cache"],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="strict", timeout=30,
                                env=env, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise RuntimeError("cannot locate original npm cache") from exc
    if result.returncode or not result.stdout.strip() or len(result.stdout.strip().splitlines()) != 1:
        raise RuntimeError("cannot locate original npm cache")
    cache = Path(result.stdout.strip())
    if not cache.is_absolute():
        raise RuntimeError("npm cache must be absolute")
    claude_registry._check_registry_path(cache)
    return cache


def _expanded_host_path(node_path, old_node, value):
    _, env, cwd = _host_context(old_node)
    try:
        # Match the locked CLI's expandHome exactly. Node path.join keeps the
        # home prefix for ~//foo; Python Path / would reset to a filesystem root.
        script = "process.stdout.write(require('node:path').join(require('node:os').homedir(), process.argv[1].slice(1)))"
        result = subprocess.run([str(node_path), "-e", script, "--", value],
                                env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="strict", timeout=30)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise RuntimeError("cannot prove original host home") from exc
    home = Path(result.stdout) if result.returncode == 0 else Path()
    if not home.is_absolute():
        raise RuntimeError("cannot prove original host home")
    return home


def _sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _memory_candidate(directory, spec, expected, trusted_digest):
    """A cache path is evidence only when its lock, bin and CLI all agree."""
    package_key = "node_modules/" + spec["package"]
    claude_registry._check_registry_path(directory)
    lock = _read_config(directory / "package-lock.json")
    item = lock.get("packages", {}).get(package_key, {})
    if any(not expected.get(key) or item.get(key) != expected[key]
           for key in ("version", "integrity", "resolved")):
        return None
    package = directory / package_key
    metadata = _read_config(package / "package.json")
    if metadata.get("name") != spec["package"] or metadata.get("version") != spec["version"]:
        return None
    bins = metadata.get("bin", {})
    paths = list(bins.values()) if isinstance(bins, dict) else [bins]
    entry = package / spec["bin"]
    claude_registry._check_registry_path(entry)
    return entry if spec["bin"] in paths and entry.is_file() and _sha_file(entry) == trusted_digest else None


def _memory_cli_hint(host, old_node, spec, expected, trusted_digest):
    hint_path = os.environ.get("ICODE_MCP_MEMORY_CLI_HINT")
    if not hint_path:
        return None
    if not Path(hint_path).is_absolute() or host not in HOSTS:
        raise RuntimeError("invalid memory CLI hint")
    hints = _read_config(Path(hint_path))
    if set(hints) != {"version", "hosts"} or hints.get("version") != 1 or not isinstance(hints.get("hosts"), dict):
        raise RuntimeError("invalid memory CLI hint")
    record = hints["hosts"].get(host)
    if (not isinstance(record, dict) or set(record) != {"before_digest", "cli"}
            or record["before_digest"] != _digest(old_node) or not isinstance(record["cli"], str)):
        raise RuntimeError("memory CLI hint snapshot conflict")
    cli = Path(record["cli"])
    if not cli.is_absolute():
        raise RuntimeError("invalid memory CLI hint")
    directory = cli
    for _ in (Path("node_modules") / spec["package"] / spec["bin"]).parts:
        directory = directory.parent
    if directory.parent.name != "_npx":
        raise RuntimeError("memory CLI hint is not an npm cache entry")
    try:
        proven = _memory_candidate(directory, spec, expected, trusted_digest)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise RuntimeError("cannot prove memory CLI hint") from exc
    if proven != cli:
        raise RuntimeError("cannot prove memory CLI hint")
    return cli


def _find_old_memory_cli(node_path, spec, managed_entry, old_node=None, *, host=None):
    """Inspect metadata/CLI only; a one-shot process hint can prove origin."""
    package_key = "node_modules/" + spec["package"]
    try:
        expected = _read_config(Path(spec["root"]) / "package-lock.json")["packages"][package_key]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("missing locked memory package metadata") from exc
    if managed_entry is None:
        raise RuntimeError("cannot prove old memory CLI semantics")
    claude_registry._check_registry_path(Path(managed_entry))
    trusted_digest = _sha_file(managed_entry)
    hinted = _memory_cli_hint(host, old_node, spec, expected, trusted_digest)
    if hinted is not None:
        return hinted
    cache = _npm_cache(node_path, old_node) / "_npx"
    candidates = []
    if cache.exists():
        claude_registry._check_registry_path(cache)
        for directory in cache.iterdir():
            if not directory.is_dir():
                continue
            try:
                entry = _memory_candidate(directory, spec, expected, trusted_digest)
                if entry is not None:
                    candidates.append(entry)
            except (OSError, RuntimeError, ValueError, TypeError):
                continue
    if len(candidates) != 1:
        raise RuntimeError("ambiguous old memory cache" if candidates else "cannot prove old memory cache")
    return candidates[0]


def memory_path(value, old_node, spec, node_path, managed_entry=None, *, host=None):
    """Keep Node's old dist-relative path semantics, based on metadata only.

    False is the explicit fresh-install marker; None still means an existing
    legacy launch whose dist directory must be proved from its npm cache.
    """
    if value is not None and not isinstance(value, str):
        raise RuntimeError("invalid memory path configuration")
    if value and (os.path.isabs(value) or ntpath.isabs(value)):
        return value
    if value == "~" or (value and value.startswith("~/")):
        return str(_expanded_host_path(node_path, old_node, value))
    if old_node is False:
        if value:
            raise RuntimeError("cannot prove original relative memory path for fresh install")
        data_dir = _packages().default_runtime_root() / "data" / "memory"
        claude_registry._check_registry_path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        return str(data_dir / "memory.jsonl")
    old_cli = _find_old_memory_cli(node_path, spec, managed_entry, old_node, host=host)
    if value:
        return os.path.abspath(old_cli.parent / value)
    jsonl = old_cli.with_name("memory.jsonl")
    json_file = old_cli.with_name("memory.json")
    # exists/is_file inspect metadata only. The old main's migration is not run.
    return str(jsonl if jsonl.exists() or not json_file.exists() else json_file)


def _memory_entry(expected, old_node, runtime, spec, node_path, host):
    env = expected.get("env")
    if env is None:
        env = expected["env"] = {}
    if not isinstance(env, dict):
        raise RuntimeError("invalid memory environment configuration")
    # Unspecified host variables inherit the installer environment. A caller
    # with evidence of a different GUI environment must supply its explicit
    # path before migration; never override a known inherited value silently.
    _, effective, _ = _host_context(old_node)
    value = next((v for k, v in effective.items() if k == "MEMORY_FILE_PATH" or
                  (os.name == "nt" and k.lower() == "memory_file_path")), None)
    if os.name == "nt":
        for key in list(env):
            if key.lower() == "memory_file_path":
                del env[key]
    env["MEMORY_FILE_PATH"] = memory_path(value,
                                        False if old_node is None else old_node,
                                        spec, node_path, runtime["entry"], host=host)
    return expected


def register_managed(server, node_path, npx_path="", package_override=None, *, hosts=None):
    """Migrate defaults; return None to preserve the nondefault override path.

    All selected hosts are preflighted before configuration changes. Every
    write rechecks the complete node, and completed hosts get durable receipts
    immediately, allowing a later failed host to be retried safely.
    """
    if server not in SERVERS:
        return None
    packages = _packages()
    spec = packages.load_spec(server)
    if package_override and package_override not in (spec["package"], spec["package"] + "@" + spec["version"]):
        return None
    selected = _host_selection(hosts)
    before = {host: _actual(host, server) for host in selected}
    plan_path = os.environ.get("ICODE_MCP_RECONCILE_PLAN")
    if plan_path:
        import install_reconcile
        plan = install_reconcile._load_plan(Path(plan_path))
        if selected != plan["hosts"]:
            raise RuntimeError("frozen managed Node host selection conflict")
        for host in selected:
            snapshot = plan.get("node_entries", {}).get(host, {}).get(server)
            if snapshot is None:
                raise RuntimeError("managed Node registration plan snapshot conflict")
            if _digest(before[host]) != snapshot["digest"]:
                prior = _read_receipt(server)["hosts"].get(host)
                if (not prior or snapshot["digest"] not in (prior["digest"], prior["before_digest"])
                        or not can_resume_host(server, host, before[host])):
                    raise RuntimeError("managed Node registration plan snapshot conflict")
    receipt = _read_receipt(server)
    pending = {}
    for host, actual in before.items():
        launch = _launch(host, actual)
        previous = receipt["hosts"].get(host)
        if previous and previous["status"] == "migrated":
            if not verify_host(server, host, actual, require_node=False, require_current=False):
                raise RuntimeError(f"managed Node ownership conflict: {host}/{server}")
            pending[host] = ("migrated", list(launch["args"][1:]), "managed", previous)
            continue
        if previous and previous["status"] == "pending":
            state = _pending_state(server, host, actual, previous)
            source = "managed" if state == "intended" else previous["source"]
            old = _completed(previous) if state == "intended" else previous.get("old_record")
            if source == "managed":
                suffix = list(launch["args"][1:])
            elif source == "legacy":
                suffix = legacy_suffix(launch, {"package": previous["package"], "version": previous["package_version"]})
                if suffix is None:
                    raise RuntimeError("managed Node pending launch conflict")
            else:
                suffix = list((launch or {}).get("args", []))
            pending[host] = ("migrated", suffix, source, old)
            continue
        adopted = _adopt_digest(host, server, actual)
        suffix = list(launch.get("args", [])) if adopted else legacy_suffix(launch, spec) if actual else []
        if suffix is None:
            pending[host] = ("retained", None, None, None)
            continue
        if host == "codex" and actual is not None and not codex_representable(actual):
            raise RuntimeError(f"Codex metadata cannot be preserved: {server}")
        pending[host] = ("migrated", suffix, "adopt" if adopted else "legacy" if actual else "fresh", None)
    runtime = packages.ensure_runtime(server, node_path) if any(item[0] == "migrated" for item in pending.values()) else None
    planned = {}
    for host, (mode, suffix, source, old) in pending.items():
        if mode == "retained":
            planned[host] = None
            continue
        expected = copy.deepcopy(_launch(host, before[host]) or {})
        expected["command"] = runtime["node"]
        expected["args"] = [runtime["entry"], *suffix]
        expected.pop("_fallback", None)
        if server == "sequential-thinking":
            expected.setdefault("env", {}).setdefault("DISABLE_THOUGHT_LOGGING", "true")
        if server == "memory":
            expected = _memory_entry(expected, before[host], runtime, spec, node_path, host)
        planned[host] = expected
    for host, expected in planned.items():
        if _digest(_actual(host, server)) != _digest(before[host]):
            raise RuntimeError(f"concurrent host configuration change: {host}/{server}")
        mode = pending[host][0]
        if expected is not None:
            record = {"digest": _digest(_expected_actual(host, server, expected)), "status": "pending",
                      "before_digest": _digest(before[host]), "source": pending[host][2],
                      "runtime_root": runtime["root"], "lock_sha256": runtime["lock_sha256"],
                      "package": runtime["package"], "package_version": runtime["version"],
                      "node": runtime["node"], "entry": runtime["entry"]}
            if pending[host][3] is not None:
                record["old_record"] = pending[host][3]
            previous = receipt["hosts"].get(host)
            if previous and previous["digest"] == record["digest"]:
                record["before_digest"] = previous["before_digest"]
            receipt["hosts"][host] = record
            # Intent must be durable before the first host mutation. Failure
            # here leaves the original config; failure afterwards is resumable.
            _write_receipt(server, receipt)
            _write_host(host, server, expected, before_digest=_digest(before[host]))
            actual = _actual(host, server)
            if actual is None or _digest(actual) != record["digest"]:
                raise RuntimeError(f"managed Node registration verification failed: {host}/{server}")
            record = _completed(record)
        else:
            actual = before[host]
            record = {"digest": _digest(actual), "status": mode, "before_digest": _digest(actual)}
        receipt["hosts"][host] = record
        _write_receipt(server, receipt)
        # Legacy consumers may use the export, but managed consumers always use
        # host receipts. Retaining Claude must never make it a shared template.
        if host == "claude" and expected is not None:
            claude_registry.export_entry(server, expected)
        print(f"managed-node {server}: {host} {mode}")
    return 0


def main(argv=None):
    claude_registry._configure_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify",))
    parser.add_argument("--server", required=True, choices=SERVERS)
    parser.add_argument("--hosts", required=True)
    args = parser.parse_args(argv)
    try:
        return 0 if all(verify_host(args.server, host) for host in _host_selection(args.hosts.split(","))) else 1
    except (RuntimeError, OSError, ValueError, TypeError, KeyError):
        print("managed Node host verification failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
