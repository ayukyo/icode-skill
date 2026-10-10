import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_delivery_inventory import ROOT, inventory_repo


def delivery():
    path = ROOT / "tools/icode_delivery.py"
    assert path.is_file(), "missing read-only delivery report"
    spec = importlib.util.spec_from_file_location("delivery_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_report_has_neutral_optional_crosscheck_and_no_writes(inventory_repo):
    repo, path, meta = inventory_repo
    before = digest(repo)
    report = delivery().build_delivery_report(path.parent)
    assert report["read_only"] is True and report["derived"] is True
    assert report["candidate"]["state"] == "legacy_untracked"
    assert report["crosscheck"]["state"] == "not_run" and report["crosscheck"]["required"] is False
    assert report["verification"]["state"] == "untracked"
    assert "manifest" not in json.dumps(report)
    assert before == digest(repo)


def test_direct_binary_result_is_independent_of_runner_and_ci(inventory_repo):
    repo, path, meta = inventory_repo
    meta["verification_runs"] = [{"run_id": "direct", "outcome": "pass", "kind": "unit_test",
        "layer": "host", "consumer": "test", "scenario": "unit", "direct_test": {
        "target_built": True, "binary_executed": True, "binary_exit_code": 7,
        "test_runner_discovered": True, "ci_registered": True}}]
    path.write_text(json.dumps(meta))
    result = delivery().build_delivery_report(path.parent)
    direct = result["verification"]["runs"][0]["direct_test"]
    assert direct["binary_result"] == "fail" and direct["ci_registered"] is True
    meta["verification_runs"][0]["direct_test"].update(binary_executed=False, binary_exit_code=None)
    path.write_text(json.dumps(meta))
    assert delivery().build_delivery_report(path.parent)["verification"]["runs"][0]["direct_test"]["binary_result"] == "not_run"


def test_report_output_cannot_overwrite_source_or_ticket_artifacts(inventory_repo):
    repo, path, _ = inventory_repo
    tool = delivery()
    artifact = path.parent / "06_audit.md"
    artifact.write_text("original review")
    before = digest(repo)
    for output in (repo / "source.py", path, artifact, path.parent / "code_worklist.json"):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(path.parent),
                               "--output", str(output)], capture_output=True, text=True)
        assert proc.returncode == 1, proc.stdout + proc.stderr
    assert before == digest(repo)
    output = path.parent / "delivery_report.json"
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(path.parent),
                           "--output", str(output)], capture_output=True, text=True)
    assert proc.returncode == 0 and output.is_file()
    assert set(digest(repo)) - set(before) == {str(output.relative_to(repo))}


def test_pending_transaction_report_is_blocked_and_not_recovered(inventory_repo):
    repo, path, meta = inventory_repo
    transaction = path.parent / ".icontrol_txn.json"
    transaction.write_text('{"unfinished":true}')
    before = digest(repo)
    report = delivery().build_delivery_report(path.parent)
    assert report["ok"] is False and "pending_transaction" in report["issues"]
    assert before == digest(repo)


def test_enabled_report_captures_candidate_once_and_reuses_context(inventory_repo):
    repo, path, meta = inventory_repo
    ticket = repo / ".icode_output/.icode_output_2"
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"), "create", "--dir", str(ticket),
                    "--ticket-id", "report-1", "--birth", "plan", "--requirement", "one capture"], capture_output=True, check=True)
    tool = delivery()
    with patch.object(tool.ctl, "candidate_snapshot", wraps=tool.ctl.candidate_snapshot) as capture:
        tool.build_delivery_report(ticket)
        assert capture.call_count == 1
    # debt's legacy entry point must also accept the exact same optional context.
    import inspect
    assert "context" in inspect.signature(tool.debt.analyze_ticket).parameters


def test_named_report_formal_delivery_collision_and_hardlink_are_rejected(inventory_repo):
    repo, path, meta = inventory_repo
    formal = path.parent / "delivery_report_user.md"
    formal.write_text("formal deliverable")
    meta["delivery_files"] = {"report": formal.name, "brief": "08_brief.md"}
    path.write_text(json.dumps(meta))
    tool = delivery()
    before = digest(repo)
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(path.parent),
                           "--output", str(path.parent / "delivery_report.json"), "--markdown", str(formal)], capture_output=True, text=True)
    assert proc.returncode == 1 and before == digest(repo)
    alias = path.parent / "delivery_report_alias.json"
    alias.hardlink_to(repo / "source.py")
    before = digest(repo)
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(path.parent),
                           "--output", str(alias)], capture_output=True, text=True)
    assert proc.returncode == 1 and before == digest(repo)


def test_crosscheck_artifact_hardlink_is_protected_as_source(inventory_repo):
    repo, path, _ = inventory_repo
    original = repo / ".icode_output/.crosscheck/.icode_output_1/crosscheck_round_1.json"
    original.parent.mkdir(parents=True)
    original.write_text("original isolated review")
    alias = path.parent / "delivery_report_alias.json"
    alias.hardlink_to(original)
    before = digest(repo)
    delivery()
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(path.parent),
                           "--output", str(alias)], capture_output=True, text=True)
    assert proc.returncode == 1 and before == digest(repo)


def test_markdown_output_contains_independent_direct_and_provenance_facts(inventory_repo):
    repo, path, meta = inventory_repo
    meta["verification_runs"] = [{"run_id": "origin", "kind": "build", "outcome": "pass", "candidate_id": "source-id",
        "device": "SN=fixture", "artifact_identity": "sha256:fixture", "evidence": "build.log", "build_provenance": {
            "source_candidate_id": "original-source", "artifacts": [{"path": "app", "sha256": "artifact-sha"}], "evidence_refs": ["source.log"]},
        "direct_test": {"target_built": True, "binary_executed": False, "binary_exit_code": None,
                        "test_runner_discovered": True, "ci_registered": False}}]
    path.write_text(json.dumps(meta))
    output = path.parent / "delivery_validation_report.json"
    markdown = path.parent / "delivery_validation_report.md"
    delivery()
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(path.parent),
                           "--output", str(output), "--markdown", str(markdown)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    content = markdown.read_text()
    assert all(value in content for value in ("Target built", "Binary executed", "original-source", "artifact-sha", "SN=fixture", "source.log", "not_run"))


def test_controlled_artifact_receipt_protects_old_formal_report_after_role_change(inventory_repo):
    repo, _, _ = inventory_repo
    ticket = repo / ".icode_output/.icode_output_2"
    def control(*args):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"), *map(str, args)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)
    control("create", "--dir", ticket, "--ticket-id", "receipt-1", "--birth", "plan", "--requirement", "artifact protection",
            "--metadata-json", '{"delivery_files":{"report":"delivery_report_user.md","brief":"brief.md"}}')
    formal = ticket / "delivery_report_user.md"
    formal.write_text("original formal artifact")
    (ticket / "03_plan_final.md").write_text("Fixture plan: protect delivery artifacts from derived output collisions.\n")
    (ticket / "06_audit.md").write_text("Fixture audit: exercise writer provenance; no device validation claimed.\n")
    control("step", "--dir", ticket, "--step", "readme", "--phase", "start", "--attempt", "doc-1")
    control("step", "--dir", ticket, "--step", "readme", "--phase", "check", "--attempt", "doc-1", "--boundary", "before_write")
    control("artifact", "--dir", ticket, "--step", "readme", "--attempt", "doc-1", "--path", formal.name)
    control("metadata-update", "--dir", ticket, "--set-json", '{"delivery_files":{"report":"another.md","brief":"brief.md"}}', "--request-id", "role-change")
    before = digest(repo)
    delivery()
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_delivery.py"), "--dir", str(ticket),
                           "--output", str(ticket / "delivery_report.json"), "--markdown", str(formal)], capture_output=True, text=True)
    assert proc.returncode == 1 and before == digest(repo)
