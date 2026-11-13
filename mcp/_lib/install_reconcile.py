"""Receipt based MCP retirement, with no registration values in saved state.

prepare is read-only until its private plan is written. The public installer
preserves owned old code before publishing a new tree, then finish verifies the
entire desired set before touching any retired registration. Existing host
adapters remain responsible for atomic configuration writes and CLI removal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

import claude_registry
import client_registry

HOSTS = ("claude", "codex", "codebuddy", "workbuddy")
CATALOG = ".icode-mcp-desired.json"
DISTRIBUTION_CATALOG = "servers.json"
RUNTIME = {".venv", ".cache", "__pycache__"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
CODEX_TIMEOUT_SECONDS = 30
REPLACEMENT_PAYLOAD_FILES = ("server.py", "tools_manifest.json", "requirements.txt",
                             "config.example.json", "install.sh", "uninstall.sh",
                             "pyproject.toml", "README.md", "policy.json")
MODULE_CONFIG_ENVS = {
    "workspace": "ICODE_WORKSPACE_CONFIG", "evidence": "ICODE_EVIDENCE_CONFIG",
    "index": "ICODE_LOCAL_INDEX_CONFIG", "health": "ICODE_MCP_HEALTH_CONFIG",
    "policy": "ICODE_MCP_POLICY_CONFIG",
}


def _name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in (".", ".."):
        raise RuntimeError("invalid MCP name")
    return name


def _safe(path, within=None):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        # st_file_attributes is present on Windows before Path.is_junction
        # (Python 3.12). Reject every reparse point, not just symlink tags.
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("refusing linked MCP path")
    if within is not None and not path.is_relative_to(Path(within).resolve()):
        raise RuntimeError("MCP path escapes permitted root")
    return path


def _read(path, label):
    path = _safe(path)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid {label}")
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _state(path, roots):
    path = _safe(path or Path.home() / ".local/state/icode/mcp-install")
    discovery = [Path.home() / f".{host}/skills" for host in HOSTS]
    discovery.append(Path.home() / ".agents/skills")
    for root in [*roots, *discovery]:
        root = _safe(root)
        if path.is_relative_to(root) or root.is_relative_to(path):
            raise RuntimeError("MCP state/archive root overlaps installation or skill discovery")
    return path


def _private_dir(path):
    path = _safe(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write(path, data):
    path = _safe(path)
    _private_dir(path.parent)
    client_registry._atomic_write_static_config(path, data)


def _receipt(state):
    path = state / "receipt.json"
    if not path.exists():
        return {"version": 1, "hosts": {}}
    data = _read(path, "receipt")
    if data.get("version") != 1 or not isinstance(data.get("hosts"), dict):
        raise RuntimeError("invalid MCP receipt schema")
    for host, services in data["hosts"].items():
        if host not in HOSTS or not isinstance(services, dict):
            raise RuntimeError("invalid MCP receipt host")
        for name, record in services.items():
            _name(name)
            if (not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{64}", record.get("digest", ""))
                    or not isinstance(record.get("roots", []), list)):
                raise RuntimeError("invalid MCP receipt ownership")
            for root in record.get("roots", []):
                _safe(root)
            payload_roots = record.get("payload_roots", [])
            if (not isinstance(payload_roots, list)
                    or any(root not in record.get("roots", []) for root in payload_roots)):
                raise RuntimeError("invalid MCP receipt payload ownership")
    return data


def _distribution(source_root):
    """The shipped catalog defines defaults, not the recovery catalog."""
    mcp = _safe(Path(source_root) / "mcp")
    path = mcp / DISTRIBUTION_CATALOG
    if not path.exists():
        return None
    data = _read(path, "distribution catalog")
    names, replacements = data.get("servers"), data.get("replacements")
    if (set(data) != {"schema_version", "servers", "replacements"}
            or type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or not isinstance(names, list) or not isinstance(replacements, dict)):
        raise RuntimeError("invalid distribution catalog schema")
    try:
        if len({_name(name) for name in names}) != len(names):
            raise RuntimeError("duplicate distribution catalog server")
        members_seen = set()
        for replacement, record in replacements.items():
            if (_name(replacement) not in names or not isinstance(record, dict)
                    or set(record) != {"members", "retain_payload"}
                    or type(record["retain_payload"]) is not bool
                    or not isinstance(record["members"], dict) or not record["members"]):
                raise RuntimeError("invalid replacement catalog record")
            for prefix, member in record["members"].items():
                _name(prefix)
                _name(member)
                if member in names or member in members_seen:
                    raise RuntimeError("conflicting replacement catalog member")
                members_seen.add(member)
        for name in set(names) | members_seen:
            if not _safe(mcp / name / "install.sh", within=mcp).is_file():
                raise RuntimeError("missing distribution catalog installer")
    except RuntimeError as exc:
        raise RuntimeError("invalid distribution catalog entries") from exc
    return data


def desired(source_root, plan=None):
    if plan:
        return _load_plan(plan)["desired_names"]
    mcp = _safe(Path(source_root) / "mcp")
    catalog = mcp / CATALOG
    distribution = _distribution(source_root)
    if distribution is not None:
        names = sorted(distribution["servers"])
        if catalog.exists():
            recovery = _read(catalog, "desired catalog")
            if (set(recovery) != {"version", "desired_names"} or recovery.get("version") != 1
                    or not isinstance(recovery.get("desired_names"), list)
                    or sorted(recovery["desired_names"]) != names):
                raise RuntimeError("distribution and recovery catalog conflict")
        return names
    # A developer may clone directly into the installed skill directory. Git
    # updates then define the distribution; an older generated catalog must
    # not hide newly added installers. Copied installations still need it to
    # exclude retained retired code.
    git_marker = _safe(Path(source_root) / ".git")
    git_source = git_marker.is_dir() or git_marker.is_file()
    if catalog.exists() and not git_source:
        data = _read(catalog, "desired catalog")
        names = data.get("desired_names")
        if data.get("version") != 1 or not isinstance(names, list):
            raise RuntimeError("invalid desired MCP catalog")
        return sorted({_name(name) for name in names})
    return sorted(_name(p.parent.name) for p in mcp.glob("*/install.sh") if _safe(p).is_file())


def _config(host):
    return {"claude": claude_registry.CLAUDE_JSON,
            "codebuddy": client_registry.CODEBUDDY_CFG,
            "workbuddy": client_registry.WORKBUDDY_CFG}[host]


def _codex_command(arguments):
    try:
        return subprocess.run([client_registry.CODEX_BIN, "mcp", *arguments],
                              stdin=subprocess.DEVNULL, timeout=CODEX_TIMEOUT_SECONDS,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex CLI timed out") from exc


def _actual(host, name):
    """Read complete host nodes. Never interpret CLI failure as absence."""
    if host == "codex":
        if not client_registry.CODEX_BIN:
            raise RuntimeError("Codex CLI unavailable")
        result = _codex_command(["get", name, "--json"])
        if result.returncode:
            if "No MCP server" in result.stderr:
                return None
            raise RuntimeError(f"Codex CLI inspection failed: {name}")
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"invalid Codex CLI response: {name}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("transport"), dict):
            raise RuntimeError(f"invalid Codex CLI response: {name}")
        return value
    cfg = _safe(_config(host))
    if not cfg.exists():
        return None
    data = _read(cfg, f"{host} configuration")
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"invalid {host} mcpServers")
    node = servers.get(name)
    if node is not None and not isinstance(node, dict):
        raise RuntimeError(f"invalid {host} MCP node: {name}")
    return node


def _export(name):
    path = _safe(claude_registry.ENTRY_DIR / f"{name}.json")
    return _read(path, f"exported MCP entry: {name}") if path.exists() else None


def _matches(host, actual, entry):
    if actual is None or entry is None:
        return False
    if host == "claude":
        return actual == entry
    expected = {key: entry[key] for key in ("command", "args", "env") if entry.get(key) is not None}
    if host != "codex":
        return actual == expected
    # Codex CLI adds a stable envelope and transport defaults; reject unknown
    # execution-affecting fields instead of reducing ownership to command alone.
    node = dict(actual)
    transport = dict(node.pop("transport"))
    defaults = {"name": actual.get("name"), "enabled": True, "disabled_reason": None,
                "startup_timeout_sec": None, "tool_timeout_sec": None,
                "enabled_tools": None, "disabled_tools": None}
    if any(key not in defaults or value != defaults[key] for key, value in node.items()):
        return False
    for key, default in (("type", "stdio"), ("env_vars", []), ("cwd", None)):
        if transport.pop(key, default) != default:
            return False
    transport["env"] = transport.get("env") or {}
    expected["env"] = expected.get("env") or {}
    return transport == expected


def _node_managed(name):
    """Narrow opt-in: only the four shipped locked npm server specifications."""
    try:
        import node_registration
        if name not in node_registration.SERVERS:
            return False
        node_registration._packages().load_spec(name)
    except (ImportError, RuntimeError, OSError, ValueError):
        return False
    return True


def _hosts(client):
    if client not in (*HOSTS, "all"):
        raise RuntimeError("invalid MCP host selection")
    if client == "all":
        hosts = ["claude"]
        if client_registry.CODEX_BIN:
            hosts.append("codex")
        hosts += [host for host in ("codebuddy", "workbuddy") if _safe(_config(host)).parent.exists()]
        return hosts
    # Child installers may register current Claude entries as a historical side
    # effect. That does not authorize retirement on an unselected Claude host.
    if client == "codex" and not client_registry.CODEX_BIN:
        raise RuntimeError("Codex CLI unavailable")
    return [client]


def _payload(root, name):
    return _safe(_safe(root) / "mcp" / _name(name), within=_safe(root))


def _service_payload(directory, known_names=()):
    if directory.name in known_names:
        return True
    return any(_safe(directory / name).exists()
               for name in ("install.sh", "server.py", ".venv", "config.json"))


def _payload_owned(root, name, actual):
    marker = _safe(Path(root) / ".icode-install-owner.json")
    if marker.exists():
        try:
            owner = _read(marker, "ICODE owner marker")
            if (type(owner.get("schema_version")) is int
                    and owner == {"schema_version": 1, "owner": "icode-skill", "skill": "icode"}):
                return True
        except RuntimeError:
            pass
    target = _payload(root, name)
    node = (actual or {}).get("transport", actual or {})
    def references(value):
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        if not isinstance(value, str):
            return False
        candidate = Path(value.split("=", 1)[-1] if value.startswith("--") and "=" in value else value)
        if not candidate.is_absolute():
            return False
        try:
            return _safe(candidate).is_relative_to(target)
        except RuntimeError:
            return False
    return references({key: node.get(key) for key in ("command", "args", "env")})


def _file_manifest(path):
    result = {}
    for parent, directories, files in os.walk(_safe(path), followlinks=False):
        # Runtime can contain interpreter symlinks. It is moved as an opaque
        # directory, never traversed or copied during staged code preservation.
        for name in directories:
            _safe(Path(parent) / name)
        directories[:] = sorted(name for name in directories if name not in RUNTIME)
        for name in sorted(files):
            item = _safe(Path(parent) / name)
            if not stat.S_ISREG(item.stat().st_mode):
                raise RuntimeError("refusing special file in MCP payload")
            result[item.relative_to(path).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def _manifest_digest(manifest):
    digest = hashlib.sha256()
    for name, value in sorted(manifest.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def _tree_digest(path):
    return _manifest_digest(_file_manifest(path)) if path.exists() else None


def _replacement_manifest(old, published):
    files = _file_manifest(old)
    files.update(_replacement_source_manifest(published))
    return files


def _replacement_source_manifest(published):
    """Hash only declared business files, never source-local private data."""
    result = {}
    for name in REPLACEMENT_PAYLOAD_FILES:
        item = _safe(published / name, within=published)
        if not item.exists():
            continue
        if not stat.S_ISREG(item.stat().st_mode):
            raise RuntimeError("refusing special file in replacement distribution")
        result[name] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def _archive_target(plan, root, name):
    return Path(plan["state_root"]) / "archives" / plan["id"] / _hash(str(root))[:16] / name


def check_runtime_dependencies(profile_path, module_dirs, *, module_configs=None):
    """Reject archival of directories that still own active business data.

    Directory names alone never prove that .cache/.venv are disposable. This
    read-only gate is shared with profile preflight and repeated at retirement.
    It does not rewrite configuration or persist configuration values.
    """
    directories = list(dict.fromkeys(_safe(path) for path in module_dirs))
    runtimes = []
    for directory in directories:
        for parent, children, _ in os.walk(directory, followlinks=False):
            for child in list(children):
                item = _safe(Path(parent) / child, within=directory)
                if child in RUNTIME:
                    runtimes.append(item)
                    children.remove(child)

    def dependency(raw, *, base=None, directory=False):
        if not isinstance(raw, (str, Path)) or not str(raw).strip():
            raise RuntimeError("invalid runtime dependency path")
        path = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        if not path.is_absolute():
            if base is None:
                raise RuntimeError("relative runtime dependency has no stable base")
            path = base / path
        path = _safe(path)
        for module in directories:
            if path.is_relative_to(module) and RUNTIME.intersection(path.relative_to(module).parts):
                raise RuntimeError("active runtime dependency prevents archival")
        if directory and any(runtime.is_relative_to(path) for runtime in runtimes):
            raise RuntimeError("active runtime dependency overlaps archival")
        return path

    configs = {_safe(directory / "config.json") for directory in directories
               if (directory / "config.json").exists()}
    if module_configs is not None and not isinstance(module_configs, dict):
        raise RuntimeError("invalid runtime dependency module configuration mapping")
    references = list((module_configs or {}).values())
    if profile_path is not None:
        profile = dependency(profile_path)
        value = _read(profile, "runtime dependency profile").get("module_configs")
        if not isinstance(value, dict):
            raise RuntimeError("invalid runtime dependency profile")
        references.extend(dependency(raw, base=profile.parent) for raw in value.values())
    for raw in references:
        configs.add(dependency(raw))
    # An original implementation can have a custom directory basename. The
    # caller supplies possible implementation roots, not config-file bases.
    policy_dirs = directories
    for config_path in configs:
        dependency(config_path)
        config = _read(config_path, "runtime dependency configuration")
        if "data_dir" in config:
            dependency(config["data_dir"], directory=True)
        if "policy_file" in config:
            # Legacy policy paths are relative to the policy implementation,
            # rather than an external config file. Check every retained policy
            # location when multiple installation roots remain in the plan.
            for base in policy_dirs or [config_path.parent]:
                dependency(config["policy_file"], base=base)


def check_payload_dependencies(source_mcp, module_dirs, *, profile_path=None, module_configs=None):
    """Keep active config aliases from being replaced by distribution files."""
    source_mcp = _safe(source_mcp)
    directories = list(dict.fromkeys(_safe(path) for path in module_dirs))

    def path_of(raw, *, base=None):
        if not isinstance(raw, (str, Path)) or not str(raw).strip():
            raise RuntimeError("invalid active payload dependency path")
        path = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        if not path.is_absolute():
            if base is None:
                raise RuntimeError("relative active payload dependency has no stable base")
            path = base / path
        return _safe(path)

    def check(path, *, policy=False):
        for directory in directories:
            if not path.is_relative_to(directory):
                continue
            relative = path.relative_to(directory)
            # Both publishers preserve config.json, including nested copies.
            if relative.name == "config.json":
                continue
            if directory.name != "icode-local" and relative.as_posix() not in REPLACEMENT_PAYLOAD_FILES:
                continue
            published = _safe(source_mcp / directory.name / relative, within=source_mcp)
            if not published.is_file() or not path.exists() or path.read_bytes() == published.read_bytes():
                continue
            if policy:
                original = _read(path, "active policy dependency")
                updated = _read(published, "distributed policy dependency")
                if ({key: value for key, value in original.items() if key != "transport"}
                        == {key: value for key, value in updated.items() if key != "transport"}):
                    continue
            raise RuntimeError("changed active payload dependency prevents publication")

    if module_configs is not None and not isinstance(module_configs, dict):
        raise RuntimeError("invalid active payload dependency mapping")
    configs = {path_of(raw) for raw in (module_configs or {}).values()}
    configs.update(directory / "config.json" for directory in directories if (directory / "config.json").exists())
    if profile_path is not None:
        profile = path_of(profile_path)
        check(profile)
        mapping = _read(profile, "active payload profile").get("module_configs")
        if not isinstance(mapping, dict):
            raise RuntimeError("invalid active payload dependency profile")
        configs.update(path_of(raw, base=profile.parent) for raw in mapping.values())
    for config in configs:
        check(config)
        data = _read(config, "active payload configuration")
        if "policy_file" in data:
            for directory in directories:
                check(path_of(data["policy_file"], base=directory), policy=True)
    for directory in directories:
        if directory.name == "icode-mcp-policy" and (directory / "policy.json").exists():
            check(directory / "policy.json", policy=True)


def _archive_dependency_inputs(plan, roots, *, module_dirs=None, replacements=None):
    directories = module_dirs if module_dirs is not None else [
        _payload(root, name) for root in roots for name in plan.get("retain_payload_names", [])
        if str(_payload(root, name)) in plan.get("archive_eligible", [])]
    if not directories:
        return None
    profiles, configs = set(), []
    for root in roots:
        profile = _payload(root, "icode-local") / "config.json"
        if profile.exists() and "module_configs" in _read(profile, "runtime dependency profile"):
            profiles.add(profile)
    members = {prefix: member for record in (replacements if replacements is not None else plan.get("replacements", {})).values()
               for prefix, member in record["members"].items()}
    for host in HOSTS:
        if host == "codex" and not client_registry.CODEX_BIN:
            continue
        node = _actual(host, "icode-local")
        if node:
            transport = node.get("transport", node)
            raw = (transport.get("env") or {}).get("ICODE_LOCAL_CONFIG")
            if raw:
                path = Path(os.path.expandvars(os.path.expanduser(raw)))
                if not path.is_absolute():
                    cwd = transport.get("cwd", node.get("cwd"))
                    if not cwd or not Path(cwd).is_absolute():
                        raise RuntimeError("relative runtime dependency profile has no stable base")
                    path = Path(cwd) / path
                profiles.add(_safe(path))
        for prefix, member in members.items():
            legacy = _actual(host, member)
            if not legacy:
                continue
            transport = legacy.get("transport", legacy)
            raw = (transport.get("env") or {}).get(MODULE_CONFIG_ENVS.get(prefix))
            if raw:
                path = Path(os.path.expandvars(os.path.expanduser(raw)))
                if not path.is_absolute():
                    cwd = transport.get("cwd", legacy.get("cwd"))
                    if not cwd or not Path(cwd).is_absolute():
                        raise RuntimeError("relative runtime dependency config has no stable base")
                    path = Path(cwd) / path
                configs.append({prefix: _safe(path)})
    return directories, profiles, configs


def _check_publication_dependencies(plan, roots):
    distribution = _distribution(plan["source_root"])
    if distribution is None:
        return
    replacements = distribution["replacements"]
    names = set(plan.get("retain_payload_names", [])) | set(replacements)
    if not names:
        return
    directories = [_payload(root, name) for root in roots for name in names]
    if not any(directory.exists() for directory in directories):
        return
    inputs = _archive_dependency_inputs(plan, roots, module_dirs=directories, replacements=replacements)
    _, profiles, configs = inputs
    source = _safe(Path(plan["source_root"]) / "mcp")
    check_payload_dependencies(source, directories)
    for profile in profiles:
        check_payload_dependencies(source, directories, profile_path=profile)
    for mapping in configs:
        check_payload_dependencies(source, directories, module_configs=mapping)


def _check_archive_dependencies(plan, roots):
    inputs = plan.get("_active_dependency_inputs")
    if inputs is None:
        inputs = _archive_dependency_inputs(plan, roots)
    if inputs is None:
        return
    directories, profiles, configs = inputs
    check_runtime_dependencies(None, directories)
    for profile in profiles:
        check_runtime_dependencies(profile, directories)
    for mapping in configs:
        check_runtime_dependencies(None, directories, module_configs=mapping)


def prepare(source_root, installed_roots, client, *, state_root=None, single=None,
            no_delete=False, dry_run=False):
    source = _safe(source_root)
    roots = list(dict.fromkeys(str(_safe(root)) for root in installed_roots))
    state = _state(state_root, [source, *roots])
    receipt = _receipt(state)
    distribution_names = desired(source)
    distribution = _distribution(source)
    replacements = distribution["replacements"] if distribution else {}
    names = distribution_names
    if single:
        if _name(single) not in names:
            raise RuntimeError("selected MCP is not in current distribution")
        names = [single]
    hosts = _hosts(client)
    selected_replacements = {name: record for name, record in replacements.items() if name in names}
    replacement_members = {member for record in selected_replacements.values() for member in record["members"].values()}
    retained_payload_names = sorted({member for record in replacements.values() if record["retain_payload"]
                                     for member in record["members"].values()})
    plan = {"version": 1, "id": uuid.uuid4().hex, "state_root": str(state),
            "source_root": str(source), "installed_roots": roots,
            "desired_names": names, "distribution_names": distribution_names,
            "hosts": hosts, "no_delete": bool(no_delete),
            "single": single, "retired": {}, "conflicts": [], "payloads": {},
            "replacements": selected_replacements, "retain_payload_names": retained_payload_names,
            "published_payloads": {}, "replacement_source_digests": {},
            "preserve_payloads": {}, "archive_eligible": [], "node_entries": {}}
    # Sync must also keep unverified old code runnable. Keeping it does not
    # grant ownership: only payloads below are eligible for later archival.
    for root in roots:
        old_mcp = _safe(Path(root) / "mcp")
        known_names = {name for services in receipt["hosts"].values() for name in services}
        for directory in old_mcp.iterdir() if old_mcp.exists() else ():
            if directory.name.startswith((".", "_")) or directory.name in distribution_names:
                continue
            _name(directory.name)
            directory = _safe(directory, within=root)
            if directory.is_dir() and _service_payload(directory, known_names):
                plan["preserve_payloads"][str(directory)] = _tree_digest(directory)
    exports = _safe(claude_registry.ENTRY_DIR)
    legacy_names = {_name(p.stem) for p in exports.glob("*.json")}
    for host in hosts:
        owned = dict(receipt["hosts"].get(host, {}))
        # Only a complete host-specific equality can adopt legacy exports.
        for name in sorted(legacy_names | set(owned) | set(names) | replacement_members):
            if single and name != single and name not in replacement_members:
                continue
            actual = _actual(host, name)
            old = owned.get(name)
            entry = _export(name)
            matches_old = old is not None and (actual is None or _hash(actual) == old["digest"])
            adopted = old is None and _matches(host, actual, entry)
            if name in names:
                if _node_managed(name):
                    import node_registration
                    prior = node_registration._read_receipt(name)["hosts"].get(host)
                    if prior and prior["status"] in ("migrated", "pending") and not node_registration.can_resume_host(name, host, actual):
                        raise RuntimeError(f"desired MCP ownership conflict: {host}/{name}")
                    # This snapshot authorizes only the child's read/retain or
                    # recognized launch-field migration; never retirement.
                    plan["node_entries"].setdefault(host, {})[name] = {"digest": _hash(actual)}
                    continue
                # A previous batch may have updated this desired child before
                # another child failed. Its complete current export is also
                # valid recovery evidence; retired items still require their
                # original receipt digest below.
                if actual is not None and not (matches_old or _matches(host, actual, entry)):
                    raise RuntimeError(f"desired MCP ownership conflict: {host}/{name}")
                continue
            if old is None and actual is None:
                continue
            if old and old.get("retained_custom"):
                plan["conflicts"].append({"host": host, "name": name, "reason": "retained-custom"})
                continue
            if not (matches_old or adopted):
                plan["conflicts"].append({"host": host, "name": name, "reason": "ownership"})
                continue
            eligible_roots = [root for root in roots if
                              root in (old or {}).get("payload_roots", []) or _payload_owned(root, name, actual)]
            record = {"digest": old["digest"] if old else _hash(actual), "roots": roots,
                      "payload_roots": eligible_roots}
            plan["retired"].setdefault(host, {})[name] = record
            for root in record["roots"]:
                item = _payload(root, name)
                if item.exists():
                    plan["payloads"][str(item)] = _tree_digest(item)
                    plan["preserve_payloads"][str(item)] = plan["payloads"][str(item)]
                    if root in eligible_roots:
                        plan["archive_eligible"].append(str(item))
    if not single:
        owned_names = {name for records in plan["retired"].values() for name in records} | set(retained_payload_names)
        reported_names = {item["name"] for item in plan["conflicts"]}
        for raw in plan["preserve_payloads"]:
            name = Path(raw).name
            if name not in owned_names | reported_names:
                plan["conflicts"].append({"name": name, "reason": "unverified-payload"})
                reported_names.add(name)
    for raw in plan["preserve_payloads"]:
        if Path(raw).name not in retained_payload_names:
            continue
        published = _payload(source, Path(raw).name)
        plan["replacement_source_digests"][str(published)] = _manifest_digest(_replacement_source_manifest(published))
        plan["published_payloads"][raw] = _manifest_digest(_replacement_manifest(Path(raw), published))
    _check_publication_dependencies(plan, roots)
    if dry_run:
        return plan
    destination = state / "plans" / f"{plan['id']}.json"
    _private_dir(state)
    _write(destination, plan)
    # Save original code, config and extensions before a staged publication can
    # update them. Runtime remains live until the registration success gate.
    for raw in plan["published_payloads"]:
        old = Path(raw)
        snapshot = _archive_target(plan, old.parent.parent, old.name) / "payload"
        _private_dir(snapshot.parent)
        _merge_payload(old, snapshot)
        if _tree_digest(snapshot) != plan["preserve_payloads"][raw]:
            raise RuntimeError("replacement recovery snapshot changed during preparation")
        _restrict_archive(snapshot)
    return destination


def _load_plan(path):
    path = _safe(path)
    data = _read(path, "MCP reconciliation plan")
    if (data.get("version") != 1 or not isinstance(data.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", data["id"])):
        raise RuntimeError("invalid MCP plan schema")
    roots = [_safe(root) for root in data["installed_roots"]]
    state = _state(data["state_root"], [data["source_root"], *roots])
    if path != state / "plans" / f"{data['id']}.json":
        raise RuntimeError("MCP plan outside private plan root")
    for name in data["desired_names"]:
        _name(name)
    for name in data.get("distribution_names", data["desired_names"]):
        _name(name)
    distribution = _distribution(data["source_root"])
    if distribution is not None:
        names = sorted(distribution["servers"])
        if (sorted(data.get("distribution_names", data["desired_names"])) != names
                or not set(data["desired_names"]).issubset(names)
                or (data.get("single") and data["desired_names"] != [data["single"]])
                or (not data.get("single") and sorted(data["desired_names"]) != names)):
            raise RuntimeError("MCP plan distribution catalog conflict")
    if "replacements" in data:
        replacements = distribution["replacements"] if distribution else {}
        expected = {name: record for name, record in replacements.items() if name in data["desired_names"]}
        retained = sorted({member for record in replacements.values() if record["retain_payload"]
                           for member in record["members"].values()})
        if data["replacements"] != expected or data.get("retain_payload_names") != retained:
            raise RuntimeError("replacement plan catalog conflict")
    if any(host not in HOSTS for host in data["hosts"]):
        raise RuntimeError("invalid plan host")
    for host, services in data.get("node_entries", {}).items():
        if host not in data["hosts"] or not isinstance(services, dict):
            raise RuntimeError("invalid managed Node plan host")
        for name, record in services.items():
            if (name not in data["desired_names"] or not _node_managed(name)
                    or not isinstance(record, dict) or set(record) != {"digest"}
                    or not re.fullmatch(r"[0-9a-f]{64}", record.get("digest", ""))):
                raise RuntimeError("invalid managed Node plan snapshot")
    for host, services in data["retired"].items():
        if host not in data["hosts"] or not isinstance(services, dict):
            raise RuntimeError("invalid plan retirement host")
        for name, record in services.items():
            _name(name)
            if data.get("single") and name not in {member for record in data.get("replacements", {}).values()
                                                   for member in record["members"].values()}:
                raise RuntimeError("single MCP retirement outside replacement catalog")
            if not re.fullmatch(r"[0-9a-f]{64}", record["digest"]):
                raise RuntimeError("invalid plan ownership digest")
            for root in record["roots"]:
                if _safe(root) not in roots:
                    raise RuntimeError("retirement root outside selected installations")
            if (not isinstance(record.get("payload_roots", []), list)
                    or any(root not in record["roots"] for root in record.get("payload_roots", []))):
                raise RuntimeError("invalid plan payload ownership")
    for item, digest in {**data["payloads"], **data.get("preserve_payloads", {})}.items():
        item = _safe(item)
        if not any(item == _payload(root, item.name) for root in roots):
            raise RuntimeError("payload outside recorded installation roots")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("invalid payload digest")
    if any(item not in data["payloads"] for item in data.get("archive_eligible", [])):
        raise RuntimeError("invalid archive ownership")
    for raw, digest in data.get("published_payloads", {}).items():
        if (raw not in data.get("preserve_payloads", {}) or Path(raw).name not in data.get("retain_payload_names", [])
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise RuntimeError("invalid replacement publication digest")
    for raw, digest in data.get("replacement_source_digests", {}).items():
        if (_safe(raw) != _payload(data["source_root"], Path(raw).name)
                or Path(raw).name not in data.get("retain_payload_names", [])
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise RuntimeError("invalid replacement source identity")
    return data


def _merge_payload(source, target):
    """Accept an existing identical subset; never overwrite a staged edit."""
    if target.exists():
        _tree_digest(target)  # Validate links and special files before copying.
        for parent, directories, files in os.walk(target, followlinks=False):
            directories[:] = [name for name in directories if name not in RUNTIME]
            relative = Path(parent).relative_to(target)
            if not _safe(source / relative, within=source).is_dir():
                raise RuntimeError(f"retired staged payload conflicts: {source.name}")
            for name in files:
                existing = _safe(Path(parent) / name, within=target)
                original = _safe(source / relative / name, within=source)
                if not original.is_file() or existing.read_bytes() != original.read_bytes():
                    raise RuntimeError(f"retired staged payload conflicts: {source.name}")
    def copy_missing(original, destination):
        original, destination = _safe(original, within=source), _safe(destination, within=target)
        if not stat.S_ISREG(original.stat().st_mode):
            raise RuntimeError("refusing special file in MCP payload")
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != original.read_bytes():
                raise RuntimeError(f"retired staged payload conflicts: {source.name}")
            return str(destination)
        return shutil.copy2(original, destination)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*RUNTIME),
                    dirs_exist_ok=True, copy_function=copy_missing)


def _merge_replacement(source, target, published):
    """Publish trusted distribution code, keeping original configs/extensions."""
    expected = _replacement_manifest(source, published)
    for name, digest in _file_manifest(target).items():
        if expected.get(name) != digest:
            raise RuntimeError(f"replacement staged payload conflicts: {source.name}")
    old_files = _file_manifest(source)
    new_files = _replacement_source_manifest(published)
    for name in expected:
        destination = _safe(target / name, within=target)
        if destination.exists():
            continue
        original = source / name if name in old_files and (
            name not in new_files or Path(name).name == "config.json") else published / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_safe(original), destination)
    if _tree_digest(target) != _manifest_digest(expected):
        raise RuntimeError(f"replacement publication verification failed: {source.name}")


def preserve(plan_path, stage_root, *, installed_root=None, source_root=None):
    """Keep old source without activating it; plain sync never reads host state."""
    stage = _safe(stage_root)
    if not stage.is_dir():
        raise RuntimeError("MCP stage root must already exist")
    if plan_path:
        plan = _load_plan(plan_path)
        if installed_root is None:
            if len(plan["installed_roots"]) != 1:
                raise RuntimeError("preserve requires corresponding installed root")
            installed_root = plan["installed_roots"][0]
        installed_root = _safe(installed_root)
        if str(installed_root) not in plan["installed_roots"]:
            raise RuntimeError("preserve root not selected by plan")
        payloads = {raw: digest for raw, digest in plan.get("preserve_payloads", plan["payloads"]).items()
                    if Path(raw).parent == installed_root / "mcp"}
        names = plan.get("distribution_names", plan["desired_names"])
    else:
        if installed_root is None or source_root is None:
            raise RuntimeError("plain preservation requires source and installed roots")
        installed_root = _safe(installed_root)
        names = desired(source_root)
        old_mcp = _safe(installed_root / "mcp")
        payloads = {}
        for directory in old_mcp.iterdir() if old_mcp.exists() else ():
            if directory.name.startswith((".", "_")) or directory.name in names:
                continue
            _name(directory.name)
            directory = _safe(directory, within=installed_root)
            if directory.is_dir() and _service_payload(directory):
                payloads[str(directory)] = _tree_digest(directory)
    for raw, digest in payloads.items():
        source = _safe(raw)
        if _tree_digest(source) != digest:
            raise RuntimeError(f"retired payload changed before preservation: {source.name}")
        target = _payload(stage, source.name)
        if plan_path and raw in plan.get("published_payloads", {}):
            _check_publication_dependencies(plan, plan["installed_roots"])
            published = _payload(plan["source_root"], source.name)
            if _manifest_digest(_replacement_source_manifest(published)) != plan["replacement_source_digests"].get(str(published)):
                raise RuntimeError("replacement source changed before publication")
            _merge_replacement(source, target, published)
            if _tree_digest(target) != plan["published_payloads"][raw]:
                raise RuntimeError("replacement staged publication drift")
            continue
        _merge_payload(source, target)
        if _tree_digest(target) != digest:
            raise RuntimeError(f"retired staged payload conflicts: {source.name}")
    _write(_safe(stage / "mcp" / CATALOG, within=stage),
           {"version": 1, "desired_names": names})


def _remove(host, name):
    if host == "claude":
        # unregister removes the shared export. Restore it before returning so
        # other hosts and retries continue to have their registration source.
        entry = _export(name)
        previous_umask = os.umask(0o077)
        try:
            claude_registry.unregister(name)
        finally:
            try:
                if entry is not None:
                    claude_registry.export_entry(name, entry)
            finally:
                os.umask(previous_umask)
    elif host == "codex":
        if not client_registry.CODEX_BIN:
            raise RuntimeError("Codex CLI unavailable")
        result = _codex_command(["remove", name])
        if result.returncode:
            raise RuntimeError(f"Codex CLI removal failed: {name}")
    else:
        _, ok = getattr(client_registry, f"{host}_unregister")(name)
        if not ok:
            raise RuntimeError(f"MCP removal failed: {host}/{name}")
    if _actual(host, name) is not None:
        raise RuntimeError(f"MCP removal verification failed: {host}/{name}")


def _archive(plan, name, roots):
    if name in plan.get("retain_payload_names", []):
        _check_archive_dependencies(plan, roots)
    state = Path(plan["state_root"])
    moved = 0
    for root in roots:
        source = _payload(root, name)
        if not source.exists():
            continue
        if str(source) not in plan.get("archive_eligible", []):
            continue
        expected = plan.get("published_payloads", {}).get(str(source), plan["payloads"].get(str(source)))
        if expected is None or _tree_digest(source) != expected:
            raise RuntimeError(f"retired payload ownership changed: {name}")
        target = _archive_target(plan, root, name)
        _private_dir(target.parent)
        if name in plan.get("retain_payload_names", []):
            # The merged server lazily imports this business module in place.
            # Preserve code/config and archive only its unused interpreter/cache.
            # A private source snapshot makes partial moves recoverable; merging
            # an identical subset allows retries without overwriting any edit.
            _private_dir(target)
            snapshot = target / "payload"
            if not snapshot.exists():
                _merge_payload(source, snapshot)
            if _tree_digest(snapshot) != plan.get("preserve_payloads", plan["payloads"])[str(source)]:
                raise RuntimeError(f"replacement snapshot conflicts: {name}")
            _restrict_archive(snapshot)
            for parent, directories, _ in os.walk(source, followlinks=False):
                for directory in list(directories):
                    runtime = _safe(Path(parent) / directory, within=source)
                    if directory not in RUNTIME:
                        continue
                    directories.remove(directory)
                    destination = _safe(target / "runtime" / runtime.relative_to(source), within=target)
                    _private_dir(destination.parent)
                    if destination.exists():
                        raise RuntimeError(f"replacement runtime archive conflicts: {name}")
                    _check_archive_dependencies(plan, roots)
                    shutil.move(str(runtime), str(destination))
                    _restrict_archive(destination)
            _restrict_archive(target)
            moved += 1
            continue
        if target.exists():
            raise RuntimeError(f"archive target already exists: {name}")
        shutil.move(str(source), str(target))
        _restrict_archive(target)
        moved += 1
    return moved


def _restrict_archive(target):
    """Restrict saved secrets without following interpreter symlinks."""
    os.chmod(target, 0o700)
    for parent, dirs, files in os.walk(target, followlinks=False):
        os.chmod(parent, 0o700)
        dirs[:] = [name for name in dirs if not (
            stat.S_ISLNK((Path(parent) / name).lstat().st_mode)
            or getattr((Path(parent) / name).lstat(), "st_file_attributes", 0) & 0x400)]
        for item in files:
            node = Path(parent) / item
            metadata = node.lstat()
            if stat.S_ISREG(metadata.st_mode) and not getattr(metadata, "st_file_attributes", 0) & 0x400:
                os.chmod(node, 0o700 if metadata.st_mode & 0o111 else 0o600)


def finish(plan_path, installed_roots=()):
    plan = _load_plan(plan_path)
    roots = list(dict.fromkeys(plan["installed_roots"] + [str(_safe(root)) for root in installed_roots]))
    state = _state(plan["state_root"], [plan["source_root"], *roots])
    receipt = _receipt(state)
    verified = {}
    retained_custom = 0
    # The all-desired gate precedes any receipt or retirement write.
    for host in plan["hosts"]:
        for name in plan["desired_names"]:
            actual = _actual(host, name)
            snapshot = plan.get("node_entries", {}).get(host, {}).get(name)
            node_record = None
            if snapshot is not None:
                import node_registration
                if not node_registration.verify_host(name, host, actual):
                    raise RuntimeError(f"desired MCP verification failed: {host}/{name}")
                node_record = node_registration._read_receipt(name)["hosts"][host]
                if snapshot["digest"] not in (node_record.get("before_digest"), node_record["digest"]):
                    raise RuntimeError(f"desired MCP snapshot conflict: {host}/{name}")
            elif not _matches(host, actual, _export(name)):
                raise RuntimeError(f"desired MCP verification failed: {host}/{name}")
            verified.setdefault(host, {})[name] = {"digest": _hash(actual), "roots": roots,
                "payload_roots": [root for root in roots if _payload_owned(root, name, actual)]}
            if node_record and node_record["status"] == "retained":
                verified[host][name]["retained_custom"] = True
                retained_custom += 1
    if not plan["no_delete"]:
        plan["_active_dependency_inputs"] = _archive_dependency_inputs(plan, roots)
        # Check the complete set before the first old registration or runtime
        # changes, including profile switches that happened after prepare.
        _check_archive_dependencies(plan, roots)
    for host, records in verified.items():
        receipt["hosts"].setdefault(host, {}).update(records)
    # Persist adopted retirement ownership before the first destructive host
    # operation; absent registrations and archive failures remain retryable.
    for host, records in plan["retired"].items():
        receipt["hosts"].setdefault(host, {}).update(records)
    _write(state / "receipt.json", receipt)
    result = {"status": "complete", "registered": sum(map(len, verified.values())),
              "retired": 0, "archived": 0, "retained": retained_custom, "conflicts": list(plan["conflicts"])}
    if plan["single"] and not plan.get("replacements"):
        return result
    retired_names = sorted({name for records in plan["retired"].values() for name in records})
    for name in retired_names:
        if plan["no_delete"]:
            result["retained"] += 1
            continue
        # Detect code/config edits before removing a runnable registration.
        # Runtime stays opaque, matching the staged synchronization contract.
        try:
            for root in roots:
                item = _payload(root, name)
                if (item.exists() and str(item) in plan.get("archive_eligible", [])
                        and _tree_digest(item) != plan.get("published_payloads", {}).get(str(item), plan["payloads"].get(str(item)))):
                    raise RuntimeError("retired payload changed")
        except (RuntimeError, OSError):
            result["conflicts"].append({"name": name, "reason": "payload-drift"})
            continue
        failed = False
        for host, records in plan["retired"].items():
            if name not in records:
                continue
            try:
                actual = _actual(host, name)
                if actual is not None and _hash(actual) != records[name]["digest"]:
                    result["conflicts"].append({"host": host, "name": name, "reason": "drift"})
                    failed = True
                    continue
                if actual is not None:
                    _remove(host, name)
                    result["retired"] += 1
            except (RuntimeError, OSError):
                result["conflicts"].append({"host": host, "name": name, "reason": "unregister-failed"})
                failed = True
        if failed:
            continue
        # Other hosts, including unselected hosts, keep their runnable payload.
        try:
            other_hosts = [host for host in HOSTS if host not in plan["hosts"]]
            in_use = any((_actual(host, name) is not None) for host in other_hosts
                         if host != "codex" or client_registry.CODEX_BIN)
            if "codex" in receipt["hosts"] and not client_registry.CODEX_BIN:
                in_use = in_use or name in receipt["hosts"]["codex"]
            if not client_registry.CODEX_BIN and (Path.home() / ".codex").exists():
                in_use = True
            if in_use:
                result["retained"] += 1
                continue
            result["archived"] += _archive(plan, name, roots)
            if name not in plan.get("retain_payload_names", []) and any(_payload(root, name).exists() for root in roots):
                result["conflicts"].append({"name": name, "reason": "payload-conflict"})
                continue
        except (RuntimeError, OSError, shutil.Error):
            result["conflicts"].append({"name": name, "reason": "archive-failed"})
            continue
        for host in plan["retired"]:
            receipt["hosts"].get(host, {}).pop(name, None)
        _write(state / "receipt.json", receipt)
    if result["conflicts"]:
        result["status"] = "conflict"
    return result


def main(argv=None):
    claude_registry._configure_utf8_stdout()
    # Bash command substitution strips LF, so Windows CRLF would become part
    # of a service name or plan path. Keep this machine-readable CLI on LF.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("prepare")
    start.add_argument("--source-root", required=True, type=Path)
    start.add_argument("--installed-root", action="append", default=[], type=Path)
    start.add_argument("--client", choices=(*HOSTS, "all"), default="claude")
    start.add_argument("--state-root", type=Path)
    start.add_argument("--single")
    start.add_argument("--no-delete", action="store_true")
    start.add_argument("--dry-run", action="store_true")
    listing = commands.add_parser("desired")
    listing.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    listing.add_argument("--plan", type=Path)
    hosts = commands.add_parser("hosts")
    hosts.add_argument("--plan", required=True, type=Path)
    profile = commands.add_parser("profile-preflight")
    profile.add_argument("--plan", required=True, type=Path)
    keep = commands.add_parser("preserve")
    keep.add_argument("--plan", type=Path)
    keep.add_argument("--source-root", type=Path)
    keep.add_argument("--installed-root", type=Path)
    keep.add_argument("--stage-root", required=True, type=Path)
    end = commands.add_parser("finish")
    end.add_argument("--plan", required=True, type=Path)
    end.add_argument("--installed-root", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.source_root, args.installed_root, args.client,
                             state_root=args.state_root, single=args.single,
                             no_delete=args.no_delete, dry_run=args.dry_run)
            print(json.dumps(result, sort_keys=True) if args.dry_run else result)
        elif args.command == "desired":
            for name in desired(args.source_root, args.plan):
                print(name)
        elif args.command == "hosts":
            for host in _load_plan(args.plan)["hosts"]:
                print(host)
        elif args.command == "profile-preflight":
            plan = _load_plan(args.plan)
            if "icode-local" in plan["desired_names"]:
                script = _safe(Path(plan["source_root"]) / "mcp/icode-local/profile.py")
                if not script.is_file():
                    raise RuntimeError("missing icode-local profile preflight")
                for root in plan["installed_roots"]:
                    try:
                        result = subprocess.run([sys.executable, str(script), "preflight", "--target",
                            str(_payload(root, "icode-local")), "--hosts", ",".join(plan["hosts"])],
                            stdin=subprocess.DEVNULL, timeout=60, capture_output=True)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError("icode-local profile preflight timed out") from exc
                    if result.returncode:
                        raise RuntimeError("icode-local profile preflight failed; check selected host configuration paths")
        elif args.command == "preserve":
            preserve(args.plan, args.stage_root, installed_root=args.installed_root,
                     source_root=args.source_root)
        else:
            result = finish(args.plan, args.installed_root)
            print(json.dumps(result, sort_keys=True))
            return 2 if result["status"] == "conflict" else 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        # Never print JSON parser text or registration values (may be secrets).
        message = str(exc) if isinstance(exc, RuntimeError) else "MCP reconciliation operation failed"
        print(message, file=sys.stderr)
        return 2 if "ownership conflict" in message else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
