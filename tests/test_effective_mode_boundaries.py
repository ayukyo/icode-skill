"""风险升级不得让旧 fast 审查冒充 full；历史执行仍可终结。"""

import json
import importlib.util
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "tools/icode_control.py"


def call(out, command, *args):
    env = dict(os.environ, ICODE_CONTROL_TEST_MODE="1")
    result = subprocess.run([sys.executable, "-B", str(CONTROL), command,
                             "--dir", str(out), *args], cwd=ROOT, env=env,
                            text=True, capture_output=True, check=False)
    return result.returncode, json.loads(result.stdout)


def start_review(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    plan = "fixture plan"
    catalog = json.loads((ROOT / "mcp/workflow-gate/gates.json").read_text(encoding="utf-8"))
    risk = catalog["fast_risk_upgrade"]
    scope = {"summary": "Bounded review fixture", "at": "2026-11-13T00:00:00Z", "source_step": "plan"}
    scope_digest = hashlib.sha256(json.dumps(
        scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    profile = {
        "policy_version": 2, "requested_mode": "fast", "effective_mode": "fast",
        "triggers": [], "risk_flags": dict.fromkeys([*risk["risk_flags"], *risk["policy_v2"]["full_flags"]], False),
        "override": False,
        "classification": {"scope": "bounded", "scope_digest": scope_digest, "evidence_refs": [
            {"scope": "ticket", "path": "01_plan.md", "sha256": hashlib.sha256(plan.encode()).hexdigest()}]},
    }
    code, result = call(out, "create", "--ticket-id", "mode-test-1",
                        "--requirement", "review fixture", "--birth", "plan",
                        "--metadata-json", json.dumps({"mode": "fast", "risk_profile": profile,
                                                       "scope_contract": scope}))
    assert code == 0, result
    (out / "01_plan.md").write_text(plan, encoding="utf-8")
    assert call(out, "transition", "--to", "plan_done", "--skip-gates")[0] == 0
    assert call(out, "transition", "--to", "review_in_progress")[0] == 0
    code, result = call(out, "step", "--step", "review", "--phase", "start", "--request", "review-start")
    assert code == 0, result
    return out, result["attempt"], profile


def upgrade(out):
    profile = {"policy_version": 2, "requested_mode": "fast", "effective_mode": "full", "triggers": ["cross_component"],
               "risk_flags": {"cross_component": True}, "override": False}
    code, result = call(out, "metadata-update", "--set-json", json.dumps({"risk_profile": profile}))
    assert code == 0, result


def test_review_attempt_blocks_on_effective_mode_change(tmp_path):
    out, attempt, _ = start_review(tmp_path)
    upgrade(out)
    code, result = call(out, "step", "--step", "review", "--phase", "check", "--attempt", attempt,
                        "--boundary", "before_write", "--request", "check-upgrade")
    assert code == 1
    assert result["result"] == "blocked"
    assert result["route"] == "review"
    assert "effective_mode" in result["changed_inputs"]
    assert call(out, "step", "--step", "review", "--phase", "finish", "--attempt", attempt,
                "--outcome", "blocked", "--evidence", "mode changed")[0] == 0


def test_review_attempt_allows_unchanged_effective_mode(tmp_path):
    out, attempt, _ = start_review(tmp_path)
    code, result = call(out, "step", "--step", "review", "--phase", "check", "--attempt", attempt,
                        "--boundary", "before_write")
    assert code == 0 and result["result"] == "pass"


def test_mode_change_after_check_cannot_finish_success(tmp_path):
    out, attempt, _ = start_review(tmp_path)
    assert call(out, "step", "--step", "review", "--phase", "check", "--attempt", attempt,
                "--boundary", "before_write")[0] == 0
    upgrade(out)
    code, result = call(out, "step", "--step", "review", "--phase", "finish", "--attempt", attempt,
                        "--outcome", "success", "--evidence", "fixture")
    assert code == 1 and result["gate_id"] == "effective_mode_drift"


def test_upgraded_v2_review_cannot_revert_effective_mode(tmp_path):
    out, attempt, profile = start_review(tmp_path)
    upgrade(out)
    code, result = call(out, "metadata-update", "--set-json", json.dumps({"risk_profile": profile}))
    assert code == 1 and result["gate_id"] == "fast_policy_mode", result
    code, result = call(out, "step", "--step", "review", "--phase", "check", "--attempt", attempt,
                        "--boundary", "before_write")
    assert code == 1 and "effective_mode" in result["changed_inputs"]


def test_full_mode_instructions_override_requested_fast():
    review = (ROOT / "steps/02_review.md").read_text(encoding="utf-8")
    deepcheck = (ROOT / "steps/05_deepcheck.md").read_text(encoding="utf-8")
    assert "有效 full 首次审查默认 3 轮" in review
    assert "须结束开放 attempt 并受控重入新 full review" in review
    assert 'effective_mode(metadata) == "fast"' in deepcheck
    assert "metadata.mode == \"fast\"" not in deepcheck


def test_same_checkout_active_history_is_one_root(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    spec = importlib.util.spec_from_file_location("mode_control", CONTROL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    meta = {"active_checkout": {"state": "active", "path": str(tmp_path)},
            "checkout_history": [{"state": "active", "path": str(tmp_path)}]}
    assert module.trusted_execution_workspace(tmp_path, meta) == tmp_path.resolve()
    for history in ([{"state": "active", "path": str(tmp_path / "missing")}],
                    [{"state": "active", "path": "relative"}],
                    [{"state": "active", "path": str(tmp_path)},
                     {"state": "active", "path": str(tmp_path)}]):
        with pytest.raises(module.ControlError):
            module.trusted_execution_workspace(tmp_path, dict(meta, checkout_history=history))


def test_manual_dedup_coverage_checks_scope_and_current_candidate(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    src = tmp_path / "src"
    src.mkdir()
    source = src / "app.py"
    source.write_text("def first():\n    return 1\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    out = tmp_path / ".icode_output/.icode_output_1"
    out.mkdir(parents=True)
    report = {"schema_version": 1, "ticket_id": "t", "attempt": "a", "review_scope": ["src"],
              "coverage_status": "complete_within_scope", "unobserved": [],
              "dedup_status": "complete_within_scope", "dedup_unobserved": [],
              "read_phases": {phase: {"src/app.py": digest} for phase in ("reverse", "fixed", "free")},
              "manual_dedup": {"schema_version": 1, "ticket_id": "t", "attempt": "a", "review_scope": ["src"],
                               "unobserved": [], "sources": [{"path": "src/app.py", "sha256": digest,
                               "catalog_method": "python_ast", "functions": [{"name": "first", "line": 1, "family": "f"}]}],
                               "families": [{"name": "f", "members": ["src/app.py:1:first"], "conclusion": "single",
                                             "reason": "only function", "evidence": ["src/app.py:1"]}]}}
    target = out / "deepcheck_coverage.json"
    target.write_text(json.dumps(report), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mode_control_manual", CONTROL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    meta = {"ticket_id": "t", "project_path": str(tmp_path), "mode": "full", "code_files": ["src/app.py"]}
    assert not module.inspection_coverage_issues(out, meta, "deepcheck", "a")
    assert any("protected Git provider" in issue for issue in module.inspection_coverage_issues(
        out, meta, "deepcheck", "a", require_git_provider=True))
    (out / ".ico_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    event = {"event_type": "step_started", "payload": {"step": "deepcheck", "attempt": "a", "dedup_evidence_version": 1}}
    (out / ".ico_events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    without_manual = dict(report)
    del without_manual["manual_dedup"]
    target.write_text(json.dumps(without_manual), encoding="utf-8")
    trace = {"schema_version": 2, "ticket_id": "t", "step": "deepcheck", "gate_id": "deepcheck.dedup",
             "tool": "extract", "eligible": True, "attempt": "old-attempt", "decision": "called",
             "attempted": True, "result": "success", "at": "2026-11-15T00:00:00Z",
             "evidence": {"mode": "full", "phase": "dedup", "function_count": 50, "threshold": 50}}
    trace_path = out / ".mcp_gate_trace.jsonl"
    trace_path.write_text(json.dumps(trace) + "\n", encoding="utf-8")
    assert "deepcheck_coverage:manual_dedup_required" in module.inspection_coverage_issues(out, meta, "deepcheck", "a")
    trace["attempt"] = "a"
    trace_path.write_text(json.dumps(trace) + "\n", encoding="utf-8")
    assert not module.inspection_coverage_issues(out, meta, "deepcheck", "a")
    target.write_text(json.dumps(report), encoding="utf-8")
    audit = dict(without_manual, read_phases={"audit": {"src/app.py": digest}})
    (out / "audit_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
    assert not module.inspection_coverage_issues(out, meta, "audit", "a")
    (src / "unreviewed.py").write_text("def second(): pass\n", encoding="utf-8")
    assert any("manual_dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "deepcheck", "a"))


def test_new_deepcheck_cannot_claim_not_eligible_for_51_functions(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "icode-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "ICODE test"], check=True)
    src = tmp_path / "src"
    src.mkdir()
    source = src / "app.py"
    source.write_text("\n".join(f"def function_{n}(): pass" for n in range(51)), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/app.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "fixture baseline"], check=True,
                   capture_output=True, text=True)
    out = tmp_path / ".icode_output/.icode_output_1"
    code, created = call(out, "create", "--ticket-id", "threshold-1", "--requirement", "fixture",
                         "--birth", "plan", "--metadata-json", json.dumps({"mode": "full", "code_files": ["src/app.py"]}))
    assert code == 0, created
    (out / "03_plan_final.md").write_text("fixture plan", encoding="utf-8")
    code, started = call(out, "step", "--step", "deepcheck", "--phase", "start", "--request", "deep-start")
    assert code == 0, started
    attempt = started["attempt"]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = {"schema_version": 1, "ticket_id": "threshold-1", "attempt": attempt, "review_scope": ["src"],
              "coverage_status": "complete_within_scope", "unobserved": [], "dedup_status": "not_eligible",
              "dedup_reason": "below threshold", "dedup_unobserved": [],
              "read_phases": {phase: {"src/app.py": digest} for phase in ("reverse", "fixed", "free")}}
    (out / "deepcheck_coverage.json").write_text(json.dumps(report), encoding="utf-8")
    trace = {"schema_version": 2, "ticket_id": "threshold-1", "step": "deepcheck", "gate_id": "deepcheck.dedup",
             "tool": "extract", "eligible": False, "attempt": attempt, "decision": "skipped_not_eligible",
             "attempted": False, "result": "not_applicable", "at": "2026-11-15T00:00:00Z",
             "evidence": {"mode": "full", "phase": "dedup", "function_count": 1, "threshold": 50}}
    (out / ".mcp_gate_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    code, result = call(out, "check-outputs", "--step", "deepcheck")
    assert code == 1
    assert any("dedup" in issue and ("threshold" in issue or "not_eligible" in issue)
               for issue in result["violations"]), result
    source.write_text("def only_one(): pass\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report["read_phases"] = {phase: {"src/app.py": digest} for phase in ("reverse", "fixed", "free")}
    (out / "deepcheck_coverage.json").write_text(json.dumps(report), encoding="utf-8")
    _, result = call(out, "check-outputs", "--step", "deepcheck")
    assert not any("dedup_not_eligible" in issue for issue in result["violations"]), result


def test_transient_mode_change_is_detected_when_profile_later_overrides_mode():
    spec = importlib.util.spec_from_file_location("mode_control_transient", CONTROL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    start = {"event_type": "step_started", "payload": {"step": "review", "effective_mode": "full"}}
    events = [start,
              {"event_type": "metadata_updated", "payload": {"set": {"mode": "fast"}}},
              {"event_type": "metadata_updated", "payload": {"set": {"risk_profile": {"effective_mode": "full"}}}}]
    assert module.step_mode_changed(start, {"mode": "fast", "risk_profile": {"effective_mode": "full"}}, events)


@pytest.mark.parametrize("replacement", ["def changed(): pass\n", "#" * (1048576 + 1)])
def test_threshold_rejects_source_changed_after_enumeration(tmp_path, monkeypatch, replacement):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    src = tmp_path / "src"
    src.mkdir()
    source = src / "app.py"
    source.write_text("def original(): pass\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    out = tmp_path / ".icode_output/.icode_output_1"
    out.mkdir(parents=True)
    report = {"schema_version": 1, "ticket_id": "t", "attempt": "a", "review_scope": ["src"],
              "coverage_status": "complete_within_scope", "unobserved": [], "dedup_status": "not_eligible",
              "dedup_reason": "below threshold", "dedup_unobserved": [],
              "read_phases": {phase: {"src/app.py": digest} for phase in ("reverse", "fixed", "free")}}
    (out / "deepcheck_coverage.json").write_text(json.dumps(report), encoding="utf-8")
    event = {"event_type": "step_started", "payload": {"step": "deepcheck", "attempt": "a", "dedup_evidence_version": 1}}
    (out / ".ico_events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    trace = {"ticket_id": "t", "attempt": "a", "gate_id": "deepcheck.dedup", "eligible": False,
             "decision": "skipped_not_eligible", "evidence": {"function_count": 1, "threshold": 50}}
    (out / ".mcp_gate_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mode_control_race", CONTROL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    original_open = Path.open
    reads = 0

    def replace_after_enumeration(path, mode="r", *args, **kwargs):
        nonlocal reads
        if path == source and mode == "rb":
            reads += 1
            if reads == 2:
                source.write_text(replacement, encoding="utf-8")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_after_enumeration)
    meta = {"ticket_id": "t", "project_path": str(tmp_path), "mode": "full", "code_files": ["src/app.py"]}
    issues = module.inspection_coverage_issues(out, meta, "deepcheck", "a")
    assert reads >= 2
    assert any("manual_dedup" in issue and ("changed" in issue or "byte budget" in issue) for issue in issues), issues


def test_transient_mode_change_when_later_mode_hides_profile_removal():
    spec = importlib.util.spec_from_file_location("mode_control_profile_removal", CONTROL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    start = {"event_type": "step_started", "payload": {"step": "deepcheck", "effective_mode": "full"}}
    events = [start,
              {"event_type": "metadata_updated", "payload": {"set": {"risk_profile": None}}},
              {"event_type": "metadata_updated", "payload": {"set": {"mode": "full"}}}]
    assert module.step_mode_changed(start, {"mode": "full", "risk_profile": None}, events)


def test_new_audit_reuses_only_receipted_current_deepcheck(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    src = tmp_path / "src"
    src.mkdir()
    source = src / "app.py"
    source.write_text("def first():\n    return 1\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    out = tmp_path / ".icode_output/.icode_output_1"
    out.mkdir(parents=True)
    manual = {"schema_version": 1, "ticket_id": "t", "attempt": "d", "review_scope": ["src"],
              "unobserved": [], "sources": [{"path": "src/app.py", "sha256": digest,
              "catalog_method": "python_ast", "functions": [{"name": "first", "line": 1, "family": "f"}]}],
              "families": [{"name": "f", "members": ["src/app.py:1:first"], "conclusion": "single",
                            "reason": "only function", "evidence": ["src/app.py:1"]}]}
    deep = {"schema_version": 1, "ticket_id": "t", "attempt": "d", "review_scope": ["src"],
            "coverage_status": "complete_within_scope", "unobserved": [],
            "dedup_status": "complete_within_scope", "dedup_unobserved": [], "manual_dedup": manual,
            "read_phases": {p: {"src/app.py": digest} for p in ("reverse", "fixed", "free")}}
    audit = {"schema_version": 1, "ticket_id": "t", "attempt": "a", "review_scope": ["src"],
             "coverage_status": "complete_within_scope", "unobserved": [],
             "dedup_status": "complete_within_scope", "dedup_unobserved": [],
             "read_phases": {"audit": {"src/app.py": digest}}}
    deep_path = out / "deepcheck_coverage.json"
    deep_path.write_text(json.dumps(deep), encoding="utf-8")
    (out / "audit_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
    event_hash = hashlib.sha256(deep_path.read_bytes()).hexdigest()
    start = {"event_type": "step_started", "payload": {"step": "deepcheck", "attempt": "d", "dedup_evidence_version": 1, "effective_mode": "full"}}
    receipt = {"event_type": "artifact_written", "payload": {"step": "deepcheck", "attempt": "d", "output": "inspection_coverage", "path": str(deep_path), "sha256": event_hash}}
    finish = {"event_type": "step_finished", "payload": {"step": "deepcheck", "attempt": "d", "outcome": "success"}}
    audit_start = {"event_type": "step_started", "payload": {"step": "audit", "attempt": "a", "dedup_evidence_version": 1}}
    events = [start, receipt, finish, audit_start]
    spec = importlib.util.spec_from_file_location("mode_audit_dedup", CONTROL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_events", lambda _out: (events, None))
    monkeypatch.setattr(module, "verify_event_chain", lambda *_a, **_kw: (events, []))
    meta = {"ticket_id": "t", "project_path": str(tmp_path), "mode": "full", "code_files": ["src/app.py"]}
    assert not module.inspection_coverage_issues(out, meta, "audit", "a")
    with monkeypatch.context() as protected:
        def forbidden(*_args, **_kwargs):
            raise AssertionError("protected path must not perform native checks")
        protected.setattr(module, "verify_event_chain", forbidden)
        for access in ({"require_git_provider": True}, {"git_provider": object()},
                       {"inspection_provider_factory": lambda *_args: None}):
            assert any("dedup" in issue for issue in module.inspection_coverage_issues(
                out, meta, "audit", "a", **access))
    events.remove(receipt)
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    events.insert(1, receipt)
    del deep["manual_dedup"]
    deep_path.write_text(json.dumps(deep), encoding="utf-8")
    receipt["payload"]["sha256"] = hashlib.sha256(deep_path.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "dedup_attempt_has_mcp_evidence", lambda *_a: True)
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    snapshot = {"manifest": [{"path": "src/app.py", "sha256": digest}]}
    finish["event_id"] = "finish-d"
    finish["payload"]["candidate_receipt"] = {"step": "deepcheck", "attempt": "d", "event_id": "finish-d", "candidate": snapshot}
    class Candidate:
        @staticmethod
        def snapshot_identity_ok(value):
            return value == snapshot
        @staticmethod
        def review_source_equivalence(old, current):
            return {"equivalent": old == current}
    monkeypatch.setattr(module, "candidate_module", lambda: Candidate)
    monkeypatch.setattr(module, "candidate_snapshot", lambda *_a, **_kw: snapshot)
    assert not module.inspection_coverage_issues(out, meta, "audit", "a")
    monkeypatch.setattr(module, "candidate_snapshot", lambda *_a, **_kw: {"manifest": []})
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    deep["manual_dedup"] = manual
    deep_path.write_text(json.dumps(deep), encoding="utf-8")
    receipt["payload"]["sha256"] = hashlib.sha256(deep_path.read_bytes()).hexdigest()
    audit["review_scope"] = ["src/app.py"]
    (out / "audit_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    audit["review_scope"] = ["src"]
    audit["dedup_status"] = deep["dedup_status"] = "not_eligible"
    audit["dedup_reason"] = deep["dedup_reason"] = "one function"
    deep_path.write_text(json.dumps(deep), encoding="utf-8")
    receipt["payload"]["sha256"] = hashlib.sha256(deep_path.read_bytes()).hexdigest()
    trace = {"ticket_id": "t", "attempt": "d", "gate_id": "deepcheck.dedup", "eligible": False,
             "decision": "skipped_not_eligible", "evidence": {"function_count": 1, "threshold": 50}}
    (out / ".mcp_gate_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    (out / "audit_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
    assert not module.inspection_coverage_issues(out, meta, "audit", "a")
    trace["evidence"]["function_count"] = 0
    (out / ".mcp_gate_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    trace["evidence"]["function_count"] = 1
    (out / ".mcp_gate_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    source.write_text("def changed():\n    return 2\n", encoding="utf-8")
    new_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    audit["read_phases"]["audit"]["src/app.py"] = new_hash
    (out / "audit_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    audit_start["payload"].pop("dedup_evidence_version")
    assert not any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    # Documents belong to review coverage but not the function denominator.
    source.write_text("def first():\n    return 1\n", encoding="utf-8")
    readme = src / "README.md"
    readme.write_text("# Module\n", encoding="utf-8")
    readme_hash = hashlib.sha256(readme.read_bytes()).hexdigest()
    meta["code_files"].append("src/README.md")
    for phase in ("reverse", "fixed", "free"):
        deep["read_phases"][phase]["src/README.md"] = readme_hash
    audit["read_phases"]["audit"] = {"src/app.py": digest, "src/README.md": readme_hash}
    audit_start["payload"]["dedup_evidence_version"] = 1
    deep_path.write_text(json.dumps(deep), encoding="utf-8")
    receipt["payload"]["sha256"] = hashlib.sha256(deep_path.read_bytes()).hexdigest()
    (out / "audit_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
    assert not module.inspection_coverage_issues(out, meta, "deepcheck", "d")
    assert not module.inspection_coverage_issues(out, meta, "audit", "a")
    (tmp_path / ".gitignore").write_text("src/app.py\n", encoding="utf-8")
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "deepcheck", "d"))
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
    (tmp_path / ".gitignore").write_text("src/unknown.xyz\n", encoding="utf-8")
    unknown = src / "unknown.xyz"
    unknown.write_text("unknown source type\n", encoding="utf-8")
    meta["code_files"].append("src/unknown.xyz")
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "deepcheck", "d"))
    assert any("dedup" in issue for issue in module.inspection_coverage_issues(out, meta, "audit", "a"))
