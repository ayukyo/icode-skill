import json
import hashlib
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys

from test_crosscheck import workspace, run_tool, write_fresh, write_json, fresh_payload, tree_digest


def crosscheck_module():
    spec = importlib.util.spec_from_file_location("crosscheck_base_test", Path(__file__).parents[1] / "tools/icode_crosscheck.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def run_control(env, *args):
    proc = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "tools/icode_control.py"), *map(str, args)],
                          env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    return payload


def enable_candidate(ticket, env):
    run_control(env, "migration", "--dir", ticket, "--apply")
    run_control(env, "candidate", "--dir", ticket, "--phase", "capture", "--request-id", "first-base")


def change_enabled_base(root, ticket, env):
    # Same tree and linear history isolate the base contract from source bytes.
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "new candidate baseline"], check=True)
    base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    run_control(env, "candidate", "--dir", ticket, "--phase", "capture", "--base", base, "--request-id", "changed-base")
    return json.loads((ticket / ".ico_metadata.json").read_text())["candidate_tracking"]


def complete(root, env):
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    fresh = fresh_payload(1)
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh, env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh)
    run_tool(env, "finish", "--dir", directory, "--round", "1")
    return directory


def test_freshness_without_container_is_neutral_and_zero_write(workspace):
    root, ticket, env = workspace
    before = tree_digest(root)
    result = run_tool(env, "freshness", str(ticket), "--workspace", root)
    assert result["state"] == "not_run" and result["required"] is False
    assert tree_digest(root) == before and not (root / ".icode_output/.crosscheck").exists()


def test_new_round_binds_candidate_and_checks_without_enabling_old_ticket(workspace):
    root, ticket, env = workspace
    original = (ticket / ".ico_metadata.json").read_bytes()
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    manifest = json.loads((Path(started["crosscheck_dir"]) / "crosscheck_manifest.json").read_text())
    item = manifest["rounds"][0]
    assert item.get("inspection_checks_version") == 1
    assert len(item["start_snapshot"]["candidate"]["candidate_id"]) == 64
    assert original == (ticket / ".ico_metadata.json").read_bytes()


def test_current_crosscheck_ignores_appended_verification_history_but_reports_input_boundary(workspace):
    root, ticket, env = workspace
    directory = complete(root, env)
    before = tree_digest(root)
    first = run_tool(env, "freshness", "--dir", directory)
    assert first["state"] == "current" and first["verdict"] == "changes_recommended"
    assert before == tree_digest(root)
    meta_path = ticket / ".ico_metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["verification_runs"] = [{"run_id": "new-history"}]
    write_json(meta_path, meta)
    second = run_tool(env, "freshness", "--dir", directory)
    assert second["state"] == "current" and second["input_boundary_changed"] is True
    (root / "src/feature.c").write_text("int feature(void) { return 2; }\n")
    assert run_tool(env, "freshness", "--dir", directory)["state"] == "stale"


def test_same_content_commit_uses_frozen_base(workspace):
    root, _, env = workspace
    (root / "src/feature.c").write_text("int feature(void) { return 2; }\n")
    directory = complete(root, env)
    old = json.loads((directory / "crosscheck_manifest.json").read_text())["rounds"][0]["start_snapshot"]["candidate"]
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "reviewed bytes"], check=True)
    result = run_tool(env, "freshness", "--dir", directory)
    assert result["state"] == "current" and result["candidate_base"] == old["base"]


def test_stale_never_bypasses_frozen_tamper_and_missing_candidate_is_legacy(workspace):
    root, _, env = workspace
    directory = complete(root, env)
    (root / "src/feature.c").write_text("int feature(void) { return 2; }\n")
    final = directory / "crosscheck_round_1.json"
    final.write_text(final.read_text() + " ")
    failed = run_tool(env, "freshness", "--dir", directory, expected=1)
    assert failed["gate_id"] == "final_immutable"


def test_checks_marker_prevents_removing_checks_version(workspace):
    root, _, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    worklist = directory / "crosscheck_round_1.worklist.json"
    report = json.loads(worklist.read_text())
    del report["checks_version"]
    del report["checks"]
    write_json(worklist, report)
    failed = run_tool(env, "inspection", "--dir", directory, "--round", "1", "--phase", "read", "--path", "src/feature.c", expected=1)
    assert failed["gate_id"] == "inspection_worklist"


def test_assess_records_real_source_cells_and_freeze_makes_them_immutable(workspace):
    root, _, env = workspace
    source = root / "src/feature.c"
    source.write_text("unsigned timestamp = (unsigned) clock;\n")
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    worklist = directory / "crosscheck_round_1.worklist.json"
    report = json.loads(worklist.read_text())
    check = report["checks"][0]
    evidence = {"kind": "source", "path": "src/feature.c", "start_line": 1, "end_line": 1,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "excerpt": source.read_text()}
    assessment = {"cells": {item: {"status": "handled", "reason": "Reviewed fixture timestamp declaration; no runtime proof",
                                   "evidence": [evidence]} for item in check["required_items"]}}
    before_identity = report["checks"][0]["check_id"]
    # Assessment is meaningful only after the actual source member was Read.
    run_tool(env, "inspection", "--dir", directory, "--round", "1", "--phase", "read", "--path", "src/feature.c")
    run_tool(env, "inspection", "--dir", directory, "--round", "1", "--phase", "assess", "--check-id", check["check_id"], "--assessment-json", json.dumps(assessment))
    updated = json.loads(worklist.read_text())
    assert updated["checks"][0]["check_id"] == before_identity and updated["checks"][0]["results"]["fresh"] == assessment
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    frozen = worklist.read_bytes()
    run_tool(env, "inspection", "--dir", directory, "--round", "1", "--phase", "assess", "--check-id", check["check_id"], "--assessment-json", json.dumps(assessment), expected=1)
    assert frozen == worklist.read_bytes()


def test_legacy_completed_round_missing_candidate_does_not_claim_current(workspace):
    root, _, env = workspace
    directory = complete(root, env)
    path = directory / "crosscheck_manifest.json"
    manifest = json.loads(path.read_text())
    item = manifest["rounds"][0]
    del item["start_snapshot"]["candidate"]
    item["start_snapshot_digest"] = hashlib.sha256(json.dumps(item["start_snapshot"], sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    write_json(path, manifest)
    before = tree_digest(root)
    result = run_tool(env, "freshness", "--dir", directory)
    assert result["state"] == "not_run" and result["tracking_status"] == "legacy_untracked"
    assert before == tree_digest(root)


def test_legacy_worktree_candidate_uses_the_actual_code_root(workspace, tmp_path):
    root, ticket, env = workspace
    checkout = tmp_path / "legacy-checkout"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(checkout)], check=True)
    meta_path = ticket / ".ico_metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["worktree_path"] = str(checkout)
    write_json(meta_path, meta)
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    manifest = json.loads((Path(started["crosscheck_dir"]) / "crosscheck_manifest.json").read_text())
    snapshot = manifest["rounds"][0]["start_snapshot"]
    assert snapshot["candidate"]["identity"]["checkout_realpath"] == snapshot["code_root"] == str(checkout)


def test_enabled_worktree_freshness_keeps_controlled_metadata_and_is_read_only(workspace, tmp_path):
    root, ticket, env = workspace
    checkout = tmp_path / "enabled-checkout"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(checkout)], check=True)
    run_control(env, "migration", "--dir", ticket, "--apply")
    run_control(env, "bind-execution-root", "--dir", ticket, "--ticket-id", "demo-1",
                "--execution-root", checkout, "--request-id", "bind-worktree")
    run_control(env, "candidate", "--dir", ticket, "--phase", "capture", "--request-id", "enable-worktree")
    directory = complete(root, env)
    manifest = json.loads((directory / "crosscheck_manifest.json").read_text())
    snapshot = manifest["rounds"][0]["start_snapshot"]
    assert snapshot["candidate"]["identity"]["checkout_realpath"] == snapshot["code_root"] == str(checkout)
    frozen = tree_digest(root), tree_digest(checkout)
    result = run_tool(env, "freshness", "--dir", directory)
    assert result["state"] == "current" and result["candidate_id"] == snapshot["candidate"]["candidate_id"]
    assert frozen == (tree_digest(root), tree_digest(checkout))


def test_legacy_open_review_keeps_crosscheck_read_only_without_enabling_candidate(workspace):
    root, ticket, env = workspace
    run_control(env, "migration", "--dir", ticket, "--apply")
    run_control(env, "step", "--dir", ticket, "--step", "code", "--phase", "start",
                "--attempt", "legacy-code", "--request", "legacy-code:start")
    ctl = crosscheck_module().control_module()
    names = (ctl.METADATA_NAME, ctl.EVENTS_NAME)
    frozen = tuple((ticket / name).read_bytes() for name in names)
    directory = complete(root, env)
    result = run_tool(env, "freshness", "--dir", directory)
    assert result["state"] == "current"
    assert frozen == tuple((ticket / name).read_bytes() for name in names)
    assert "candidate_tracking" not in json.loads((ticket / ".ico_metadata.json").read_text())


def test_freeze_keeps_historical_derived_reports_valid_across_clock_tick(workspace, monkeypatch):
    root, _, env = workspace
    directory = complete(root, env)
    run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    write_fresh(directory / "crosscheck_round_2.fresh.json", fresh_payload(2), env)
    spec = importlib.util.spec_from_file_location("crosscheck_clock_test", Path(__file__).parents[1] / "tools/icode_crosscheck.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    # A real phase transition updates the manifest even if wall-clock seconds
    # differ from start. Its derived history must remain immediately readable.
    monkeypatch.setattr(tool, "now_iso", lambda: "2099-01-01T00:00:00+08:00")
    tool.cmd_freeze(argparse.Namespace(dir=str(directory), round=2))
    assert tool.cmd_validate(argparse.Namespace(dir=str(directory)))["valid"] is True


def test_enabled_base_change_makes_completed_review_stale_without_query_writes(workspace):
    root, ticket, env = workspace
    enable_candidate(ticket, env)
    directory = complete(root, env)
    old = json.loads((directory / "crosscheck_manifest.json").read_text())["rounds"][0]["start_snapshot"]["candidate"]
    tracking = change_enabled_base(root, ticket, env)
    tool = crosscheck_module()
    meta = tool.control_module().load_metadata(ticket)
    live = tool.control_module().candidate_snapshot(ticket, meta)
    assert live["candidate_id"] == tracking["current"]["candidate_id"] != old["candidate_id"]
    before = tree_digest(root)
    result = run_tool(env, "freshness", "--dir", directory)
    assert result["state"] == "stale" and result["source_reason"] == "source_or_contract_drift"
    assert result["candidate_id"] == live["candidate_id"] and result["candidate_base"] == tracking["base"]
    assert tree_digest(root) == before


def test_enabled_new_round_uses_current_base_instead_of_first_round_base(workspace):
    root, ticket, env = workspace
    enable_candidate(ticket, env)
    directory = complete(root, env)
    tracking = change_enabled_base(root, ticket, env)
    before = tree_digest(ticket)
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    assert started["round"] == 2
    manifest = json.loads((directory / "crosscheck_manifest.json").read_text())
    candidate = manifest["rounds"][1]["start_snapshot"]["candidate"]
    assert candidate["base"] == tracking["base"] and candidate["candidate_id"] == tracking["current"]["candidate_id"]
    assert tree_digest(ticket) == before


def test_enabled_active_finish_compares_current_base_and_preserves_original_ticket(workspace):
    root, ticket, env = workspace
    enable_candidate(ticket, env)
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh_payload(1))
    change_enabled_base(root, ticket, env)
    tool = crosscheck_module()
    target, meta = tool.validate_target(ticket)
    expected = tool.capture_target_snapshot(target, meta)
    before = tree_digest(ticket)
    finished = run_tool(env, "finish", "--dir", directory, "--round", "1", expected=1)
    assert finished["state"] == "stale_input"
    item = json.loads((directory / "crosscheck_manifest.json").read_text())["rounds"][0]
    assert item["stale_snapshot_digest"] == tool.object_digest(expected)
    assert tree_digest(ticket) == before
