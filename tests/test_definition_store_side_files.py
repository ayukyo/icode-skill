"""Content-addressed manifest side store (normal init keeps references only).

The ledger, journal and metadata keep ``$icode_manifest_ref`` slots; the first
definition is written once to ``.ico_storage/definitions/<kind>/<sha256>.json``.
These tests cover the birth path, later definition growth, fail-closed reads,
legacy inline compatibility and archive inclusion.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILE_COUNT = 400
MARKER = "$icode_manifest_ref"


def control():
    name = "definition_store_control"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools/icode_control.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def snapshot():
    candidate = control().candidate_module()
    value = dict(version=1, identity=dict(common_git_dir=str(ROOT / ".git"),
        checkout_realpath=str(ROOT), object_format="sha1", common_git_device=1,
        common_git_inode=1, checkout_device=1, checkout_inode=1), base="1"*40,
        head="2"*40, head_tree="3"*40, code_files=["a.py"], scope_contract=None,
        inspection_scope={}, excluded_side_effects=[],
        control_artifact_exclusions=list(candidate.CONTROL_DIRS),
        manifest=[dict(path="a.py", kind="file", mode="100644", sha256="4"*64,
            size=1, git_oid="5"*40)], index_manifest=[dict(path="a.py", mode="100644",
            oid="5"*40)], untracked_manifest=[])
    keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
            "excluded_side_effects", "control_artifact_exclusions", "manifest")
    value["content_id"] = digest({k: value[k] for k in keys})
    value["candidate_id"] = digest(value)
    assert candidate.snapshot_identity_ok(value)
    return value


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8")


def run_ctl(workdir, *args):
    env = dict(os.environ, ICODE_CONTROL_TEST_MODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(ROOT / "tools/icode_control.py"), *args],
                          cwd=str(workdir), env=env, capture_output=True, encoding="utf-8")


def make_repo(directory):
    directory.mkdir()
    git(directory, "init", "-b", "main")
    git(directory, "config", "user.email", "store@example.com")
    git(directory, "config", "user.name", "store")
    for index in range(FILE_COUNT):
        (directory / f"module_{index:03d}_with_a_long_predictable_name.py").write_text(
            f"VALUE = {index}\n", encoding="utf-8")
    git(directory, "add", ".")
    git(directory, "commit", "-m", "init")
    return directory


def physical_metadata(ticket):
    return json.loads((ticket / ".ico_metadata.json").read_text(encoding="utf-8"))


def event_lines(ticket):
    return (ticket / ".ico_events.jsonl").read_text(encoding="utf-8").splitlines()


def definition_files(ticket, kind):
    return sorted((ticket / ".ico_storage" / "definitions" / kind).glob("*.json"))


@pytest.fixture
def ticket(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for the birth path")
    repo = make_repo(tmp_path / "repo")
    directory = repo / ".icode_output" / ".icode_output_1"
    directory.mkdir(parents=True)
    result = run_ctl(repo, "create", "--dir", str(directory), "--ticket-id", "store-1",
                     "--requirement", "definition store", "--birth", "init",
                     "--request-id", "birth-1")
    assert result.returncode == 0, result.stderr + result.stdout
    return directory


def test_init_first_event_keeps_references_only(ticket):
    lines = event_lines(ticket)
    assert len(lines) == 1
    # 400 files put both manifests well over 100 KiB when inlined.
    assert len(lines[0].encode("utf-8")) < 100_000
    event = json.loads(lines[0])["value"]
    snap = event["payload"]["candidate_tracking"]["current"]
    for slot in ("manifest", "index_manifest", "untracked_manifest"):
        assert set(snap[slot]) == {MARKER}

    storage = control().storage_module()
    for slot in ("manifest", "index_manifest", "untracked_manifest"):
        ref = snap[slot][MARKER]
        path = ticket / ".ico_storage" / "definitions" / ref["kind"] / (ref["sha256"] + ".json")
        assert path.is_file()
        data = path.read_bytes()
        assert storage._manifest_digest_bytes(ref["kind"], data) == ref["sha256"]
    rows = json.loads((ticket / ".ico_storage" / "definitions" / "file_manifest"
                       / (snap["manifest"][MARKER]["sha256"] + ".json")).read_bytes())
    assert len(rows) == FILE_COUNT
    # The empty untracked manifest is a definition too; nothing is inlined.
    assert len(definition_files(ticket, "file_manifest")) == 2

    stored = physical_metadata(ticket)
    assert stored["storage_version"] == 2
    assert set(stored["value"]["candidate_tracking"]["current"]["manifest"]) == {MARKER}


def test_readers_resolve_from_side_store(ticket):
    ctl = control()
    meta = ctl.read_metadata_readonly(ticket)
    assert len(meta["candidate_tracking"]["current"]["manifest"]) == FILE_COUNT
    events, _ = ctl.read_events(ticket)
    assert events[0]["event_type"] == "ticket_created"
    assert ctl.verify_event_chain(ticket, meta)[1] == []
    # A store-less decode keeps failing closed instead of silently emptying
    # the manifest; the side store is not optional once refs are written.
    with pytest.raises(ctl.ControlError, match="missing or forward manifest reference"):
        ctl.decode_control_ledger([json.loads(line) for line in event_lines(ticket)])


def test_missing_definition_fails_closed(ticket):
    ctl = control()
    definition_files(ticket, "file_manifest")[0].unlink()
    with pytest.raises(ctl.ControlError, match="missing or forward manifest reference"):
        ctl.read_metadata_readonly(ticket)


def test_corrupt_definition_fails_closed(ticket):
    ctl = control()
    snap = json.loads(event_lines(ticket)[0])["value"]["payload"]["candidate_tracking"]["current"]
    target = ticket / ".ico_storage" / "definitions" / "file_manifest" \
        / (snap["manifest"][MARKER]["sha256"] + ".json")
    target.write_bytes(b"[]")
    with pytest.raises(ctl.ControlError, match="corrupt manifest definition side file"):
        ctl.read_metadata_readonly(ticket)


def test_candidate_capture_writes_new_definition(ticket):
    ctl = control()
    before = json.loads(event_lines(ticket)[0])["value"]["payload"]["candidate_tracking"]
    old_sha = before["current"]["manifest"][MARKER]["sha256"]
    repo = ticket.parents[1]
    modified = repo / "module_000_with_a_long_predictable_name.py"
    modified.write_text("VALUE = -1\n", encoding="utf-8")
    result = run_ctl(repo, "candidate", "--dir", str(ticket), "--phase", "capture",
                     "--request-id", "capture-2")
    assert result.returncode == 0, result.stderr + result.stdout

    lines = event_lines(ticket)
    new_sha = json.loads(lines[1])["value"]["payload"]["set"][
        "candidate_tracking"]["current"]["manifest"][MARKER]["sha256"]
    assert new_sha != old_sha
    assert (ticket / ".ico_storage" / "definitions" / "file_manifest"
            / (new_sha + ".json")).is_file()
    assert len(lines) == 2
    assert len(lines[1].encode("utf-8")) < 100_000
    snap = json.loads(lines[1])["value"]["payload"]["set"]["candidate_tracking"]["current"]
    assert set(snap["manifest"]) == {MARKER}
    stored = physical_metadata(ticket)
    assert set(stored["value"]["candidate_tracking"]["current"]["manifest"]) == {MARKER}

    meta = ctl.read_metadata_readonly(ticket)
    row = next(item for item in meta["candidate_tracking"]["current"]["manifest"]
               if item["path"] == "module_000_with_a_long_predictable_name.py")
    assert row["sha256"] == hashlib.sha256(modified.read_bytes()).hexdigest()


def test_legacy_inline_ledger_still_reads(tmp_path):
    ctl = control()
    storage = ctl.storage_module()
    directory = tmp_path / "legacy"
    directory.mkdir()
    snap = snapshot()
    meta = dict(schema_version=3, ticket_id="legacy-1",
                candidate_tracking={"version": 1, "current": snap})
    first = dict(schema_version=1, event_id=str(uuid.uuid4()), ticket_id="legacy-1",
                 timestamp="2026-11-20T10:00:00", actor="icode", event_type="external_note",
                 payload={"candidate": snap}, previous_event_hash=ctl.GENESIS_HASH)
    first["event_hash"] = ctl.canonical_event_hash(first)
    physical, registry = storage.encode_event(first, committed_registry=storage.Registry())
    assert isinstance(physical["value"]["payload"]["candidate"]["manifest"], list)
    encoded_meta = storage.encode_document(meta, kind="metadata", committed_registry=registry)
    (directory / ctl.METADATA_NAME).write_text(json.dumps(encoded_meta), encoding="utf-8")
    (directory / ctl.EVENTS_NAME).write_text(json.dumps(physical) + "\n", encoding="utf-8")
    assert not (directory / ".ico_storage").exists()
    restored = ctl.read_metadata_readonly(directory)
    assert restored["candidate_tracking"]["current"]["manifest"][0]["path"] == "a.py"


def test_archive_requires_and_records_side_store(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for the birth path")
    ctl = control()
    repo = make_repo(tmp_path / "repo")
    source = repo / ".icode_output" / ".icode_output_1"
    source.mkdir(parents=True)
    result = run_ctl(repo, "create", "--dir", str(source), "--ticket-id", "store-arch-1",
                     "--requirement", "archive", "--birth", "init",
                     "--request-id", "birth-arch")
    assert result.returncode == 0, result.stderr + result.stdout
    (source / "00_init.md").write_text("# init\n", encoding="utf-8")

    def copy_small(target, *, with_store):
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if item.is_file() and item.name not in {
                ".icontrol.lock", ctl.TXN_NAME, ctl.ARCHIVE_MANIFEST_NAME
            } and not item.name.endswith(".tmp") and item.stat().st_size <= 2 * 1024 * 1024:
                shutil.copy2(item, target / item.name)
        if with_store:
            shutil.copytree(source / ".ico_storage", target / ".ico_storage")

    missing = tmp_path / "archive_missing"
    copy_small(missing, with_store=False)
    failed = run_ctl(repo, "archive-manifest", "--dir", str(source),
                     "--archive-dir", str(missing), "--write", "--skip-linters")
    assert failed.returncode == 1
    assert ".ico_storage" in failed.stdout

    archive = tmp_path / "archive"
    copy_small(archive, with_store=True)
    written = run_ctl(repo, "archive-manifest", "--dir", str(source),
                      "--archive-dir", str(archive), "--write", "--skip-linters")
    assert written.returncode == 0, written.stderr + written.stdout
    manifest = json.loads((archive / ctl.ARCHIVE_MANIFEST_NAME).read_text(encoding="utf-8"))
    side_rows = [row for row in manifest["files"] if row["path"].startswith(".ico_storage/")]
    assert {row["path"] for row in side_rows} == ctl.definition_store_entries(source)

    # The archived copy must stay decodable from its own side store.
    assert ctl.read_metadata_readonly(archive)["ticket_id"] == "store-arch-1"
    side_rows_paths = [row["path"] for row in side_rows]
    (archive / side_rows_paths[0]).unlink()
    broken = run_ctl(repo, "archive-manifest", "--dir", str(source), "--archive-dir", str(archive))
    assert broken.returncode == 1
