import importlib.util
import json
import os
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace
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


@pytest.fixture
def lx_inventory(inventory_repo, monkeypatch):
    """Real Git index/HEAD; only the Windows reparse metadata reader is injected."""
    repo, path, meta = inventory_repo
    name, target = "library.so", b"library.so.1"
    link = repo / name
    link.write_bytes(target)
    git(repo, "config", "core.symlinks", "false")
    oid = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                         input=target, capture_output=True, check=True).stdout.decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", "120000," + oid + "," + name)
    git(repo, "commit", "-qm", "link fixture")
    candidate = guard.control_module().candidate_module()
    real_lstat, real_git = Path.lstat, guard._inventory_git
    state = {"target": target, "changed": False, "read_error": False, "replace": False,
             "false_status": False, "after_diff": False, "reads": 0}

    def lstat(item, *args, **kwargs):
        info = real_lstat(item, *args, **kwargs)
        if item != link:
            return info
        fields = {key: getattr(info, key) for key in (
            "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
        fields.update(st_reparse_tag=0xA000001D, st_mode=stat.S_IFREG | 0o644)
        fields["st_ino"] += int(state["changed"])
        return SimpleNamespace(**fields)

    def read_link(item, before):
        state["reads"] += 1
        if state["read_error"]:
            raise OSError("fixture reparse read unavailable")
        if state["replace"]:
            state["changed"] = True
        return state["target"]

    def read_git(root, *args):
        # Reproduce native Windows Git's inability to open LX links. Real Git
        # still supplies all objects/status/numstat for every other path.
        if args[0] == "diff" and "HEAD" in args and ":(literal,exclude)" + name not in args:
            raise candidate.CandidateError("git failed: diff; fixture LX open not implemented")
        raw = real_git(root, *args)
        if args[0] == "diff" and "HEAD" in args and state["after_diff"]:
            state["target"] = b"changed-after-scan.so"
        return raw + b" M library.so\0" if args[0] == "status" and state["false_status"] else raw

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(candidate, "_read_lx_symlink", read_link)
    monkeypatch.setattr(guard, "_inventory_git", read_git)
    return repo, path, meta, state, oid


@pytest.mark.parametrize("false_status", [False, True])
def test_inventory_checks_unchanged_lx_link_without_hiding_other_changes(lx_inventory, false_status):
    repo, path, meta, state, _ = lx_inventory
    state["false_status"] = false_status
    (repo / "source.py").write_text("value = 2\n")
    result = inventory(path, meta)
    assert result["ok"], result["violations"]
    assert [row["path"] for row in result["intended"]] == ["source.py"]


@pytest.mark.parametrize("change", ["target", "index-mode", "staged-target"])
def test_inventory_preserves_lx_target_index_mode_and_head_changes(lx_inventory, change):
    repo, path, meta, state, oid = lx_inventory
    meta["code_files"] = ["library.so"]
    if change == "target":
        state["target"] = b"different.so"
    elif change == "index-mode":
        git(repo, "update-index", "--cacheinfo", "100644," + oid + ",library.so")
    else:
        oid = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                             input=b"staged.so", capture_output=True, check=True).stdout.decode().strip()
        git(repo, "update-index", "--cacheinfo", "120000," + oid + ",library.so")
    result = inventory(path, meta)
    assert result["ok"], result["violations"]
    row = next(row for row in result["intended"] if row["path"] == "library.so")
    assert row["kind"] == "symlink" and row["mode_changed"] == (change == "index-mode")
    assert row["unstaged"] in {"M", "T"}
    if change != "target":
        assert row["staged"] in {"M", "T"}


@pytest.mark.parametrize("target_kind", ["relative-external", "absolute-external"])
def test_inventory_hashes_external_link_target_bytes_without_accessing_target(lx_inventory, monkeypatch, target_kind):
    repo, path, meta, state, _ = lx_inventory
    outside = repo.parent / "external-target.bin"
    outside.write_bytes(b"outside fixture data")
    state["target"] = b"../external-target.bin" if target_kind == "relative-external" else os.fsencode(outside)
    meta["code_files"] = ["library.so"]
    real_open = Path.open

    def no_target_access(item, *args, **kwargs):
        assert Path(os.path.abspath(item)) != outside, "inventory must not open a symlink target"
        return real_open(item, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_target_access)
    result = inventory(path, meta)
    assert result["ok"], result["violations"]
    row = next(row for row in result["intended"] if row["path"] == "library.so")
    assert row["kind"] == "symlink" and row["unstaged"] == "M" and state["reads"] >= 2


def test_inventory_rejects_linked_parent_before_reading_link_bytes(lx_inventory, monkeypatch):
    repo, path, meta, state, _ = lx_inventory
    candidate = guard.control_module().candidate_module()
    real_check = candidate._path_is_candidate_link
    monkeypatch.setattr(candidate, "_path_is_candidate_link", lambda item: item == repo or real_check(item))
    result = inventory(path, meta)
    assert not result["ok"] and any("symlink parent" in item for item in result["violations"])
    assert state["reads"] == 0


@pytest.mark.parametrize("fault", ["read_error", "replace", "after_diff"])
def test_inventory_never_exempts_unvalidated_lx_links(lx_inventory, fault):
    _, path, meta, state, _ = lx_inventory
    state[fault] = True
    result = inventory(path, meta)
    assert not result["ok"] and result["violations"]


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


def test_inventory_preserves_space_rename_delete_binary_and_both_status_columns(inventory_repo):
    repo, path, meta = inventory_repo
    special = "renamed name.py"
    git(repo, "mv", "source.py", special)
    (repo / special).write_text("value = 2\n")
    git(repo, "rm", "lib.so")
    meta["code_files"] = ["source.py", special, "lib.so"]
    rows = {r["path"]: r for r in inventory(path, meta)["intended"]}
    assert rows[special]["original_path"] == "source.py"
    assert rows[special]["staged"] == "R" and rows[special]["unstaged"] == "M"
    assert rows["lib.so"]["staged"] == "D" and rows["lib.so"]["binary"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX newline filename and executable mode require a POSIX filesystem")
def test_inventory_preserves_posix_newline_mode_binary_and_both_status_columns(inventory_repo):
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
