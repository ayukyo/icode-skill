import http.client
import io
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))

from icode_agent.host_runner import HostJobRunner, HostRunnerError
from icode_agent import __version__
from icode_agent.ticket_catalog import ResolvedTicket
from icode_agent.ui_server import create_ui_server


CONTROL = ROOT / "tools" / "icode_control.py"


class Lifecycle:
    def action_policy(self, target, action, expected_revision):
        return {
            "action_allowed": True,
            "execution_root": str(target.project_path),
            "revision": {"token": expected_revision},
        }

    def record_spawn(self, target, **_kwargs):
        return {"already_applied": False, "spawn": {"spawn_id": "spawn-v2"}}

    def record_result(self, target, **_kwargs):
        return {"ok": True}


class StreamingProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            '{"type":"status","message":"正在分析需求"}\n'
            '{"type":"result","text":"分析完成"}\n'
        )
        self.returncode = None

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode


def resolved_ticket(tmp_path: Path) -> ResolvedTicket:
    project = tmp_path / "project"
    out_dir = project / ".icode_output" / ".icode_output_1"
    out_dir.mkdir(parents=True)
    return ResolvedTicket(
        ticket_id="M2-1",
        project_path=project.resolve(),
        out_dir=out_dir.resolve(),
        status="plan_done",
        executable=True,
        generation="v3",
    )


def wait_terminal(runner: HostJobRunner, job_id: str):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job["state"] in {"succeeded", "failed", "cancelled", "outcome_unknown"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_host_runner_exposes_incremental_normalized_events(tmp_path):
    process = StreamingProcess()
    runner = HostJobRunner(
        lifecycle=Lifecycle(),
        executable_resolver=lambda _host: "/usr/bin/codex",
        process_factory=lambda *_args, **_kwargs: process,
        recovery_path=tmp_path / "ui_jobs.json",
    )

    started = runner.start(
        resolved_ticket(tmp_path),
        step="plan",
        note="",
        settings={"host": "codex", "model": "", "fallback": False},
        request_id="manager-v2-stream",
        expected_revision="a" * 64,
    )
    finished = wait_terminal(runner, started["job_id"])
    batch = runner.events(after=0, ticket_id="M2-1")

    assert finished["state"] == "succeeded"
    assert batch["cursor"] >= 4
    assert [event["seq"] for event in batch["events"]] == sorted(
        event["seq"] for event in batch["events"]
    )
    assert any(event["kind"] == "progress" for event in batch["events"])
    assert any("正在分析需求" in event["message"] for event in batch["events"])
    assert process.stdin.getvalue().startswith("/icode plan")


def test_active_job_recovers_as_unknown_without_raw_output_or_replay(tmp_path):
    recovery_path = tmp_path / "ui_jobs.json"
    recovery_path.write_text(json.dumps({
        "schema_version": 1,
        "jobs": [{
            "job_id": "job-before-restart",
            "request_id": "request-before-restart",
            "ticket_id": "M2-1",
            "step": "code",
            "host": "codex",
            "model": "",
            "state": "running",
            "created_at": "2026-09-14T00:00:00+00:00",
            "finished_at": None,
            "returncode": None,
            "truncated": False,
            "cancel_requested": False,
            "spawn_id": "spawn-before-restart",
            "output": "must not survive",
        }],
    }), encoding="utf-8")

    runner = HostJobRunner(
        lifecycle=Lifecycle(),
        recovery_path=recovery_path,
    )
    recovered = runner.get("job-before-restart")

    assert recovered["state"] == "outcome_unknown"
    assert recovered["recovery_reason"] == "runtime_restarted"
    assert "output" not in recovered
    assert runner.list()[0]["job_id"] == "job-before-restart"
    assert json.loads(recovery_path.read_text(encoding="utf-8"))["jobs"][0].get("output") is None


def test_recovery_write_failure_does_not_orphan_a_started_process(tmp_path):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    process = StreamingProcess()
    runner = HostJobRunner(
        lifecycle=Lifecycle(),
        executable_resolver=lambda _host: "/usr/bin/codex",
        process_factory=lambda *_args, **_kwargs: process,
        recovery_path=blocked_parent / "ui_jobs.json",
    )

    started = runner.start(
        resolved_ticket(tmp_path), step="plan", note="",
        settings={"host": "codex", "model": "", "fallback": False},
        request_id="manager-v2-no-recovery",
        expected_revision="a" * 64,
    )

    assert wait_terminal(runner, started["job_id"])["state"] == "succeeded"
    assert runner.recovery_available is False


def test_host_output_redacts_common_secret_shapes(tmp_path):
    process = StreamingProcess()
    process.stdout = io.StringIO(
        '{"type":"status","message":"api_key=private-value"}\n'
        'Authorization: Bearer private-token\n'
    )
    runner = HostJobRunner(
        lifecycle=Lifecycle(),
        executable_resolver=lambda _host: "/usr/bin/codex",
        process_factory=lambda *_args, **_kwargs: process,
    )
    started = runner.start(
        resolved_ticket(tmp_path), step="plan", note="",
        settings={"host": "codex", "model": "", "fallback": False},
        request_id="manager-v2-redaction",
        expected_revision="a" * 64,
    )
    finished = wait_terminal(runner, started["job_id"])
    encoded = json.dumps(
        {"job": finished, "events": runner.events()["events"]},
        ensure_ascii=False,
    )

    assert "private-value" not in encoded
    assert "private-token" not in encoded
    assert "[已隐藏]" in encoded


def make_ticket(project: Path) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    out_dir = project / ".icode_output" / ".icode_output_1"
    subprocess.run([
        sys.executable, str(CONTROL), "create", "--dir", str(out_dir),
        "--ticket-id", "M2-UI-1", "--requirement", "manager cockpit",
        "--birth", "init",
    ], check=True, capture_output=True, text=True)
    subprocess.run([
        sys.executable, str(CONTROL), "snapshot", "--dir", str(out_dir),
    ], check=True, capture_output=True, text=True)
    (out_dir / "00_init.md").write_text("# 初稿\n", encoding="utf-8")
    return out_dir


class DashboardRunner:
    def capabilities(self):
        return {"codex": True, "claude": True}

    def list(self):
        return []

    def events(self, *, after=0, ticket_id=None):
        del ticket_id
        return {"cursor": after, "events": []}

    def start(self, *_args, **_kwargs):
        raise HostRunnerError("sensitive detail", code="ticket_busy")

    def cancel(self, _job_id):
        raise AssertionError("not used")


@contextmanager
def dashboard(tmp_path: Path):
    project = tmp_path / "project"
    out_dir = make_ticket(project)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({
        "version": 1,
        "updated_at": "2026-09-14T00:00:00+00:00",
        "tickets": [{
            "control_schema_version": 3,
            "ticket_id": "M2-UI-1",
            "project_path": str(project),
            "out_dir": str(out_dir.relative_to(project)),
            "status": "init_in_progress",
            "requirement_summary": "manager cockpit",
            "updated_at": "2026-09-14T00:00:00+00:00",
        }],
    }), encoding="utf-8")
    server = create_ui_server(
        ticket_dir=None,
        control_path=CONTROL,
        index_path=index,
        settings_path=tmp_path / "settings.json",
        host_runner=DashboardRunner(),
        seed_project_paths=[project],
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(server, method, path, payload=None):
    host, port = server.server_address
    headers = {
        "Host": f"{host}:{port}",
        "X-ICODE-UI-Token": server.ui_token,
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    result = response.status, json.loads(response.read())
    conn.close()
    return result


def test_ticket_detail_contains_sanitized_cockpit_and_job_events(tmp_path):
    with dashboard(tmp_path) as server:
        status, payload = request(server, "GET", "/api/v1/tickets/M2-UI-1")
        assert status == 200
        cockpit = payload["ticket"]["cockpit"]
        assert cockpit["progress"][0] == {
            "id": "0", "label": "需求确认", "state": "current",
        }
        assert cockpit["delivery"]["verdict"] == "unknown"
        assert cockpit["verification"]["total"] == 0
        assert {item["name"] for item in cockpit["artifacts"]} >= {
            "00_init.md", "ticket_snapshot.json",
        }
        assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)

        event_status, events = request(
            server, "GET", "/api/v1/job-events?after=7&ticket_id=M2-UI-1")
        assert event_status == 200
        assert events == {"ok": True, "cursor": 7, "events": []}


def test_host_error_is_safe_and_has_beginner_recovery_action(tmp_path):
    with dashboard(tmp_path) as server:
        _, detail = request(server, "GET", "/api/v1/tickets/M2-UI-1")
        status, payload = request(server, "POST", "/api/v1/steps/run", {
            "ticket_id": "M2-UI-1",
            "step": "plan",
            "note": "",
            "request_id": "manager-v2-error",
            "expected_revision": detail["ticket"]["revision"]["token"],
        })

        assert status == 409
        assert payload["error_code"] == "ticket_busy"
        assert payload["recovery_action"] == "wait"
        assert payload["error"] == "这个工单已有任务在运行，请等待完成或先取消现有任务。"
        assert "sensitive detail" not in json.dumps(payload, ensure_ascii=False)


def test_manager_v2_frontend_contract_is_visible_and_beginner_friendly():
    html = (ROOT / "agent_runtime/icode_agent/ui_assets/index.html").read_text(
        encoding="utf-8")
    js = (ROOT / "agent_runtime/icode_agent/ui_assets/app.js").read_text(
        encoding="utf-8")

    for element_id in (
        "ticket-status-filter", "ticket-sort", "progress-steps",
        "artifact-list", "verification-summary", "delivery-verdict",
        "notice-action",
    ):
        assert f'id="{element_id}"' in html
    assert "STEP_LABELS" in js
    assert 'plan: "制定实施计划"' in js
    assert "/api/v1/job-events" in js
    assert "globalThis.confirm" in js
    assert '&& !job.cancel_requested' in js
    assert "recovery_action" in js
    assert "ticket.executable && ticketMatchesStatus(ticket)" in js
    assert __version__ == "0.4.0"
