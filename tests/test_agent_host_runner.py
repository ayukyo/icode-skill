import io
import os
import signal
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))

from icode_agent.host_runner import (
    ALLOWED_STEPS,
    HostJobRunner,
    HostRunnerError,
    build_icode_prompt,
)
from icode_agent.ticket_catalog import ResolvedTicket


def ticket(tmp_path: Path, ticket_id: str = "T-1") -> ResolvedTicket:
    project = tmp_path / "project"
    out_dir = project / ".icode_output" / ".icode_output_1"
    out_dir.mkdir(parents=True, exist_ok=True)
    return ResolvedTicket(
        ticket_id=ticket_id,
        project_path=project.resolve(),
        out_dir=out_dir.resolve(),
        status="plan_done",
        executable=True,
        generation="v3",
    )


class FakeProcess:
    def __init__(self, output="done", returncode=0, block=False):
        self.output = output
        self.returncode = returncode
        self.block = block
        self.terminated = False
        self.input_text = None

    def communicate(self, input=None):
        self.input_text = input
        deadline = time.monotonic() + 2
        while self.block and not self.terminated and time.monotonic() < deadline:
            time.sleep(0.01)
        if self.terminated:
            return "", None
        return self.output, None

    def poll(self):
        if self.block and not self.terminated:
            return None
        return -15 if self.terminated else self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        del timeout
        return self.poll()


class ProcessFactory:
    def __init__(self, processes):
        self.processes = list(processes)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        process = self.processes.pop(0)
        if isinstance(process, Exception):
            raise process
        return process


class FakeLifecycle:
    def __init__(self, *, already_applied=False):
        self.calls = []
        self.already_applied = already_applied

    def action_policy(self, target, action, expected_revision, verify_action=None):
        self.calls.append(("policy", target.ticket_id, action, expected_revision))
        self.verify_action = verify_action
        return {
            "action_allowed": True,
            "execution_root": str(target.project_path),
            "revision": {"token": expected_revision},
        }

    def record_spawn(self, target, *, step, host, model, request_id):
        self.calls.append(("spawn", target.ticket_id, step, host, model, request_id))
        return {
            "already_applied": self.already_applied,
            "spawn": {"spawn_id": "spawn-1"},
        }

    def record_result(self, target, *, spawn_id, result, adopted,
                      reason, evidence_ref, summary, request_id,
                      error_class=None):
        self.calls.append((
            "result", target.ticket_id, spawn_id, result, adopted, reason,
            evidence_ref, summary, request_id, error_class,
        ))
        return {"ok": True}


def wait_terminal(runner: HostJobRunner, job_id: str):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not reach terminal state")


def test_prompt_and_step_are_strictly_bounded():
    assert {"plan", "code", "verify", "ppt", "status", "study"}.issubset(ALLOWED_STEPS)
    assert build_icode_prompt("plan", "only inspect inputs") == (
        "/icode plan\n\n用户补充说明：\nonly inspect inputs"
    )
    assert build_icode_prompt("status", "") == "/icode status"
    assert build_icode_prompt("study", "") == "/icode study"
    pinned = build_icode_prompt("plan", "", "T-1")
    assert 'ticket_id = "T-1"' in pinned
    assert "禁止改用 latest" in pinned
    build = build_icode_prompt("verify", "--build 仅增量编译 module_a -j6", "T-1")
    assert build.splitlines()[0] == "/icode verify --build"
    assert 'ticket_id = "T-1"' in build
    assert "仅增量编译 module_a -j6" in build
    assert build_icode_prompt("verify", "--deploy").splitlines()[0] == "/icode verify --deploy"
    with pytest.raises(HostRunnerError, match="未知 verify"):
        build_icode_prompt("verify", "--builder")
    with pytest.raises(HostRunnerError, match="未登记步骤"):
        build_icode_prompt("shell", "rm -rf /tmp/x")
    with pytest.raises(HostRunnerError, match="过长"):
        build_icode_prompt("plan", "x" * 8001)


def test_codex_command_uses_argv_stdin_trusted_cwd_and_no_shell(tmp_path):
    process = FakeProcess('{"type":"result","text":"ok"}')
    factory = ProcessFactory([process])
    lifecycle = FakeLifecycle()
    runner = HostJobRunner(
        executable_resolver=lambda host: f"/usr/bin/{host}",
        process_factory=factory,
        lifecycle=lifecycle,
    )
    target = ticket(tmp_path)
    job = runner.start(
        target,
        step="plan",
        note="review inputs",
        settings={"host": "codex", "model": "gpt-test", "fallback": False},
        request_id="request-1",
        expected_revision="a" * 64,
    )
    finished = wait_terminal(runner, job["job_id"])

    argv, kwargs = factory.calls[0]
    assert argv == [
        "/usr/bin/codex", "exec", "--json", "-C", str(target.project_path),
        "--sandbox", "workspace-write", "--ephemeral", "-m", "gpt-test", "-",
    ]
    assert kwargs["cwd"] == str(target.project_path)
    assert kwargs["shell"] is False
    assert 'ticket_id = "T-1"' in process.input_text
    assert factory.calls[0][0].count("review inputs") == 0
    assert finished["state"] == "succeeded"
    assert finished["output"]
    assert "project_path" not in finished and "out_dir" not in finished
    assert [call[0] for call in lifecycle.calls] == ["policy", "spawn", "result"]
    assert lifecycle.calls[-1][3:5] == ("joined", "yes")


def test_claude_command_and_auto_selection(tmp_path):
    factory = ProcessFactory([FakeProcess("ok")])
    available = {"codex": None, "claude": "/opt/claude"}
    runner = HostJobRunner(
        executable_resolver=lambda host: available[host],
        process_factory=factory,
        lifecycle=FakeLifecycle(),
    )
    job = runner.start(
        ticket(tmp_path),
        step="status",
        note="",
        settings={"host": "auto", "model": "", "fallback": True},
        request_id="request-2",
        expected_revision="a" * 64,
    )
    wait_terminal(runner, job["job_id"])
    argv, kwargs = factory.calls[0]
    assert argv == [
        "/opt/claude", "-p", "--verbose", "--output-format", "stream-json",
        "--input-format", "text", "--permission-mode", "acceptEdits",
        "--no-session-persistence",
    ]
    assert kwargs["shell"] is False


@pytest.mark.parametrize("note,first", [
    ("--ticket T-1 --build 增量编译", "/icode verify --build --ticket T-1"),
    ("--plan", "/icode verify --plan"),
    ("--listen --reuse build-1", "/icode verify --listen --reuse build-1"),
    ("--test smoke", "/icode verify --test smoke"),
])
def test_verify_prompt_preserves_action_and_options(note, first):
    assert build_icode_prompt("verify", note, "T-1").splitlines()[0] == first


@pytest.mark.parametrize("note", ["--plan --deploy", "--ticket OTHER --build", "--build --reuse old"])
def test_verify_conflicts_fail_before_host_spawn(note):
    with pytest.raises(HostRunnerError):
        build_icode_prompt("verify", note, "T-1")


def test_verify_build_policy_receives_parsed_action(tmp_path):
    lifecycle = FakeLifecycle()
    runner = HostJobRunner(executable_resolver=lambda host: "/opt/codex",
        process_factory=ProcessFactory([FakeProcess()]), lifecycle=lifecycle)
    job = runner.start(ticket(tmp_path), step="verify", note="--ticket T-1 --build",
        settings={"host":"codex","model":"","fallback":False},
        request_id="verify-build-policy", expected_revision="a"*64)
    wait_terminal(runner, job["job_id"])
    assert lifecycle.verify_action == "build"


def test_fallback_happens_only_before_spawn(tmp_path):
    available = {"codex": None, "claude": "/opt/claude"}
    factory = ProcessFactory([FakeProcess("fallback ok")])
    runner = HostJobRunner(
        executable_resolver=lambda host: available[host],
        process_factory=factory,
        lifecycle=FakeLifecycle(),
    )
    job = runner.start(
        ticket(tmp_path), step="plan", note="",
        settings={"host": "codex", "model": "", "fallback": True},
        request_id="fallback-before-start",
        expected_revision="a" * 64,
    )
    assert wait_terminal(runner, job["job_id"])["host"] == "claude"
    assert len(factory.calls) == 1

    spawn_failure = ProcessFactory([OSError("synthetic spawn failure"), FakeProcess("must not run")])
    runner = HostJobRunner(
        executable_resolver=lambda host: f"/usr/bin/{host}",
        process_factory=spawn_failure,
        lifecycle=FakeLifecycle(),
    )
    with pytest.raises(HostRunnerError, match="启动失败"):
        runner.start(
            ticket(tmp_path), step="plan", note="",
            settings={"host": "codex", "model": "", "fallback": True},
            request_id="no-replay-after-spawn",
            expected_revision="a" * 64,
        )
    assert len(spawn_failure.calls) == 1


def test_one_active_job_per_ticket_duplicate_request_and_cancel(tmp_path):
    process = FakeProcess(block=True)
    factory = ProcessFactory([process, FakeProcess("unused")])
    runner = HostJobRunner(
        executable_resolver=lambda host: f"/usr/bin/{host}",
        process_factory=factory,
        lifecycle=FakeLifecycle(),
    )
    target = ticket(tmp_path)
    first = runner.start(
        target, step="code", note="",
        settings={"host": "codex", "model": "", "fallback": False},
        request_id="active-1",
        expected_revision="a" * 64,
    )
    with pytest.raises(HostRunnerError, match="已有活动任务"):
        runner.start(
            target, step="code", note="",
            settings={"host": "codex", "model": "", "fallback": False},
            request_id="active-2",
            expected_revision="a" * 64,
        )
    with pytest.raises(HostRunnerError, match="request_id"):
        runner.start(
            ticket(tmp_path, "T-2"), step="code", note="",
            settings={"host": "codex", "model": "", "fallback": False},
            request_id="active-1",
            expected_revision="a" * 64,
        )
    cancelled = runner.cancel(first["job_id"])
    assert cancelled["cancel_requested"] is True
    assert wait_terminal(runner, first["job_id"])["state"] == "cancelled"
    assert len(factory.calls) == 1


def test_cancel_targets_the_process_group_when_pid_is_available(tmp_path, monkeypatch):
    process = FakeProcess(block=True)
    process.pid = 43210
    killed = []
    monkeypatch.setattr(os, "killpg", lambda pid, signo: killed.append((pid, signo)))
    runner = HostJobRunner(
        executable_resolver=lambda host: f"/usr/bin/{host}",
        process_factory=ProcessFactory([process]),
        lifecycle=FakeLifecycle(),
    )
    started = runner.start(
        ticket(tmp_path), step="code", note="",
        settings={"host": "codex", "model": "", "fallback": False},
        request_id="process-group-cancel",
        expected_revision="a" * 64,
    )
    runner.cancel(started["job_id"])
    assert killed == [(process.pid, signal.SIGTERM)]
    process.terminated = True
    assert wait_terminal(runner, started["job_id"])["state"] == "cancelled"


def test_output_is_bounded_and_nonzero_exit_is_failed(tmp_path):
    factory = ProcessFactory([FakeProcess("界" * 100, returncode=9)])
    runner = HostJobRunner(
        executable_resolver=lambda host: f"/usr/bin/{host}",
        process_factory=factory,
        lifecycle=FakeLifecycle(),
        max_output_bytes=64,
    )
    started = runner.start(
        ticket(tmp_path), step="verify", note="",
        settings={"host": "codex", "model": "", "fallback": False},
        request_id="bounded-output",
        expected_revision="a" * 64,
    )
    job = wait_terminal(runner, started["job_id"])
    assert job["state"] == "failed"
    assert job["returncode"] == 9
    assert job["truncated"] is True
    assert len(job["output"].encode("utf-8")) <= 64


def test_control_plane_duplicate_spawn_never_starts_process(tmp_path):
    factory = ProcessFactory([FakeProcess("must not run")])
    runner = HostJobRunner(
        executable_resolver=lambda host: f"/usr/bin/{host}",
        process_factory=factory,
        lifecycle=FakeLifecycle(already_applied=True),
    )
    with pytest.raises(HostRunnerError, match="控制面"):
        runner.start(
            ticket(tmp_path), step="plan", note="",
            settings={"host": "codex", "model": "", "fallback": False},
            request_id="already-recorded",
            expected_revision="a" * 64,
        )
    assert factory.calls == []
