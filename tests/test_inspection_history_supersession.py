"""Read-only final checks preserve authenticated historical source reviews."""
import json
import contextlib
import copy
import io
import os
import sys
import subprocess
from unittest.mock import patch

import pytest

import test_candidate_freshness as fixtures

ctl = fixtures.ctl


@pytest.fixture
def candidate():
    fixture = fixtures.CandidateTests(methodName="runTest")
    fixture.setUp()
    yield fixture
    fixture.doCleanups()


def reviewed_change(candidate, earlier="code", successor="audit"):
    candidate.create()
    candidate.finish_review(earlier, earlier + "-old")
    old_worklist = candidate.ticket / (earlier + "_worklist.json")
    original = old_worklist.read_bytes()
    (candidate.repo / "source.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
    candidate.call("candidate", "--dir", candidate.ticket, "--phase", "capture",
                   "--request-id", "changed-candidate")
    candidate.finish_review(successor, successor + "-current")
    return old_worklist, original


@pytest.mark.parametrize("earlier,successor", [("code", "deepcheck"), ("code", "audit"), ("deepcheck", "audit")])
def test_later_complete_current_review_preserves_old_review_bytes(candidate, earlier, successor):
    old_worklist, original = reviewed_change(candidate, earlier, successor)
    result = candidate.call("check-outputs", "--dir", candidate.ticket, "--step", earlier)
    assert result["ok"]
    assert old_worklist.read_bytes() == original
    assert ctl.delivery_freshness(candidate.ticket, required_steps=(successor,))["ok"]


@pytest.fixture
def fast_candidate(candidate):
    # Exercise the same public CLI dispatcher in process; only argv/stdout are
    # adapted, never gates, candidate providers, artifacts or ledger writers.
    def call(*args, ok=True):
        output = io.StringIO()
        with patch.object(sys, "argv", ["icode_control.py", *map(str, args)]), contextlib.redirect_stdout(output):
            code = ctl.main()
        assert (code == 0) == ok, output.getvalue()
        return json.loads(output.getvalue())
    candidate.call = call
    return candidate


def check_rejected(candidate, step="code"):
    result = candidate.call("check-outputs", "--dir", candidate.ticket, "--step", step, ok=False)
    assert not result["ok"]
    assert not any("historical_inspection_superseded" in item for item in result.get("warnings", []))
    return result


def start_unfinished(candidate, step, attempt, scope="."):
    candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "start", "--attempt", attempt)
    candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "check", "--attempt", attempt,
                   "--boundary", "before_write")
    candidate.call("inspection", "--dir", candidate.ticket, "--step", step, "--phase", "prepare",
                   "--attempt", attempt, "--scope", scope)


def prerequisites(candidate):
    (candidate.ticket / "01_plan.md").write_text("fixture plan\n", encoding="utf-8")
    # This fixture targets inspection receipts, not the unrelated plan/review
    # content linters. Use their existing test-only native transition route.
    with patch.dict(os.environ, ICODE_CONTROL_TEST_MODE="1"):
        candidate.call("transition", "--dir", candidate.ticket, "--to", "plan_done", "--skip-gates")
        candidate.call("transition", "--dir", candidate.ticket, "--to", "review_in_progress", "--skip-gates")
    candidate.call("step", "--dir", candidate.ticket, "--step", "review", "--phase", "start", "--attempt", "review-prerequisite")
    candidate.call("step", "--dir", candidate.ticket, "--step", "review", "--phase", "check", "--attempt", "review-prerequisite",
                   "--boundary", "before_write")
    (candidate.ticket / "02_review.md").write_text("fixture review\n", encoding="utf-8")
    detail = candidate.ticket / "review_round_1.json"
    detail.write_text(json.dumps({"new_issues": [], "refuted_issues": [], "pending_verification": []}), encoding="utf-8")
    manifest = {"schema_version": 1, "ticket_id": "candidate-1", "review_run": "review-prerequisite",
                "rounds": [{"round": 1, "origin_attempt": "review-prerequisite", "new_issues": 0,
                            "refuted_issues": 0, "pending_verification": 0, "detail_path": detail.name,
                            "detail_sha256": ctl.file_sha256(detail)}]}
    (candidate.ticket / "review_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in ("02_review.md", detail.name, "review_manifest.json"):
        candidate.call("artifact", "--dir", candidate.ticket, "--step", "review", "--attempt", "review-prerequisite", "--path", name)
    for boundary in ("after_wait", "before_transition"):
        candidate.call("step", "--dir", candidate.ticket, "--step", "review", "--phase", "check", "--attempt", "review-prerequisite",
                       "--boundary", boundary)
    candidate.call("step", "--dir", candidate.ticket, "--step", "review", "--phase", "finish", "--attempt", "review-prerequisite",
                   "--outcome", "success", "--evidence", "fixture review")


def test_audit_final_check_uses_later_current_review_without_rewriting_code(fast_candidate):
    f = fast_candidate
    f.create()
    prerequisites(f)
    f.finish_review("code", "code-old")
    old = (f.ticket / "code_worklist.json").read_bytes()
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "changed-candidate")
    f.finish_review("deepcheck", "deepcheck-current")
    f.finish_review("audit", "audit-current")
    result = f.call("check-outputs", "--dir", f.ticket, "--step", "audit")
    assert result["ok"] and (f.ticket / "code_worklist.json").read_bytes() == old
    assert any("code:historical_inspection_superseded_by=audit" in item for item in result["warnings"])


@pytest.mark.parametrize("target", ["old-worklist", "old-human-report", "current-worklist", "current-coverage", "current-human-report", "ledger", "source", "head"])
def test_changed_authenticity_or_current_identity_cannot_accept_history(fast_candidate, target):
    f = fast_candidate
    reviewed_change(f)
    paths = {"old-worklist": "code_worklist.json", "old-human-report": "04_code_review_fix.md",
             "current-worklist": "audit_worklist.json", "current-coverage": "audit_coverage.json",
             "current-human-report": "06_audit.md", "ledger": ctl.EVENTS_NAME}
    if target in paths:
        path = f.ticket / paths[target]
        # Whitespace preserves valid JSON/shape and must still invalidate exact
        # native output bytes (or the authenticated ledger's raw representation).
        if target == "ledger":
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace('"actor": "icode"', '"actor": "system"', 1), encoding="utf-8")
        else:
            path.write_bytes(path.read_bytes() + b"\n")
    elif target == "source":
        (f.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
    else:
        f.git("commit", "--allow-empty", "-qm", "changed checkout head")
    check_rejected(f)


def test_old_deepcheck_coverage_bytes_are_not_exempt(fast_candidate):
    f = fast_candidate
    reviewed_change(f, "deepcheck", "audit")
    path = f.ticket / "deepcheck_coverage.json"
    path.write_bytes(path.read_bytes() + b"\n")
    check_rejected(f, "deepcheck")


def test_no_later_review_cannot_accept_changed_source(fast_candidate):
    f = fast_candidate
    f.create()
    f.finish_review("code", "code-old")
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    check_rejected(f)


@pytest.mark.parametrize("unfinished", ["old", "later", "retry-after-success"])
def test_unfinished_review_never_supersedes_history(fast_candidate, unfinished):
    f = fast_candidate
    f.create()
    if unfinished == "old":
        (f.ticket / "03_plan_final.md").write_text("fixture plan\n", encoding="utf-8")
        start_unfinished(f, "code", "code-open")
    else:
        f.finish_review("code", "code-old")
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "changed-candidate")
    if unfinished == "later":
        start_unfinished(f, "audit", "audit-open")
    else:
        f.finish_review("audit", "audit-current")
    if unfinished == "retry-after-success":
        start_unfinished(f, "deepcheck", "deepcheck-open")
    check_rejected(f)


def test_unreceipted_report_cannot_replace_native_read_and_finish(fast_candidate):
    f = fast_candidate
    f.create()
    f.finish_review("code", "code-old")
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "changed-candidate")
    start_unfinished(f, "audit", "audit-unreceipted")
    path = f.ticket / "audit_worklist.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    for unit in report["units"]:
        for row in unit["files"]:
            row["reads"] = {phase: row["sha256"] for phase in report["required_phases"]}
    path.write_text(json.dumps(report), encoding="utf-8")
    (f.ticket / "06_audit.md").write_text("self-claimed pass\n", encoding="utf-8")
    check_rejected(f)


def test_later_scope_must_cover_all_old_related_members(fast_candidate):
    f = fast_candidate
    (f.repo / "helper.py").write_text("helper_value = 1\n", encoding="utf-8")
    f.create()
    f.finish_review("code", "code-wide", scope=".")
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "changed-candidate")
    f.finish_review("audit", "audit-narrow", scope="source.py")
    check_rejected(f)


def test_current_audit_is_never_treated_as_history(fast_candidate):
    f = fast_candidate
    reviewed_change(f)
    (f.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
    result = f.call("check-outputs", "--dir", f.ticket, "--step", "audit", ok=False)
    assert any("audit" in item and ("drift" in item or "unconfirmed_read" in item) for item in result["violations"])



def test_later_finished_review_on_new_head_does_not_rebind_old_history(fast_candidate):
    f = fast_candidate
    f.create()
    f.finish_review("code", "code-old")
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.git("add", "source.py")
    f.git("commit", "-qm", "new fixture source")
    f.base = f.git("rev-parse", "HEAD").strip()
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--base", f.base,
           "--request-id", "changed-base")
    f.finish_review("audit", "audit-new-base")
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    check_rejected(f)


def test_forward_commit_with_same_base_accepts_history_without_rewriting_receipts(candidate):
    f = candidate
    f.create()
    f.finish_review("code", "code-before-commit")
    old_worklist = (f.ticket / "code_worklist.json").read_bytes()
    old_report = (f.ticket / "04_code_review_fix.md").read_bytes()
    old_receipt = copy.deepcopy(ctl.load_metadata(f.ticket)["review_receipts"][0])
    old_ledger = (f.ticket / ctl.EVENTS_NAME).read_bytes()
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.git("add", "source.py")
    f.git("commit", "-qm", "normal forward source change")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--base", f.base,
           "--request-id", "forward-same-base")
    f.finish_review("audit", "audit-after-commit")
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    frozen = f.bytes()
    result = f.call("check-outputs", "--dir", f.ticket, "--step", "code")
    assert result["ok"]
    assert any("historical_inspection_superseded_by=audit" in item for item in result["warnings"])
    assert f.bytes() == frozen
    assert (f.ticket / "code_worklist.json").read_bytes() == old_worklist
    assert (f.ticket / "04_code_review_fix.md").read_bytes() == old_report
    assert ctl.load_metadata(f.ticket)["review_receipts"][0] == old_receipt
    assert (f.ticket / ctl.EVENTS_NAME).read_bytes().startswith(old_ledger)


def test_native_history_proof_keeps_64_commit_boundary_and_same_head_tree_guard(candidate):
    f = candidate
    old = f.capture()
    for index in range(64):
        f.git("commit", "--allow-empty", "-qm", "forward proof " + str(index))
    current = f.capture()
    assert ctl._inspection_history_extends(old, current)
    f.git("commit", "--allow-empty", "-qm", "forward proof exceeds budget")
    assert not ctl._inspection_history_extends(old, f.capture())
    assert not ctl._inspection_history_extends(old, dict(old, head_tree="0" * 40))


def test_history_query_failures_keep_check_outputs_structured_and_read_only(fast_candidate):
    f = fast_candidate
    f.create()
    f.finish_review("code", "code-before-query-failure")
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.git("commit", "-qam", "normal forward source change")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--base", f.base,
           "--request-id", "forward-query-failure")
    f.finish_review("audit", "audit-before-query-failure")
    module = ctl.candidate_module()
    native_git = module._git
    frozen = f.bytes()
    for failure in (module.CandidateError("Git query unavailable"),
                    module.CandidateError("Git query timeout"), b"\xff"):
        proof_queries = []

        def failed_proof(root, args, limit, **kwargs):
            if args[:2] == ["--no-replace-objects", "rev-list"]:
                proof_queries.append(args)
                if isinstance(failure, Exception):
                    raise failure
                return failure
            return native_git(root, args, limit, **kwargs)

        with patch.object(module, "_git", side_effect=failed_proof):
            check_rejected(f)
        assert len(proof_queries) == 1
        assert f.bytes() == frozen


@pytest.mark.parametrize("history", ["rollback", "amend", "divergence", "replacement", "graft"])
def test_fresh_audit_cannot_certify_rewritten_or_rolled_back_history(fast_candidate, history):
    f = fast_candidate
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.git("commit", "-qam", "old reviewed head")
    f.create()
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--base", f.base,
           "--request-id", "original-base")
    f.finish_review("code", "code-old-head")
    old_head = f.git("rev-parse", "HEAD").strip()
    if history == "rollback":
        f.git("checkout", "--detach", f.base)
    elif history == "amend":
        (f.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
        f.git("commit", "-qam", "rewritten reviewed head", "--amend")
    else:
        f.git("checkout", "-qb", "other-history", f.base)
        (f.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
        f.git("commit", "-qam", "divergent head")
        current_head = f.git("rev-parse", "HEAD").strip()
        if history == "replacement":
            tree = f.git("rev-parse", "HEAD^{tree}").strip()
            replacement = f.git("commit-tree", tree, "-p", old_head, "-m", "forged parent").strip()
            f.git("replace", current_head, replacement)
        elif history == "graft":
            (f.repo / ".git/info/grafts").write_text(current_head + " " + old_head + "\n", encoding="ascii")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--base", f.base,
           "--request-id", "changed-history")
    f.finish_review("audit", "audit-changed-history")
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    frozen = f.bytes()
    check_rejected(f)
    assert f.bytes() == frozen


def test_later_finished_review_in_another_root_does_not_rebind_history(fast_candidate):
    f = fast_candidate
    f.create()
    f.finish_review("code", "code-old")
    other = f.repo.parent / "other-root"
    subprocess.run(["git", "clone", "-q", str(f.repo), str(other)], check=True, capture_output=True)
    (other / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.call("bind-execution-root", "--dir", f.ticket, "--ticket-id", "candidate-1",
           "--execution-root", other, "--request-id", "other-root")
    f.repo = other.resolve()
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "new-root-candidate")
    f.finish_review("audit", "audit-other-root")
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    check_rejected(f)


def test_later_failed_retry_does_not_revive_previous_success(fast_candidate):
    f = fast_candidate
    reviewed_change(f)
    start_unfinished(f, "deepcheck", "deepcheck-failed")
    f.call("step", "--dir", f.ticket, "--step", "deepcheck", "--phase", "finish",
           "--attempt", "deepcheck-failed", "--outcome", "failure", "--evidence", "real failed retry")
    check_rejected(f)


def finish_scoped_review(candidate, step, attempt, scope, *, generic_worklist_artifact=False):
    """Finish real native receipts for every member of a changed file scope."""
    f = candidate
    (f.ticket / "03_plan_final.md").write_text("fixture plan\n", encoding="utf-8")
    f.call("step", "--dir", f.ticket, "--step", step, "--phase", "start", "--attempt", attempt)
    f.call("step", "--dir", f.ticket, "--step", step, "--phase", "check", "--attempt", attempt,
           "--boundary", "before_write")
    f.call("inspection", "--dir", f.ticket, "--step", step, "--phase", "prepare", "--attempt", attempt,
           "--scope", scope, "--baselines-json", json.dumps({".": f.base}))
    report = json.loads((f.ticket / (step + "_worklist.json")).read_text(encoding="utf-8"))
    members = {row["path"]: row["sha256"] for unit in report["units"] for row in unit["files"]}
    for phase in report["required_phases"]:
        f.call("inspection", "--dir", f.ticket, "--step", step, "--phase", "read", "--attempt", attempt,
               "--read-phase", phase, "--paths-json", json.dumps(list(members)))
    name = {"code": "04_code_review_fix.md", "deepcheck": "05_deepcheck.md", "audit": "06_audit.md"}[step]
    (f.ticket / name).write_text("current scoped fixture review\n", encoding="utf-8")
    f.call("artifact", "--dir", f.ticket, "--step", step, "--attempt", attempt, "--path", name)
    if step == "code":
        for path in ctl.load_metadata(f.ticket)["code_files"]:
            f.call("artifact", "--dir", f.ticket, "--step", step, "--attempt", attempt,
                   "--path", path, "--scope", "workspace")
    else:
        coverage = {"schema_version": 1, "ticket_id": "candidate-1", "attempt": attempt,
                    "review_scope": [scope], "coverage_status": "complete_within_scope", "unobserved": [],
                    "dedup_unobserved": [], "dedup_status": "not_eligible", "dedup_reason": "small fixture",
                    "read_phases": {phase: members for phase in report["required_phases"]}}
        name = step + "_coverage.json"
        (f.ticket / name).write_text(json.dumps(coverage), encoding="utf-8")
        f.call("artifact", "--dir", f.ticket, "--step", step, "--attempt", attempt, "--path", name)
        f.call("step", "--dir", f.ticket, "--step", step, "--phase", "check", "--attempt", attempt,
               "--boundary", "after_wait")
    if generic_worklist_artifact:
        f.call("artifact", "--dir", f.ticket, "--step", step, "--attempt", attempt,
               "--path", step + "_worklist.json")
    f.call("step", "--dir", f.ticket, "--step", step, "--phase", "check", "--attempt", attempt,
           "--boundary", "before_transition")
    f.call("step", "--dir", f.ticket, "--step", step, "--phase", "finish", "--attempt", attempt,
           "--outcome", "success", "--evidence", "complete native scoped review")


@pytest.mark.parametrize("earlier", ["code", "deepcheck"])
def test_monotonically_extended_file_scope_is_certified_by_current_audit(fast_candidate, earlier):
    f = fast_candidate
    f.create()
    finish_scoped_review(f, earlier, earlier + "-old-scope", "source.py")
    old = f.ticket / (earlier + "_worklist.json")
    original = old.read_bytes()
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    (f.repo / "added_test.py").write_text("extra_case = True\n", encoding="utf-8")
    f.call("metadata-update", "--dir", f.ticket, "--append-json", '{"code_files":["added_test.py"]}')
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "expanded-scope")
    finish_scoped_review(f, "audit", "audit-expanded-scope", ".")
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    result = f.call("check-outputs", "--dir", f.ticket, "--step", earlier)
    assert result["ok"] and old.read_bytes() == original


@pytest.mark.parametrize("change", ["contracting", "replacing"])
def test_non_monotonic_file_scope_cannot_certify_history_even_with_member_coverage(fast_candidate, change):
    f = fast_candidate
    f.create()
    if change == "contracting":
        (f.repo / "helper.py").write_text("helper_value = 1\n", encoding="utf-8")
        f.call("metadata-update", "--dir", f.ticket, "--append-json", '{"code_files":["helper.py"]}')
    finish_scoped_review(f, "code", "code-original-scope", ".")
    old = json.loads((f.ticket / "code_worklist.json").read_text(encoding="utf-8"))
    old_members = {row["path"] for unit in old["units"] for row in unit["files"]}
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    current = ["source.py"]
    if change == "replacing":
        (f.repo / "replacement.py").write_text("replacement_value = 1\n", encoding="utf-8")
        current = ["replacement.py"]
    f.call("metadata-update", "--dir", f.ticket, "--set-json", json.dumps({"code_files": current}))
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "non-monotonic-scope")
    finish_scoped_review(f, "audit", "audit-non-monotonic-scope", ".")
    new = json.loads((f.ticket / "audit_worklist.json").read_text(encoding="utf-8"))
    assert old_members <= {row["path"] for unit in new["units"] for row in unit["files"]}
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    check_rejected(f)


def test_same_bytes_generic_artifact_does_not_invalidate_native_inspection_proof(fast_candidate):
    f = fast_candidate
    f.create()
    f.finish_review("code", "code-original")
    original = (f.ticket / "code_worklist.json").read_bytes()
    (f.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    f.call("candidate", "--dir", f.ticket, "--phase", "capture", "--request-id", "changed-source")
    finish_scoped_review(f, "audit", "audit-generic-copy", ".", generic_worklist_artifact=True)
    assert ctl.delivery_freshness(f.ticket, required_steps=("audit",))["ok"]
    assert f.call("check-outputs", "--dir", f.ticket, "--step", "code")["ok"]
    assert (f.ticket / "code_worklist.json").read_bytes() == original


def test_generic_artifact_cannot_replace_missing_inspection_declaration_proof(fast_candidate):
    f = fast_candidate
    reviewed_change(f)
    metadata = ctl.load_metadata(f.ticket)
    events, problems = ctl.verify_event_chain(f.ticket, metadata)
    assert not problems
    original_ledger = (f.ticket / ctl.EVENTS_NAME).read_bytes()
    # Probe this helper's proof precondition with an invalid in-memory input,
    # without changing or re-signing any real ledger or manufacturing a finish.
    invalid = copy.deepcopy(events)
    for event in invalid:
        event["payload"].pop("inspection_declaration_hash", None)
    start = next(event for event in reversed(invalid) if event["event_type"] == "step_started"
                 and event["payload"]["step"] == "audit")
    assert ctl._authenticated_inspection_review(f.ticket, metadata, ctl.load_execution_model(), invalid, start) is None
    assert (f.ticket / ctl.EVENTS_NAME).read_bytes() == original_ledger


def test_changed_raw_worklist_cannot_be_recertified_by_generic_artifact_after_finish(fast_candidate):
    f = fast_candidate
    reviewed_change(f)
    path = f.ticket / "audit_worklist.json"
    path.write_bytes(path.read_bytes() + b"\n")
    rejected = f.call("artifact", "--dir", f.ticket, "--step", "audit", "--attempt", "audit-current",
                      "--path", "audit_worklist.json", ok=False)
    assert not rejected["ok"]
    check_rejected(f)
