import errno
import http.client
import importlib.util
import json
import os
import signal
import socket
import time
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))
from icode_agent.ui_server import create_ui_server
from icode_agent.host_runner import ControlLifecycle, HostJobRunner, HostRunnerError

CLI_SPEC = importlib.util.spec_from_file_location(
    "icode_agent_cli", ROOT / "tools" / "icode_agent.py")
assert CLI_SPEC is not None and CLI_SPEC.loader is not None
agent_cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(agent_cli)


CONTROL = ROOT / "tools" / "icode_control.py"


def make_ticket(project: Path, number: int, ticket_id: str) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    out_dir = project / ".icode_output" / f".icode_output_{number}"
    subprocess.run(
        [
            sys.executable, str(CONTROL), "create", "--dir", str(out_dir),
            "--ticket-id", ticket_id, "--requirement", f"requirement {ticket_id}",
            "--birth", "init",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out_dir


def write_index(path: Path, project: Path, out_dir: Path, ticket_id: str,
                status: str = "plan_done") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "updated_at": "2026-09-13T10:00:00+08:00",
        "tickets": [{
            "control_schema_version": 3,
            "ticket_id": ticket_id,
            "project_path": str(project),
            "out_dir": str(out_dir.relative_to(project)),
            "status": status,
            "requirement_summary": f"requirement {ticket_id}",
            "updated_at": "2026-09-13T10:00:00+08:00",
        }],
    }), encoding="utf-8")


class StubRunner:
    def __init__(self):
        self.started = []
        self.cancelled = []

    def capabilities(self):
        return {"codex": True, "claude": True}

    def list(self):
        return [{
            "job_id": "job-1", "ticket_id": "UI-1", "step": "review",
            "host": "codex", "model": "", "state": "running", "output": "",
            "truncated": False, "cancel_requested": False,
        }]

    def get(self, job_id):
        if job_id != "job-1":
            raise RuntimeError("missing")
        return self.list()[0]

    def start(self, ticket, *, step, note, settings, request_id,
              expected_revision):
        self.started.append((
            ticket, step, note, settings, request_id, expected_revision))
        return self.list()[0]

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        result = self.list()[0]
        result["cancel_requested"] = True
        return result


@contextmanager
def running_dashboard(tmp_path: Path):
    project = tmp_path / "project"
    out_dir = make_ticket(project, 1, "UI-1")
    index_path = tmp_path / "index.json"
    settings_path = tmp_path / "settings.json"
    write_index(index_path, project, out_dir, "UI-1")
    runner = StubRunner()
    server = create_ui_server(
        ticket_dir=None,
        control_path=CONTROL,
        index_path=index_path,
        settings_path=settings_path,
        host_runner=runner,
        seed_project_paths=[project],
        initial_ticket_id="UI-1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, runner, settings_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(server, method, path, *, payload=None, token=True):
    host, port = server.server_address
    headers = {"Host": f"{host}:{port}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-ICODE-UI-Token"] = server.ui_token
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    parsed = json.loads(response.read())
    conn.close()
    return response.status, parsed


def isolated_ui_home(tmp_path: Path, preferred_port: int) -> tuple[Path, Path]:
    home = tmp_path / "home"
    data_dir = home / ".claude" / "icode_data"
    data_dir.mkdir(parents=True)
    (data_dir / "ui_settings.json").write_text(json.dumps({
        "host": "auto",
        "model": "",
        "mode": "simple",
        "fallback": True,
        "show_next_step": True,
        "preferred_port": preferred_port,
        "refresh_interval_seconds": 30,
    }), encoding="utf-8")
    return home, data_dir


def start_isolated_ui(home: Path):
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "icode_agent.py"),
         "ui", "--no-browser"],
        cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def stop_ui_processes(processes) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_bootstrap_tickets_detail_and_jobs_do_not_expose_paths(tmp_path):
    with running_dashboard(tmp_path) as (server, _, _):
        denied, response = request(
            server, "GET", "/api/v1/bootstrap", token=False)
        assert denied == 403 and response["error_class"] == "UIAccessError"

        status, bootstrap = request(server, "GET", "/api/v1/bootstrap")
        assert status == 200 and bootstrap["ok"] is True
        assert bootstrap["initial_ticket_id"] == "UI-1"
        assert bootstrap["hosts"] == {"codex": True, "claude": True}
        assert bootstrap["settings"]["preferred_port"] == 8765
        assert bootstrap["settings"]["refresh_interval_seconds"] == 30
        assert bootstrap["tickets"][0]["ticket_id"] == "UI-1"

        status, tickets = request(server, "GET", "/api/v1/tickets?query=UI")
        assert status == 200 and len(tickets["tickets"]) == 1

        status, detail = request(server, "GET", "/api/v1/tickets/UI-1")
        assert status == 200
        assert detail["ticket"]["status"] == "init_in_progress"
        assert detail["ticket"]["next_step"] == "plan"
        assert "validation" in detail["ticket"]

        status, jobs = request(server, "GET", "/api/v1/jobs")
        assert status == 200 and jobs["jobs"][0]["job_id"] == "job-1"

        status, refresh = request(
            server, "GET", "/api/v1/refresh?ticket_id=UI-1")
        assert status == 200 and refresh["ticket"]["ticket_id"] == "UI-1"
        assert refresh["jobs"][0]["job_id"] == "job-1"
        assert refresh["observed_at"]

        encoded = json.dumps(
            [bootstrap, tickets, detail, jobs, refresh], ensure_ascii=False)
        assert str(tmp_path) not in encoded


def test_refresh_degrades_one_broken_ticket_without_hiding_catalog(tmp_path):
    with running_dashboard(tmp_path) as (server, _, _):
        def fail_projection(_ticket):
            raise HostRunnerError(
                "sensitive local failure /must/not/leak",
                code="control_plane_error",
            )

        server.service.lifecycle.projection = fail_projection
        status, refresh = request(
            server, "GET", "/api/v1/refresh?ticket_id=UI-1")

        assert status == 200
        assert refresh["ok"] is True
        assert refresh["schema_version"] == 2
        assert refresh["tickets"][0]["ticket_id"] == "UI-1"
        assert refresh["ticket"]["ticket_id"] == "UI-1"
        assert refresh["ticket"]["allowed_actions"] == []
        assert refresh["ticket"]["revision"] is None
        assert refresh["ticket"]["validation"] == {
            "ok": False, "mode": "control-unavailable",
        }
        assert refresh["ticket_error"]["code"] == "ticket_detail_unavailable"
        encoded = json.dumps(refresh, ensure_ascii=False)
        assert "must/not/leak" not in encoded
        assert str(tmp_path) not in encoded


def test_settings_step_run_and_cancel_require_token_and_strict_fields(tmp_path):
    with running_dashboard(tmp_path) as (server, runner, settings_path):
        _, detail = request(server, "GET", "/api/v1/tickets/UI-1")
        revision = detail["ticket"]["revision"]["token"]
        status, response = request(
            server, "PUT", "/api/v1/settings",
            payload={
                "host": "claude", "model": "sonnet", "mode": "advanced",
                "fallback": False, "show_next_step": True, "preferred_port": 8765,
            },
            token=False,
        )
        assert status == 403 and response["ok"] is False

        status, response = request(
            server, "PUT", "/api/v1/settings",
            payload={
                "host": "claude", "model": "sonnet", "mode": "advanced",
                "fallback": False, "show_next_step": True, "preferred_port": 8765,
            },
        )
        assert status == 200 and response["settings"]["host"] == "claude"
        assert settings_path.is_file()

        status, response = request(
            server, "POST", "/api/v1/steps/run",
            payload={
                "ticket_id": "UI-1", "step": "review", "note": "check plan",
                "request_id": "ui-request-1", "expected_revision": revision,
            },
        )
        assert status == 202 and response["job"]["job_id"] == "job-1"
        assert runner.started[0][1:3] == ("review", "check plan")

        status, response = request(
            server, "POST", "/api/v1/steps/run",
            payload={
                "ticket_id": "UI-1", "step": "review", "note": "",
                "request_id": "ui-request-2", "expected_revision": revision,
                "ticket_dir": "/tmp/escape",
            },
        )
        assert status == 400 and "未登记字段" in response["error"]

        status, response = request(
            server, "POST", "/api/v1/jobs/job-1/cancel", payload={}
        )
        assert status == 200 and response["job"]["cancel_requested"] is True
        assert runner.cancelled == ["job-1"]


def test_management_operations_do_not_launch_codex_or_claude(tmp_path):
    with running_dashboard(tmp_path) as (server, runner, _):
        for path in (
            "/api/v1/bootstrap",
            "/api/v1/tickets",
            "/api/v1/tickets/UI-1",
            "/api/v1/refresh?ticket_id=UI-1",
            "/api/v1/jobs",
            "/api/v1/settings",
        ):
            status, response = request(server, "GET", path)
            assert status == 200, response

        status, response = request(
            server, "PUT", "/api/v1/settings",
            payload={
                "host": "auto", "model": "", "mode": "simple",
                "fallback": True, "show_next_step": True,
                "preferred_port": 8765,
                "refresh_interval_seconds": 45,
            },
        )
        assert status == 200
        assert response["settings"]["refresh_interval_seconds"] == 45
        assert runner.started == []


def test_create_ticket_uses_opaque_project_and_returns_no_paths(tmp_path):
    with running_dashboard(tmp_path) as (server, runner, _):
        _, bootstrap = request(server, "GET", "/api/v1/bootstrap")
        project_id = bootstrap["projects"][0]["project_id"]

        denied, response = request(
            server, "POST", "/api/v1/tickets/create",
            payload={
                "project_id": project_id,
                "requirement": "new UI-managed ticket",
                "request_id": "ui-create-dashboard-1",
            },
            token=False,
        )
        assert denied == 403 and response["error_class"] == "UIAccessError"

        status, response = request(
            server, "POST", "/api/v1/tickets/create",
            payload={
                "project_id": project_id,
                "requirement": "new UI-managed ticket",
                "request_id": "ui-create-dashboard-1",
            },
        )
        assert status == 201, response
        assert response["ticket"]["status"] == "init_in_progress"
        assert response["ticket"]["next_step"] == "plan"
        assert str(tmp_path) not in json.dumps(response, ensure_ascii=False)

        replay_status, replay = request(
            server, "POST", "/api/v1/tickets/create",
            payload={
                "project_id": project_id,
                "requirement": "new UI-managed ticket",
                "request_id": "ui-create-dashboard-1",
            },
        )
        assert replay_status == 200
        assert replay["already_applied"] is True

        bad_status, bad = request(
            server, "POST", "/api/v1/tickets/create",
            payload={
                "project_id": project_id,
                "requirement": "bad",
                "request_id": "ui-create-dashboard-2",
                "workspace": "/tmp/escape",
            },
        )
        assert bad_status == 400 and "未登记字段" in bad["error"]
        assert runner.started == []


def test_global_legacy_status_endpoint_returns_clean_client_error(tmp_path):
    with running_dashboard(tmp_path) as (server, _, _):
        status, response = request(server, "GET", "/api/v1/status", token=False)
        assert status == 400
        assert response["error_class"] == "UIRequestError"


def test_ui_cli_defaults_are_simple_and_keep_advanced_dir():
    parser = agent_cli.build_parser()
    simple = parser.parse_args(["ui", "--no-browser"])
    assert simple.dir is None
    assert simple.port is None
    assert simple.ticket is None
    assert simple.project is None

    advanced = parser.parse_args([
        "ui", "--dir", "/tmp/ticket", "--port", "9010", "--no-browser"
    ])
    assert advanced.dir == "/tmp/ticket"
    assert advanced.port == 9010


def test_repeated_simple_ui_reuses_fallback_instance(tmp_path):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    blocked_port = blocker.getsockname()[1]
    home, data_dir = isolated_ui_home(tmp_path, blocked_port)
    processes = []
    try:
        first = start_isolated_ui(home)
        processes.append(first)
        first_payload = json.loads(first.stdout.readline())
        assert first_payload["port_fallback"] is True
        assert first_payload["reused"] is False
        assert first_payload["url"] != f"http://127.0.0.1:{blocked_port}/"

        second = start_isolated_ui(home)
        processes.append(second)
        second_payload = json.loads(second.stdout.readline())
        assert second_payload["reused"] is True
        assert second_payload["url"] == first_payload["url"]
        assert second.wait(timeout=3) == 0
        assert first.poll() is None
        instance = json.loads((data_dir / "ui_instance.json").read_text())
        assert instance["port"] == int(first_payload["url"].split(":")[-1][:-1])
        assert "token" not in instance
        assert os.stat(data_dir / "ui_instance.json").st_mode & 0o077 == 0
    finally:
        blocker.close()
        stop_ui_processes(processes)
    assert not (data_dir / "ui_instance.json").exists()


def test_concurrent_simple_ui_launches_share_one_instance(tmp_path):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    preferred_port = probe.getsockname()[1]
    probe.close()
    home, data_dir = isolated_ui_home(tmp_path, preferred_port)
    processes = [start_isolated_ui(home), start_isolated_ui(home)]
    try:
        payloads = [json.loads(process.stdout.readline()) for process in processes]
        assert payloads[0]["url"] == payloads[1]["url"]
        assert sorted(payload["reused"] for payload in payloads) == [False, True]
        server_index = next(
            index for index, payload in enumerate(payloads) if not payload["reused"])
        reused_index = 1 - server_index
        assert processes[reused_index].wait(timeout=3) == 0
        assert processes[server_index].poll() is None
    finally:
        stop_ui_processes(processes)
    assert not (data_dir / "ui_instance.json").exists()


def test_stale_ui_instance_is_replaced_and_cleaned(tmp_path):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    preferred_port = probe.getsockname()[1]
    probe.close()
    home, data_dir = isolated_ui_home(tmp_path, preferred_port)
    stale_id = "stale-instance-id-0001"
    (data_dir / "ui_instance.json").write_text(json.dumps({
        "schema_version": 1,
        "service": "icode-agent-ui",
        "instance_id": stale_id,
        "host": "127.0.0.1",
        "port": preferred_port,
        "pid": 999999,
        "mode": "global",
    }), encoding="utf-8")
    process = start_isolated_ui(home)
    try:
        payload = json.loads(process.stdout.readline())
        assert payload["reused"] is False
        current = json.loads((data_dir / "ui_instance.json").read_text())
        assert current["instance_id"] != stale_id
        assert current["port"] == preferred_port
    finally:
        stop_ui_processes([process])
    assert not (data_dir / "ui_instance.json").exists()


def test_default_port_conflict_falls_back_but_explicit_port_does_not():
    calls = []
    sentinel = object()

    def factory(*, port, **_kwargs):
        calls.append(port)
        if port == 8765:
            raise OSError(errno.EADDRINUSE, "in use")
        return sentinel

    server, fell_back = agent_cli.bind_ui_server(
        factory, {"control_path": CONTROL}, preferred_port=8765,
        explicit_port=False,
    )
    assert server is sentinel and fell_back is True and calls == [8765, 0]

    calls.clear()
    with pytest.raises(OSError):
        agent_cli.bind_ui_server(
            factory, {"control_path": CONTROL}, preferred_port=8765,
            explicit_port=True,
        )
    assert calls == [8765]


class ImmediateProcess:
    def __init__(self):
        self.returncode = 0

    def communicate(self, input=None):
        assert input and input.startswith("/icode plan")
        return '{"type":"result","text":"offline"}', None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


def test_real_control_lifecycle_rejects_stale_revision_and_closes_spawn(tmp_path):
    project = tmp_path / "project"
    out_dir = make_ticket(project, 1, "UI-1")
    index_path = tmp_path / "index.json"
    write_index(index_path, project, out_dir, "UI-1", status="init_in_progress")
    process_calls = []

    def process_factory(argv, **kwargs):
        process_calls.append((argv, kwargs))
        return ImmediateProcess()

    lifecycle = ControlLifecycle(CONTROL)
    runner = HostJobRunner(
        lifecycle=lifecycle,
        executable_resolver=lambda host: "/usr/bin/codex" if host == "codex" else None,
        process_factory=process_factory,
    )
    server = create_ui_server(
        ticket_dir=None, control_path=CONTROL, index_path=index_path,
        settings_path=tmp_path / "settings.json", host_runner=runner, port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, detail = request(server, "GET", "/api/v1/tickets/UI-1")
        revision = detail["ticket"]["revision"]["token"]

        stale_status, stale = request(
            server, "POST", "/api/v1/steps/run",
            payload={
                "ticket_id": "UI-1", "step": "plan", "note": "",
                "request_id": "stale-request", "expected_revision": "0" * 64,
            },
        )
        assert stale_status == 409
        assert stale["error_class"] == "HostRunnerError"
        assert process_calls == []

        run_status, started = request(
            server, "POST", "/api/v1/steps/run",
            payload={
                "ticket_id": "UI-1", "step": "plan", "note": "",
                "request_id": "safe-request", "expected_revision": revision,
            },
        )
        assert run_status == 202
        deadline = time.monotonic() + 3
        job = started["job"]
        while time.monotonic() < deadline:
            _, jobs = request(server, "GET", "/api/v1/jobs")
            job = jobs["jobs"][0]
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        assert job["state"] == "succeeded"
        assert len(process_calls) == 1

        trace = subprocess.run(
            [sys.executable, str(CONTROL), "trace", "--dir", str(out_dir)],
            check=True, capture_output=True, text=True)
        payload = json.loads(trace.stdout)
        assert payload["open_agents"] == {}
        assert payload["agent_spawns"][-1]["result"] == "joined"
        assert payload["agent_spawns"][-1]["exclusive_key"] == "ui-step-run"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
