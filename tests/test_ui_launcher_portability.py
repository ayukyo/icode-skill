"""Actual launcher processes, isolated profiles, no models/devices/Git writes."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import urllib.request
from unittest import mock

import pytest

ROOT = Path(os.environ.get("ICODE_UI_TEST_ROOT", Path(__file__).resolve().parents[1])).resolve()
CLI = ROOT / "tools/icode_agent.py"
# CPython's Windows venv redirector can hide the persistent server's PID.
# The launcher has no required third-party runtime; own the base process directly.
PYTHON = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable


def load_cli():
    spec = importlib.util.spec_from_file_location("portable_ui_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def processes():
    owned = []
    yield owned
    # Windows TerminateProcess does not run Python finally blocks. All profiles
    # are temporary; stale registration recovery is separately exercised below.
    for process in reversed(owned):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=8)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()


def profile(tmp_path, preferred_port=0):
    home = tmp_path / "profile"
    data = home / ".claude/icode_data"
    data.mkdir(parents=True)
    (data / "ui_settings.json").write_text(json.dumps({"preferred_port": preferred_port}), encoding="utf-8")
    env = dict(os.environ, HOME=str(home), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    return data, env


def spawn(arguments, processes, *, env=None, cwd=None):
    process = subprocess.Popen([PYTHON, "-X", "utf8", "-B", *map(str, arguments)],
        cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    processes.append(process)
    return process


def pending_line(process):
    rows = queue.Queue()
    threading.Thread(target=lambda: rows.put(process.stdout.readline()), daemon=True).start()
    return rows


def line(process, rows=None):
    rows = pending_line(process) if rows is None else rows
    try:
        result = rows.get(timeout=15)
    except queue.Empty:
        pytest.fail("launcher did not announce within 15 seconds")
    if not result:
        process.wait(timeout=5)
        pytest.fail("launcher exited before announcement: " + process.stderr.read()[-2000:])
    if result.strip() == "{":
        output, error = process.communicate(timeout=8)
        pytest.fail("launcher error: " + result + output + error)
    return result


def start_ui(env, processes, tmp_path, *extra):
    return spawn([CLI, "ui", "--no-browser", *extra], processes, env=env, cwd=tmp_path)


def http(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def health(payload, data):
    registration = json.loads((data / "ui_instance.json").read_text(encoding="utf-8"))
    code, raw = http(payload["url"] + "api/v1/health")
    assert code == 200
    assert json.loads(raw) == {"ok": True, "schema_version": 1, "service": "icode-agent-ui",
                              "instance_id": registration["instance_id"], "mode": "global"}
    return registration


def test_cli_help_is_importable_on_native_platform():
    result = subprocess.run([PYTHON, "-X", "utf8", "-B", str(CLI), "--help"], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "{status,run,ui}" in result.stdout


def test_startup_lock_serializes_real_processes_and_releases(processes, tmp_path):
    script = """import importlib.util,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('lock_probe',sys.argv[1])
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print('waiting',flush=True)
with m.ui_startup_lock(Path(sys.argv[2])):
 print('locked',flush=True)
 sys.stdin.readline()
"""
    first = spawn(["-c", script, CLI, tmp_path / "data"], processes)
    assert line(first).strip() == "waiting"
    assert line(first).strip() == "locked"
    second = spawn(["-c", script, CLI, tmp_path / "data"], processes)
    assert line(second).strip() == "waiting"
    waiting = pending_line(second)
    with pytest.raises(queue.Empty):
        waiting.get(timeout=.25)
    first.stdin.write("release\n")
    first.stdin.flush()
    assert first.wait(timeout=5) == 0
    assert line(second, waiting).strip() == "locked"
    second.stdin.write("release\n")
    second.stdin.flush()
    assert second.wait(timeout=5) == 0


def test_startup_lock_releases_after_exception(tmp_path):
    cli = load_cli()
    with pytest.raises(RuntimeError, match="fixture"):
        with cli.ui_startup_lock(tmp_path):
            raise RuntimeError("fixture")
    with cli.ui_startup_lock(tmp_path):
        assert (tmp_path / cli.UI_START_LOCK_NAME).is_file()


def test_instance_write_is_atomic_and_clear_is_owner_bound(tmp_path):
    cli = load_cli()
    path = tmp_path / "instance.json"
    record = {"schema_version": 1, "service": cli.UI_SERVICE_NAME, "instance_id": "fixture-instance-0001",
              "host": "127.0.0.1", "port": 12345, "pid": os.getpid(), "mode": "global"}
    cli.write_ui_instance(path, record)
    assert cli.read_ui_instance(path) == record
    assert not list(tmp_path.glob(".*.tmp"))
    cli.clear_ui_instance(path, "another-instance-0001")
    assert path.is_file()
    cli.clear_ui_instance(path, record["instance_id"])
    assert not path.exists()


def test_instance_replace_failure_preserves_old_record(tmp_path):
    cli = load_cli()
    path = tmp_path / "instance.json"
    path.write_bytes(b"old record")
    with mock.patch.object(cli.os, "replace", side_effect=OSError("fixture replace failure")):
        with pytest.raises(OSError, match="fixture replace failure"):
            cli.write_ui_instance(path, {"new": True})
    assert path.read_bytes() == b"old record"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory durability barrier")
def test_posix_keeps_directory_fsync(tmp_path):
    cli = load_cli()
    with mock.patch.object(cli.os, "open", wraps=os.open) as opened:
        cli.write_ui_instance(tmp_path / "instance.json", {"value": 1})
    assert any(Path(call.args[0]) == tmp_path for call in opened.call_args_list)


def test_actual_ui_serves_page_health_and_reuses_instance(tmp_path, processes):
    data, env = profile(tmp_path)
    before = (data / "ui_settings.json").read_bytes()
    first = start_ui(env, processes, tmp_path)
    payload = json.loads(line(first))
    assert payload["ok"] and payload["reused"] is False
    registration = health(payload, data)
    assert registration["pid"] == first.pid
    code, page = http(payload["url"])
    assert code == 200 and b"ICODE" in page
    assert b"script" in page
    second = start_ui(env, processes, tmp_path)
    reused = json.loads(line(second))
    assert reused["reused"] is True and reused["url"] == payload["url"]
    assert second.wait(timeout=8) == 0 and first.poll() is None
    assert (data / "ui_settings.json").read_bytes() == before


def test_actual_concurrent_launches_share_one_instance(tmp_path, processes):
    data, env = profile(tmp_path)
    pair = [start_ui(env, processes, tmp_path), start_ui(env, processes, tmp_path)]
    payloads = [json.loads(line(process)) for process in pair]
    assert payloads[0]["url"] == payloads[1]["url"]
    assert sorted(payload["reused"] for payload in payloads) == [False, True]
    for process, payload in zip(pair, payloads):
        if payload["reused"]:
            assert process.wait(timeout=8) == 0
        else:
            assert health(payload, data)["pid"] == process.pid and process.poll() is None


def test_actual_terminated_instance_is_replaced(tmp_path, processes):
    data, env = profile(tmp_path)
    first = start_ui(env, processes, tmp_path)
    initial = json.loads(line(first))
    old = health(initial, data)
    first.terminate()
    first.wait(timeout=8)
    replacement = start_ui(env, processes, tmp_path)
    payload = json.loads(line(replacement))
    assert payload["reused"] is False
    current = health(payload, data)
    assert current["pid"] == replacement.pid and current["instance_id"] != old["instance_id"]


def test_actual_busy_port_falls_back_but_explicit_port_fails(tmp_path, processes):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        data, env = profile(tmp_path, port)
        first = start_ui(env, processes, tmp_path)
        payload = json.loads(line(first))
        assert payload["port_fallback"] is True and payload["url"] != f"http://127.0.0.1:{port}/"
        health(payload, data)
        explicit = start_ui(env, processes, tmp_path, "--port", str(port))
        output, error = explicit.communicate(timeout=12)
        assert explicit.returncode != 0 and not error
        assert json.loads(output)["ok"] is False
