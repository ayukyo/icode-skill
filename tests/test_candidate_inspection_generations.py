"""Inspection declarations follow controlled candidate generations, not review refs."""
import copy
import json

import pytest

import test_candidate_freshness as fixtures
import test_delivery_inventory as inventories

ctl = fixtures.ctl


@pytest.fixture
def candidate():
    fixture = fixtures.CandidateTests(methodName="runTest")
    fixture.setUp()
    yield fixture
    fixture.doCleanups()


def start_review(candidate, step, attempt, *, scope=".", related=(), baselines=None):
    (candidate.ticket / "03_plan_final.md").write_text("fixture plan\n", encoding="utf-8")
    candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "start",
                   "--attempt", attempt, "--request", attempt + ":start")
    candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "check",
                   "--attempt", attempt, "--boundary", "before_write")
    args = ["inspection", "--dir", candidate.ticket, "--step", step, "--phase", "prepare",
            "--attempt", attempt, "--scope", scope]
    for path in related:
        args.extend(["--related", path])
    if baselines is not None:
        args.extend(["--baselines-json", json.dumps(baselines)])
    candidate.call(*args)
    return json.loads((candidate.ticket / (step + "_worklist.json")).read_text())


def finish_review(candidate, step, attempt, **options):
    """Use every real writer boundary, including the default baseline entry point."""
    report = start_review(candidate, step, attempt, **options)
    paths = [row["path"] for unit in report["units"] for row in unit["files"]]
    for phase in report["required_phases"]:
        for path in paths:
            candidate.call("inspection", "--dir", candidate.ticket, "--step", step, "--phase", "read",
                           "--attempt", attempt, "--read-phase", phase, "--path", path)
    name = {"code": "04_code_review_fix.md", "deepcheck": "05_deepcheck.md", "audit": "06_audit.md"}[step]
    (candidate.ticket / name).write_text("fixture review report\n", encoding="utf-8")
    candidate.call("artifact", "--dir", candidate.ticket, "--step", step, "--attempt", attempt, "--path", name)
    if step == "code":
        candidate.call("artifact", "--dir", candidate.ticket, "--step", step, "--attempt", attempt,
                       "--path", "source.py", "--scope", "workspace")
    else:
        coverage = {"schema_version": 1, "ticket_id": "candidate-1", "attempt": attempt,
                    "review_scope": [options.get("scope", ".")], "coverage_status": "complete_within_scope",
                    "unobserved": [], "dedup_unobserved": [], "dedup_status": "not_eligible",
                    "dedup_reason": "single fixture", "read_phases": {
                        phase: {path: ctl.file_sha256(candidate.repo / path) for path in paths}
                        for phase in report["required_phases"]}}
        coverage_name = step + "_coverage.json"
        (candidate.ticket / coverage_name).write_text(json.dumps(coverage), encoding="utf-8")
        candidate.call("artifact", "--dir", candidate.ticket, "--step", step, "--attempt", attempt,
                       "--path", coverage_name)
        candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "check",
                       "--attempt", attempt, "--boundary", "after_wait")
    candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "check",
                   "--attempt", attempt, "--boundary", "before_transition")
    return candidate.call("step", "--dir", candidate.ticket, "--step", step, "--phase", "finish",
                          "--attempt", attempt, "--outcome", "success", "--evidence", name,
                          "--request", attempt + ":finish")


def next_base(candidate):
    candidate.git("commit", "--allow-empty", "-qm", "new candidate baseline")
    return candidate.git("rev-parse", "HEAD").strip()


def capture_base(candidate, base, request, *, ok=True):
    return candidate.call("candidate", "--dir", candidate.ticket, "--phase", "capture",
                          "--base", base, "--request-id", request, ok=ok)


def test_new_base_default_sequential_reviews_close_and_preserve_history(candidate):
    (candidate.repo / "retired.py").write_text("value = 1\n", encoding="utf-8")
    candidate.git("add", "retired.py")
    candidate.git("commit", "-qm", "legacy related source")
    candidate.base = candidate.git("rev-parse", "HEAD").strip()
    candidate.create()
    for step in ("code", "deepcheck", "audit"):
        finish_review(candidate, step, step + "-A", related=("retired.py",))
    old_receipts = copy.deepcopy(ctl.load_metadata(candidate.ticket)["review_receipts"])
    old_worklists = {step: (candidate.ticket / (step + "_worklist.json")).read_bytes()
                     for step in ("code", "deepcheck", "audit")}
    base_b = next_base(candidate)
    capture_base(candidate, base_b, "bind-B")
    # capture uses B even though its input metadata still carried A.
    tracking = ctl.load_metadata(candidate.ticket)["candidate_tracking"]
    assert tracking["current"]["inspection_scope"] == {"scopes": [], "related": [], "baselines": {}}
    stale = ctl.delivery_freshness(candidate.ticket)
    assert stale["state"] == "stale" and stale["applicable_receipts"] == []
    (candidate.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    (candidate.repo / "retired.py").write_text("value = 2\n", encoding="utf-8")
    for index, step in enumerate(("code", "deepcheck", "audit")):
        finish_review(candidate, step, step + "-B")
        meta = ctl.load_metadata(candidate.ticket)
        assert meta["review_receipts"][:3] == old_receipts
        for untouched in ("code", "deepcheck", "audit")[index + 1:]:
            assert (candidate.ticket / (untouched + "_worklist.json")).read_bytes() == old_worklists[untouched]
        result = ctl.delivery_freshness(candidate.ticket)
        assert result["state"] != "blocked", result
        if step == "code":
            frozen = candidate.bytes()
            inventory = inventories.guard.build_submission_inventory(meta, candidate.ticket / ctl.METADATA_NAME)
            assert inventory["violations"] == [], inventory
            assert {row["path"] for row in inventory["intended"]} == {"source.py"}
            assert {row["path"] for row in inventory["unknown"]} == {"retired.py"}
            assert candidate.bytes() == frozen
    assert result["ok"] and result["state"] == "fresh", result
    assert {receipt["attempt"] for receipt in result["applicable_receipts"]} == {"code-B", "deepcheck-B", "audit-B"}
    assert len(meta["review_receipts"]) == 6
    assert len({receipt["candidate_id"] for receipt in meta["review_receipts"][3:]}) == 1


@pytest.mark.parametrize("prepared", [False, True])
def test_old_open_attempt_blocks_base_change_without_mutation(candidate, prepared):
    candidate.create()
    if prepared:
        start_review(candidate, "code", "code-A")
    else:
        (candidate.ticket / "03_plan_final.md").write_text("fixture plan\n", encoding="utf-8")
        candidate.call("step", "--dir", candidate.ticket, "--step", "code", "--phase", "start",
                       "--attempt", "code-A", "--request", "code-A:start")
    base_b = next_base(candidate)
    before = candidate.bytes()
    result = capture_base(candidate, base_b, "bind-B", ok=False)
    assert result["gate_id"] == "candidate_scope"
    assert candidate.bytes() == before
    candidate.call("step", "--dir", candidate.ticket, "--step", "code", "--phase", "finish",
                   "--attempt", "code-A", "--outcome", "blocked", "--evidence", "baseline changed")
    capture_base(candidate, base_b, "bind-B")


def test_review_baseline_can_be_earlier_than_controlled_candidate_base(candidate):
    review_base = candidate.base
    candidate.base = next_base(candidate)
    candidate.create()
    for step in ("code", "deepcheck", "audit"):
        finish_review(candidate, step, step + "-wide", baselines={".": review_base})
    fresh = ctl.delivery_freshness(candidate.ticket)
    assert fresh["ok"], fresh
    current = ctl.load_metadata(candidate.ticket)["candidate_tracking"]["current"]
    assert current["base"] == candidate.base
    assert current["inspection_scope"]["baselines"] == {".": review_base}


def dependency_repository(candidate):
    dependency = candidate.repo / "dep"
    dependency.mkdir()
    candidate.git("init", "-q", cwd=dependency)
    candidate.git("config", "user.email", "test@example.invalid", cwd=dependency)
    candidate.git("config", "user.name", "Candidate Test", cwd=dependency)
    (dependency / "helper.py").write_text("answer = 7\n", encoding="utf-8")
    candidate.git("add", ".", cwd=dependency)
    candidate.git("commit", "-qm", "dependency base", cwd=dependency)
    dep_a = candidate.git("rev-parse", "HEAD", cwd=dependency).strip()
    candidate.git("commit", "--allow-empty", "-qm", "dependency next base", cwd=dependency)
    dep_b = candidate.git("rev-parse", "HEAD", cwd=dependency).strip()
    with (candidate.repo / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("/dep/\n")
    candidate.git("commit", "-qam", "declare separate dependency repository")
    candidate.base = candidate.git("rev-parse", "HEAD").strip()
    return dep_a, dep_b


def test_current_generation_related_repository_baseline_conflict_fails_closed(candidate):
    dep_a, dep_b = dependency_repository(candidate)
    candidate.create()
    finish_review(candidate, "code", "code-A", related=("dep/helper.py",),
                  baselines={".": candidate.base, "dep": dep_a})
    before = ctl.load_metadata(candidate.ticket)["candidate_tracking"]["current"]
    assert before["inspection_scope"]["related"] == ["dep/helper.py"]
    assert before["inspection_scope"]["baselines"]["dep"] == dep_a
    start_review(candidate, "deepcheck", "deepcheck-A", related=("dep/helper.py",),
                 baselines={".": candidate.base, "dep": dep_b})
    frozen = candidate.bytes()
    with pytest.raises(ctl.ControlError) as error:
        ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))
    assert error.value.extra["gate_id"] == "candidate_scope"
    assert candidate.bytes() == frozen


def test_related_and_repository_baseline_growth_stales_current_receipt(candidate):
    _, dep_b = dependency_repository(candidate)
    candidate.create()
    finish_review(candidate, "code", "code-local")
    old = copy.deepcopy(ctl.load_metadata(candidate.ticket)["review_receipts"])
    start_review(candidate, "deepcheck", "deepcheck-related", related=("dep/helper.py",))
    current = ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))
    assert current["inspection_scope"]["related"] == ["dep/helper.py"]
    assert current["inspection_scope"]["baselines"] == {".": candidate.base, "dep": dep_b}
    assert current["candidate_id"] != old[0]["candidate_id"]
    result = ctl.delivery_freshness(candidate.ticket, required_steps=("code",))
    assert result["state"] == "stale" and not result["ok"]
    assert ctl.load_metadata(candidate.ticket)["review_receipts"] == old


def test_current_generation_explicit_root_baseline_conflict_is_not_history(candidate):
    earlier = candidate.base
    candidate.base = next_base(candidate)
    candidate.create()
    finish_review(candidate, "code", "code-wide", baselines={".": earlier})
    start_review(candidate, "deepcheck", "deepcheck-default")
    with pytest.raises(ctl.ControlError, match="baseline 合同不一致"):
        ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))


def test_scope_growth_stales_current_receipt_and_preserves_declaration(candidate):
    candidate.create()
    finish_review(candidate, "code", "code-narrow", scope="source.py")
    old_receipts = copy.deepcopy(ctl.load_metadata(candidate.ticket)["review_receipts"])
    start_review(candidate, "deepcheck", "deepcheck-wide", scope=".")
    current = ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))
    assert current["inspection_scope"]["scopes"] == [".", "source.py"]
    assert current["candidate_id"] != old_receipts[0]["candidate_id"]
    stale = ctl.delivery_freshness(candidate.ticket, required_steps=("code",))
    assert stale["state"] == "stale" and not stale["ok"]
    assert ctl.load_metadata(candidate.ticket)["review_receipts"] == old_receipts


def test_report_cannot_claim_an_old_attempt_to_hide_current_declaration(candidate):
    candidate.create()
    finish_review(candidate, "code", "code-A")
    capture_base(candidate, next_base(candidate), "bind-B")
    report = start_review(candidate, "code", "code-B")
    report.update(attempt="code-A", resolved_baselines={".": candidate.base})
    (candidate.ticket / "code_worklist.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ctl.ControlError, match="declaration/receipt drift"):
        ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))


def test_returning_to_old_base_does_not_revive_previous_generation(candidate):
    candidate.create()
    finish_review(candidate, "code", "code-A")
    capture_base(candidate, next_base(candidate), "bind-B")
    capture_base(candidate, candidate.base, "return-A")
    current = ctl.load_metadata(candidate.ticket)["candidate_tracking"]["current"]
    assert current["inspection_scope"] == {"scopes": [], "related": [], "baselines": {}}
    assert not ctl.delivery_freshness(candidate.ticket, required_steps=("code",))["ok"]


def test_recapture_same_base_keeps_current_generation_declarations(candidate):
    candidate.create()
    finish_review(candidate, "code", "code-A")
    current = ctl.load_metadata(candidate.ticket)["candidate_tracking"]["current"]
    capture_base(candidate, candidate.base, "same-A")
    assert ctl.load_metadata(candidate.ticket)["candidate_tracking"]["current"] == current
    assert ctl.delivery_freshness(candidate.ticket, required_steps=("code",))["ok"]


def test_explicit_enable_keeps_legacy_completed_declaration_as_history(candidate):
    control = candidate.repo.parent / "legacy-control"
    control.mkdir()
    candidate.ticket = control / ".icode_output/.icode_output_1"
    candidate.call("create", "--dir", candidate.ticket, "--ticket-id", "candidate-1", "--birth", "plan",
                   "--requirement", "legacy review", "--metadata-json", '{"code_files":["source.py"]}',
                   "--request-id", "legacy-birth")
    candidate.call("bind-execution-root", "--dir", candidate.ticket, "--ticket-id", "candidate-1",
                   "--execution-root", candidate.repo, "--request-id", "legacy-root")
    finish_review(candidate, "code", "legacy-code", baselines={".": candidate.base})
    historical = (candidate.ticket / "code_worklist.json").read_bytes()
    capture_base(candidate, candidate.base, "enable-native")
    meta = ctl.load_metadata(candidate.ticket)
    assert meta.get("review_receipts", []) == []
    assert (candidate.ticket / "code_worklist.json").read_bytes() == historical
    assert meta["candidate_tracking"]["current"]["inspection_scope"] == {"scopes": [], "related": [], "baselines": {}}
    assert ctl.candidate_snapshot(candidate.ticket, meta) == meta["candidate_tracking"]["current"]


def migrate_old_completed(candidate):
    candidate.ticket.mkdir(parents=True)
    metadata = {"ticket_id": "candidate-1", "requirement": "legacy completed review",
                "created_at": "2026-09-14T08:00:00", "status": "completed",
                "completed_steps": ["0", "1", "2", "3", "4", "5", "6"],
                "project_path": str(candidate.repo), "code_files": ["source.py"], "patch_count": 0}
    (candidate.ticket / ctl.METADATA_NAME).write_text(json.dumps(metadata), encoding="utf-8")
    helper = ctl.inspection_module()
    for step in ("code", "deepcheck", "audit"):
        report = helper.build_worklist(candidate.repo, code_files=["source.py"], ticket_id="candidate-1",
                                       step=step, attempt="legacy-" + step, mode="full")
        # Historical v1 reports predate special checks and v3 writer events.
        del report["checks_version"], report["checks"]
        (candidate.ticket / (step + "_worklist.json")).write_text(json.dumps(report), encoding="utf-8")
    candidate.call("migration", "--dir", candidate.ticket, "--apply")
    capture_base(candidate, candidate.base, "enable-legacy-A")


def test_unreceipted_legacy_reviews_reenter_sequentially_after_controlled_base_change(candidate):
    migrate_old_completed(candidate)
    before = ctl.load_metadata(candidate.ticket)
    assert before["candidate_tracking"]["current"]["inspection_scope"]["baselines"] == {".": candidate.base}
    assert not before.get("review_receipts")
    old_worklists = {step: (candidate.ticket / (step + "_worklist.json")).read_bytes()
                     for step in ("code", "deepcheck", "audit")}
    capture_base(candidate, next_base(candidate), "legacy-bind-B")
    current = ctl.load_metadata(candidate.ticket)["candidate_tracking"]["current"]
    assert current["inspection_scope"] == {"scopes": [], "related": [], "baselines": {}}
    for index, step in enumerate(("code", "deepcheck", "audit")):
        finish_review(candidate, step, step + "-B")
        for untouched in ("code", "deepcheck", "audit")[index + 1:]:
            assert (candidate.ticket / (untouched + "_worklist.json")).read_bytes() == old_worklists[untouched]
    meta = ctl.load_metadata(candidate.ticket)
    assert meta["completed_steps"] == before["completed_steps"]
    assert len(meta["review_receipts"]) == 3
    assert ctl.delivery_freshness(candidate.ticket)["ok"]


@pytest.mark.parametrize("field", ["scopes", "related", "resolved_baselines"])
def test_unreceipted_new_scope_is_not_hidden_by_legacy_boundary(candidate, field):
    dep_b = dependency_repository(candidate)[1] if field == "resolved_baselines" else None
    migrate_old_completed(candidate)
    capture_base(candidate, next_base(candidate), "legacy-bind-B")
    finish_review(candidate, "code", "code-B")
    path = candidate.ticket / "audit_worklist.json"
    report = json.loads(path.read_text())
    if field == "resolved_baselines":
        report[field]["dep"] = dep_b
    else:
        report[field].append("source.py")
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ctl.ControlError, match="baseline 合同不一致"):
        ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))
