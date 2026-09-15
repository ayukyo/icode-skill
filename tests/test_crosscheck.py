import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "icode_crosscheck.py"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_fresh(path, value, env):
    """Real fixture Read declarations, separate from writing a result JSON."""
    report = json.loads(path.with_name(path.name.replace(".fresh.json", ".worklist.json")).read_text())
    for unit in report["units"]:
        for entry in unit["files"]:
            data = (Path(report["workspace"]) / entry["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            run_tool(env, "inspection", "--dir", path.parent, "--round", str(value["round"]),
                     "--phase", "read", "--path", entry["path"])
    write_json(path, value)


def tree_digest(root: Path, *, exclude_crosscheck: bool = False) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if exclude_crosscheck and rel.parts[:2] == (".icode_output", ".crosscheck"):
            continue
        if path.is_symlink():
            digest.update(f"L:{rel}:{os.readlink(path)}\n".encode())
        elif path.is_file():
            digest.update(f"F:{rel}:".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "project"
    ticket = root / ".icode_output" / ".icode_output_1"
    ticket.mkdir(parents=True)
    source = root / "src" / "feature.c"
    source.parent.mkdir(parents=True)
    source.write_text("int feature(void) { return 1; }\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture baseline"], check=True)
    write_json(
        ticket / ".ico_metadata.json",
        {
            "ticket_id": "demo-1",
            "requirement": "crosscheck fixture",
            "created_at": "2026-09-14T08:00:00",
            "status": "completed",
            "completed_steps": ["0", "1", "2", "3", "4", "5", "6"],
            "project_path": str(root),
            "code_files": ["src/feature.c"],
            "patch_count": 0,
        },
    )
    (ticket / "03_plan_final.md").write_text("# Plan\n\nImplement feature.\n", encoding="utf-8")
    (ticket / "04_code_review_fix.md").write_text("# Code\n\nReviewed.\n", encoding="utf-8")
    index = tmp_path / "home" / ".claude" / "icode_data" / "index.json"
    write_json(index, {"schema_version": 3, "tickets": []})
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return root, ticket, env


def run_tool(env: dict[str, str], *args: str, expected: int = 0) -> dict:
    proc = subprocess.run(
        [sys.executable, str(TOOL), *map(str, args)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is (expected == 0)
    return payload


def fresh_payload(round_no: int, ticket_id: str = "demo-1") -> dict:
    return {
        "schema_version": 1,
        "target_ticket_id": ticket_id,
        "round": round_no,
        "reviewed_at": f"2026-09-14T08:0{round_no}:00",
        "verdict": "changes_recommended",
        "evidence_boundary": "静态检查；未运行设备验证",
        "summary": "发现一个可维护性建议",
        "findings": [
            {
                "finding_id": "CC-001",
                "title": "补充边界测试",
                "severity": "minor",
                "status": "new",
                "category": "test",
                "evidence": ["src/feature.c:1"],
                "analysis": "边界值尚未覆盖",
                "recommendation": "增加边界测试",
                "requires_change": True,
                "locations": [],
                "verification_status": "needs_more_evidence",
                "evidence_boundary": "静态模拟建议，尚未证明边界测试缺失",
            }
        ],
    }


def test_new_round_requires_independent_worklist(workspace):
    root, _, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    assert "inspection_worklist" in started
    directory = Path(started["crosscheck_dir"])
    # A result JSON alone must not satisfy a real new round's Read worklist.
    (directory / "crosscheck_round_1.fresh.json").write_text(json.dumps(fresh_payload(1)))
    failed = run_tool(env, "freeze", "--dir", directory, "--round", "1", expected=1)
    assert failed["gate_id"] == "inspection_worklist"


def test_related_source_drift_reenters_a_new_round(workspace):
    root, _, env = workspace
    header = root / "src/feature.h"
    header.write_text("int feature(void);\n")
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    header.write_text("int feature(void); /* newer source, same untracked status */\n")
    restarted = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    assert restarted["round"] == 2 and not restarted["resumed"]
    manifest = json.loads((Path(started["crosscheck_dir"]) / "crosscheck_manifest.json").read_text())
    assert manifest["rounds"][0]["state"] == "stale_input"


def test_freeze_replay_rejects_worklist_symlink(workspace, tmp_path):
    root, _, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    path = Path(started["inspection_worklist"])
    copy = tmp_path / "same-worklist.json"
    copy.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(copy)
    assert run_tool(env, "freeze", "--dir", directory, "--round", "1", expected=1)["gate_id"] == "inspection_worklist"


def test_start_rejects_frozen_worklist_tampering_without_manifest_write(workspace):
    root, _, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    before = (directory / "crosscheck_manifest.json").read_bytes()
    path = Path(started["inspection_worklist"])
    report = json.loads(path.read_text())
    report["units"][0]["files"][0]["sha256"] = "0" * 64
    write_json(path, report)
    failed = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1", expected=1)
    assert failed["gate_id"] == "inspection_worklist"
    assert (directory / "crosscheck_manifest.json").read_bytes() == before


def test_finding_position_and_frozen_worklist_are_enforced(workspace):
    root, _, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    fresh = fresh_payload(1)
    finding = fresh["findings"][0]
    source = root / "src/feature.c"
    finding.update(verification_status="confirmed", locations=[dict(path="src/feature.c", start_line=1,
        end_line=1, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), excerpt="made-up source\n")])
    path = directory / "crosscheck_round_1.fresh.json"
    write_fresh(path, fresh, env)
    assert run_tool(env, "freeze", "--dir", directory, "--round", "1", expected=1)["gate_id"] == "inspection_worklist"
    finding["locations"][0]["excerpt"] = source.read_text()
    write_json(path, fresh)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh)
    run_tool(env, "finish", "--dir", directory, "--round", "1")
    # Historic immutable evidence remains valid after a later source update.
    source.write_text("int feature(void) { return 2; }\n")
    run_tool(env, "validate", "--dir", directory)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    worklist = Path(started["inspection_worklist"])
    report = json.loads(worklist.read_text())
    report["units"][0]["files"][0]["reads"] = {}
    write_json(worklist, report)
    assert run_tool(env, "finish", "--dir", directory, "--round", "1", expected=1)["gate_id"] == "inspection_worklist"


def test_new_round_does_not_reuse_old_reads_or_allow_scope_rewrite(workspace):
    root, _, env = workspace
    first = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(first["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh_payload(1))
    run_tool(env, "finish", "--dir", directory, "--round", "1")
    second = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    path = Path(second["inspection_worklist"])
    report = json.loads(path.read_text())
    assert all(f["reads"] == {} for u in report["units"] for f in u["files"])
    report["max_files"] = 1
    write_json(path, report)
    failed = run_tool(env, "inspection", "--dir", directory, "--round", "2", "--phase", "read", "--path", "src/feature.c", expected=1)
    assert failed["gate_id"] == "inspection_worklist"


def test_crosscheck_roundtrip_multi_round_and_zero_write(workspace):
    root, ticket, env = workspace
    index = Path(env["HOME"]) / ".claude" / "icode_data" / "index.json"
    target_before = tree_digest(ticket)
    source_before = tree_digest(root / "src")
    index_before = index.read_bytes()

    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    crosscheck_dir = Path(started["crosscheck_dir"])
    assert started["round"] == 1
    assert started["phase"] == "fresh_review"
    assert started["resumed"] is False
    assert "previous_round" not in started
    assert crosscheck_dir.parent == root / ".icode_output" / ".crosscheck"
    assert not (crosscheck_dir / ".ico_metadata.json").exists()

    fresh = crosscheck_dir / "crosscheck_round_1.fresh.json"
    write_fresh(fresh, fresh_payload(1), env)
    frozen = run_tool(env, "freeze", "--dir", crosscheck_dir, "--round", "1")
    assert frozen["previous_round"] is None

    final = fresh_payload(1)
    write_json(crosscheck_dir / "crosscheck_round_1.json", final)
    finished = run_tool(env, "finish", "--dir", crosscheck_dir, "--round", "1")
    assert finished["state"] == "completed"
    assert (crosscheck_dir / "crosscheck_round_1.md").is_file()
    assert (crosscheck_dir / "findings.json").is_file()
    assert (crosscheck_dir / "crosscheck_report.md").is_file()
    run_tool(env, "validate", "--dir", crosscheck_dir)

    second = run_tool(env, "start", "--workspace", root, ticket / "03_plan_final.md")
    assert second["crosscheck_dir"] == str(crosscheck_dir)
    assert second["round"] == 2
    assert "previous_round" not in second
    resumed = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    assert resumed["round"] == 2
    assert resumed["resumed"] is True

    write_fresh(crosscheck_dir / "crosscheck_round_2.fresh.json", fresh_payload(2), env)
    frozen2 = run_tool(env, "freeze", "--dir", crosscheck_dir, "--round", "2")
    assert frozen2["previous_round"] == str(crosscheck_dir / "crosscheck_round_1.json")
    final2 = fresh_payload(2)
    final2["findings"][0]["status"] = "still_present"
    write_json(crosscheck_dir / "crosscheck_round_2.json", final2)
    run_tool(env, "finish", "--dir", crosscheck_dir, "--round", "2")
    validated = run_tool(env, "validate", "--dir", crosscheck_dir)
    assert validated["rounds"] == 2

    assert tree_digest(ticket) == target_before
    assert tree_digest(root / "src") == source_before
    assert index.read_bytes() == index_before
    assert not list(crosscheck_dir.rglob(".ico_metadata.json"))


def test_no_argument_uses_active_pointer_but_never_latest(workspace):
    root, ticket, env = workspace
    failed = run_tool(env, "start", "--workspace", root, expected=1)
    assert "当前工单" in failed["error"]

    write_json(
        root / ".icode_output" / ".active_ticket.json",
        {"ticket_id": "demo-1", "out_dir": str(ticket)},
    )
    started = run_tool(env, "start", "--workspace", root)
    assert started["target_ticket_id"] == "demo-1"


def test_stale_input_is_preserved_and_next_round_can_start(workspace):
    root, ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    crosscheck_dir = Path(started["crosscheck_dir"])
    write_fresh(crosscheck_dir / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", crosscheck_dir, "--round", "1")
    write_json(crosscheck_dir / "crosscheck_round_1.json", fresh_payload(1))

    (ticket / "03_plan_final.md").write_text("# Plan\n\nChanged during review.\n", encoding="utf-8")
    stale = run_tool(env, "finish", "--dir", crosscheck_dir, "--round", "1", expected=1)
    assert stale["state"] == "stale_input"
    assert (crosscheck_dir / "crosscheck_round_1.json").is_file()

    next_round = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    assert next_round["round"] == 2


def test_rejects_ineligible_ambiguous_and_escaping_targets(workspace):
    root, ticket, env = workspace
    metadata = json.loads((ticket / ".ico_metadata.json").read_text(encoding="utf-8"))
    metadata["status"] = "code_done"
    write_json(ticket / ".ico_metadata.json", metadata)
    denied = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1", expected=1)
    assert "completed" in denied["error"]

    metadata["status"] = "completed"
    write_json(ticket / ".ico_metadata.json", metadata)
    first = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    first_dir = Path(first["crosscheck_dir"])
    duplicate = first_dir.parent / ".icode_output_99"
    duplicate.mkdir()
    write_json(duplicate / "crosscheck_manifest.json", json.loads((first_dir / "crosscheck_manifest.json").read_text()))
    ambiguous = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1", expected=1)
    assert "多个 crosscheck" in ambiguous["error"]

    bad_root = root / ".icode_output" / ".crosscheck-link"
    bad_root.symlink_to(root.parent, target_is_directory=True)
    escaped = run_tool(
        env,
        "start",
        "--workspace",
        root,
        "--ticket",
        "demo-1",
        "--crosscheck-root",
        bad_root,
        expected=1,
    )
    assert "crosscheck" in escaped["error"]


def test_fresh_file_is_immutable_after_freeze(workspace):
    root, _ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    crosscheck_dir = Path(started["crosscheck_dir"])
    fresh = crosscheck_dir / "crosscheck_round_1.fresh.json"
    write_fresh(fresh, fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", crosscheck_dir, "--round", "1")
    changed = fresh_payload(1)
    changed["summary"] = "tampered"
    write_json(fresh, changed)
    write_json(crosscheck_dir / "crosscheck_round_1.json", fresh_payload(1))
    denied = run_tool(env, "finish", "--dir", crosscheck_dir, "--round", "1", expected=1)
    assert "fresh" in denied["error"]


def test_finish_idempotently_repairs_missing_derived_reports(workspace):
    root, _ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh_payload(1))
    run_tool(env, "finish", "--dir", directory, "--round", "1")

    (directory / "findings.json").unlink()
    (directory / "crosscheck_report.md").unlink()
    repaired = run_tool(env, "finish", "--dir", directory, "--round", "1")
    assert repaired["already_applied"] is True
    assert (directory / "findings.json").is_file()
    assert (directory / "crosscheck_report.md").is_file()
    run_tool(env, "validate", "--dir", directory)


def test_history_compare_skips_stale_rounds(workspace):
    root, ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh_payload(1))
    run_tool(env, "finish", "--dir", directory, "--round", "1")

    run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    write_fresh(directory / "crosscheck_round_2.fresh.json", fresh_payload(2), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "2")
    write_json(directory / "crosscheck_round_2.json", fresh_payload(2))
    (ticket / "04_code_review_fix.md").write_text("# changed\n", encoding="utf-8")
    run_tool(env, "finish", "--dir", directory, "--round", "2", expected=1)

    third = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    assert third["round"] == 3
    write_fresh(directory / "crosscheck_round_3.fresh.json", fresh_payload(3), env)
    frozen = run_tool(env, "freeze", "--dir", directory, "--round", "3")
    assert frozen["previous_round"].endswith("crosscheck_round_1.json")
    final = fresh_payload(3)
    final["findings"][0]["status"] = "still_present"
    write_json(directory / "crosscheck_round_3.json", final)
    run_tool(env, "finish", "--dir", directory, "--round", "3")
    run_tool(env, "validate", "--dir", directory)


def test_status_change_during_review_becomes_stale_input(workspace):
    root, ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh_payload(1))
    metadata = json.loads((ticket / ".ico_metadata.json").read_text(encoding="utf-8"))
    metadata["status"] = "code_done"
    write_json(ticket / ".ico_metadata.json", metadata)
    stale = run_tool(env, "finish", "--dir", directory, "--round", "1", expected=1)
    assert stale["state"] == "stale_input"


def test_validate_detects_tampered_derived_report(workspace):
    root, _ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    write_json(directory / "crosscheck_round_1.json", fresh_payload(1))
    run_tool(env, "finish", "--dir", directory, "--round", "1")
    (directory / "crosscheck_report.md").write_text("tampered\n", encoding="utf-8")
    denied = run_tool(env, "validate", "--dir", directory, expected=1)
    assert "report" in denied["error"].lower() or "报告" in denied["error"]


def test_validate_detects_tampered_stale_final(workspace):
    root, ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    write_fresh(directory / "crosscheck_round_1.fresh.json", fresh_payload(1), env)
    run_tool(env, "freeze", "--dir", directory, "--round", "1")
    final_path = directory / "crosscheck_round_1.json"
    write_json(final_path, fresh_payload(1))
    (ticket / "03_plan_final.md").write_text("changed\n", encoding="utf-8")
    run_tool(env, "finish", "--dir", directory, "--round", "1", expected=1)
    changed = fresh_payload(1)
    changed["summary"] = "tampered stale draft"
    write_json(final_path, changed)
    denied = run_tool(env, "validate", "--dir", directory, expected=1)
    assert "final" in denied["error"]


def test_ticket_physical_project_root_wins_over_stale_metadata_path(workspace, tmp_path):
    root, ticket, env = workspace
    stale_root = tmp_path / "old-existing-project"
    stale_root.mkdir()
    metadata = json.loads((ticket / ".ico_metadata.json").read_text(encoding="utf-8"))
    metadata["project_path"] = str(stale_root)
    write_json(ticket / ".ico_metadata.json", metadata)
    started = run_tool(env, "start", "--workspace", root, ticket)
    assert Path(started["crosscheck_dir"]).parent == root / ".icode_output" / ".crosscheck"
    assert not (stale_root / ".icode_output" / ".crosscheck").exists()


def test_manifest_paths_and_container_symlinks_fail_closed(workspace):
    root, _ticket, env = workspace
    started = run_tool(env, "start", "--workspace", root, "--ticket", "demo-1")
    directory = Path(started["crosscheck_dir"])
    manifest_path = directory / "crosscheck_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rounds"][0]["fresh_file"] = "../outside.json"
    write_json(manifest_path, manifest)
    denied = run_tool(env, "validate", "--dir", directory, expected=1)
    assert denied["gate_id"] == "crosscheck_manifest_schema"

    manifest["rounds"][0]["fresh_file"] = "crosscheck_round_1.fresh.json"
    write_json(manifest_path, manifest)
    alias = directory.parent / ".icode_output_2"
    alias.symlink_to(directory, target_is_directory=True)
    denied_alias = run_tool(env, "validate", "--dir", alias, expected=1)
    assert denied_alias["gate_id"] == "crosscheck_symlink"
