"""Cross-layer regressions from the September capability review.

Use real CLI boundaries and isolated tickets; no model, device or global writes.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


control = load("followup_control", "tools/icode_control.py")
coverage = load("followup_coverage", "tools/lint_mcp_coverage.py")
CATALOG = json.loads((ROOT / "mcp/cheap-research/gates.json").read_text())


@pytest.fixture
def ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ICODE_CONTROL_TEST_MODE", raising=False)
    out = tmp_path / ".icode_output/.icode_output_1"
    out.mkdir(parents=True)

    def run(*args, success=True, env=None):
        proc = subprocess.run([sys.executable, str(ROOT / "tools/icode_control.py"),
            args[0], "--dir", str(out), *args[1:]], capture_output=True, text=True, env=env)
        result = json.loads(proc.stdout)
        if success:
            assert proc.returncode == 0, result
        else:
            assert proc.returncode != 0, result
        return result

    run("create", "--ticket-id", "followup", "--birth", "plan", "--requirement", "probe")
    run("metadata-update", "--set-json", '{"semantic_decisions":[],"requirement_deltas":[]}')
    trace = dict(schema_version=1, ticket_id="followup", step="plan", tier="L2",
        default_tier="L2", triggers=[], mechanism="sequential-thinking", attempted=True,
        result="success", degraded_reason=None, over_invoked=False, at="2026-09-16T00:00:00Z")
    (out / ".thinking_gate_trace.jsonl").write_text(json.dumps(trace) + "\n")
    return out, run


def finish_plan(out, run):
    run("step", "--step", "plan", "--phase", "start", "--attempt", "plan-a")
    for boundary in ("before_write", "before_transition"):
        run("step", "--step", "plan", "--phase", "check", "--attempt", "plan-a", "--boundary", boundary)
    (out / "01_plan.md").write_text("# Complete synthetic plan\n")
    run("artifact", "--step", "plan", "--attempt", "plan-a", "--path", "01_plan.md")
    run("step", "--step", "plan", "--phase", "finish", "--attempt", "plan-a",
        "--outcome", "success", "--evidence", "01_plan.md")


def test_new_ticket_requires_step_even_without_start(ticket):
    out, run = ticket
    result = run("transition", "--to", "plan_done", success=False)
    assert result["gate_id"] == "step_receipt"
    assert json.loads((out / ".ico_metadata.json").read_text())["status"] == "init_in_progress"


def test_complete_step_transitions_and_validates(ticket):
    out, run = ticket
    finish_plan(out, run)
    run("transition", "--to", "plan_done")
    run("validate")


def test_output_changed_after_finish_blocks_transition(ticket):
    out, run = ticket
    finish_plan(out, run)
    (out / "01_plan.md").write_text("# Changed after receipt\n")
    assert run("transition", "--to", "plan_done", success=False)["gate_id"] == "step_receipt"


def test_validate_detects_missing_completed_output(ticket):
    out, run = ticket
    finish_plan(out, run)
    run("transition", "--to", "plan_done")
    (out / "01_plan.md").unlink()
    assert any(v["gate_id"] == "step_receipt" for v in run("validate", success=False)["violations"])


def test_historical_birth_is_not_retroactively_required(ticket):
    out, run = ticket
    # Model an existing pre-version birth. Rehash only this isolated fixture.
    rows = [json.loads(s) for s in (out / ".ico_events.jsonl").read_text().splitlines()]
    rows[0]["payload"].pop("execution_contract_version", None)
    previous = control.GENESIS_HASH
    for row in rows:
        row["previous_event_hash"] = previous
        row["event_hash"] = control.canonical_event_hash(row)
        previous = row["event_hash"]
    (out / ".ico_events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    run("transition", "--to", "plan_done")
    run("validate")


def test_delivery_paths_are_single_source_and_keep_legacy_defaults(ticket):
    out, run = ticket
    model = control.load_execution_model()
    for name in ("03_plan_final.md", "06_audit.md"):
        (out / name).write_text("# Upstream fixture\n")
    run("metadata-update", "--set-json", json.dumps({"delivery_files": {
        "report": "probe_feature.md", "brief": "probe_feature_brief.md"}}))
    run("step", "--step", "readme", "--phase", "start", "--attempt", "readme-a")
    run("step", "--step", "readme", "--phase", "check", "--attempt", "readme-a", "--boundary", "before_write")
    for name in ("probe_feature.md", "probe_feature_brief.md"):
        (out / name).write_text("# Delivery\n")
        run("artifact", "--step", "readme", "--attempt", "readme-a", "--path", name)
    # Readme's L1 trace is required when a new-contract standalone step finishes.
    with (out / ".thinking_gate_trace.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(schema_version=1,ticket_id="followup",step="readme",tier="L1",
            default_tier="L1",triggers=[],mechanism="decision_record",attempted=False,
            result="success",degraded_reason=None,over_invoked=False,at="2026-09-16T00:00:00Z"))+"\n")
    run("step", "--step", "readme", "--phase", "finish", "--attempt", "readme-a",
        "--outcome", "success", "--evidence", "probe_feature.md")
    assert not (out / "07_readme.md").exists()
    assert control.resolve_port(out, {}, model["step_contracts"]["readme"]["outputs"][0])["path"] == "07_readme.md"


@pytest.mark.parametrize("path", ["../escape.md", "/tmp/escape.md", "", "same.md"])
def test_delivery_mapping_rejects_unsafe_or_duplicate_paths(ticket, path):
    _, run = ticket
    run("metadata-update", "--set-json", json.dumps({"delivery_files": {
        "report": path, "brief": "same.md"}}), success=False)


def test_readonly_validation_rejects_duplicate_delivery_mapping(ticket):
    out, run = ticket
    path = out / ".ico_metadata.json"
    metadata = json.loads(path.read_text())
    metadata["delivery_files"] = {"report":"same.md", "brief":"same.md"}
    path.write_text(json.dumps(metadata))
    result = run("validate", success=False)
    assert any(v["path"] == "$.delivery_files" for v in result["violations"])


def test_execution_catalog_rejects_invalid_finish_steps_and_path_pointer():
    module = load("followup_contract", "tools/lint_workflow_contract.py")
    catalog = json.loads((ROOT / "mcp/workflow-gate/gates.json").read_text())
    assert not module.validate_execution_model_catalog(catalog)
    catalog["execution_model"]["finish_gate_steps"] = ["typo-step"]
    catalog["execution_model"]["step_contracts"]["readme"]["outputs"][0]["path_pointer"] = 42
    issues = module.validate_execution_model_catalog(catalog)
    assert any("finish_gate_steps" in issue for issue in issues)
    assert any("path_pointer" in issue for issue in issues)
    control_issues = control.validate_execution_catalog(catalog["execution_model"])
    assert any("finish_gate_steps" in issue for issue in control_issues)
    assert any("path_pointer" in issue for issue in control_issues)


@pytest.mark.parametrize("tool", ["lint_thinking_gate.py", "lint_mcp_coverage.py"])
def test_unknown_linter_step_is_parameter_error(ticket, tool):
    out, _ = ticket
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / tool), str(out),
        "--step", "typo-step", "--strict", "--json"], capture_output=True, text=True)
    assert proc.returncode == 2


def summary_row(**changes):
    row = dict(schema_version=2,ticket_id="followup",step="review",gate_id="review.result_summary",
        tool="summarize",eligible=True,evidence={"result_source":"02_review.md","review_rounds":1},
        decision="unavailable_before_call",attempted=False,result="unavailable",at="2026-09-16T00:00:00Z",
        availability={"tool_visible":False,"discovery_ref":"session:tools-list","reason":"not_exposed",
                      "fallback":"main_agent","evidence_ref":"02_review.md"})
    row.update(changes)
    return row


def test_unexposed_tool_has_honest_degradation(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.result_summary")
    report = coverage.build_report(tmp_path,"review",False,{"ticket_id":"followup","mcp_gate_schema_version":2},
        [summary_row()],{**CATALOG,"gates":[gate]},[])
    assert coverage.report_is_pass(report,False,True), report


@pytest.mark.parametrize("changes", [{"availability":{}}, {"attempted":True},
    {"availability":{"tool_visible":True,"discovery_ref":"session:tools-list","reason":"not_exposed","fallback":"main_agent","evidence_ref":"02_review.md"}}])
def test_unavailable_does_not_allow_unsubstantiated_skip(changes):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.result_summary")
    assert coverage.validate_row(summary_row(**changes),gate,1)


def test_verify_has_own_listener_gate(tmp_path):
    gates = [g for g in CATALOG["gates"] if g["step"] == "verify"]
    assert len(gates) == 1
    row = summary_row(step="verify",gate_id=gates[0]["id"],eligible=False,
        evidence={"listen_mode":False,"incremental_bytes":0,"threshold":8192},
        decision="skipped_not_eligible",result="not_applicable")
    report = coverage.build_report(tmp_path,"verify",False,{"mcp_gate_schema_version":2},[row],CATALOG,[])
    assert coverage.report_is_pass(report,False,True),report
    assert report["total_gates_in_scope"] == 1


def test_early_verify_only_build_and_plan(ticket):
    _, run = ticket
    projection = run("action-policy")
    assert "verify" in projection["allowed_actions"]
    for action in ("build", "plan", "intent", "default"):
        run("action-policy", "--action", "verify", "--verify-action", action)
    for action in ("deploy", "listen", "device_test"):
        run("action-policy", "--action", "verify", "--verify-action", action, success=False)


def test_verification_reports_can_omit_derived_markdown(tmp_path):
    debt = load("followup_debt", "tools/verification_debt.py")
    output = tmp_path / "plan.json"
    def must_not_render(_):
        raise AssertionError("Markdown rendering was not requested")
    debt.write_reports({"units":[]},str(output),None,must_not_render,allowed_root=tmp_path)
    assert json.loads(output.read_text()) == {"units":[]}
    assert list(tmp_path.iterdir()) == [output]
    with pytest.raises(debt.VerificationDebtError):
        debt.write_reports({},str(tmp_path/".ico_metadata.json"),None,must_not_render,allowed_root=tmp_path)


def test_merge_preflight_uses_no_global_git_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("GIT_COMMITTER_NAME","GIT_COMMITTER_EMAIL","GIT_AUTHOR_NAME","GIT_AUTHOR_EMAIL"):
        monkeypatch.delenv(key,raising=False)
    repo = tmp_path / "repo";repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git","-C",str(repo),*args],text=True).strip()
    git("init","-q");git("config","user.name","Test");git("config","user.email","test@example.invalid")
    (repo/"base").write_text("base");git("add",".");git("commit","-qm","base")
    base=git("rev-parse","HEAD")
    (repo/"left").write_text("left");git("add",".");git("commit","-qm","left");left=git("rev-parse","HEAD")
    git("checkout","-q","--detach",base)
    (repo/"right").write_text("right");git("add",".");git("commit","-qm","right");right=git("rev-parse","HEAD")
    module=load("followup_submission","scripts/submission_guard.py")
    assert module._simulate_merge(repo,left,right)==(True,"clean")
    assert git("rev-parse","HEAD")==right and not git("status","--porcelain")
