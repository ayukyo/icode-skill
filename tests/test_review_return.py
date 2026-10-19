"""Controlled review recovery uses real isolated control transactions.

Test-only skipped gates create historical positions, never production receipts.
The recovery request itself always runs with production checks enabled.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_return_control", ROOT / "tools/icode_control.py")
control = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = control
spec.loader.exec_module(control)


def cli(out, *args, fixture=False, ok=True):
    env = dict(os.environ)
    env.pop("ICODE_CONTROL_TEST_MODE", None)
    if fixture:
        env["ICODE_CONTROL_TEST_MODE"] = "1"
    p = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"),
        args[0], "--dir", str(out), *args[1:]], capture_output=True, text=True,
        encoding="utf-8", env=env)
    result = json.loads(p.stdout)
    assert (p.returncode == 0) == ok, (args, result, p.stderr)
    return result


@pytest.fixture
def ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    out = tmp_path / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "generic-review-return", "--requirement", "review recovery", "--birth", "plan",
        "--metadata-json", '{"semantic_decisions":[],"requirement_deltas":[]}')
    (out / "01_plan.md").write_text("# A complete fixture plan\n", encoding="utf-8")
    # Strict validate also checks the retained plan. Supply its actual writer
    # receipts and the isolated fixture's trace rather than weakening validate.
    plan = cli(out, "step", "--step", "plan", "--phase", "start", "--request", "fixture-plan")["attempt"]
    for boundary in ("before_write", "before_transition"):
        cli(out, "step", "--step", "plan", "--phase", "check", "--attempt", plan, "--boundary", boundary)
    cli(out, "artifact", "--step", "plan", "--attempt", plan, "--path", "01_plan.md")
    cli(out, "step", "--step", "plan", "--phase", "finish", "--attempt", plan,
        "--outcome", "success", "--evidence", "isolated fixture plan")
    (out / ".thinking_gate_trace.jsonl").write_text(json.dumps(dict(schema_version=1,
        ticket_id="generic-review-return",step="plan",tier="L2",default_tier="L2",triggers=[],
        mechanism="sequential-thinking",attempted=True,result="success",degraded_reason=None,
        over_invoked=False,at="2026-10-18T00:00:00Z"))+"\n",encoding="utf-8")
    cli(out, "transition", "--to", "plan_done")
    for state in ("review_in_progress", "review_done", "plan_finalized",
                  "code_in_progress", "code_done", "deepcheck_in_progress", "deepcheck_done"):
        cli(out, "transition", "--to", state, "--skip-gates", fixture=True)
    return out


def revision(out):
    return cli(out, "action-policy")["revision"]["token"]


def recovery(out, token=None, reason="User authorized a real current review", request="return-1", **kw):
    return cli(out, "transition", "--to", "review_in_progress",
        "--expected-revision", token or revision(out), "--reason", reason,
        "--request-id", request, **kw)


def bytes_state(out):
    return tuple((out / n).read_bytes() for n in (".ico_metadata.json", ".ico_events.jsonl"))


def test_production_recovery_keeps_history_and_invalidates_completion(ticket):
    before = bytes_state(ticket)
    policy = cli(ticket, "action-policy", "--action", "review")
    assert policy["review_return_required"] is True
    result = recovery(ticket, policy["revision"]["token"])
    assert result["ok"] and result["to"] == "review_in_progress"
    meta = json.loads((ticket / ".ico_metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "review_in_progress"
    assert not set(meta["completed_steps"]) & {"2", "3", "4", "5", "6"}
    assert meta["delivery_verdict"] == "verification_pending"
    assert (ticket / ".ico_events.jsonl").read_bytes().startswith(before[1])
    cli(ticket, "validate")


def test_exact_replay_is_read_only_and_conflicting_reason_rejected(ticket):
    token = revision(ticket)
    recovery(ticket, token)
    done = bytes_state(ticket)
    assert recovery(ticket, token)["already_applied"]
    assert bytes_state(ticket) == done
    r = recovery(ticket, token, reason="Different intent", ok=False)
    assert r["gate_id"] == "idempotency_conflict"
    assert bytes_state(ticket) == done


def test_stale_revision_cannot_return(ticket):
    token = revision(ticket)
    cli(ticket, "metadata-update", "--set-json", '{"requirement_summary":"changed"}')
    before = bytes_state(ticket)
    assert recovery(ticket, token, ok=False)["gate_id"] == "revision_mismatch"
    assert bytes_state(ticket) == before


@pytest.mark.parametrize("options", [[], ["--reason", "why"], ["--expected-revision", "0" * 64]])
def test_return_requires_reason_revision_and_request_identity(ticket, options):
    before = bytes_state(ticket)
    cli(ticket, "transition", "--to", "review_in_progress", *options, ok=False)
    assert bytes_state(ticket) == before


@pytest.mark.parametrize("opclass", ["read_only", "managed_write", "external_side_effect"])
def test_open_operation_blocks_policy_and_return(ticket, opclass):
    cli(ticket, "operation", "--phase", "start", "--name", "fixture-work",
        "--opclass", opclass, "--input", "isolated fixture only", "--request", "open-op")
    token = revision(ticket)
    assert "review" not in cli(ticket, "action-policy")["allowed_actions"]
    before = bytes_state(ticket)
    assert recovery(ticket, token, ok=False)["gate_id"] == "review_return_quiescence"
    assert bytes_state(ticket) == before


def test_low_level_review_cannot_skip_controlled_return(ticket):
    before = bytes_state(ticket)
    r = cli(ticket, "step", "--step", "review", "--phase", "start", "--request", "raw-review", ok=False)
    assert r["gate_id"] == "review_return_required"
    assert bytes_state(ticket) == before


def test_downstream_receipt_before_return_is_not_current():
    events = [
        {"event_type":"step_started", "payload":{"step":"code", "attempt":"old-code"}},
        {"event_type":"step_finished", "payload":{"step":"code", "attempt":"old-code", "outcome":"success"}},
        {"event_type":"state_changed", "payload":{"to":"review_in_progress", "review_return":1}},
    ]
    assert control.completion_receipt_issues(Path("unused"), {}, events, "code") == ["code:before_review_return"]


def test_old_manifest_stays_stale_after_controlled_return(ticket):
    recovery(ticket)
    meta = control.load_metadata(ticket)
    events = control.read_events(ticket)[0]
    manifest = {"schema_version":1, "ticket_id":meta["ticket_id"], "review_run":"old-review",
        "rounds":[{"round":1,"origin_attempt":"old-review","new_issues":0,
                   "refuted_issues":0,"pending_verification":0,"detail_path":None,"detail_sha256":None}]}
    (ticket / "review_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "review_manifest:stale_review_run" in control.review_evidence_issues(ticket, meta, events)


def test_fresh_review_receipts_after_return_satisfy_original_gate(ticket):
    recovery(ticket)
    result = cli(ticket, "step", "--step", "review", "--phase", "start", "--request", "new-review")
    attempt = result["attempt"]
    cli(ticket, "step", "--step", "review", "--phase", "check", "--attempt", attempt, "--boundary", "before_write")
    detail = {"new_issues":[],"refuted_issues":[],"pending_verification":[],"has_new_issues":False}
    data = json.dumps(detail).encode("utf-8")
    (ticket / "review_round_1.json").write_bytes(data)
    (ticket / "02_review.md").write_text("# Actual isolated fresh review receipt test\n", encoding="utf-8")
    manifest = {"schema_version":1,"ticket_id":"generic-review-return","review_run":attempt,
        "rounds":[{"round":1,"origin_attempt":attempt,"new_issues":0,"refuted_issues":0,
                   "pending_verification":0,"detail_path":"review_round_1.json","detail_sha256":hashlib.sha256(data).hexdigest()}]}
    (ticket / "review_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in ("review_round_1.json", "review_manifest.json", "02_review.md"):
        cli(ticket, "artifact", "--step", "review", "--attempt", attempt, "--path", name)
    for boundary in ("after_wait", "before_transition"):
        cli(ticket, "step", "--step", "review", "--phase", "check", "--attempt", attempt, "--boundary", boundary)
    cli(ticket, "step", "--step", "review", "--phase", "finish", "--attempt", attempt,
        "--outcome", "success", "--evidence", "real isolated writer calls")
    cli(ticket, "check-outputs", "--step", "review")
