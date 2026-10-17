"""Regressions for equivalent review declarations and selected report evidence."""
import copy
import contextlib
import hashlib
import io
import json
import sys
from unittest import mock

import pytest

import test_candidate_inspection_generations as generations
import test_stage_acceptance as stages

@pytest.fixture
def candidate():
    fixture = generations.fixtures.CandidateTests(methodName="runTest")
    fixture.setUp()

    def call(*argv, ok=True):
        # Exercise the public CLI dispatcher without repeated interpreter startup.
        if argv[0] == "inspection" and argv[argv.index("--phase") + 1] == "prepare" and "--request" not in argv:
            argv = (*argv, "--request", "prepare:" + argv[argv.index("--attempt") + 1])
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["icode_control", *map(str, argv)]), contextlib.redirect_stdout(output):
            result = generations.ctl.main()
        assert (result == 0) == ok, output.getvalue()
        return json.loads(output.getvalue())

    fixture.call = call
    yield fixture
    fixture.doCleanups()


def test_redundant_related_keeps_candidate_and_exact_prepare_replay(candidate):
    candidate.create()
    (candidate.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    generations.finish_review(candidate, "code", "code-original")
    api = generations.ctl
    before = api.candidate_snapshot(candidate.ticket, api.load_metadata(candidate.ticket))
    old_report = (candidate.ticket / "code_worklist.json").read_bytes()
    report = generations.start_review(candidate, "deepcheck", "deep-equivalent", related=("source.py",))
    after = api.candidate_snapshot(candidate.ticket, api.load_metadata(candidate.ticket))
    assert after["candidate_id"] == before["candidate_id"], "redundant required file changed candidate"
    assert report["related"] == []
    assert all(row["reads"] == {} for unit in report["units"] for row in unit["files"])
    assert (candidate.ticket / "code_worklist.json").read_bytes() == old_report
    events, problems = api.verify_event_chain(candidate.ticket, api.load_metadata(candidate.ticket))
    assert not problems
    original = next(event for event in reversed(events) if event["event_type"] == "artifact_written")
    candidate.call("inspection", "--dir", candidate.ticket, "--step", "deepcheck", "--phase", "read",
                   "--attempt", "deep-equivalent", "--read-phase", report["required_phases"][0], "--path", "source.py")
    path = candidate.ticket / "deepcheck_worklist.json"
    progress = path.read_bytes()
    replay = candidate.call("inspection", "--dir", candidate.ticket, "--step", "deepcheck", "--phase", "prepare",
                            "--attempt", "deep-equivalent", "--scope", ".", "--related", "source.py",
                            "--request", original["request_id"])
    assert replay["already_applied"] and replay["event_id"] == original["event_id"]
    assert path.read_bytes() == progress
    refused = candidate.call("inspection", "--dir", candidate.ticket, "--step", "deepcheck", "--phase", "prepare",
                             "--attempt", "deep-equivalent", "--scope", ".", "--related", "source.py",
                             "--request", "different-request", ok=False)
    assert refused["gate_id"] == "inspection_worklist"
    assert path.read_bytes() == progress


def report_fixture(tmp_path):
    cell = {"layer": "host", "consumer": "tool", "scenario": "steady"}
    sha = "sha256:" + "1" * 64
    receipt = {"review_id": "current-review", "run_id": "current-run", "candidate_id": "current-candidate",
               "contract_digest": "contract", "cell": cell, "outcome": "pass", "channels": ["native"],
               "frame_results": [{"frame_set_id": "scene", "frame_id": "same-frame", "channel": "native",
                   "input_sha256": sha, "source_sha256": sha, "outcome": "pass", "model": "fixture",
                   "conclusion": "current observation"}], "limitations": ["current limitation"],
               "frames": [{"frame_set_id": "scene", "frame_id": "same-frame",
                           "preview_path": str(tmp_path / "current.png")} ]}
    history = copy.deepcopy(receipt)
    history.update(review_id="history-review", run_id="history-run", candidate_id="history-candidate")
    history["frame_results"][0]["conclusion"] = "history observation"
    history["frames"][0]["preview_path"] = str(tmp_path / "history.png")
    history["limitations"] = ["history limitation"]
    item = {"id": "image", "requirement_id": "display", "kind": "frame_set", "path": "current.png",
            "sha256": sha, "bytes": 1, "metadata": {**cell}}
    bundle = {"run_id": receipt["run_id"], "candidate_id": receipt["candidate_id"],
              "contract_sha256": "contract", "items": [item], "visual": {"frame_sets": [
                  {"id": "scene", **cell, "frames": [{"id": "same-frame", "source_item_id": "image",
                      "preview_item_id": "image", "review_refs": [{"review_id": "current-review"}]}]}]}}
    raw = json.dumps(bundle).encode()
    (tmp_path / "bundle.json").write_bytes(raw)
    run = {"run_id": receipt["run_id"], "candidate_id": receipt["candidate_id"], **cell, "outcome": "pass",
           "evidence_bundle": {"path": "bundle.json", "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}}
    contract = {"evidence_contract": {"required_items": [{"id": "display", "method": "ai_review",
                  "min_count": 1, "complete": True}], "visual": {"required": True}}}
    snapshot = {"evidence_metadata": {"verification_contract": contract, "extensions": {"verification": {
        "visual_reviews": [history, receipt], "evidence_reviews": []}}}, "accepted_runs": [run],
        "outcome": "pass", "facts_digest": "frozen", "reasons": []}
    state = {"directory": str(tmp_path), "stage_id": "one", "program": {"plan": {"stages": [
        {"id": "one", "title": "First", "required_actions": ["verify"]}]}}}
    return stages.load("stage_acceptance"), snapshot, state, bundle


def test_report_uses_only_selected_visual_references(tmp_path):
    api, snapshot, state, _ = report_fixture(tmp_path)
    frozen = copy.deepcopy(snapshot)
    text = api._report_text(snapshot, state)
    assert "current observation" in text
    assert "history observation" not in text, "unselected historical review leaked into current report"
    assert "history.png" not in text
    assert "history limitation" not in text
    assert text.count("![代表性帧") == 1
    assert snapshot == frozen


@pytest.mark.parametrize("step", ["code", "deepcheck", "audit"])
@pytest.mark.parametrize("mode", ["full", "fast"])
def test_equivalent_new_round_preserves_mode_phases_and_never_copies_progress(candidate, step, mode):
    helper = generations.ctl.inspection_module()
    options = dict(step=step, ticket_id="fixture", attempt="fresh", mode=mode,
                   scopes=["."], baselines={".": candidate.base})
    canonical = helper.build_worklist(candidate.repo, ["source.py"], **options)
    scope = {"scopes": canonical["scopes"], "related": [], "baselines": canonical["resolved_baselines"]}
    proposed = helper.build_worklist(candidate.repo, ["source.py"], related=["source.py"], **options)
    selected = helper._reuse_equivalent_related(proposed, candidate.repo, scope)
    assert selected["related"] == []
    assert selected["required_phases"] == canonical["required_phases"]
    assert selected["mode"] == mode and selected["attempt"] == "fresh"
    assert all(not row["reads"] for unit in selected["units"] for row in unit["files"])


@pytest.mark.parametrize("change", ["consumer", "source", "baseline", "scope", "unknown"])
def test_real_changes_and_unknown_are_not_erased(candidate, change):
    helper = generations.ctl.inspection_module()
    (candidate.repo / "consumer.py").write_text("consumer = 1\n", encoding="utf-8")
    candidate.git("add", "consumer.py")
    candidate.git("commit", "-qm", "consumer fixture")
    candidate.base = candidate.git("rev-parse", "HEAD").strip()
    candidate.create()
    (candidate.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    generations.finish_review(candidate, "code", "before-change")
    api = generations.ctl
    old = api.candidate_snapshot(candidate.ticket, api.load_metadata(candidate.ticket))
    scope = copy.deepcopy(old["inspection_scope"])
    options = dict(step="deepcheck", ticket_id="candidate-1", attempt="new", scopes=["."],
                   baselines={".": candidate.base}, related=["source.py"])
    if change == "consumer":
        options["related"] = ["consumer.py"]
    elif change == "source":
        (candidate.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
    elif change == "baseline":
        candidate.git("commit", "--allow-empty", "-qm", "same bytes new baseline")
        options["baselines"] = {".": candidate.git("rev-parse", "HEAD").strip()}
    elif change == "scope":
        options["scopes"] = ["source.py"]
    else:
        options["max_bytes"] = 2
    proposed = helper.build_worklist(candidate.repo, ["source.py"], **options)
    selected = helper._reuse_equivalent_related(proposed, candidate.repo, scope)
    if change == "source":
        # Declaration reuse never rewrites the source manifest or old receipt.
        live = api.candidate_snapshot(candidate.ticket, api.load_metadata(candidate.ticket))
        assert live["candidate_id"] != old["candidate_id"]
        assert api.delivery_freshness(candidate.ticket)["state"] == "stale"
    else:
        assert selected is proposed
    if change == "unknown":
        assert selected["unobserved"] and selected["coverage_status"] == "partial"


def save_bundle(state, snapshot, bundle):
    from pathlib import Path
    raw = json.dumps(bundle).encode()
    (Path(state["directory"]) / "bundle.json").write_bytes(raw)
    snapshot["accepted_runs"][0]["evidence_bundle"]["sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()


def test_related_mandatory_file_can_still_add_an_associated_consumer(candidate):
    helper = generations.ctl.inspection_module()
    for name in ("other.py", "test_other.py"):
        (candidate.repo / name).write_text("value = 1\n", encoding="utf-8")
    candidate.git("add", ".")
    candidate.git("commit", "-qm", "association fixture")
    base = candidate.git("rev-parse", "HEAD").strip()
    (candidate.repo / "other.py").write_text("value = 2\n", encoding="utf-8")
    options = dict(step="audit", ticket_id="fixture", attempt="fresh", scopes=["."], baselines={".": base})
    canonical = helper.build_worklist(candidate.repo, ["source.py"], **options)
    scope = {"scopes": ["."], "related": [], "baselines": canonical["resolved_baselines"]}
    proposed = helper.build_worklist(candidate.repo, ["source.py"], related=["other.py"], **options)
    assert "other.py" in helper._flatten(canonical)
    assert "test_other.py" not in helper._flatten(canonical)
    assert "test_other.py" in helper._flatten(proposed)
    assert helper._reuse_equivalent_related(proposed, candidate.repo, scope) is proposed


@pytest.mark.parametrize("deleted", [False, True])
def test_removed_clock_trigger_and_deleted_content_are_preserved(candidate, deleted):
    helper = generations.ctl.inspection_module()
    (candidate.repo / "source.py").write_text("timestamp_ns = 1\n", encoding="utf-8")
    candidate.git("add", ".")
    candidate.git("commit", "-qm", "clock baseline fixture")
    base = candidate.git("rev-parse", "HEAD").strip()
    if deleted:
        (candidate.repo / "source.py").unlink()
    else:
        (candidate.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
    options = dict(step="deepcheck", ticket_id="fixture", attempt="fresh", scopes=["."], baselines={".": base})
    canonical = helper.build_worklist(candidate.repo, ["source.py"], **options)
    scope = {"scopes": ["."], "related": [], "baselines": canonical["resolved_baselines"]}
    proposed = helper.build_worklist(candidate.repo, ["source.py"], related=["source.py"], **options)
    selected = helper._reuse_equivalent_related(proposed, candidate.repo, scope)
    assert selected["related"] == []
    assert [row["kind"] for row in selected["checks"]] == ["timestamp_contract"]
    assert all(not row["results"] for row in selected["checks"])
    assert helper._flatten(selected)["source.py"]["kind"] == ("deleted" if deleted else "file")


def test_same_run_other_review_and_unreferenced_frames_do_not_leak(tmp_path):
    api, snapshot, state, bundle = report_fixture(tmp_path)
    reviews = snapshot["evidence_metadata"]["extensions"]["verification"]["visual_reviews"]
    other = copy.deepcopy(reviews[-1])
    other["review_id"] = "other-review"
    other["frame_results"][0]["conclusion"] = "unreferenced same run"
    reviews.append(other)
    extra = copy.deepcopy(reviews[1]["frame_results"][0])
    extra.update(frame_id="unused-frame", conclusion="unreferenced frame")
    reviews[1]["frame_results"].append(extra)
    bridge = copy.deepcopy(reviews[1]["frame_results"][0])
    bridge.update(channel="bridge", conclusion="current bridge observation")
    reviews[1]["channels"].append("bridge")
    reviews[1]["frame_results"].append(bridge)
    text = api._report_text(snapshot, state)
    assert "unreferenced" not in text
    assert "current bridge observation" in text
    assert text.count("![代表性帧") == 1
    # The selected run's candidate is authoritative, not a live candidate field.
    snapshot["candidate"] = {"candidate_id": "later-same-content-candidate"}
    assert api._report_text(snapshot, state) == text


@pytest.mark.parametrize("mismatch", ["run_id", "candidate_id", "contract_digest", "cell", "source_sha256", "input_sha256"])
def test_selected_report_reference_binding_mismatch_rejects(tmp_path, mismatch):
    api, snapshot, state, _ = report_fixture(tmp_path)
    receipt = snapshot["evidence_metadata"]["extensions"]["verification"]["visual_reviews"][-1]
    if mismatch in ("source_sha256", "input_sha256"):
        receipt["frame_results"][0][mismatch] = "wrong"
    else:
        receipt[mismatch] = "wrong"
    with pytest.raises(api.ctl().ControlError, match="cannot be rendered safely"):
        api._report_text(snapshot, state)


def test_no_selected_visual_and_exact_nonvisual_review(tmp_path):
    api, snapshot, state, bundle = report_fixture(tmp_path)
    bundle["visual"] = None
    item = bundle["items"][0]
    item["kind"] = "log"
    item["metadata"]["review_ref"] = {"review_id": "chosen-item-review"}
    receipt = {"review_id": "chosen-item-review", "run_id": bundle["run_id"],
        "candidate_id": bundle["candidate_id"], "contract_digest": bundle["contract_sha256"],
        "cell": {key: snapshot["accepted_runs"][0][key] for key in ("layer", "consumer", "scenario")},
        "outcome": "pass", "method": "ai_review", "requirement_id": "display",
        "item_sha256": item["sha256"], "conclusion": "selected item observation"}
    namespace = snapshot["evidence_metadata"]["extensions"]["verification"]
    old = dict(receipt, review_id="old-item-review", conclusion="old item observation")
    namespace["evidence_reviews"] = [old, receipt]
    snapshot["evidence_metadata"]["verification_contract"]["evidence_contract"]["visual"] = None
    save_bundle(state, snapshot, bundle)
    text = api._report_text(snapshot, state)
    assert "selected item observation" in text and "old item observation" not in text
    assert "本阶段合同没有视觉验收项" in text and "observation（native" not in text
    assert "![代表性帧" not in text


def test_machine_item_optional_metadata_does_not_create_a_review_requirement(tmp_path):
    api, snapshot, state, bundle = report_fixture(tmp_path)
    bundle["visual"] = None
    bundle["items"][0]["kind"] = "log"
    bundle["items"][0]["metadata"]["review_ref"] = {"review_id": "ignored-optional-metadata"}
    evidence = snapshot["evidence_metadata"]["verification_contract"]["evidence_contract"]
    evidence["visual"] = None
    evidence["required_items"][0]["method"] = "machine"
    save_bundle(state, snapshot, bundle)
    assert "本阶段合同没有视觉验收项" in api._report_text(snapshot, state)


@pytest.mark.parametrize("boundary", ["read", "json", "finish"])
def test_report_reader_failure_never_falls_back_to_history(tmp_path, monkeypatch, boundary):
    api, snapshot, state, _ = report_fixture(tmp_path)
    reader = api.ctl().verification_evidence_module().bundle_module().Reader
    def unavailable(*args, **kwargs):
        raise ValueError("injected " + boundary + " failure")
    monkeypatch.setattr(reader, boundary, unavailable)
    with pytest.raises(api.ctl().ControlError, match="cannot be rendered safely"):
        api._report_text(snapshot, state)


def test_public_stage_review_excludes_unselected_receipt_and_old_report_replays(tmp_path):
    fixture = stages.StageAcceptanceTests(methodName="runTest")
    fixture.setUp()
    try:
        access, run, bundle, frames, route = fixture.visual_bundle()
        unused = fixture.actual_visual_review(access, run, frames, route, outcome="pass", request_id="unselected")
        # The protected receipt is real, but this bundle still references the first review.
        fixture.record(access, run, bundle)
        review = fixture.ctl.review_stage(fixture.first, request_id="current-report", **access)
        snapshot = review["projection"]["report"]["text"]
        assert snapshot.count("Actual synthetic visual observation: pass") == 5
        assert snapshot.count("![代表性帧") == 5
        api = fixture.ctl.stage_module()
        with mock.patch.object(api, "_report_text", side_effect=AssertionError("must not regenerate frozen report")):
            replay = fixture.ctl.review_stage(fixture.first, request_id="current-report", **access)
            assert replay["already_applied"]
            view = fixture.ctl.stage_projection(fixture.first, **access)
            request = {**view["challenge"], "decision": "confirm", "request_id": "accept-frozen"}
            result = fixture.ctl.confirm_stage(fixture.first, request, trusted_confirmation=lambda request: {
                "source": "ui", "source_id": "test:accept-frozen", "intent": "确认本阶段验收"}, **access)
            assert result["projection"]["state"] == "accepted"
        assert unused["review_id"] in json.dumps(fixture.ctl.load_metadata(fixture.first))
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("boundary", ["read", "json", "finish"])
def test_public_report_failure_publishes_no_review_or_challenge(boundary):
    fixture = stages.StageAcceptanceTests(methodName="runTest")
    fixture.setUp()
    try:
        access, _, _, run, bundle = fixture.ready()
        fixture.record(access, run, bundle)
        api = fixture.ctl.stage_module()
        original = api._report_text
        reader = api.ctl().verification_evidence_module().bundle_module().Reader
        def unavailable(*args, **kwargs):
            raise ValueError("injected formatter " + boundary)
        def format_failed(snapshot, state):
            with mock.patch.object(reader, boundary, unavailable):
                return original(snapshot, state)
        before = fixture.ctl.load_metadata(fixture.coord)
        with mock.patch.object(api, "_report_text", format_failed), pytest.raises(fixture.ctl.ControlError):
            fixture.ctl.review_stage(fixture.first, request_id="unreadable-report", **access)
        assert fixture.ctl.load_metadata(fixture.coord) == before
        view = fixture.ctl.stage_projection(fixture.first, **access)
        assert not view["can_confirm"]
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("outcome", ["fail", "inconclusive"])
def test_public_nonpass_bundle_still_produces_a_nonconfirmable_report(outcome):
    fixture = stages.StageAcceptanceTests(methodName="runTest")
    fixture.setUp()
    try:
        access, _, item, run, bundle = fixture.ready(method="ai_review")
        if outcome == "fail":
            receipt = fixture.actual_generic_review(access, item, run, outcome="fail", request_id="actual-failure")
            item["metadata"]["review_ref"] = {"review_id": receipt["review_id"]}
        (fixture.first / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        args = fixture.ctl.build_parser().parse_args(["record-verification", "--dir", str(fixture.first),
            "--kind", "device_test", "--outcome", outcome, "--environment", "host",
            "--candidate-id", run["candidate_id"], "--layer", "host", "--consumer", "tool",
            "--scenario", "steady", "--baseline", "fixture", "--evidence", "raw.log",
            "--evidence-bundle", "bundle.json", "--request-id", "record-nonpass"])
        with contextlib.redirect_stdout(io.StringIO()):
            assert fixture.ctl.cmd_record_verification(args, **access) == 0
        report = fixture.ctl.review_stage(fixture.first, request_id="report-nonpass", **access)
        assert report["outcome"] == outcome
        assert not report["projection"]["can_confirm"]
        assert "raw.log" in report["projection"]["report"]["text"]
    finally:
        fixture.doCleanups()
