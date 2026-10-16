"""Offline host boundaries: a recorded intent is not permission to start a process."""
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))
from icode_agent.host_runner import ControlLifecycle, HostJobRunner, HostRunnerError
from icode_agent.ticket_catalog import ResolvedTicket


def ticket(tmp_path):
    return ResolvedTicket(ticket_id="stage-host", project_path=tmp_path,
                          out_dir=tmp_path / ".icode_output_1", status="plan_done",
                          executable=True, generation="v3")


class Lifecycle:
    def __init__(self, root):
        self.root, self.policies, self.results, self.checks = root, [], [], []
        self.stage = {"enabled": True, "program_id": "p", "stage_id": "s", "generation": 4}

    def action_policy(self, target, step, revision, **kwargs):
        self.policies.append(kwargs.get("verify_action", step))
        if kwargs.get("verify_action") == "deploy" and getattr(self, "deny_deploy", False):
            raise HostRunnerError("deployment not authorized", code="stage_gate")
        return {"execution_root": str(self.root), "stage": dict(self.stage)}

    def record_spawn(self, target, **kwargs):
        self.spawn_options = kwargs
        if getattr(self, "invalidate_on_spawn", False):
            self.stage["generation"] += 1
        return {"spawn": {"spawn_id": "spawn-stage"}}

    def before_spawn(self, target, **kwargs):
        self.checks.append(kwargs)
        if kwargs["stage_snapshot"] != self.stage:
            raise HostRunnerError("stage changed", code="stage_gate")

    def record_result(self, target, **kwargs):
        self.results.append(kwargs)
        return {"ok": True}


class Process:
    def __init__(self):
        self.stdin, self.stdout, self.returncode = io.StringIO(), io.StringIO("done\n"), None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode


def run(runner, target, step="plan", note="", request_id="one"):
    return runner.start(target, step=step, note=note,
                        settings={"host": "codex", "fallback": False},
                        request_id=request_id, expected_revision="a" * 64)


def setup(tmp_path, lifecycle):
    calls = []
    def factory(*args, **kwargs):
        calls.append(args)
        return Process()
    return HostJobRunner(lifecycle=lifecycle, executable_resolver=lambda host: "offline-host",
                         process_factory=factory), calls


@pytest.mark.parametrize("missing_adapter", [False, True])
def test_stage_invalidated_after_spawn_record_never_starts_process(tmp_path, missing_adapter):
    lifecycle = Lifecycle(tmp_path)
    lifecycle.invalidate_on_spawn = True
    if missing_adapter:
        lifecycle.before_spawn = None
    runner, calls = setup(tmp_path, lifecycle)
    with pytest.raises(HostRunnerError, match="stage|阶段"):
        run(runner, ticket(tmp_path))
    assert calls == []
    assert lifecycle.results[0]["result"] == "failed"
    assert lifecycle.results[0]["error_class"] == "stage_gate"
    with pytest.raises(HostRunnerError) as again:
        run(runner, ticket(tmp_path))
    assert again.value.code == "duplicate_request"


def test_verify_checks_each_action_before_recording_spawn(tmp_path):
    lifecycle = Lifecycle(tmp_path)
    lifecycle.deny_deploy = True
    runner, calls = setup(tmp_path, lifecycle)
    with pytest.raises(HostRunnerError):
        run(runner, ticket(tmp_path), "verify", "--build --deploy --test smoke")
    assert lifecycle.policies == ["build", "deploy"]
    assert calls == [] and not hasattr(lifecycle, "spawn_options")


@pytest.mark.parametrize("note,actions,stage_action", [
    ("--build --deploy --test smoke", ["build", "deploy", "device_test"], "verify"),
    ("--plan", ["plan"], "plan"), ("", ["intent"], "plan")])
def test_host_rechecks_all_verify_actions_and_preserves_read_only_plan(tmp_path, note, actions, stage_action):
    lifecycle = Lifecycle(tmp_path)
    runner, calls = setup(tmp_path, lifecycle)
    started = run(runner, ticket(tmp_path), "verify", note)
    assert calls and lifecycle.policies == actions
    assert lifecycle.checks[0]["verify_actions"] == actions
    assert lifecycle.spawn_options["stage_action"] == stage_action
    deadline = time.monotonic() + 3
    while runner.get(started["job_id"])["state"] == "running" and time.monotonic() < deadline:
        time.sleep(.01)
    assert runner.get(started["job_id"])["state"] == "succeeded"


def test_control_lifecycle_final_check_uses_actual_stage_guard_not_general_open_agent_policy(tmp_path):
    lifecycle = ControlLifecycle(ROOT / "tools/icode_control.py")
    previous = {"enabled": True, "program_id": "p", "stage_id": "s", "generation": 4}
    calls = []
    module = SimpleNamespace(stage_projection=lambda directory: dict(previous))
    def guard(directory, action, **kwargs):
        calls.append((action, kwargs))
        if action == "deploy":
            raise ValueError("predecessor invalidated")
        return dict(previous)
    module.require_stage_action = guard
    lifecycle._stage_module = module
    with pytest.raises(HostRunnerError):
        lifecycle.before_spawn(ticket(tmp_path), step="verify", spawn_id="own-spawn",
                               stage_snapshot=previous, verify_actions=["build", "deploy", "device_test"])
    assert [action for action, _ in calls] == ["build", "deploy"]
    assert all(options["expected_generation"] == 4 for _, options in calls)


@pytest.mark.parametrize("change", [{"enabled": False}, {"generation": 5}, {"stage_id": "other"}])
def test_control_lifecycle_rejects_changed_stage_identity(tmp_path, change):
    lifecycle = ControlLifecycle(ROOT / "tools/icode_control.py")
    previous = {"enabled": True, "program_id": "p", "stage_id": "s", "generation": 4}
    lifecycle._stage_module = SimpleNamespace(stage_projection=lambda directory: {**previous, **change})
    with pytest.raises(HostRunnerError):
        lifecycle.before_spawn(ticket(tmp_path), step="plan", spawn_id="own-spawn",
                               stage_snapshot=previous, verify_actions=[])
