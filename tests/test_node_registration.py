"""Isolated contracts for host-specific managed Node registration."""
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import runpy

import pytest

LIB = Path(__file__).resolve().parents[1] / "mcp" / "_lib"
sys.path.insert(0, str(LIB))


def registration():
    assert (LIB / "node_registration.py").is_file(), "managed registration helper is missing"
    return importlib.import_module("node_registration")


def test_literal_legacy_wrappers_keep_cli_suffix():
    module = registration()
    spec = {"package": "@upstash/context7-mcp", "version": "4.7.0"}
    forms = [
        {"command": "npx", "args": ["-y", spec["package"], "--transport", "stdio"]},
        {"command": "C:/Node/node.exe", "args": ["C:/Node/npx-cli.js", "-y", spec["package"] + "@4.7.0", "--transport", "stdio"]},
        {"command": "cmd", "args": ["/c", "C:/Node/node.exe", "C:/Node/npx-cli.js", "-y", spec["package"], "--transport", "stdio"]},
    ]
    for entry in forms:
        assert module.legacy_suffix(entry, spec) == ["--transport", "stdio"]
    assert module.legacy_suffix({"command": "cmd", "args": ["/c", "node npx-cli.js -y " + spec["package"]]}, spec) is None
    assert module.legacy_suffix({"command": "cmd", "args": ["/c", "node", "npx-cli.js", "-y", spec["package"], "&", "echo"]}, spec) is None
    assert module.legacy_suffix({"command": "npx", "args": ["-y", spec["package"] + "@9.0.0"]}, spec) is None


def test_codex_nondefault_metadata_is_not_representable():
    module = registration()
    base = {"name": "context7", "enabled": True, "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@upstash/context7-mcp"], "env": {}, "env_vars": [], "cwd": None}}
    assert module.codex_representable(base)
    for field, value in [("enabled", False), ("startup_timeout_sec", 41), ("enabled_tools", ["foo"])]:
        assert not module.codex_representable({**base, field: value})
    assert not module.codex_representable({**base, "transport": {**base["transport"], "cwd": "/private"}})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    module = registration()
    assert hasattr(module, "register_managed"), "host-specific registration is missing"
    monkeypatch.setattr(module.claude_registry, "CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(module.claude_registry, "ENTRY_DIR", tmp_path / "exports")
    monkeypatch.setattr(module.client_registry, "CODEBUDDY_CFG", tmp_path / ".codebuddy/mcp.json")
    monkeypatch.setattr(module.client_registry, "WORKBUDDY_CFG", tmp_path / ".workbuddy/mcp.json")
    spec = {"package": "@upstash/context7-mcp", "version": "4.7.0", "bin": "dist/index.js", "root": str(tmp_path / "catalog")}
    runtime = {"root": str(tmp_path / "runtime/context7/hash"), "entry": str(tmp_path / "runtime/context7/hash/node_modules/pkg/dist/index.js"), "package": spec["package"], "version": spec["version"], "lock_sha256": "a" * 64, "node": str(tmp_path / "node.exe")}
    Path(runtime["node"]).write_text("isolated node fixture", encoding="utf-8")
    packages = SimpleNamespace(load_spec=lambda *a, **kw: spec, ensure_runtime=lambda *a, **kw: dict(runtime), verify_runtime=lambda *a, **kw: dict(runtime), verify_owned_runtime=lambda server, root, **kw: {"root": root, "entry": kw["entry"], "package": kw["package"], "version": kw["version"], "lock_sha256": kw["lock_sha256"], "node": runtime["node"]}, default_runtime_root=lambda: tmp_path / "runtime")
    monkeypatch.setattr(module, "_packages", lambda: packages)
    return SimpleNamespace(module=module, spec=spec, runtime=runtime, root=tmp_path, packages=packages)


def put(host, fixture, entry):
    path = fixture.module._config(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme": "unchanged", "mcpServers": {"other": {"command": "keep"}, "context7": entry}}), encoding="utf-8")


def legacy(fixture, **extra):
    return {"command": "npx", "args": ["-y", fixture.spec["package"], "--transport", "stdio"], **extra}


def test_each_host_preserves_its_settings_and_receipts_have_no_values(isolated):
    f = isolated
    originals = {}
    for host in ("claude", "codebuddy", "workbuddy"):
        originals[host] = legacy(f, env={"TOKEN": "private-" + host}, cwd="/host/" + host, disabled=True, timeout=91, toolsFilter=[host])
        put(host, f, originals[host])
    result = f.module.register_managed("context7", f.runtime["node"], hosts=list(originals))
    assert result == 0
    for host, original in originals.items():
        data = json.loads(f.module._config(host).read_text(encoding="utf-8"))
        actual = data["mcpServers"]["context7"]
        assert actual == {**original, "command": f.runtime["node"], "args": [f.runtime["entry"], "--transport", "stdio"]}
        assert data["theme"] == "unchanged" and data["mcpServers"]["other"] == {"command": "keep"}
        assert f.module.verify_host("context7", host)
    receipt_text = (f.root / "runtime/registrations/context7.json").read_text(encoding="utf-8")
    assert "private-" not in receipt_text and "TOKEN" not in receipt_text and "--transport" not in receipt_text


def test_unselected_hosts_and_opaque_custom_are_preserved(isolated):
    f = isolated
    put("claude", f, {"command": "custom-context7", "args": [], "env": {"SECRET": "hidden"}})
    put("workbuddy", f, legacy(f))
    before = f.module._config("workbuddy").read_bytes()
    assert f.module.register_managed("context7", f.runtime["node"], hosts=["claude"]) == 0
    assert f.module._config("workbuddy").read_bytes() == before
    assert f.module.verify_host("context7", "claude")
    assert not f.module.verify_host("context7", "workbuddy")
    assert f.module._read_receipt("context7")["hosts"]["claude"]["status"] == "retained"


def test_all_preflight_before_any_config_write(isolated):
    f = isolated
    put("claude", f, legacy(f))
    path = f.module._config("workbuddy")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    before = f.module._config("claude").read_bytes()
    with pytest.raises(RuntimeError, match="configuration"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude", "workbuddy"])
    assert f.module._config("claude").read_bytes() == before


def test_concurrent_host_edit_rejected_and_partial_install_retryable(isolated, monkeypatch):
    f = isolated
    for host in ("claude", "workbuddy"):
        put(host, f, legacy(f))
    original_write = f.module._write_host
    def changed(host, name, entry, **kwargs):
        original_write(host, name, entry, **kwargs)
        if host == "claude":
            put("workbuddy", f, legacy(f, env={"EDIT": "new"}))
    monkeypatch.setattr(f.module, "_write_host", changed)
    with pytest.raises(RuntimeError, match="concurrent"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude", "workbuddy"])
    assert f.module.verify_host("context7", "claude")
    assert not f.module.verify_host("context7", "workbuddy")
    monkeypatch.setattr(f.module, "_write_host", original_write)
    assert f.module.register_managed("context7", f.runtime["node"], hosts=["claude", "workbuddy"]) == 0
    assert f.module.verify_host("context7", "workbuddy")


def test_custom_override_uses_legacy_registration_path(isolated):
    assert isolated.module.register_managed("context7", isolated.runtime["node"], package_override="custom-package") is None


def test_host_registration_entrypoint_exists():
    assert callable(getattr(registration(), "register_managed", None)), "host-specific registration entrypoint is missing"


def test_codex_metadata_conflict_stops_all_writes(isolated, monkeypatch):
    f = isolated
    put("claude", f, legacy(f))
    before = f.module._config("claude").read_bytes()
    original = f.module._actual
    codex = {"name": "context7", "enabled": False, "transport": {"type": "stdio", **legacy(f)}}
    monkeypatch.setattr(f.module, "_actual", lambda host, name: codex if host == "codex" else original(host, name))
    with pytest.raises(RuntimeError, match="metadata"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude", "codex"])
    assert f.module._config("claude").read_bytes() == before


def test_memory_preserves_original_graph_location_without_reading_it(isolated, monkeypatch):
    f = isolated
    assert callable(getattr(f.module, "memory_path", None)), "memory path preservation is missing"
    old_cli = f.root / "old-cache/node_modules/pkg/dist/index.js"
    old_cli.parent.mkdir(parents=True)
    old_cli.write_text("not executed", encoding="utf-8")
    graph = old_cli.with_name("memory.json")
    graph.write_text("private graph never read", encoding="utf-8")
    monkeypatch.setattr(f.module, "_find_old_memory_cli", lambda *a, **kw: old_cli)
    monkeypatch.setattr(f.module, "_expanded_host_path", lambda node, old, value: Path.home() / value[2:].lstrip("/"), raising=False)
    assert f.module.memory_path(None, None, f.spec, "node") == str(graph)
    graph_jsonl = old_cli.with_name("memory.jsonl")
    graph_jsonl.write_text("private graph never read", encoding="utf-8")
    assert f.module.memory_path(None, None, f.spec, "node") == str(graph_jsonl)
    assert f.module.memory_path("relative/data.jsonl", None, f.spec, "node") == str(old_cli.parent / "relative/data.jsonl")
    assert f.module.memory_path(str(f.root / "explicit.jsonl"), None, f.spec, "node") == str(f.root / "explicit.jsonl")
    assert f.module.memory_path("~/graph.jsonl", None, f.spec, "node") == str(Path.home() / "graph.jsonl")
    # Fresh install has no old node and gets a stable directory outside packages.
    fresh = f.module.memory_path(None, False, f.spec, "node")
    assert fresh == str(f.root / "runtime/data/memory/memory.jsonl")


def test_memory_ambiguous_cache_never_changes_registration(isolated, monkeypatch):
    f = isolated
    assert callable(getattr(f.module, "memory_path", None)), "memory path preservation is missing"
    def ambiguous(*args, **kwargs):
        raise RuntimeError("ambiguous old memory cache")
    monkeypatch.setattr(f.module, "_find_old_memory_cli", ambiguous)
    f.spec.update(package="@modelcontextprotocol/server-memory", version="1.0.0")
    path = f.module._config("claude")
    path.write_text(json.dumps({"mcpServers": {"memory": {"command": "npx", "args": ["-y", f.spec["package"]]}}}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="ambiguous"):
        f.module.register_managed("memory", f.runtime["node"], hosts=["claude"])
    assert path.read_bytes() == before


def test_old_memory_cli_requires_unique_matching_lock_and_bin(isolated, monkeypatch):
    f = isolated
    assert callable(getattr(f.module, "_find_old_memory_cli", None)), "old memory cache proof is missing"
    f.spec.update(package="@modelcontextprotocol/server-memory", version="1.0.0")
    metadata = {"version": "1.0.0", "resolved": "https://registry.npmjs.org/@modelcontextprotocol/server-memory/-/server-memory-1.0.0.tgz", "integrity": "sha512-fixture", "bin": {"mcp-server-memory": "dist/index.js"}}
    key = "node_modules/" + f.spec["package"]
    catalog = Path(f.spec["root"])
    catalog.mkdir()
    (catalog / "package-lock.json").write_text(json.dumps({"packages": {key: metadata}}), encoding="utf-8")
    cache = f.root / "cache"
    monkeypatch.setattr(f.module, "_npm_cache", lambda *a: cache)
    def candidate(name, version="1.0.0"):
        directory = cache / "_npx" / name
        package = directory / key
        (package / "dist").mkdir(parents=True)
        (package / "dist/index.js").write_text("not executed", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({"name": f.spec["package"], "version": version, "bin": metadata["bin"]}), encoding="utf-8")
        (directory / "package-lock.json").write_text(json.dumps({"packages": {key: {**metadata, "version": version}}}), encoding="utf-8")
        return package / "dist/index.js"
    expected = candidate("one")
    candidate("wrong", "9.0.0")
    trusted_cli = f.root / "trusted.js"
    trusted_cli.write_text("not executed", encoding="utf-8")
    assert f.module._find_old_memory_cli("node", f.spec, trusted_cli) == expected
    expected.write_text("locally modified CLI", encoding="utf-8")
    with pytest.raises(RuntimeError, match="prove"):
        f.module._find_old_memory_cli("node", f.spec, trusted_cli)
    expected.write_text("not executed", encoding="utf-8")
    candidate("two")
    with pytest.raises(RuntimeError, match="ambiguous"):
        f.module._find_old_memory_cli("node", f.spec, trusted_cli)


@pytest.mark.parametrize("server,package", [("context7", "@upstash/context7-mcp"), ("memory", "@modelcontextprotocol/server-memory"), ("playwright", "@playwright/mcp"), ("sequential-thinking", "@modelcontextprotocol/server-sequential-thinking")])
def test_default_registrar_needs_no_npx(isolated, monkeypatch, server, package):
    f = isolated
    f.spec["package"] = package
    monkeypatch.setenv("ICODE_MCP_SELECTED_HOSTS", "claude")
    monkeypatch.setattr(sys, "argv", ["register_mcp.py", sys.executable, ""])
    with pytest.raises(SystemExit) as outcome:
        runpy.run_path(str(LIB.parent / server / "scripts/register_mcp.py"), run_name="__main__")
    assert outcome.value.code == 0
    node = f.module._actual("claude", server)
    assert node["command"] == f.runtime["node"] and node["args"] == [f.runtime["entry"]]


def test_thought_logging_explicit_false_survives_migration(isolated):
    f = isolated
    f.spec["package"] = "@modelcontextprotocol/server-sequential-thinking"
    path = f.module._config("claude")
    old = legacy(f, env={"DISABLE_THOUGHT_LOGGING": "false", "PRIVATE": "keep"})
    path.write_text(json.dumps({"mcpServers": {"sequential-thinking": old}}), encoding="utf-8")
    assert f.module.register_managed("sequential-thinking", f.runtime["node"], hosts=["claude"]) == 0
    assert f.module._actual("claude", "sequential-thinking")["env"] == old["env"]


def test_explicit_adoption_requires_exact_host_name_digest(isolated, monkeypatch):
    f = isolated
    f.spec["package"] = "@modelcontextprotocol/server-sequential-thinking"
    old = {"command": "custom-thinking", "args": [], "env": {"DISABLE_THOUGHT_LOGGING": "false"}}
    path = f.module._config("claude")
    path.write_text(json.dumps({"mcpServers": {"sequential-thinking": old}}), encoding="utf-8")
    monkeypatch.setenv("ICODE_MCP_NODE_ADOPT", "claude/sequential-thinking:" + f.module._digest(old))
    assert f.module.register_managed("sequential-thinking", f.runtime["node"], hosts=["claude"]) == 0
    assert f.module._actual("claude", "sequential-thinking")["command"] == f.runtime["node"]
    assert f.module._read_receipt("sequential-thinking")["hosts"]["claude"]["before_digest"] == f.module._digest(old)


@pytest.mark.parametrize("custom", [False, True])
def test_reconcile_accepts_host_receipts_and_retains_unknown_ownership(isolated, monkeypatch, custom):
    f = isolated
    r = importlib.import_module("install_reconcile")
    monkeypatch.setattr(r.client_registry, "CODEX_BIN", None)
    source, installed, state = f.root / "source", f.root / "installed", f.root / "state"
    for root in (source, installed):
        directory = root / "mcp/context7"
        directory.mkdir(parents=True)
        (directory / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    node = {"command": "custom-context7", "args": [], "disabled": True} if custom else legacy(f, env={"HOST_SECRET": "private"}, timeout=72)
    put("codebuddy", f, node)
    # Per-host settings do not match a shared Claude export, but are safe to
    # migrate or explicitly retain without granting ownership of custom code.
    plan = r.prepare(source, [installed], "codebuddy", state_root=state)
    f.module.register_managed("context7", f.runtime["node"], hosts=["codebuddy"])
    result = r.finish(plan, [installed])
    assert result["status"] == "complete" and result["registered"] == 1
    assert "private" not in Path(plan).read_text(encoding="utf-8")
    if custom:
        (source / "mcp/context7/install.sh").unlink()
        retired = r.prepare(source, [installed], "codebuddy", state_root=state)
        result = r.finish(retired, [installed])
        assert result["status"] == "conflict"
        assert f.module._actual("codebuddy", "context7") == node


def test_parent_installer_verifies_managed_hosts_without_copying_claude():
    text = (LIB.parent / "install.sh").read_text(encoding="utf-8")
    assert "node_registration.py" in text, "parent still copies Claude export over managed host settings"


@pytest.mark.parametrize("fail_add", [False, True])
def test_codex_cli_success_and_errors_preserve_host_env_without_logging_values(isolated, monkeypatch, capsys, fail_add):
    f = isolated
    r = importlib.import_module("install_reconcile")
    monkeypatch.setattr(f.module.client_registry, "CODEX_BIN", "stub-codex")
    current = {"name": "context7", "enabled": True, "disabled_reason": None,
               "transport": {"type": "stdio", **legacy(f, env={"TOKEN": "private-codex"}), "env_vars": [], "cwd": None},
               "enabled_tools": None, "disabled_tools": None, "startup_timeout_sec": None, "tool_timeout_sec": None}
    calls = []
    def cli(args):
        calls.append(args)
        if args[0] == "get":
            return SimpleNamespace(returncode=0, stdout=json.dumps(current), stderr="")
        if fail_add:
            return SimpleNamespace(returncode=7, stdout="private-output", stderr="private-error")
        env, index = {}, 2
        while args[index] == "--env":
            key, value = args[index + 1].split("=", 1)
            env[key] = value
            index += 2
        assert args[index] == "--"
        current["transport"].update(command=args[index + 1], args=args[index + 2:], env=env)
        return SimpleNamespace(returncode=0, stdout="added", stderr="")
    monkeypatch.setattr(r, "_codex_command", cli)
    if fail_add:
        with pytest.raises(RuntimeError, match="CLI registration failed"):
            f.module.register_managed("context7", f.runtime["node"], hosts=["codex"])
        assert current["transport"]["command"] == "npx"
    else:
        assert f.module.register_managed("context7", f.runtime["node"], hosts=["codex"]) == 0
        assert current["transport"]["env"] == {"TOKEN": "private-codex"}
        assert f.module.verify_host("context7", "codex")
    captured = capsys.readouterr()
    assert "private-" not in captured.out + captured.err


def test_selected_host_snapshot_rejects_edits_between_prepare_and_child(isolated, monkeypatch):
    f = isolated
    r = importlib.import_module("install_reconcile")
    source, installed = f.root / "source", f.root / "installed"
    directory = source / "mcp/context7"
    directory.mkdir(parents=True)
    (directory / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    put("codebuddy", f, legacy(f))
    plan = r.prepare(source, [installed], "codebuddy", state_root=f.root / "state")
    monkeypatch.setenv("ICODE_MCP_RECONCILE_PLAN", str(plan))
    put("codebuddy", f, legacy(f, env={"USER_EDIT": "preserved"}))
    before = f.module._config("codebuddy").read_bytes()
    with pytest.raises(RuntimeError, match="snapshot"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["codebuddy"])
    assert f.module._config("codebuddy").read_bytes() == before


def test_new_node_path_does_not_use_initial_package_receipt_node(isolated):
    f = isolated
    put("claude", f, legacy(f))
    original = f.runtime["node"]
    f.packages.verify_runtime = lambda *a, **kw: {**f.runtime, "node": original + ".old"}
    f.module.register_managed("context7", original, hosts=["claude"])
    assert f.module.verify_host("context7", "claude"), "registration must verify the chosen Node, not initial package installer Node"


def test_owned_registration_can_repair_removed_node_executable(isolated):
    f = isolated
    put("claude", f, legacy(f))
    f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    Path(f.runtime["node"]).unlink()
    replacement = f.root / "new-node.exe"
    replacement.write_text("replacement node fixture", encoding="utf-8")
    f.runtime["node"] = str(replacement)
    assert f.module.register_managed("context7", str(replacement), hosts=["claude"]) == 0
    assert f.module.verify_host("context7", "claude")


def test_corrupt_registration_receipt_fails_closed_without_traceback_values(isolated):
    f = isolated
    path = f.module._receipt_path("context7")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "server": "context7", "hosts": {"claude": {"status": "migrated", "digest": "a" * 64}}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="ownership"):
        f.module._read_receipt("context7")


def test_npm_cache_query_uses_original_node_npm_env_and_cwd(isolated, monkeypatch):
    f = isolated
    old_node = f.root / "old-node/node.exe"
    old_node.parent.mkdir()
    old_node.touch()
    old_bin = f.root / "old-npm/bin"
    old_bin.mkdir(parents=True)
    for name in ("npx-cli.js", "npm-cli.js"):
        (old_bin / name).touch()
    old_cwd = f.root / "old-cwd"
    old_cwd.mkdir()
    old = {"command": str(old_node), "args": [str(old_bin / "npx-cli.js"), "-y", "@modelcontextprotocol/server-memory"],
           "env": {"NPM_CONFIG_CACHE": str(f.root / "host-cache"), "HOST_ONLY": "private"}, "cwd": str(old_cwd)}
    def run(args, **kwargs):
        assert args == [str(old_node), str(old_bin / "npm-cli.js"), "config", "get", "cache"]
        assert kwargs["cwd"] == str(old_cwd)
        assert kwargs["env"]["HOST_ONLY"] == "private"
        return SimpleNamespace(returncode=0, stdout=kwargs["env"]["NPM_CONFIG_CACHE"] + "\n")
    monkeypatch.setattr(f.module.subprocess, "run", run)
    assert f.module._npm_cache(f.runtime["node"], old) == f.root / "host-cache"


@pytest.mark.parametrize("value,suffix", [("~", ""), ("~/graph.jsonl", "graph.jsonl"), ("~//graph.jsonl", "graph.jsonl")])
def test_memory_tilde_uses_old_host_node_homedir(isolated, monkeypatch, value, suffix):
    f = isolated
    host_home = f.root / "host-home"
    old = {"command": "npx", "args": [], "env": {"HOME": str(host_home), "USERPROFILE": str(host_home)}}
    def run(args, **kwargs):
        assert "homedir" in args[2]
        assert kwargs["env"]["HOME"] == str(host_home)
        assert kwargs["env"]["USERPROFILE"] == str(host_home)
        return SimpleNamespace(returncode=0, stdout=str(host_home / suffix))
    monkeypatch.setattr(f.module.subprocess, "run", run)
    assert f.module.memory_path(value, old, f.spec, f.runtime["node"]) == str(host_home / suffix)


def test_memory_backslash_tilde_keeps_locked_cli_relative_semantics(isolated, monkeypatch):
    f = isolated
    old_cli = f.root / "old-dist/index.js"
    monkeypatch.setattr(f.module, "_find_old_memory_cli", lambda *a, **kw: old_cli)
    assert f.module.memory_path("~\\graph.jsonl", {"env": {}}, f.spec, "node") == str(old_cli.parent / "~\\graph.jsonl")


def test_unknown_host_cwd_and_local_package_refuse_cache_guess(isolated):
    f = isolated
    old = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}
    with pytest.raises(RuntimeError, match="cwd"):
        f.module._npm_cache(f.runtime["node"], old)
    cwd = f.root / "project/subdir"
    cwd.mkdir(parents=True)
    local = f.root / "project/node_modules/@modelcontextprotocol/server-memory"
    local.mkdir(parents=True)
    (local / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="local"):
        f.module._npm_cache(f.runtime["node"], {**old, "cwd": str(cwd)})


def test_final_config_read_rejects_target_edit(isolated, monkeypatch):
    f = isolated
    put("claude", f, legacy(f, disabled=False))
    original = f.module._read_config
    reads = 0
    def changed(path):
        nonlocal reads
        if path == f.module._config("claude"):
            reads += 1
            if reads == 3:
                put("claude", f, legacy(f, disabled=True))
        return original(path)
    monkeypatch.setattr(f.module, "_read_config", changed)
    with pytest.raises(RuntimeError, match="concurrent"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    assert original(f.module._config("claude"))["mcpServers"]["context7"]["disabled"] is True


def test_explicit_old_cli_hint_is_bound_to_host_digest_and_package_proof(isolated, monkeypatch):
    f = isolated
    f.spec.update(package="@modelcontextprotocol/server-memory", version="1.0.0")
    f.runtime.update(package=f.spec["package"], version=f.spec["version"])
    key = "node_modules/" + f.spec["package"]
    record = {"version": "1.0.0", "resolved": "https://registry.npmjs.org/memory.tgz", "integrity": "sha512-fixture", "bin": {"memory": "dist/index.js"}}
    catalog = Path(f.spec["root"])
    catalog.mkdir()
    (catalog / "package-lock.json").write_text(json.dumps({"packages": {key: record}}), encoding="utf-8")
    trusted = Path(f.runtime["entry"])
    trusted.parent.mkdir(parents=True)
    trusted.write_text("locked CLI", encoding="utf-8")
    cli = f.root / "host-cache/_npx/proven" / key / f.spec["bin"]
    cli.parent.mkdir(parents=True)
    cli.write_text("locked CLI", encoding="utf-8")
    package = cli.parent.parent
    (package / "package.json").write_text(json.dumps({"name": f.spec["package"], "version": "1.0.0", "bin": record["bin"]}), encoding="utf-8")
    directory = f.root / "host-cache/_npx/proven"
    (directory / "package-lock.json").write_text(json.dumps({"packages": {key: record}}), encoding="utf-8")
    old = {"command": "npx", "args": ["-y", f.spec["package"]], "env": {"NPM_CONFIG_CACHE": str(f.root / "host-cache")}}
    config = f.module._config("claude")
    config.write_text(json.dumps({"mcpServers": {"memory": old}}), encoding="utf-8")
    hint = f.root / "private-cli-hint.json"
    hints = {"version": 1, "hosts": {"claude": {"before_digest": f.module._digest(old), "cli": str(cli)}}}
    hint.write_text(json.dumps(hints), encoding="utf-8")
    monkeypatch.setenv("ICODE_MCP_MEMORY_CLI_HINT", str(hint))
    def no_guess(*args):
        raise AssertionError("process evidence must avoid guessing installer npm context")
    monkeypatch.setattr(f.module, "_npm_cache", no_guess)
    assert f.module.register_managed("memory", f.runtime["node"], hosts=["claude"]) == 0
    actual = f.module._actual("claude", "memory")
    assert actual["env"]["MEMORY_FILE_PATH"] == str(cli.with_name("memory.jsonl"))
    assert f.module.verify_host("memory", "claude")
    # The evidence is one-shot and cannot authorize a different old node.
    with pytest.raises(RuntimeError, match="hint"):
        f.module._find_old_memory_cli(f.runtime["node"], f.spec, trusted, {**old, "disabled": True}, host="claude")
    cli.write_text("locally edited CLI", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hint"):
        f.module._find_old_memory_cli(f.runtime["node"], f.spec, trusted, old, host="claude")


@pytest.mark.parametrize("after_host_write", [False, True])
def test_pending_receipt_recovers_only_old_or_intended_node(isolated, monkeypatch, after_host_write):
    f = isolated
    original_node = legacy(f, env={"SECRET": "kept-private"}, disabled=True)
    put("claude", f, original_node)
    original_write = f.module._write_receipt
    def fail_receipt(server, data):
        if not after_host_write or data["hosts"]["claude"]["status"] == "migrated":
            raise OSError("injected private disk failure")
        original_write(server, data)
    monkeypatch.setattr(f.module, "_write_receipt", fail_receipt)
    with pytest.raises(OSError):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    if after_host_write:
        assert f.module._actual("claude", "context7")["command"] == f.runtime["node"]
        assert f.module._read_receipt("context7")["hosts"]["claude"]["status"] == "pending"
        assert not f.module.verify_host("context7", "claude")
        assert "kept-private" not in f.module._receipt_path("context7").read_text(encoding="utf-8")
    else:
        assert f.module._actual("claude", "context7") == original_node
    monkeypatch.setattr(f.module, "_write_receipt", original_write)
    assert f.module.register_managed("context7", f.runtime["node"], hosts=["claude"]) == 0
    assert f.module._read_receipt("context7")["hosts"]["claude"]["status"] == "migrated"
    assert f.module.verify_host("context7", "claude")


def test_pending_old_node_retries_but_third_edit_is_not_rebased(isolated, monkeypatch):
    f = isolated
    put("claude", f, legacy(f))
    original_write = f.module._write_host
    def fail(*args, **kwargs):
        raise OSError("injected host write failure")
    monkeypatch.setattr(f.module, "_write_host", fail)
    with pytest.raises(OSError):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    assert f.module._read_receipt("context7")["hosts"]["claude"]["status"] == "pending"
    assert not f.module.verify_host("context7", "claude")
    monkeypatch.setattr(f.module, "_write_host", original_write)
    put("claude", f, legacy(f, disabled=True))
    before = f.module._config("claude").read_bytes()
    with pytest.raises(RuntimeError, match="conflict"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    assert f.module._config("claude").read_bytes() == before


@pytest.mark.parametrize("version", ["4.7.0", "4.8.0"])
def test_catalog_upgrade_verifies_old_identity_then_installs_new_lock(isolated, monkeypatch, version):
    f = isolated
    original = legacy(f, env={"SECRET": "preserved"}, cwd="/host/cwd", disabled=True)
    put("claude", f, original)
    f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    old_runtime = dict(f.runtime)
    seen = []
    def owned(server, root, **identity):
        seen.append(identity)
        assert identity["lock_sha256"] == old_runtime["lock_sha256"]
        assert identity["version"] == old_runtime["version"]
        assert identity["entry"] == old_runtime["entry"]
        return old_runtime
    monkeypatch.setattr(f.packages, "verify_owned_runtime", owned)
    def current_verify(*args, **kwargs):
        raise ValueError("old runtime cannot be validated with new catalog")
    monkeypatch.setattr(f.packages, "verify_runtime", current_verify)
    f.spec["version"] = version
    f.runtime.update(version=version, lock_sha256="b" * 64, root=str(f.root / "runtime/context7/new-lock"), entry=str(f.root / "runtime/context7/new-lock/cli.js"))
    assert f.module.register_managed("context7", f.runtime["node"], hosts=["claude"]) == 0
    assert seen
    actual = f.module._actual("claude", "context7")
    assert actual == {**original, "command": f.runtime["node"], "args": [f.runtime["entry"], "--transport", "stdio"]}
    assert f.module._read_receipt("context7")["hosts"]["claude"]["lock_sha256"] == "b" * 64


@pytest.mark.parametrize("value", ["absolute", "relative", "~/graph.jsonl", "~//graph.jsonl"])
def test_memory_preserves_inherited_memory_path(isolated, monkeypatch, value):
    f = isolated
    f.spec.update(package="@modelcontextprotocol/server-memory", version="1.0.0")
    old_cli = f.root / "old-cache/dist/index.js"
    monkeypatch.setattr(f.module, "_find_old_memory_cli", lambda *a, **kw: old_cli)
    monkeypatch.setattr(f.module, "_expanded_host_path", lambda node, old, val: f.root / "host-home" / val.lstrip("~/"))
    configured = str(f.root / "inherited.jsonl") if value == "absolute" else "relative/graph.jsonl" if value == "relative" else value
    monkeypatch.setenv("MEMORY_FILE_PATH", configured)
    path = f.module._config("claude")
    path.write_text(json.dumps({"mcpServers": {"memory": {"command": "npx", "args": ["-y", f.spec["package"]]}}}), encoding="utf-8")
    f.module.register_managed("memory", f.runtime["node"], hosts=["claude"])
    expected = str(f.root / "inherited.jsonl") if value == "absolute" else str(old_cli.parent / "relative/graph.jsonl") if value == "relative" else str(f.root / "host-home/graph.jsonl")
    assert f.module._actual("claude", "memory")["env"]["MEMORY_FILE_PATH"] == expected


def test_non_node_classification_when_node_helper_is_unavailable(monkeypatch):
    r = importlib.import_module("install_reconcile")
    monkeypatch.setitem(sys.modules, "node_registration", None)
    assert r._node_managed("ordinary-python") is False


@pytest.mark.skipif(os.name != "nt", reason="Windows environment keys are case insensitive")
def test_memory_lowercase_explicit_key_wins_over_inherited_without_duplicates(isolated, monkeypatch):
    f = isolated
    f.spec.update(package="@modelcontextprotocol/server-memory", version="1.0.0")
    old_cli = f.root / "old-cache/dist/index.js"
    monkeypatch.setattr(f.module, "_find_old_memory_cli", lambda *a, **kw: old_cli)
    monkeypatch.setenv("MEMORY_FILE_PATH", str(f.root / "inherited.jsonl"))
    path = f.module._config("claude")
    old = {"command": "npx", "args": ["-y", f.spec["package"]], "env": {"memory_file_path": "relative.jsonl", "KEEP": "unchanged"}}
    path.write_text(json.dumps({"mcpServers": {"memory": old}}), encoding="utf-8")
    f.module.register_managed("memory", f.runtime["node"], hosts=["claude"])
    env = f.module._actual("claude", "memory")["env"]
    keys = [key for key in env if key.lower() == "memory_file_path"]
    assert len(keys) == 1
    assert env[keys[0]] == str(old_cli.parent / "relative.jsonl")
    assert env["KEEP"] == "unchanged"


@pytest.mark.parametrize("value", ["absolute", "relative"])
def test_fresh_memory_inherited_path_is_explicit_or_refused(isolated, monkeypatch, value):
    f = isolated
    f.spec.update(package="@modelcontextprotocol/server-memory", version="1.0.0")
    configured = str(f.root / "inherited.jsonl") if value == "absolute" else "relative.jsonl"
    monkeypatch.setenv("MEMORY_FILE_PATH", configured)
    if value == "relative":
        with pytest.raises(RuntimeError, match="relative memory"):
            f.module.register_managed("memory", f.runtime["node"], hosts=["claude"])
        assert f.module._actual("claude", "memory") is None
    else:
        f.module.register_managed("memory", f.runtime["node"], hosts=["claude"])
        assert f.module._actual("claude", "memory")["env"]["MEMORY_FILE_PATH"] == configured


def test_pending_finish_rejected_and_same_plan_resume_preserves_snapshot(isolated, monkeypatch):
    f = isolated
    r = importlib.import_module("install_reconcile")
    source, installed, state = f.root / "source", f.root / "installed", f.root / "state"
    directory = source / "mcp/context7"
    directory.mkdir(parents=True)
    (directory / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    put("codebuddy", f, legacy(f))
    plan = r.prepare(source, [installed], "codebuddy", state_root=state)
    monkeypatch.setenv("ICODE_MCP_RECONCILE_PLAN", str(plan))
    original = f.module._write_receipt
    def fail_promote(server, data):
        if data["hosts"]["codebuddy"]["status"] == "migrated":
            raise OSError("injected promotion failure")
        original(server, data)
    monkeypatch.setattr(f.module, "_write_receipt", fail_promote)
    with pytest.raises(OSError):
        f.module.register_managed("context7", f.runtime["node"], hosts=["codebuddy"])
    with pytest.raises(RuntimeError, match="verification"):
        r.finish(plan, [installed])
    # New outer prepares may also inspect recoverable pending state safely.
    r.prepare(source, [installed], "codebuddy", state_root=state)
    monkeypatch.setattr(f.module, "_write_receipt", original)
    f.module.register_managed("context7", f.runtime["node"], hosts=["codebuddy"])
    assert r.finish(plan, [installed])["status"] == "complete"


def test_old_runtime_tamper_blocks_upgrade_before_any_write(isolated, monkeypatch):
    f = isolated
    put("claude", f, legacy(f))
    f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    before = f.module._config("claude").read_bytes()
    def damaged(*args, **kwargs):
        raise ValueError("owned runtime file digest mismatch")
    monkeypatch.setattr(f.packages, "verify_owned_runtime", damaged)
    f.spec["version"] = "4.8.0"
    with pytest.raises(ValueError, match="digest"):
        f.module.register_managed("context7", f.runtime["node"], hosts=["claude"])
    assert f.module._config("claude").read_bytes() == before
