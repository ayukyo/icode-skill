"""Managed MCP convergence uses isolated homes and real JSON registry adapters."""
import importlib
import json
import os
import subprocess
import stat
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

LIB = Path(__file__).resolve().parents[1] / "mcp/_lib"
sys.path.insert(0, str(LIB))


def test_engine_entrypoint_exists():
    assert (LIB / "install_reconcile.py").exists(), "MCP reconciliation engine is missing"


@pytest.fixture
def env(tmp_path, monkeypatch):
    r = importlib.import_module("install_reconcile")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(r.claude_registry, "CLAUDE_JSON", home / ".claude.json")
    monkeypatch.setattr(r.claude_registry, "ENTRY_DIR", home / "entries")
    monkeypatch.setattr(r.client_registry, "ENTRY_DIR", home / "entries")
    monkeypatch.setattr(r.client_registry, "CODEBUDDY_CFG", home / ".codebuddy/mcp.json")
    monkeypatch.setattr(r.client_registry, "WORKBUDDY_CFG", home / ".workbuddy/mcp.json")
    monkeypatch.setattr(r.client_registry, "CODEX_BIN", None)
    source = tmp_path / "source"
    installed = home / ".claude/skills/icode"
    state = home / "state"
    source.mkdir()
    installed.mkdir(parents=True)
    return r, source, installed, state


def payload(root, name):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".icode-install-owner.json").write_text(json.dumps(
        {"schema_version": 1, "owner": "icode-skill", "skill": "icode"}), encoding="utf-8")
    directory = root / "mcp" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (directory / "server.py").write_text("print('ok')\n", encoding="utf-8")
    return directory


def register(r, name, **extra):
    entry = {"command": "python", "args": [name], "env": {"API_KEY": "secret-value"}, **extra}
    r.claude_registry.register(name, entry)
    return entry


def prepare(env, **kwargs):
    r, source, installed, state = env
    return r.prepare(source, [installed], "claude", state_root=state, **kwargs)


def test_ab_to_bc_archives_retired_payload_and_preserves_foreign_nodes(env):
    r, source, installed, state = env
    for name in ("a", "b"):
        payload(source, name)
        payload(installed, name)
        register(r, name)
    plan = prepare(env)
    assert r.finish(plan, [installed])["status"] == "complete"
    # Simulate the next distribution without removing installed recovery code.
    (source / "mcp/a/install.sh").unlink()
    payload(source, "c")
    payload(installed, "c")
    register(r, "c")
    cfg = r.claude_registry._read_cfg(r.claude_registry.CLAUDE_JSON)
    cfg["extra"] = {"keep": True}
    cfg["mcpServers"]["foreign"] = {"command": "keep"}
    r.claude_registry._write_cfg(r.claude_registry.CLAUDE_JSON, cfg)
    plan = prepare(env)
    assert r.finish(plan, [installed])["status"] == "complete"
    assert r.claude_registry.inspect("a") is None
    assert r.claude_registry.inspect("foreign") == {"command": "keep"}
    assert not (installed / "mcp/a").exists()
    archives = list((state / "archives").rglob("server.py"))
    assert archives and all("skills" not in p.relative_to(state).parts for p in archives)
    receipt = (state / "receipt.json").read_text(encoding="utf-8")
    assert "secret-value" not in receipt
    assert r.claude_registry._read_cfg(r.claude_registry.CLAUDE_JSON)["extra"] == {"keep": True}
    assert r.finish(prepare(env), [installed])["status"] == "complete"


def test_missing_desired_registration_never_retires_old_entry(env):
    r, source, installed, state = env
    payload(installed, "a")
    register(r, "a")
    payload(source, "b")
    plan = prepare(env)
    with pytest.raises(RuntimeError, match="desired"):
        r.finish(plan, [installed])
    assert r.claude_registry.inspect("a") is not None
    assert (installed / "mcp/a/server.py").exists()
    assert not (state / "receipt.json").exists()


def test_legacy_adoption_requires_complete_entry_and_conflicts_are_retained(env):
    r, source, installed, _ = env
    payload(installed, "a")
    register(r, "a")
    config = r.claude_registry._read_cfg(r.claude_registry.CLAUDE_JSON)
    config["mcpServers"]["a"]["user_extra"] = True
    r.claude_registry._write_cfg(r.claude_registry.CLAUDE_JSON, config)
    result = r.finish(prepare(env), [installed])
    assert result["status"] == "conflict"
    assert r.claude_registry.inspect("a")["user_extra"] is True
    assert (installed / "mcp/a").exists()


def test_dry_run_writes_nothing_and_single_and_no_delete_keep_old(env):
    r, source, installed, state = env
    payload(installed, "a")
    payload(source, "b")
    register(r, "a")
    before = sorted(str(p) for p in installed.parent.parent.parent.rglob("*"))
    preview = prepare(env, dry_run=True)
    assert "a" in preview["retired"]["claude"]
    assert not state.exists()
    assert before == sorted(str(p) for p in installed.parent.parent.parent.rglob("*"))
    register(r, "b")
    assert r.finish(prepare(env, single="b"), [installed])["status"] == "complete"
    assert r.claude_registry.inspect("a") is not None
    result = r.finish(prepare(env, no_delete=True), [installed])
    assert result["retained"] and r.claude_registry.inspect("a") is not None


def test_preserve_copies_retired_source_but_catalog_excludes_it(env):
    r, source, installed, _ = env
    old = payload(installed, "a")
    (old / ".venv").mkdir()
    (old / ".venv/python").write_text("large runtime", encoding="utf-8")
    payload(source, "b")
    register(r, "a")
    plan = prepare(env)
    stage = installed.parent / ".icode-stage-123"
    payload(stage, "b")
    r.preserve(plan, stage)
    assert (stage / "mcp/a/server.py").exists()
    assert not (stage / "mcp/a/.venv").exists()
    assert r.desired(stage) == ["b"]
    assert "secret-value" not in plan.read_text(encoding="utf-8")


def test_modified_owned_registration_is_not_overwritten_or_deleted(env):
    r, source, installed, _ = env
    payload(source, "a")
    payload(installed, "a")
    register(r, "a")
    r.finish(prepare(env), [installed])
    config = r.claude_registry._read_cfg(r.claude_registry.CLAUDE_JSON)
    config["mcpServers"]["a"] = {"command": "user-python"}
    r.claude_registry._write_cfg(r.claude_registry.CLAUDE_JSON, config)
    with pytest.raises(RuntimeError, match="conflict"):
        prepare(env)
    (source / "mcp/a/install.sh").unlink()
    result = r.finish(prepare(env), [installed])
    assert result["status"] == "conflict"
    assert r.claude_registry.inspect("a") == {"command": "user-python"}


def test_drift_after_prepare_blocks_retirement(env):
    r, _, installed, _ = env
    payload(installed, "a")
    register(r, "a")
    plan = prepare(env)
    register(r, "a", cwd="changed-after-prepare")
    result = r.finish(plan, [installed])
    assert result["status"] == "conflict"
    assert r.claude_registry.inspect("a")["cwd"] == "changed-after-prepare"


def test_explicit_codex_without_cli_does_not_claim_success(env):
    r, source, installed, state = env
    payload(source, "a")
    register(r, "a")
    with pytest.raises(RuntimeError, match="CLI"):
        r.prepare(source, [installed], "codex", state_root=state)


def test_invalid_state_and_symlink_fail_closed(env):
    r, source, installed, state = env
    state.mkdir()
    (state / "receipt.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="receipt"):
        prepare(env)
    (state / "receipt.json").unlink()
    outside = source.parent / "outside"
    outside.mkdir()
    (installed / "mcp").mkdir()
    try:
        (installed / "mcp/a").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privileges unavailable")
    register(r, "a")
    with pytest.raises(RuntimeError, match="link"):
        prepare(env)


def test_preserve_uses_corresponding_root_and_keeps_unknown_code_without_registry(env):
    r, source, installed, state = env
    payload(source, "b")
    payload(installed, "unknown")
    second = installed.parent.parent / "second-icode"
    payload(second, "unknown")
    (second / "mcp/unknown/server.py").write_text("second", encoding="utf-8")
    stage = installed.parent / ".icode-stage-test"
    payload(stage, "b")
    # No host configuration or state should be consulted by plain sync.
    r.claude_registry.CLAUDE_JSON.write_text("broken", encoding="utf-8")
    r.preserve(None, stage, installed_root=second, source_root=source)
    assert (stage / "mcp/unknown/server.py").read_text(encoding="utf-8") == "second"
    assert r.desired(stage) == ["b"]
    assert not state.exists()


def test_multihost_removal_preserves_export_and_foreign_json(env):
    r, source, installed, state = env
    payload(installed, "a")
    entry = register(r, "a")
    for host in ("codebuddy", "workbuddy"):
        path = r._config(host)
        path.parent.mkdir()
        projected = {key: entry[key] for key in ("command", "args", "env")}
        path.write_text(json.dumps({"foreign": True, "mcpServers": {"a": projected, "user": {"command": "keep"}}}), encoding="utf-8")
    plan = r.prepare(source, [installed], "all", state_root=state)
    assert r.finish(plan, [installed])["status"] == "complete"
    for host in ("claude", "codebuddy", "workbuddy"):
        assert r._actual(host, "a") is None
    assert r._export("a") == entry
    assert json.loads(r._config("workbuddy").read_text())["foreign"] is True
    assert r._actual("workbuddy", "user") == {"command": "keep"}


def test_unselected_host_keeps_payload_and_later_retry_archives(env):
    r, source, installed, state = env
    payload(installed, "a")
    entry = register(r, "a")
    cfg = r.client_registry.CODEBUDDY_CFG
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"a": entry}}), encoding="utf-8")
    first = r.finish(prepare(env), [installed])
    assert first["retained"] == 1 and (installed / "mcp/a/server.py").exists()
    second = r.prepare(source, [installed], "codebuddy", state_root=state)
    assert r.finish(second, [installed])["status"] == "complete"
    assert not (installed / "mcp/a").exists()


def test_unregister_and_archive_failures_remain_retryable(env, monkeypatch):
    r, _, installed, state = env
    payload(installed, "a")
    register(r, "a")
    plan = prepare(env)
    original = r._archive
    monkeypatch.setattr(r, "_archive", lambda *args: (_ for _ in ()).throw(OSError("injected")))
    assert r.finish(plan, [installed])["status"] == "conflict"
    assert "a" in json.loads((state / "receipt.json").read_text())["hosts"]["claude"]
    assert r.claude_registry.inspect("a") is None
    monkeypatch.setattr(r, "_archive", original)
    assert r.finish(prepare(env), [installed])["status"] == "complete"
    assert not (installed / "mcp/a").exists()


def test_corrupt_host_dry_run_does_not_create_registry_backup(env):
    r, _, installed, state = env
    payload(installed, "a")
    register(r, "a")
    cfg = r.claude_registry.CLAUDE_JSON
    cfg.write_text("broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="configuration"):
        prepare(env, dry_run=True)
    assert cfg.read_text() == "broken"
    assert not list(cfg.parent.glob("*.bak.*")) and not state.exists()


def test_four_hosts_with_fake_official_codex_cli_and_failed_remove(env, monkeypatch):
    r, source, installed, state = env
    payload(installed, "a")
    entry = register(r, "a")
    cli_state = state.parent / "codex-servers.json"
    cli_state.write_text(json.dumps({"a": {"name": "a", "enabled": True,
                         "transport": {"type": "stdio", "env_vars": [], "cwd": None, **entry}}}), encoding="utf-8")
    fake = state.parent / "fake_codex.py"
    fake.write_text("""import json, os, sys
from pathlib import Path
p = Path(os.environ['TEST_CODEX_STATE'])
data = json.loads(p.read_text())
verb, name = sys.argv[2:4]
if verb == 'get':
    if name not in data:
        print('No MCP server named ' + name, file=sys.stderr)
        sys.exit(1)
    print(json.dumps(data[name]))
elif verb == 'remove':
    if os.environ.get('TEST_CODEX_FAIL'):
        sys.exit(7)
    data.pop(name, None)
    p.write_text(json.dumps(data))
else:
    sys.exit(9)
""", encoding="utf-8")
    wrapper = state.parent / ("codex.cmd" if os.name == "nt" else "codex")
    wrapper.write_text((f'@"{sys.executable}" "{fake}" %*\n' if os.name == "nt"
                        else f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n'), encoding="utf-8")
    wrapper.chmod(0o700)
    monkeypatch.setenv("TEST_CODEX_STATE", str(cli_state))
    monkeypatch.setattr(r.client_registry, "CODEX_BIN", str(wrapper))
    for host in ("codebuddy", "workbuddy"):
        path = r._config(host)
        path.parent.mkdir()
        path.write_text(json.dumps({"mcpServers": {"a": entry}}), encoding="utf-8")
    plan = r.prepare(source, [installed], "all", state_root=state)
    assert set(r._load_plan(plan)["hosts"]) == set(r.HOSTS)
    monkeypatch.setenv("TEST_CODEX_FAIL", "1")
    assert r.finish(plan, [installed])["status"] == "conflict"
    assert (installed / "mcp/a/server.py").exists()
    assert r._export("a") == entry
    assert r._actual("codex", "a") is not None
    monkeypatch.delenv("TEST_CODEX_FAIL")
    assert r.finish(r.prepare(source, [installed], "all", state_root=state), [installed])["status"] == "complete"
    assert not (installed / "mcp/a").exists()


def test_unknown_payload_is_reported_and_preserved_in_plan(env):
    r, source, installed, _ = env
    payload(installed, "unknown")
    plan = prepare(env)
    stage = installed.parent / ".icode-stage-unknown"
    stage.mkdir()
    r.preserve(plan, stage, installed_root=installed)
    assert (stage / "mcp/unknown/server.py").exists()
    result = r.finish(plan, [installed])
    assert result["status"] == "conflict"
    assert any(c["name"] == "unknown" for c in result["conflicts"])
    assert (installed / "mcp/unknown/server.py").exists()


def test_payload_changed_after_prepare_keeps_registration(env):
    r, _, installed, _ = env
    old = payload(installed, "a")
    register(r, "a")
    plan = prepare(env)
    (old / "server.py").write_text("user edit", encoding="utf-8")
    assert r.finish(plan, [installed])["status"] == "conflict"
    assert r.claude_registry.inspect("a") is not None


def test_plan_boundaries_and_installed_mapping_are_validated(env):
    r, source, installed, state = env
    payload(installed, "a")
    register(r, "a")
    plan = prepare(env)
    data = json.loads(plan.read_text())
    data["retired"]["claude"]["a"]["roots"] = [str(source)]
    plan.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="outside selected"):
        r.finish(plan, [installed])
    assert r.claude_registry.inspect("a") is not None


def test_registration_ownership_does_not_grant_same_named_payload_ownership(env):
    r, _, installed, _ = env
    payload(installed, "a")
    (installed / ".icode-install-owner.json").unlink()
    register(r, "a")
    result = r.finish(prepare(env), [installed])
    assert result["status"] == "conflict"
    assert any(c["reason"] == "payload-conflict" for c in result["conflicts"])
    assert r.claude_registry.inspect("a") is None
    assert (installed / "mcp/a/server.py").exists()


def test_absolute_entry_path_can_prove_payload_without_main_owner_marker(env):
    r, _, installed, _ = env
    old = payload(installed, "a")
    (installed / ".icode-install-owner.json").unlink()
    register(r, "a", args=[str(old / "server.py")])
    assert r.finish(prepare(env), [installed])["status"] == "complete"
    assert not old.exists()


def test_special_source_file_is_rejected_before_reading(env, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation requires POSIX")
    r, _, installed, _ = env
    old = payload(installed, "a")
    os.mkfifo(old / "pipe")
    original = Path.read_bytes
    def guarded_read(path):
        assert path.name != "pipe", "special file must be rejected before reading"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(RuntimeError, match="special"):
        prepare(env)


def test_unselected_codex_keeps_shared_payload_and_export(env, monkeypatch):
    r, _, installed, _ = env
    payload(installed, "a")
    entry = register(r, "a")
    actual = r._actual
    monkeypatch.setattr(r.client_registry, "CODEX_BIN", "detected-codex")
    monkeypatch.setattr(r, "_actual", lambda host, name:
                        {"transport": entry} if host == "codex" and name == "a" else actual(host, name))
    result = r.finish(prepare(env), [installed])
    assert result["status"] == "complete" and result["retained"] == 1
    assert r.claude_registry.inspect("a") is None
    assert (installed / "mcp/a/server.py").exists()
    assert r._export("a") == entry


def test_cli_dry_run_and_conflict_exit_codes_do_not_expose_values(env, capsys):
    r, source, installed, state = env
    payload(installed, "a")
    register(r, "a")
    arguments = ["prepare", "--source-root", str(source), "--installed-root", str(installed),
                 "--state-root", str(state)]
    assert r.main([*arguments, "--dry-run"]) == 0
    preview = capsys.readouterr().out
    assert "secret-value" not in preview and not state.exists()
    assert "a" in json.loads(preview)["retired"]["claude"]
    assert r.main(arguments) == 0
    plan = Path(capsys.readouterr().out.strip())
    register(r, "a", cwd="user changed")
    assert r.main(["finish", "--plan", str(plan)]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "conflict" and "secret-value" not in output


def test_corrupt_receipt_payload_ownership_is_rejected(env):
    r, source, installed, state = env
    payload(source, "a")
    payload(installed, "a")
    register(r, "a")
    r.finish(prepare(env), [installed])
    receipt = state / "receipt.json"
    data = json.loads(receipt.read_text())
    data["hosts"]["claude"]["a"]["payload_roots"] = [str(source)]
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="payload ownership"):
        prepare(env)


def test_main_payload_marker_requires_existing_exact_schema(env):
    r, _, installed, _ = env
    payload(installed, "a")
    marker = installed / ".icode-install-owner.json"
    data = json.loads(marker.read_text())
    data["unrecognized"] = True
    marker.write_text(json.dumps(data), encoding="utf-8")
    register(r, "a")
    result = r.finish(prepare(env), [installed])
    assert result["status"] == "conflict" and (installed / "mcp/a").exists()


def test_cli_subprocess_outputs_lf_for_bash_consumers(tmp_path):
    source = tmp_path / "source"
    installed = tmp_path / "installed"
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    installed.mkdir()
    payload(source, "a")
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    script = str(LIB / "install_reconcile.py")
    listing = subprocess.run([sys.executable, script, "desired", "--source-root", str(source)],
                             capture_output=True, env=environment, check=True)
    assert listing.stdout == b"a\n"
    planning = subprocess.run([sys.executable, script, "prepare", "--source-root", str(source),
                               "--installed-root", str(installed), "--state-root", str(state)],
                              capture_output=True, env=environment, check=True)
    assert planning.stdout.endswith(b"\n") and b"\r" not in planning.stdout
    assert Path(planning.stdout.decode("utf-8").strip()).is_file()


def test_explicit_codebuddy_never_retires_unselected_claude(env):
    r, source, installed, state = env
    payload(installed, "a")
    payload(source, "b")
    old = register(r, "a")
    current = register(r, "b")
    config = r.client_registry.CODEBUDDY_CFG
    config.parent.mkdir()
    config.write_text(json.dumps({"mcpServers": {"a": old, "b": current}}), encoding="utf-8")
    plan = r.prepare(source, [installed], "codebuddy", state_root=state)
    assert r._load_plan(plan)["hosts"] == ["codebuddy"]
    result = r.finish(plan, [installed])
    assert result["status"] == "complete" and result["registered"] == 1
    assert result["retained"] == 1
    assert r._actual("codebuddy", "a") is None
    assert r.claude_registry.inspect("a") == old
    assert (installed / "mcp/a/server.py").exists()
    receipt = json.loads((state / "receipt.json").read_text())
    assert set(receipt["hosts"]) == {"codebuddy"}
    assert "b" in receipt["hosts"]["codebuddy"]


@pytest.mark.parametrize("client", ["claude", "codex", "codebuddy", "workbuddy"])
def test_explicit_host_selection_is_exact(env, monkeypatch, client):
    r, _, _, _ = env
    monkeypatch.setattr(r.client_registry, "CODEX_BIN", "detected-codex")
    r.client_registry.CODEBUDDY_CFG.parent.mkdir()
    r.client_registry.WORKBUDDY_CFG.parent.mkdir()
    assert r._hosts(client) == [client]


def test_reparse_attribute_is_rejected_without_junction_api(env, monkeypatch):
    r, source, _, _ = env
    original = Path.lstat
    monkeypatch.setattr(Path, "is_junction", lambda self: False, raising=False)
    monkeypatch.setattr(Path, "lstat", lambda self, *args, **kwargs:
                        SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
                        if self == source else original(self, *args, **kwargs))
    with pytest.raises(RuntimeError, match="link"):
        r._safe(source)


def test_payload_digest_frames_file_names_and_contents(env, tmp_path):
    r, _, _, _ = env
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a").write_bytes(b"bc")
    (second / "ab").write_bytes(b"c")
    assert r._tree_digest(first) != r._tree_digest(second)


@pytest.mark.parametrize("changed", [False, True])
def test_preserve_merges_matching_partial_stage_without_overwriting_config(env, changed):
    r, source, installed, _ = env
    old = payload(installed, "a")
    (old / "config.json").write_text('{"secret":"original"}', encoding="utf-8")
    register(r, "a")
    plan = prepare(env)
    stage = installed.parent / ".icode-stage-partial"
    partial = stage / "mcp/a"
    partial.mkdir(parents=True)
    content = '{"secret":"changed"}' if changed else '{"secret":"original"}'
    (partial / "config.json").write_text(content, encoding="utf-8")
    if changed:
        with pytest.raises(RuntimeError, match="conflict"):
            r.preserve(plan, stage, installed_root=installed)
        assert (partial / "config.json").read_text() == content
        assert not (partial / "server.py").exists()
    else:
        r.preserve(plan, stage, installed_root=installed)
        assert (partial / "server.py").exists()
        assert (partial / "config.json").read_text() == content


def test_rule_directories_are_not_unknown_retired_services(env):
    r, source, installed, _ = env
    for root in (source, installed):
        rules = root / "mcp/workflow-gate"
        rules.mkdir(parents=True)
        (rules / "gates.json").write_text('{"rules":[]}', encoding="utf-8")
    old_rules = installed / "mcp/other-rules"
    old_rules.mkdir()
    (old_rules / "gates.json").write_text("{}", encoding="utf-8")
    plan = prepare(env)
    assert r.finish(plan, [installed])["status"] == "complete"
    stage = installed.parent / ".icode-stage-rules"
    stage.mkdir()
    r.preserve(None, stage, source_root=source, installed_root=installed)
    assert not (stage / "mcp/other-rules").exists()


def test_single_selection_preserves_full_distribution_catalog(env):
    r, source, installed, _ = env
    payload(source, "a")
    payload(source, "b")
    payload(installed, "a")
    (source / "mcp/a/server.py").write_text("new current a", encoding="utf-8")
    plan = prepare(env, single="b")
    stage = installed.parent / ".icode-stage-single"
    payload(stage, "a")
    payload(stage, "b")
    (stage / "mcp/a/server.py").write_text("new current a", encoding="utf-8")
    r.preserve(plan, stage, installed_root=installed)
    assert r.desired(stage) == ["a", "b"]
    assert (stage / "mcp/a/server.py").read_text() == "new current a"
    result = subprocess.run([sys.executable, str(LIB / "install_reconcile.py"), "desired", "--plan", str(plan)],
                            capture_output=True, check=True)
    assert result.stdout == b"b\n"


@pytest.mark.parametrize("operation", ["inspect", "remove"])
def test_codex_cli_is_noninteractive_bounded_and_timeout_is_redacted(env, monkeypatch, operation):
    r, _, _, _ = env
    monkeypatch.setattr(r.client_registry, "CODEX_BIN", "fake-codex")
    calls = []
    def timed_out(arguments, **kwargs):
        calls.append((arguments, kwargs))
        assert kwargs.get("stdin") == subprocess.DEVNULL
        assert 0 < kwargs.get("timeout", 0) <= 30
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"],
                                        output="secret stdout", stderr="secret stderr")
    monkeypatch.setattr(r.subprocess, "run", timed_out)
    with pytest.raises(RuntimeError, match="timed out") as error:
        if operation == "inspect":
            r._actual("codex", "a")
        else:
            r._remove("codex", "a")
    assert "secret" not in str(error.value)
    expected = ["fake-codex", "mcp", "get", "a", "--json"] if operation == "inspect" else ["fake-codex", "mcp", "remove", "a"]
    assert calls[0][0] == expected


@pytest.mark.parametrize("git_kind", ["directory", "worktree-file"])
def test_git_source_ignores_stale_catalog_but_installed_copy_honors_it(env, git_kind):
    r, source, installed, _ = env
    for root in (source, installed):
        payload(root, "a")
        payload(root, "b")
        (root / "mcp" / r.CATALOG).write_text(json.dumps(
            {"version": 1, "desired_names": ["b"]}), encoding="utf-8")
    if git_kind == "directory":
        (source / ".git").mkdir()
    else:
        (source / ".git").write_text("gitdir: ../git-metadata/worktrees/source\n", encoding="utf-8")
    assert r.desired(source) == ["a", "b"]
    assert r.desired(installed) == ["b"]


@pytest.mark.parametrize("client", ["codebuddy", "workbuddy"])
def test_explicit_missing_json_host_can_be_planned_without_false_finish(env, client):
    r, source, installed, state = env
    payload(source, "a")
    assert not r._config(client).parent.exists()
    plan = r.prepare(source, [installed], client, state_root=state)
    assert r._load_plan(plan)["hosts"] == [client]
    assert not r._config(client).parent.exists()
    with pytest.raises(RuntimeError, match="desired"):
        r.finish(plan, [installed])
    assert not (state / "receipt.json").exists()


def test_cli_hosts_outputs_frozen_detected_plan_after_new_host_directories_appear(env):
    r, source, installed, state = env
    plan = r.prepare(source, [installed], "all", state_root=state)
    assert r._load_plan(plan)["hosts"] == ["claude"]
    r.client_registry.CODEBUDDY_CFG.parent.mkdir()
    r.client_registry.WORKBUDDY_CFG.parent.mkdir()
    result = subprocess.run([sys.executable, str(LIB / "install_reconcile.py"), "hosts", "--plan", str(plan)],
                            capture_output=True, check=True)
    assert result.stdout == b"claude\n"
    assert r._hosts("all") == ["claude", "codebuddy", "workbuddy"]


def test_retry_adopts_completed_desired_update_after_another_install_failed(env):
    r, source, installed, state = env
    payload(source, "a")
    payload(installed, "a")
    register(r, "a")
    r.finish(prepare(env), [installed])
    old_receipt = (state / "receipt.json").read_bytes()
    payload(source, "b")
    # The A child installer completed, but B failed before finish was called.
    register(r, "a", command="updated-python")
    retry = prepare(env)
    assert (state / "receipt.json").read_bytes() == old_receipt
    register(r, "b")
    result = r.finish(retry, [installed])
    assert result["status"] == "complete" and result["registered"] == 2
    assert r.claude_registry.inspect("a")["command"] == "updated-python"
    assert (state / "receipt.json").read_bytes() != old_receipt
