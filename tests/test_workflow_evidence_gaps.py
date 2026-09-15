"""Regression tests for review receipts and truthful tool evidence."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = load("icode_control")
coverage = load("lint_mcp_coverage")
CATALOG = json.loads((ROOT / "mcp/cheap-research/gates.json").read_text())
GATE = next(g for g in CATALOG["gates"] if g["id"] == "deepcheck.dedup")


def row(**changes):
    result = dict(schema_version=2, ticket_id="probe", step=GATE["step"],
                  gate_id=GATE["id"], tool=GATE["tool"], eligible=True,
                  evidence=dict(mode="full", phase="dedup", function_count=67, threshold=50),
                  decision="called", attempted=True, result="success", at="2026-09-15T17:30:00Z")
    result.update(changes)
    return result


def test_called_without_attempt_is_not_fulfilled(tmp_path):
    report = coverage.build_report(tmp_path, "deepcheck", False, {},
                                   [row(attempted=False, evidence={})], {"gates": [GATE]}, [])
    assert report["fulfilled"] == 0
    assert report["coverage"] == 0.0
    assert report["schema_errors"] > 0


@pytest.mark.parametrize("evidence", [{}, {"mode": "full"},
    dict(mode="full", phase="dedup", function_count=True, threshold=50)])
def test_catalog_evidence_is_required_and_typed(evidence):
    assert coverage.validate_row(row(evidence=evidence), GATE, 1)


def test_real_called_evidence_is_valid():
    assert not coverage.validate_row(row(), GATE, 1)


def test_cache_without_input_identity_is_invalid():
    assert coverage.validate_row(row(decision="cache_hit", attempted=False), GATE, 1)


def ticket(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    out.mkdir(parents=True)
    (out / "02_review.md").write_text("# review\n")
    return out, {"project_path": str(tmp_path), "ticket_id": "probe"}


def manifest(out, counts=(0, 0, 1), detail=True):
    detail_path = "review_round_1.json" if detail else None
    if detail:
        payload = dict(new_issues=[{}] * counts[0], refuted_issues=[{}] * counts[1],
                       pending_verification=[{}] * counts[2])
        (out / detail_path).write_text(json.dumps(payload))
    digest = control.file_sha256(out / detail_path) if detail else None
    value = dict(schema_version=1, ticket_id="probe", review_run="review-a", rounds=[
        dict(round=1, origin_attempt="review-a", new_issues=counts[0],
             refuted_issues=counts[1], pending_verification=counts[2],
             detail_path=detail_path, detail_sha256=digest)])
    (out / "review_manifest.json").write_text(json.dumps(value))
    return value


def test_new_review_cannot_omit_manifest(tmp_path):
    out, meta = ticket(tmp_path)
    _, missing = control.validate_step_outputs(out, meta, control.load_execution_model(), "review")
    assert "review_manifest" in missing


def test_pending_round_cannot_omit_detail(tmp_path):
    out, meta = ticket(tmp_path)
    manifest(out, detail=False)
    _, missing = control.validate_step_outputs(out, meta, control.load_execution_model(), "review")
    assert missing


def test_manifest_counts_cannot_hide_pending(tmp_path):
    out, meta = ticket(tmp_path)
    value = manifest(out)
    value["rounds"][0]["pending_verification"] = 0
    (out / "review_manifest.json").write_text(json.dumps(value))
    _, missing = control.validate_step_outputs(out, meta, control.load_execution_model(), "review")
    assert missing


def test_first_clean_round_has_json_but_later_clean_need_not(tmp_path):
    out, meta = ticket(tmp_path)
    value = manifest(out, counts=(0, 0, 0))
    value["rounds"].append(dict(round=2, origin_attempt="review-a", new_issues=0,
        refuted_issues=0, pending_verification=0, detail_path=None, detail_sha256=None))
    (out / "review_manifest.json").write_text(json.dumps(value))
    _, missing = control.validate_step_outputs(out, meta, control.load_execution_model(), "review")
    assert not missing


@pytest.mark.parametrize("mutation", ["bool_count", "wrong_hash", "escape", "first_clean_missing", "sequence"])
def test_malformed_review_evidence_is_rejected(tmp_path, mutation):
    out, meta = ticket(tmp_path)
    value = manifest(out)
    current = value["rounds"][0]
    if mutation == "bool_count":
        current["new_issues"] = False
    elif mutation == "wrong_hash":
        current["detail_sha256"] = "0" * 64
    elif mutation == "escape":
        current["detail_path"] = "../review_round_1.json"
    elif mutation == "first_clean_missing":
        current.update(new_issues=0, pending_verification=0, refuted_issues=0,
                       detail_path=None, detail_sha256=None)
    else:
        current["round"] = 2
    (out / "review_manifest.json").write_text(json.dumps(value))
    assert control.review_evidence_issues(out, meta)


def test_v2_ticket_rejects_trace_downgrade(tmp_path):
    report = coverage.build_report(tmp_path, "deepcheck", False,
        {"ticket_id": "probe", "mcp_gate_schema_version": 2}, [row(schema_version=1)],
        {"gates": [GATE]}, [])
    assert report["schema_errors"] and report["fulfilled"] == 0


def test_historical_v1_partial_fields_are_explicitly_untracked(tmp_path):
    report = coverage.build_report(tmp_path, "deepcheck", False,
        {"ticket_id": "probe", "mcp_gate_schema_version": 1},
        [row(schema_version=1, evidence={"mode": "full"})], {"gates": [GATE]}, [])
    assert report["evidence_untracked"] == 1
    assert report["schema_errors"] == 0


def test_real_cache_has_input_identity():
    assert not coverage.validate_row(row(decision="cache_hit", attempted=False,
        cache_key="a" * 64, input_digest="b" * 64), GATE, 1)


def quality(out, meta, step="deepcheck", mode="full"):
    source = Path(meta["project_path"]) / "source.cpp"
    source.write_text("int value = 0;\n")
    meta.update(code_files=["source.cpp"], mode=mode)
    phases = ["audit"] if step == "audit" else ["reverse"] if mode == "fast" else ["reverse", "fixed", "free"]
    report = dict(schema_version=1, ticket_id="probe", attempt="final-a",
        review_scope=["."], coverage_status="complete_within_scope", unobserved=[],
        read_phases={p: {"source.cpp": control.file_sha256(source)} for p in phases},
        dedup_status="not_eligible", dedup_reason="scope below threshold", dedup_unobserved=[])
    (out / (step + "_coverage.json")).write_text(json.dumps(report))
    return report


@pytest.mark.parametrize("step,mode", [("deepcheck", "full"), ("deepcheck", "fast"), ("audit", "full")])
def test_current_phase_coverage_passes(tmp_path, step, mode):
    out, meta = ticket(tmp_path)
    quality(out, meta, step, mode)
    assert not control.inspection_coverage_issues(out, meta, step, "final-a")


@pytest.mark.parametrize("mutation", ["phase", "hash", "partial", "dedup", "scope", "attempt"])
def test_incomplete_or_stale_coverage_cannot_be_success(tmp_path, mutation):
    out, meta = ticket(tmp_path)
    report = quality(out, meta)
    if mutation == "phase":
        del report["read_phases"]["fixed"]
    elif mutation == "hash":
        report["read_phases"]["reverse"]["source.cpp"] = "0" * 64
    elif mutation == "partial":
        report.update(coverage_status="partial", unobserved=["caller"], debt_reason="budget")
    elif mutation == "dedup":
        report.update(dedup_status="partial", dedup_unobserved=["classification"])
    elif mutation == "scope":
        report["review_scope"] = ["unrelated"]
    else:
        report["attempt"] = "old-a"
    (out / "deepcheck_coverage.json").write_text(json.dumps(report))
    assert control.inspection_coverage_issues(out, meta, "deepcheck", "final-a")


def test_degraded_requires_explicit_debt_and_can_preserve_missing_reads(tmp_path):
    out, meta = ticket(tmp_path)
    report = quality(out, meta)
    report.update(coverage_status="partial", read_phases={}, unobserved=["fixed/free reads"],
                  dedup_status="degraded", dedup_unobserved=["groups"], debt_reason="budget exhausted")
    (out / "deepcheck_coverage.json").write_text(json.dumps(report))
    assert not control.inspection_coverage_issues(out, meta, "deepcheck", "final-a", allow_incomplete=True)
    del report["debt_reason"]
    (out / "deepcheck_coverage.json").write_text(json.dumps(report))
    assert control.inspection_coverage_issues(out, meta, "deepcheck", "final-a", allow_incomplete=True)


def cli(out, *args, ok=True):
    result = subprocess.run([sys.executable, str(ROOT / "tools/icode_control.py"),
        *args, "--dir", str(out)], env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"),
        capture_output=True, text=True)
    assert (result.returncode == 0) == ok, (args, result.stdout, result.stderr)
    return json.loads(result.stdout)


def tracked_review(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "probe", "--requirement", "review evidence", "--birth", "plan")
    (out / "01_plan.md").write_text("# plan\n")
    cli(out, "transition", "--to", "plan_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-a")
    cli(out, "step", "--step", "review", "--phase", "check", "--attempt", "review-a", "--boundary", "before_write")
    (out / "02_review.md").write_text("# review\n")
    return out


def register_review(out, attempt, detail=False):
    names = ["02_review.md", "review_manifest.json"]
    if detail:
        names.insert(1, "review_round_1.json")
    for name in names:
        cli(out, "artifact", "--step", "review", "--attempt", attempt, "--path", name)
    for boundary in ("after_wait", "before_transition"):
        cli(out, "step", "--step", "review", "--phase", "check", "--attempt", attempt, "--boundary", boundary)


def test_real_receipts_survive_planned_drift_and_clean_resume(tmp_path):
    out = tracked_review(tmp_path)
    value = manifest(out, counts=(0, 0, 0))
    register_review(out, "review-a", detail=True)
    (out / "01_plan.md").write_text("# updated plan\n")
    result = cli(out, "step", "--step", "review", "--phase", "check", "--attempt", "review-a",
                 "--boundary", "after_wait", ok=False)
    assert result["changed_inputs"] == ["plan"]
    cli(out, "step", "--step", "review", "--phase", "finish", "--attempt", "review-a",
        "--outcome", "blocked", "--evidence", "planned drift")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-b")
    cli(out, "step", "--step", "review", "--phase", "check", "--attempt", "review-b", "--boundary", "before_write")
    value["rounds"].append(dict(round=2, origin_attempt="review-b", new_issues=0,
        refuted_issues=0, pending_verification=0, detail_path=None, detail_sha256=None))
    (out / "review_manifest.json").write_text(json.dumps(value))
    register_review(out, "review-b")
    cli(out, "step", "--step", "review", "--phase", "finish", "--attempt", "review-b",
        "--outcome", "success", "--evidence", "actual final manifest")
    cli(out, "check-outputs", "--step", "review")
    # A complete new review entry cannot reuse the previous run's manifest.
    cli(out, "transition", "--to", "review_done", "--skip-gates")
    cli(out, "transition", "--to", "review_in_progress", "--skip-gates")
    result = cli(out, "check-outputs", "--step", "review", ok=False)
    assert "review_manifest:stale_review_run" in result["violations"]


def test_clean_round_cannot_be_backdated_to_closed_origin(tmp_path):
    out = tracked_review(tmp_path)
    value = manifest(out, counts=(0, 0, 0))
    register_review(out, "review-a", detail=True)
    cli(out, "step", "--step", "review", "--phase", "finish", "--attempt", "review-a",
        "--outcome", "blocked", "--evidence", "end first attempt")
    cli(out, "step", "--step", "review", "--phase", "start", "--attempt", "review-b")
    cli(out, "step", "--step", "review", "--phase", "check", "--attempt", "review-b", "--boundary", "before_write")
    value["rounds"].append(dict(round=2, origin_attempt="review-a", new_issues=0,
        refuted_issues=0, pending_verification=0, detail_path=None, detail_sha256=None))
    (out / "review_manifest.json").write_text(json.dumps(value))
    register_review(out, "review-b")
    result = cli(out, "step", "--step", "review", "--phase", "finish", "--attempt", "review-b",
        "--outcome", "success", "--evidence", "backdated round", ok=False)
    assert "review_manifest:round=2:missing_origin_round_receipt" in result["missing_output_receipts"]


def test_audit_final_check_fails_without_first_review_json_and_is_read_only(tmp_path):
    out = tracked_review(tmp_path)
    for name in ("03_plan_final.md", "04_code_review_fix.md", "05_deepcheck.md", "06_audit.md"):
        (out / name).write_text("# report\n")
    (tmp_path / "source.cpp").write_text("int value;\n")
    cli(out, "metadata-update", "--set-json", json.dumps({"code_files": ["source.cpp"]}))
    before = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    result = cli(out, "check-outputs", "--step", "audit", ok=False)
    assert result["read_only"] and any("review_manifest" in v for v in result["violations"])
    after = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    assert after == before


@pytest.mark.parametrize("tamper", ["false_eligible", "threshold"])
def test_v2_cannot_skip_eligible_facts_or_change_catalog_threshold(tmp_path, tamper):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture/repo"], function_count=1000,
                    threshold=50, rg_available=True, cheap_available=True)
    current = row(step="review", gate_id=gate["id"], tool=gate["tool"], evidence=evidence)
    if tamper == "false_eligible":
        current.update(eligible=False, decision="skipped_not_eligible", attempted=False)
    else:
        evidence["threshold"] = 10000
    report = coverage.build_report(tmp_path, "review", False, {}, [current],
        dict(gates=[gate], constants=CATALOG["constants"]), [])
    assert report["schema_errors"] == 1
    assert report["fulfilled"] == 0 and report["skipped_not_eligible"] == 0
    assert report["eligible"] == 1 and report["coverage"] == 0.0


def test_effective_full_cannot_use_fast_stage_skip(tmp_path):
    current = row(eligible=False, decision="skipped_stage_not_reached", attempted=False,
                  evidence=dict(mode="fast", phase="reverse"))
    report = coverage.build_report(tmp_path, "deepcheck", False,
        dict(mode="fast", risk_profile=dict(effective_mode="full")), [current],
        dict(gates=[GATE], constants=CATALOG["constants"]), [])
    assert report["schema_errors"] == 1 and report["skipped_stage_not_reached"] == 0


@pytest.mark.parametrize("complete", [False, True])
def test_fast_upgraded_to_full_requires_three_phases(tmp_path, complete):
    out, meta = ticket(tmp_path)
    quality(out, meta, mode="full" if complete else "fast")
    meta.update(mode="fast", risk_profile=dict(requested_mode="fast", effective_mode="full"))
    issues = control.inspection_coverage_issues(out, meta, "deepcheck", "final-a")
    assert bool(issues) is not complete
    if not complete:
        assert "deepcheck_coverage:missing_phase=fixed" in issues
        assert "deepcheck_coverage:missing_phase=free" in issues


def tracked_quality(tmp_path, degraded=True, finish=True):
    out = tracked_review(tmp_path)
    manifest(out, counts=(0, 0, 0))
    register_review(out, "review-a", detail=True)
    cli(out, "step", "--step", "review", "--phase", "finish", "--attempt", "review-a",
        "--outcome", "success", "--evidence", "review complete")
    for state in ("review_done", "plan_finalized", "code_in_progress", "code_done", "deepcheck_in_progress"):
        cli(out, "transition", "--to", state, "--skip-gates")
    for name in ("03_plan_final.md", "04_code_review_fix.md", "05_deepcheck.md", "06_audit.md"):
        (out / name).write_text("# actual fixture report\n")
    (tmp_path / "source.cpp").write_text("int value = 0;\n")
    cli(out, "metadata-update", "--set-json", json.dumps(dict(code_files=["source.cpp"])))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "source.cpp"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "inspection fixture baseline"], check=True)
    for step in ("deepcheck", "audit"):
        if step == "audit":
            cli(out, "transition", "--to", "deepcheck_done", "--skip-gates")
        attempt = step + "-a"
        cli(out, "step", "--step", step, "--phase", "start", "--attempt", attempt)
        cli(out, "step", "--step", step, "--phase", "check", "--attempt", attempt, "--boundary", "before_write")
        meta = control.load_metadata(out)
        report = quality(out, meta, step)
        report["attempt"] = attempt
        prepared = cli(out, "inspection", "--step", step, "--attempt", attempt, "--phase", "prepare")
        worklist = json.loads(Path(prepared["path"]).read_text())
        for unit in worklist["units"]:
            for entry in unit["files"]:
                (tmp_path / entry["path"]).read_text()
                for phase in worklist["required_phases"]:
                    cli(out, "inspection", "--step", step, "--attempt", attempt, "--phase", "read",
                        "--read-phase", phase, "--path", entry["path"])
        if step == "deepcheck" and degraded:
            report.update(coverage_status="partial", read_phases={}, unobserved=["fixed/free"],
                          dedup_status="partial", dedup_unobserved=["groups"], debt_reason="budget")
        (out / (step + "_coverage.json")).write_text(json.dumps(report))
        for name in ("05_deepcheck.md" if step == "deepcheck" else "06_audit.md", step + "_coverage.json"):
            cli(out, "artifact", "--step", step, "--attempt", attempt, "--path", name)
        for boundary in ("after_wait", "before_transition"):
            cli(out, "step", "--step", step, "--phase", "check", "--attempt", attempt, "--boundary", boundary)
        if step == "deepcheck" and not finish:
            break
        cli(out, "step", "--step", step, "--phase", "finish", "--attempt", attempt,
            "--outcome", "degraded" if step == "deepcheck" and degraded else "success", "--evidence", "actual coverage")
    return out


def test_actual_degraded_final_check_passes_with_debt_and_is_read_only(tmp_path):
    out = tracked_quality(tmp_path)
    before = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    result = cli(out, "check-outputs", "--step", "audit")
    assert "deepcheck:verification_debt" in result["warnings"]
    assert before == {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}


@pytest.mark.parametrize("mutation", ["unclosed", "hash", "attempt", "new_attempt"])
def test_partial_or_replaced_coverage_without_actual_degraded_receipt_fails(tmp_path, mutation):
    out = tracked_quality(tmp_path, finish=mutation != "unclosed")
    if mutation == "new_attempt":
        cli(out, "transition", "--to", "deepcheck_in_progress", "--skip-gates")
        cli(out, "step", "--step", "deepcheck", "--phase", "start", "--attempt", "deepcheck-b")
    elif mutation != "unclosed":
        path = out / "deepcheck_coverage.json"
        report = json.loads(path.read_text())
        report["debt_reason" if mutation == "hash" else "attempt"] = "replaced"
        path.write_text(json.dumps(report))
    result = cli(out, "check-outputs", "--step", "audit", ok=False)
    assert any("deepcheck" in v for v in result["violations"])


@pytest.mark.parametrize("eligible", [False, True])
@pytest.mark.parametrize("gid,evidence,count_key", [
    ("log.comments_extract", dict(tb_comments_count=8, threshold=8, cheap_available=True), "tb_comments_count"),
    ("log.long_log_summary", dict(candidate_text_bytes=8192, threshold=8192, cheap_available=True), "candidate_text_bytes"),
    ("review.dedup", dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50, rg_available=True, cheap_available=True), "function_count"),
    ("review.result_summary", dict(result_source="02_review.md", review_rounds=1), "review_rounds"),
    ("merge.cross_round_summary", dict(review_rounds=2, threshold=2), "review_rounds"),
    ("deepcheck.fixed_scan", dict(mode="full", phase="fixed", function_points_count=1), "function_points_count"),
    ("deepcheck.dedup", dict(mode="full", phase="dedup", function_count=50, threshold=50), "function_count"),
    ("audit.repo_facts", dict(affected_repo_roots=["/fixture"]), "affected_repo_roots"),
    ("audit.plan_diff", dict(plan_source="03_plan_final.md", code_sources=["source.cpp"]), "code_sources"),
    ("patch.context_summary", dict(cross_session=True, candidate_text_bytes=8192, threshold=8192), "candidate_text_bytes"),
    ("patch.listen_log_summary", dict(listen_mode=True, incremental_bytes=8192, threshold=8192), "incremental_bytes"),
])
def test_each_v2_gate_recomputes_its_eligible_boundary(tmp_path, gid, evidence, count_key, eligible):
    gate = next(g for g in CATALOG["gates"] if g["id"] == gid)
    evidence = dict(evidence)
    if not eligible:
        evidence[count_key] = [] if isinstance(evidence[count_key], list) else 0
    current = row(step=gate["step"], gate_id=gid, tool=gate["tool"], evidence=evidence,
        eligible=eligible, decision="called" if eligible else "skipped_not_eligible", attempted=eligible)
    report = coverage.build_report(tmp_path, gate["step"], False, {}, [current],
        dict(gates=[gate], constants=CATALOG["constants"]), [])
    assert report["schema_errors"] == 0
    assert report["fulfilled"] == int(eligible)
    assert report["skipped_not_eligible"] == int(not eligible)


def test_invalid_utf8_coverage_is_rejected_without_crash(tmp_path):
    out, meta = ticket(tmp_path)
    (out / "deepcheck_coverage.json").write_bytes(b"\xff")
    assert control.inspection_coverage_issues(out, meta, "deepcheck") == ["deepcheck_coverage:invalid_json"]


@pytest.mark.parametrize("evidence", [dict(mode="unknown", phase="dedup", function_count=0, threshold=50),
    dict(mode="full", phase="reverse", function_count=0, threshold=50), dict(mode="fast", phase="fixed")])
def test_v2_unknown_mode_or_wrong_phase_cannot_justify_skip(tmp_path, evidence):
    current = row(eligible=False, attempted=False, evidence=evidence,
        decision="skipped_stage_not_reached" if evidence["mode"] == "fast" else "skipped_not_eligible")
    report = coverage.build_report(tmp_path, "deepcheck", False, {}, [current],
        dict(gates=[GATE], constants=CATALOG["constants"]), [])
    assert report["schema_errors"] == 1 and report["skipped_not_eligible"] == 0
