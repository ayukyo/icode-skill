"""Offline diagnostics must neither spawn configured commands nor expose secrets."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def diagnostics():
    spec = importlib.util.spec_from_file_location(
        "test_environment_diagnostics", ROOT / "mcp/_lib/environment_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding=encoding)
    return path


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_registration_is_read_only_redacted_and_uses_path(tmp_path, encoding):
    module = diagnostics()
    entry = {"command": "python", "args": ["--credential=never-echo"],
             "env": {"API_KEY": "never-echo", "PATH": "fixture-path"}}
    config = write(tmp_path / "host.json", {"unknown": "keep", "mcpServers": {"demo": entry}}, encoding)
    expected = tmp_path / "entries"
    write(expected / "demo.json", dict(entry, _fallback={"command": "ignored"}))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch.object(module.shutil, "which", return_value=sys.executable) as which:
        with patch.object(subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            report = module.diagnose_registration(config, expected)
    assert report["servers"][0]["status"] == "static_ok"
    assert report["session"]["status"] == "unverified"
    assert report["summary"]["problems"] == 0
    assert "never-echo" not in json.dumps(report)
    which.assert_called_with("python", path="fixture-path")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("system,command", [("windows", "/home/user/mcp"),
    ("windows", "/usr/bin/npx"), ("linux", "C:\\tools\\node.exe")])
def test_foreign_os_is_distinct_from_missing_command(tmp_path, system, command):
    config = write(tmp_path / "host.json", {"mcpServers": {"demo": {"command": command}}})
    report = diagnostics().diagnose_registration(config, system=system)
    assert report["servers"][0]["status"] == "foreign_os"


@pytest.mark.parametrize("host", [".codebuddy", ".workbuddy", ".codex"])
def test_adapter_omitted_cwd_is_not_drift_but_explicit_change_is(tmp_path, host):
    directory = tmp_path / host
    directory.mkdir()
    config = directory / ("config.toml" if host == ".codex" else "mcp.json")
    entry = {"command": sys.executable, "args": []}
    entries = tmp_path / "entries"
    write(entries / "demo.json", dict(entry, cwd=str(tmp_path)))
    for cwd, drift in [(None, False), (str(directory), True)]:
        actual = dict(entry, **({"cwd": cwd} if cwd is not None else {}))
        if host == ".codex":
            config.write_text("[mcp_servers.demo]\ncommand = " + json.dumps(sys.executable) +
                              "\nargs = []\n" + ("cwd = " + json.dumps(cwd) + "\n" if cwd else ""), encoding="utf-8")
        else:
            write(config, {"mcpServers": {"demo": actual}})
        row = diagnostics().diagnose_registration(config, entries)["servers"][0]
        assert row["drift"] is drift


def test_nested_foreign_arg_drift_missing_and_remote_are_distinct(tmp_path):
    config = write(tmp_path / "host.json", {"mcpServers": {
        "nested": {"command": "cmd", "args": ["/c", "/usr/local/bin/npx"]},
        "remote": {"type": "http", "url": "https://private.invalid/?token=never-echo"},
        "absent-command": {"command": "fixture-does-not-exist"},
    }})
    entries = tmp_path / "entries"
    write(entries / "nested.json", {"command": "node"})
    write(entries / "missing.json", {"command": "node"})
    with patch.object(diagnostics().shutil, "which", return_value=None):
        report = diagnostics().diagnose_registration(config, entries, system="windows")
    rows = {row["name"]: row for row in report["servers"]}
    assert rows["nested"]["status"] == "foreign_os" and rows["nested"]["drift"]
    assert rows["remote"]["status"] == "remote_unprobed"
    assert rows["absent-command"]["status"] == "command_not_found"
    assert rows["missing"]["status"] == "missing"
    assert "never-echo" not in json.dumps(report)


@pytest.mark.parametrize("value", [[], {"mcpServers": []}, {"mcpServers": {"x": []}},
    {"mcpServers": {"x": {"command": "node", "args": "bad"}}}])
def test_malformed_configuration_is_reported_without_writes(tmp_path, value):
    config = write(tmp_path / "host.json", value)
    before = config.read_bytes()
    result = diagnostics().diagnose_registration(config)
    assert result["summary"]["problems"] > 0
    assert config.read_bytes() == before and list(tmp_path.iterdir()) == [config]


def test_invalid_json_does_not_echo_content_or_make_backup(tmp_path):
    config = tmp_path / "host.json"
    config.write_text('{"token":"never-echo", invalid', encoding="utf-8")
    result = diagnostics().diagnose_registration(config)
    assert result["status"] == "invalid_config"
    assert "never-echo" not in json.dumps(result)
    assert list(tmp_path.iterdir()) == [config]


def test_entry_reads_apply_the_same_path_policy(tmp_path):
    config = write(tmp_path / "host.json", {"mcpServers": {"demo": {"command": sys.executable}}})
    entries = tmp_path / "entries"
    denied = write(entries / "demo.json", {"command": "never-read"})
    def resolve(path):
        if path == denied:
            raise PermissionError("not allowed")
        return path
    result = diagnostics().diagnose_registration(config, entries, resolve_path=resolve)
    assert result["servers"][0]["expected_status"] == "unreadable"
    assert result["summary"]["problems"] == 1


def test_bounded_inventory_and_unsafe_server_name(tmp_path):
    config = write(tmp_path / "host.json", {"mcpServers": {
        "../escape": {"command": sys.executable}, "other": {"command": sys.executable}}})
    result = diagnostics().diagnose_registration(config, tmp_path, max_servers=1)
    assert result["status"] == "limit_exceeded" and not result["servers"]
    result = diagnostics().diagnose_registration(config, tmp_path)
    assert result["servers"][0]["status"] == "invalid_name"


def test_core_installation_hashes_detect_drift_without_reading_configs(tmp_path):
    module = diagnostics()
    source, installed = tmp_path / "source", tmp_path / "installed"
    for relative in module.CORE_RESOURCES:
        for root in (source, installed):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("same", encoding="utf-8")
    write(installed / "mcp/config.json", {"token": "never-echo"})
    assert module.compare_installation(source, installed)["status"] == "match"
    (installed / "mcp/_lib/platform_entry.py").write_text("old", encoding="utf-8")
    result = module.compare_installation(source, installed)
    assert result["scope"] == "core_runtime_resources"
    assert [row["path"] for row in result["changed"]] == ["mcp/_lib/platform_entry.py"]
    assert "never-echo" not in json.dumps(result)


def test_gate_summary_uses_trace_counts_and_does_not_echo_reasons(tmp_path):
    module = diagnostics()
    trace = tmp_path / ".thinking_gate_trace.jsonl"
    trace.write_text('\n'.join(json.dumps(row) for row in [
        {"step": "plan", "result": "degraded", "degraded_reason": "never-echo"},
        {"step": "plan", "result": "success"},
        {"step": "audit", "result": "degraded"},
    ]), encoding="utf-8")
    write(tmp_path / ".mcp_gate_report.json", {"unavailable_before_call": 2, "degraded_after_attempt": 1})
    report = module.summarize_gates(tmp_path)
    assert report["thinking"]["total"] == 3 and report["thinking"]["degraded"] == 2
    assert report["mcp"]["unavailable_before_call"] == 2
    assert report["status"] == "attention" and "never-echo" not in json.dumps(report)
    trace.write_text("invalid never-echo", encoding="utf-8")
    assert module.summarize_gates(tmp_path)["status"] == "partial"


def test_gate_summary_resolves_only_superseded_pending_and_keeps_terminal_history(tmp_path):
    rows = [{"step": "plan", "result": "pending"}, {"step": "plan", "result": "degraded"},
            {"step": "plan", "result": "success"}, {"step": "audit", "result": "success"}]
    (tmp_path / ".thinking_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    report = diagnostics().summarize_gates(tmp_path)
    assert report["errors"] == []
    assert report["thinking"] == {"total": 3, "success": 2, "degraded": 1, "blocked": 0}
    assert report["status"] == "attention"


@pytest.mark.parametrize("rows", [
    [{"step": "plan", "result": "pending"}, {"step": "audit", "result": "success"}],
    [{"step": "plan", "result": "success"}, {"step": "plan", "result": "pending"}],
])
def test_gate_summary_reports_unfinished_pending_without_calling_file_unreadable(tmp_path, rows):
    (tmp_path / ".thinking_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    report = diagnostics().summarize_gates(tmp_path)
    assert report["errors"] == ["thinking_trace_unfinished_pending"]
    assert report["status"] == "partial" and report["thinking"]["success"] == 1


@pytest.mark.parametrize("raw,error", [("not json", "thinking_trace_invalid_json"),
                                      ('{"step":"plan","result":"unknown"}', "thinking_trace_invalid_record")])
def test_gate_summary_separates_invalid_json_and_record_from_read_failure(tmp_path, raw, error):
    (tmp_path / ".thinking_gate_trace.jsonl").write_text(raw, encoding="utf-8")
    report = diagnostics().summarize_gates(tmp_path)
    assert report["errors"] == [error] and report["status"] == "partial"


def test_doctor_cli_keeps_problems_separate_from_query_failure(tmp_path):
    config = write(tmp_path / "host.json", {"mcpServers": {"x": {"command": "missing-fixture-command"}}})
    args = [sys.executable, str(ROOT / "tools/mcp_doctor.py"), "--config", str(config),
            "--entries-dir", str(tmp_path / "entries"), "--json"]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["read_only"] and report["registrations"][0]["summary"]["problems"] == 1
    assert subprocess.run([*args, "--strict"], capture_output=True).returncode == 1


def test_explicit_script_entrypoint_missing_and_relative_cwd_are_visible(tmp_path):
    module = diagnostics()
    config = write(tmp_path / "host.json", {"mcpServers": {
        "script": {"command": sys.executable, "args": [str(tmp_path / "missing.py")]},
        "relative": {"command": "./python"},
        "cwd": {"command": sys.executable, "cwd": str(tmp_path / "missing-directory")},
    }})
    rows = {r["name"]: r for r in module.diagnose_registration(config)["servers"]}
    assert rows["script"]["status"] == "stale_entrypoint"
    assert rows["relative"]["status"] == "relative_command_unresolved"
    assert rows["cwd"]["status"] == "cwd_not_found"


def test_oversized_input_is_partial_instead_of_healthy(tmp_path):
    module = diagnostics()
    config = tmp_path / "host.json"
    config.write_bytes(b" " * (module.MAX_BYTES + 1))
    report = module.diagnose_registration(config)
    assert report["status"] == "invalid_config" and report["summary"]["problems"] == 1
    (tmp_path / ".thinking_gate_trace.jsonl").write_bytes(config.read_bytes())
    assert module.summarize_gates(tmp_path)["status"] == "partial"


@pytest.mark.parametrize("value", [[], {}, None, 1, True])
def test_malformed_trace_result_cannot_crash_delivery_observation(tmp_path, value):
    (tmp_path / ".thinking_gate_trace.jsonl").write_text(json.dumps({"result": value}), encoding="utf-8")
    assert diagnostics().summarize_gates(tmp_path)["status"] == "partial"


def test_command_content_obeys_the_same_path_policy(tmp_path):
    module = diagnostics()
    config = write(tmp_path / "host.json", {"mcpServers": {"demo": {"command": sys.executable}}})
    calls = []
    def resolve(path):
        calls.append(path)
        if path != config:
            raise PermissionError("command outside allowed scope")
        return path
    original_open = Path.open
    def allowed_open(path, *args, **kwargs):
        assert path == config, "must not read an executable outside allowed scope"
        return original_open(path, *args, **kwargs)
    with patch.object(Path, "open", allowed_open):
        result = module.diagnose_registration(config, resolve_path=resolve)
    assert result["servers"][0]["status"] == "launch_path_uninspected"
    assert Path(sys.executable) in calls


def test_windows_path_environment_is_case_insensitive(tmp_path):
    module = diagnostics()
    config = write(tmp_path / "host.json", {"mcpServers": {"demo": {
        "command": "fixture-node", "env": {"Path": "fixture-path"}}}})
    with patch.object(module.shutil, "which", return_value=None) as which:
        module.diagnose_registration(config, system="windows")
    which.assert_called_once_with("fixture-node", path="fixture-path")


def test_path_search_is_checked_before_probing_candidates(tmp_path):
    module = diagnostics()
    with patch.object(module.shutil, "which", side_effect=AssertionError("must not search network PATH")):
        located, state = module._search_command("node", r"\\fixture.invalid\share", True)
    assert located is None and state == "network_path_uninspected"
    def deny(path):
        raise PermissionError("outside search scope")
    with patch.object(module.shutil, "which", side_effect=AssertionError("must not search outside policy")):
        located, state = module._search_command("node", str(tmp_path), False, deny)
    assert located is None and state == "launch_path_uninspected"


def test_network_entries_directory_is_rejected_before_enumeration(tmp_path):
    module = diagnostics()
    config = write(tmp_path / "host.json", {"mcpServers": {}})
    with patch.object(Path, "glob", side_effect=AssertionError("must not enumerate network")):
        report = module.diagnose_registration(config, r"\\fixture.invalid\share", system="windows")
    assert report["status"] == "entries_unreadable"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_codex_toml_is_read_only_redacted_and_honors_enabled(tmp_path, encoding):
    module = diagnostics()
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.thinking]\ncommand = ' + json.dumps(sys.executable)
        + '\nargs = ["--private=never-echo"]\n'
        '[mcp_servers.offline]\nenabled = false\n'
        '[mcp_servers.remote]\nurl = "https://private.invalid/?token=never-echo"\n',
        encoding=encoding)
    before = config.read_bytes()
    with patch.object(subprocess, "Popen", side_effect=AssertionError("must not spawn")):
        report = module.diagnose_registration(config)
    rows = {r["name"]: r for r in report["servers"]}
    assert set(rows) == {"thinking", "offline", "remote"}
    assert rows["thinking"]["status"] == "static_ok"
    assert rows["offline"]["status"] == "disabled"
    assert rows["remote"]["status"] == "remote_unprobed"
    assert report["summary"]["problems"] == 0
    assert report["session"]["status"] == "unverified"
    assert "never-echo" not in json.dumps(report)
    assert config.read_bytes() == before and list(tmp_path.iterdir()) == [config]


def test_missing_toml_parser_is_explicit_and_never_healthy(tmp_path):
    module = diagnostics()
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.demo]\ncommand = "never-echo"\n', encoding="utf-8")
    with patch.object(module, "tomllib", None, create=True):
        report = module.diagnose_registration(config)
    assert report["status"] == "toml_parser_unavailable"
    assert report["summary"]["problems"] == 1 and not report["servers"]
    assert "never-echo" not in json.dumps(report)


@pytest.mark.parametrize("content", [
    '[mcp_servers.demo\ncommand = "never-echo"',
    'mcp_servers = []',
    '[mcp_servers]\ndemo = []',
])
def test_invalid_codex_toml_is_reported_without_echo_or_backup(tmp_path, content):
    config = tmp_path / "config.toml"
    config.write_text(content, encoding="utf-8")
    before = config.read_bytes()
    report = diagnostics().diagnose_registration(config)
    assert report["summary"]["problems"] > 0
    assert "never-echo" not in json.dumps(report)
    assert config.read_bytes() == before and list(tmp_path.iterdir()) == [config]


@pytest.mark.parametrize("child_available,expected", [
    (False, "node_runner_not_found"), (True, "static_ok"),
])
def test_windows_absolute_node_npx_checks_child_node_path(tmp_path, child_available, expected):
    module = diagnostics()
    node, runner = tmp_path / "node.exe", tmp_path / "npx-cli.js"
    node.write_bytes(b"MZfixture")
    runner.write_text("// fixture only", encoding="utf-8")
    config = write(tmp_path / "host.json", {"mcpServers": {"demo": {
        "command": str(node), "args": [str(runner), "never-echo"],
        "env": {"Path": str(tmp_path)}}}})
    # The temporary files use the native host's paths. Platform classification
    # has separate coverage; exercise the Windows runner branch on both hosts.
    with patch.object(module, "_foreign", return_value=False), \
            patch.object(module.shutil, "which", return_value=str(node) if child_available else None) as which:
        report = module.diagnose_registration(config, system="windows")
    assert report["servers"][0]["status"] == expected
    which.assert_called_once_with("node", path=str(tmp_path))
    assert report["summary"]["problems"] == (0 if child_available else 1)
    assert "never-echo" not in json.dumps(report)


def test_windows_npx_does_not_search_a_network_path(tmp_path):
    module = diagnostics()
    node, runner = tmp_path / "node.exe", tmp_path / "npx-cli.js"
    node.write_bytes(b"MZfixture")
    runner.write_text("// fixture only", encoding="utf-8")
    config = write(tmp_path / "host.json", {"mcpServers": {"demo": {
        "command": str(node), "args": [str(runner)],
        "env": {"PATH": r"\\fixture.invalid\share"}}}})
    with patch.object(module, "_foreign", return_value=False), \
            patch.object(module.shutil, "which", side_effect=AssertionError("must not search network")):
        report = module.diagnose_registration(config, system="windows")
    assert report["servers"][0]["status"] == "network_path_uninspected"


def test_doctor_default_includes_existing_codex_configuration(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("test_mcp_doctor_default", ROOT / "tools/mcp_doctor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.demo]\ncommand = ' + json.dumps(sys.executable), encoding="utf-8")
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))
    assert module.main(["--json", "--entries-dir", str(tmp_path / "entries")]) == 0
    report = json.loads(capsys.readouterr().out)
    rows = [r for r in report["registrations"] if r["config"] == str(config)]
    assert len(rows) == 1 and rows[0]["servers"][0]["status"] == "static_ok"
