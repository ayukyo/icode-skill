"""Real CLI/port regression for deterministic inspection gates."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "tools/icode_control.py"


def cli(out, *args, ok=True):
    proc = subprocess.run([sys.executable, str(CONTROL), *args, "--dir", str(out)],
                          env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"),
                          capture_output=True, text=True)
    assert (proc.returncode == 0) == ok, (proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


@pytest.fixture
def inspected(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.c").write_text("int a(void) { return 1; }\n")
    (tmp_path / "src/a.h").write_text("int a(void);\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "base"], check=True)
    out = tmp_path / ".icode_output/.icode_output_1"
    cli(out, "create", "--ticket-id", "inspect-1", "--requirement", "inspection",
        "--birth", "plan", "--metadata-json", json.dumps({"code_files": ["src/a.c"]}))
    (out / "03_plan_final.md").write_text("# final\n")
    cli(out, "step", "--step", "code", "--phase", "start", "--attempt", "inspect-a")
    cli(out, "step", "--step", "code", "--phase", "check", "--attempt", "inspect-a",
        "--boundary", "before_write")
    return tmp_path, out


@pytest.mark.parametrize("step", ["code", "deepcheck", "audit"])
def test_new_contract_requires_inspection_worklist(step):
    gates = json.loads((ROOT / "mcp/workflow-gate/gates.json").read_text())
    outputs = gates["execution_model"]["step_contracts"][step]["outputs"]
    assert any(p["id"] == "inspection_worklist" and p.get("required", True) for p in outputs)


def test_prepare_does_not_fabricate_read_and_receipts_are_valid(inspected):
    _, out = inspected
    result = cli(out, "inspection", "--step", "code", "--phase", "prepare",
                 "--attempt", "inspect-a", "--request", "prepare-1")
    report = json.loads(Path(result["path"]).read_text())
    assert all(not f["reads"] for u in report["units"] for f in u["files"])
    assert result["output"] == "inspection_worklist"
    cli(out, "inspection", "--step", "code", "--phase", "check", "--attempt", "inspect-a", ok=False)
    for unit in report["units"]:
        for item in unit["files"]:
            cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
                "--read-phase", "code_review", "--path", item["path"], "--request", "read-" + item["path"])
    assert cli(out, "inspection", "--step", "code", "--phase", "check", "--attempt", "inspect-a")["ok"]
    cli(out, "validate", "--skip-linters")


def test_prepare_reuses_pending_work_without_erasing_reads(inspected):
    _, out = inspected
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a")
    cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.c")
    path = out / "code_worklist.json"
    before = path.read_bytes()
    result = cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a")
    assert result["resumed"] and path.read_bytes() == before


def test_read_cannot_accept_source_drift(inspected):
    root, out = inspected
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a")
    (root / "src/a.c").write_text("int a(void) { return 2; }\n")
    result = cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
                 "--read-phase", "code_review", "--path", "src/a.c", ok=False)
    assert result["gate_id"] == "inspection_worklist"


def test_worklist_writer_rejects_foreign_or_finished_attempt(inspected):
    _, out = inspected
    cli(out, "inspection", "--step", "audit", "--phase", "prepare", "--attempt", "inspect-a", ok=False)
    cli(out, "step", "--step", "code", "--phase", "finish", "--attempt", "inspect-a",
        "--outcome", "blocked", "--evidence", "fixture")
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a", ok=False)


def test_request_conflict_does_not_modify_worklist(inspected):
    _, out = inspected
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a", "--request", "shared-key")
    path = out / "code_worklist.json"
    before = path.read_bytes()
    result = cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.c", "--request", "shared-key", ok=False)
    assert result["gate_id"] == "idempotency_conflict"
    assert path.read_bytes() == before


def test_scope_declaration_cannot_shrink_after_receipt(inspected):
    _, out = inspected
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a")
    path = out / "code_worklist.json"
    report = json.loads(path.read_text())
    report["max_files"] = 1
    path.write_text(json.dumps(report))
    failed = cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.c", ok=False)
    assert failed["gate_id"] == "inspection_worklist"


def test_prepare_request_replay_and_read_request_replay(inspected):
    _, out = inspected
    for phase, extra in (("prepare", []), ("read", ["--read-phase", "code_review", "--path", "src/a.c"])):
        first = cli(out, "inspection", "--step", "code", "--phase", phase, "--attempt", "inspect-a", "--request", phase, *extra)
        second = cli(out, "inspection", "--step", "code", "--phase", phase, "--attempt", "inspect-a", "--request", phase, *extra)
        assert second["already_applied"] and first["event_id"] == second["event_id"]


def test_read_replay_preserves_later_progress_and_binds_source_path(inspected):
    _, out = inspected
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a", "--request", "prep")
    first = cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.c", "--request", "read-a")
    cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.h", "--request", "read-b")
    before = (out / "code_worklist.json").read_bytes()
    replay = cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.c", "--request", "read-a")
    assert replay["event_id"] == first["event_id"] and replay["already_applied"]
    assert (out / "code_worklist.json").read_bytes() == before
    bad = cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--path", "src/a.h", "--request", "read-a", ok=False)
    assert bad["gate_id"] == "idempotency_conflict"


def complete_code(out):
    result = cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a")
    report = json.loads(Path(result["path"]).read_text())
    for unit in report["units"]:
        for item in unit["files"]:
            (Path(report["workspace"]) / item["path"]).read_text()
            cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
                "--read-phase", "code_review", "--path", item["path"])
    (out / "04_code_review_fix.md").write_text("# Actual fixture review\n")
    for path, scope in (("src/a.c", "workspace"), ("04_code_review_fix.md", "ticket")):
        cli(out, "artifact", "--step", "code", "--attempt", "inspect-a", "--path", path, "--scope", scope)
    for boundary in ("after_wait", "before_transition"):
        cli(out, "step", "--step", "code", "--phase", "check", "--attempt", "inspect-a", "--boundary", boundary)


def test_real_finish_and_tampered_hash_receipt(inspected):
    _, out = inspected
    complete_code(out)
    cli(out, "step", "--step", "code", "--phase", "finish", "--attempt", "inspect-a",
        "--outcome", "success", "--evidence", "actual fixture review")
    assert cli(out, "check-outputs", "--step", "code")["ok"]
    path = out / "code_worklist.json"
    report = json.loads(path.read_text())
    report["findings"] = [{"finding_id": "F1", "locations": [], "category": "design", "evidence_boundary": "new unreceipted suggestion"}]
    path.write_text(json.dumps(report))
    failed = cli(out, "check-outputs", "--step", "code", ok=False)
    assert any("receipt" in item for item in failed["violations"])


def test_degraded_does_not_allow_source_drift(inspected):
    root, out = inspected
    complete_code(out)
    (root / "src/a.c").write_text("int a(void) { return 2; }\n")
    failed = cli(out, "step", "--step", "code", "--phase", "finish", "--attempt", "inspect-a",
        "--outcome", "degraded", "--evidence", "budget", ok=False)
    assert failed["gate_id"] == "inspection_worklist"


def test_archive_cannot_drop_new_worklist_after_source_is_gone(inspected, tmp_path):
    _, out = inspected
    complete_code(out)
    archive = tmp_path / "archive"
    cli(out, "step", "--step", "code", "--phase", "finish", "--attempt", "inspect-a",
        "--outcome", "success", "--evidence", "actual fixture review")
    cli(out, "metadata-update", "--set-json", json.dumps(dict(archive_path=str(archive))))
    shutil.copytree(out, archive)
    cli(out, "archive-manifest", "--archive-dir", str(archive), "--write", "--skip-linters")
    path = archive / "archive_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"] = [f for f in manifest["files"] if f["path"] != "code_worklist.json"]
    path.write_text(json.dumps(manifest))
    (archive / "code_worklist.json").unlink()
    shutil.rmtree(out)
    failed = cli(archive, "archive-manifest", "--archive-dir", str(archive), ok=False)
    assert any("code_worklist.json" in problem for problem in failed["problems"])


@pytest.mark.parametrize("step", ["04_code.md", "05_deepcheck.md", "06_audit.md", "crosscheck.md"])
def test_all_entry_points_use_shared_inspection_contract(step):
    assert "inspection_worklist.md" in (ROOT / "steps" / step).read_text()
