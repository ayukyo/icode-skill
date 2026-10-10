import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("submission_inventory", ROOT / "scripts/submission_guard.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def inventory_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "source.py").write_text("value = 1\n")
    (repo / "lib.so").write_bytes(b"\0old")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    ticket = repo / ".icode_output/.icode_output_1"
    ticket.mkdir(parents=True)
    meta = {"ticket_id": "inv-1", "project_path": str(repo), "code_files": ["source.py"]}
    path = ticket / ".ico_metadata.json"
    path.write_text(json.dumps(meta))
    return repo, path, meta


def inventory(path, meta):
    assert callable(getattr(guard, "build_submission_inventory", None)), "missing read-only inventory"
    return guard.build_submission_inventory(meta, path)


def test_inventory_requires_explicit_generated_exclusions_and_unknown_scope(inventory_repo):
    repo, path, meta = inventory_repo
    (repo / "source.py").write_text("value = 2\n")
    (repo / "other.py").write_text("outside declared scope\n")
    (repo / "lib.so").write_bytes(b"\0new")
    result = inventory(path, meta)
    assert [r["path"] for r in result["intended"]] == ["source.py"]
    assert {r["path"] for r in result["unknown"]} == {"other.py", "lib.so"}
    assert "lib.so" in result["suggested_exclusions"] and not result["ok"]
    # Free text handoff exclusions are observations, not candidate authorization.
    meta["extensions"] = {"handoff": {"inputs": [{"excluded_paths": [{"path": "other.py", "reason": "skip"}]}]}}
    assert not inventory(path, meta)["ok"]
    meta["excluded_side_effects"] = ["lib.so"]
    result = inventory(path, meta)
    assert [r["path"] for r in result["side_effects"] if r["reason"] == "explicit_candidate_exclusion"] == ["lib.so"]
    assert {r["path"] for r in result["unknown"]} == {"other.py"}
    assert (repo / "lib.so").read_bytes() == b"\0new"


def test_wide_inspection_scope_does_not_authorize_generated_binary(inventory_repo):
    repo, path, meta = inventory_repo
    (path.parent / "code_worklist.json").write_text(json.dumps({"scopes": ["."], "related": [], "baselines": {}}))
    (repo / "lib.so").write_bytes(b"\0new")
    result = inventory(path, meta)
    assert "lib.so" in {r["path"] for r in result["unknown"]} and not result["ok"]


def test_inventory_preserves_z_rename_delete_mode_binary_and_both_status_columns(inventory_repo):
    repo, path, meta = inventory_repo
    special = "renamed\n name.py"
    git(repo, "mv", "source.py", special)
    (repo / special).write_text("value = 2\n")
    os.chmod(repo / special, 0o755)
    git(repo, "rm", "lib.so")
    meta["code_files"] = ["source.py", special, "lib.so"]
    rows = {r["path"]: r for r in inventory(path, meta)["intended"]}
    assert rows[special]["original_path"] == "source.py"
    assert rows[special]["staged"] == "R" and rows[special]["unstaged"] == "M"
    assert rows[special]["mode_changed"] is True
    assert rows["lib.so"]["staged"] == "D" and rows["lib.so"]["binary"] is True


def test_submodule_dirty_paths_are_not_silently_omitted(inventory_repo, tmp_path):
    repo, path, meta = inventory_repo
    sub = tmp_path / "sub"
    sub.mkdir()
    git(sub, "init", "-q")
    git(sub, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "base")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "submodule")
    (repo / "vendor/unknown.cpp").write_text("changed\n")
    result = inventory(path, meta)
    assert any(r["path"] == "vendor/unknown.cpp" for r in result["unknown"])
    assert not result["ok"]


def test_in_place_submit_check_has_inventory_without_submission_contract(inventory_repo):
    repo, path, _ = inventory_repo
    (repo / "unknown.cpp").write_text("changed\n")
    before = path.read_bytes()
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/submission_guard.py"),
                           "submit-check", "--metadata", str(path)], capture_output=True, text=True)
    assert proc.returncode == 2 and "unknown.cpp" in proc.stdout
    assert path.read_bytes() == before


def test_enabled_submit_check_requires_real_review_and_verification(inventory_repo):
    repo, old_path, _ = inventory_repo
    ticket = repo / ".icode_output/.icode_output_2"
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"), "create", "--dir", str(ticket),
                    "--ticket-id", "enabled-1", "--birth", "plan", "--requirement", "candidate",
                    "--metadata-json", '{"code_files":["source.py"]}'], capture_output=True, check=True)
    path = ticket / ".ico_metadata.json"
    before = path.read_bytes(), (ticket / ".ico_events.jsonl").read_bytes()
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/submission_guard.py"), "submit-check", "--metadata", str(path)],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "review" in proc.stdout
    assert before == (path.read_bytes(), (ticket / ".ico_events.jsonl").read_bytes())


def test_submit_check_never_recovers_pending_transaction(inventory_repo):
    repo, path, _ = inventory_repo
    transaction = path.parent / ".icontrol_txn.json"
    transaction.write_text('{"unfinished":true}')
    before = path.read_bytes(), transaction.read_bytes()
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/submission_guard.py"),
                           "submit-check", "--metadata", str(path)], capture_output=True, text=True)
    assert proc.returncode == 2 and "pending_transaction" in proc.stdout
    assert before == (path.read_bytes(), transaction.read_bytes())


def test_rename_into_excluded_build_directory_does_not_hide_source_deletion(inventory_repo):
    repo, path, meta = inventory_repo
    (repo / "build").mkdir()
    git(repo, "mv", "source.py", "build/source.py")
    meta["excluded_side_effects"] = ["build"]
    result = inventory(path, meta)
    assert not result["ok"] and "build/source.py" in {row["path"] for row in result["unknown"]}
