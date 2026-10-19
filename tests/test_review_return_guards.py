"""Failure, transaction, stage and host-entry coverage for review return."""
import contextlib
import io
import json
from pathlib import Path
import sys

import pytest

from test_review_return import control, cli, ticket, recovery, revision, bytes_state


def arguments(out):
    return control.build_parser().parse_args(["transition", "--dir", str(out),
        "--to", "review_in_progress", "--expected-revision", revision(out),
        "--reason", "Explicitly authorized recovery", "--request-id", "in-process-return"])


@pytest.mark.parametrize("kind", ["step", "agent"])
def test_real_open_execution_rejects_native_return(ticket, kind):
    if kind == "step":
        cli(ticket, "step", "--step", "plan", "--phase", "start", "--request", "open-plan")
    else:
        cli(ticket, "record-agent-spawn", "--task-scope", "isolated recovery test",
            "--expected-artifact", "note", "--evidence-boundary", "no real model",
            "--join-condition", "terminal", "--backend", "test", "--model", "test",
            "--capability", "text", "--request-id", "open-agent")
    before = bytes_state(ticket)
    assert recovery(ticket, ok=False)["gate_id"] == "review_return_quiescence"
    assert bytes_state(ticket) == before


@pytest.mark.parametrize("event", [
    {"event_type":"step_started","payload":{}},
    {"event_type":"operation_started","payload":{}},
    {"event_type":"agent_spawned","payload":{}},
])
def test_unpaired_legacy_execution_does_not_prove_quiescence(event):
    assert control.review_return_problem({}, [event], {}) == "review_return_quiescence"


def test_closed_and_accepted_contexts_are_not_eligible():
    assert control.review_return_problem({"close_state":"close_planned"}, [], {}) == "closed_ticket_status_frozen"
    assert control.review_return_problem({}, [], {"enabled":True,"stage_id":"generic","state":"accepted"}) == "stage_gate"


@pytest.mark.parametrize("failure_after_append", [False, True])
def test_original_transaction_rollback_and_recovery(ticket, monkeypatch, failure_after_append):
    args = arguments(ticket)
    before = bytes_state(ticket)
    original = control.append_prepared_event
    def fail(directory, event):
        if failure_after_append:
            original(directory, event)
        raise OSError("injected append boundary")
    with monkeypatch.context() as patch:
        patch.setattr(control, "append_prepared_event", fail)
        with contextlib.redirect_stdout(io.StringIO()):
            if failure_after_append:
                assert control.cmd_transition(args) == 0
            else:
                with pytest.raises(OSError, match="injected"):
                    control.cmd_transition(args)
    if failure_after_append:
        meta = control.load_metadata(ticket)
        assert meta["status"] == "review_in_progress"
        assert (ticket / control.EVENTS_NAME).read_bytes().startswith(before[1])
        assert control.verify_event_chain(ticket, meta)[1] == []
    else:
        assert bytes_state(ticket) == before
    assert not (ticket / control.TXN_NAME).exists()


def test_stage_revision_changed_before_publication_cannot_apply(ticket, monkeypatch):
    args = arguments(ticket)
    before = bytes_state(ticket)
    original = control.require_stage_action
    calls = []
    def guard(*args, **kwargs):
        result = dict(original(*args, **kwargs))
        calls.append(1)
        if len(calls) >= 3:
            result["revision"] = "concurrent-stage-revision"
        return result
    monkeypatch.setattr(control, "require_stage_action", guard)
    with pytest.raises(control.ControlError) as error:
        control.cmd_transition(args)
    assert error.value.extra["gate_id"] == "revision_mismatch"
    assert bytes_state(ticket) == before


def test_bad_execution_root_is_rejected_before_return(ticket):
    cli(ticket, "metadata-update", "--set-json", json.dumps({"active_checkout":{
        "path":str(ticket / "missing-root"),"state":"active"}}))
    meta = control.load_metadata(ticket)
    events = control.read_events(ticket)[0]
    token = control.workflow_revision(meta, events, control.stage_projection(ticket))["token"]
    before = bytes_state(ticket)
    assert recovery(ticket, token, ok=False)["gate_id"] == "execution_root_topology"
    assert bytes_state(ticket) == before


@pytest.mark.parametrize("step", ["merge", "code", "deepcheck", "audit"])
def test_missing_downstream_ports_cannot_skip_new_review(ticket, step):
    recovery(ticket)
    before = bytes_state(ticket)
    result = cli(ticket, "step", "--step", step, "--phase", "start", "--request", "premature-"+step, ok=False)
    assert result["gate_id"] == "step_ports"
    assert bytes_state(ticket) == before


@pytest.mark.parametrize("step", ["merge", "code", "deepcheck", "audit"])
def test_complete_downstream_ports_still_require_new_review(ticket, step, monkeypatch):
    recovery(ticket)
    before = bytes_state(ticket)
    # Unit boundary only: assume the port producer supplied complete facts so
    # state admission is exercised independently of missing files or real Git.
    # No production metadata/receipts or candidate provider is replaced.
    monkeypatch.setattr(control, "capture_step_inputs", lambda *a, **kw:
        ({"contract_digest":"1"*64,"input_digest":"2"*64,"inputs":[],"protected":{}}, []))
    args = control.build_parser().parse_args(["step", "--dir", str(ticket), "--step", step,
        "--phase", "start", "--request", "complete-port-"+step])
    with pytest.raises(control.ControlError) as error:
        control.cmd_step(args)
    assert error.value.extra["gate_id"] == "action_policy"
    assert bytes_state(ticket) == before


def test_accepted_stage_native_transition_is_rejected():
    from test_stage_acceptance import StageAcceptanceTests
    f = StageAcceptanceTests("runTest")
    f.setUp()
    try:
        access, _, _, run, bundle = f.ready()
        f.record(access, run, bundle)
        f.accept(access)
        before = bytes_state(f.first)
        with pytest.raises(f.ctl.ControlError, match="action is blocked"):
            f.cli("transition", "--dir", str(f.first), "--to", "review_in_progress",
                "--expected-revision", "0"*64, "--reason", "must reject", "--request-id", "accepted-return")
        assert bytes_state(f.first) == before
    finally:
        f.doCleanups()


def test_ui_host_returns_before_registering_its_own_agent(tmp_path):
    from test_stage_host_runner import Lifecycle, setup, ticket as host_ticket, run
    class RecoveryLifecycle(Lifecycle):
        def __init__(self, root):
            super().__init__(root)
            self.order = []
        def action_policy(self, *args, **kwargs):
            result = super().action_policy(*args, **kwargs)
            result["review_return_required"] = True
            self.order.append("policy")
            return result
        def return_to_review(self, target, **kwargs):
            assert not hasattr(self, "spawn_options")
            self.order.append("return")
            return {"execution_root":str(self.root),"stage":dict(self.stage),"review_return_required":False}
        def record_spawn(self, *args, **kwargs):
            self.order.append("spawn")
            return super().record_spawn(*args, **kwargs)
    lifecycle = RecoveryLifecycle(tmp_path)
    runner, calls = setup(tmp_path, lifecycle)
    run(runner, host_ticket(tmp_path), step="review")
    assert lifecycle.order == ["policy", "return", "spawn"]
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["plan_done", "review_done", "completed"])
def test_ordinary_review_must_enter_before_start(tmp_path, status):
    out = tmp_path / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "entry-order", "--requirement", "entry order", "--birth", "plan")
    (out / "01_plan.md").write_text("# Entry fixture\n", encoding="utf-8")
    for state in ("plan_done", "review_in_progress", "review_done", "plan_finalized",
                  "code_in_progress", "code_done", "deepcheck_in_progress", "deepcheck_done", "completed"):
        extra = ["--delivery-verdict", "verification_pending"] if state == "completed" else []
        cli(out, "transition", "--to", state, *extra, "--skip-gates", fixture=True)
        if state == status:
            break
    before = bytes_state(out)
    result = cli(out, "step", "--step", "review", "--phase", "start", "--request", "wrong-order", ok=False)
    assert result["gate_id"] == "review_entry_required"
    assert bytes_state(out) == before


@pytest.mark.parametrize("target", ["review_in_progress", "code_in_progress"])
def test_legacy_migration_exit_code_precedes_stage_guard(tmp_path, target):
    import subprocess
    out = tmp_path / ".icode_output/.icode_output_1"
    out.mkdir(parents=True)
    path = out / ".ico_metadata.json"
    path.write_text(json.dumps({"ticket_id":"legacy", "requirement":"fixture",
        "created_at":"2026-01-01T00:00:00", "status":"init_in_progress", "completed_steps":["0"]}),encoding="utf-8")
    before = path.read_bytes()
    r = subprocess.run([sys.executable,"-B",str(control.SKILL_ROOT/"tools/icode_control.py"),
        "transition","--dir",str(out),"--to",target],capture_output=True,encoding="utf-8")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert path.read_bytes() == before and not (out / ".ico_events.jsonl").exists()


def test_exact_start_replay_after_review_done_returns_only_original_receipt(ticket):
    from test_review_return import test_fresh_review_receipts_after_return_satisfy_original_gate as complete
    complete(ticket)
    cli(ticket, "transition", "--to", "review_done", "--skip-gates", fixture=True)
    before = bytes_state(ticket)
    result = cli(ticket, "step", "--step", "review", "--phase", "start", "--request", "new-review")
    assert result["already_applied"]
    assert bytes_state(ticket) == before
