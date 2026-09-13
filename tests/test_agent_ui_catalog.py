import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))

from icode_agent.ticket_catalog import CatalogError, TicketCatalog
from icode_agent.ui_settings import DEFAULT_SETTINGS, SettingsError, UISettingsStore


CONTROL = ROOT / "tools" / "icode_control.py"


def make_ticket(project: Path, number: int, ticket_id: str, requirement: str) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    out_dir = project / ".icode_output" / f".icode_output_{number}"
    subprocess.run(
        [
            sys.executable,
            str(CONTROL),
            "create",
            "--dir",
            str(out_dir),
            "--ticket-id",
            ticket_id,
            "--requirement",
            requirement,
            "--birth",
            "init",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out_dir


def write_index(path: Path, tickets) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "updated_at": "2026-09-13T10:00:00+08:00", "tickets": tickets},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def entry(project: Path, number: int, ticket_id: str, **extra):
    value = {
        "control_schema_version": 3,
        "ticket_id": ticket_id,
        "project_path": str(project),
        "out_dir": f".icode_output/.icode_output_{number}",
        "status": "init_in_progress",
        "requirement_summary": f"summary for {ticket_id}",
        "updated_at": "2026-09-13T10:00:00+08:00",
    }
    value.update(extra)
    return value


def test_empty_or_missing_index_returns_empty_dashboard(tmp_path):
    missing = TicketCatalog(tmp_path / "missing.json")
    assert missing.snapshot() == {
        "schema_version": 1,
        "projects": [],
        "tickets": [],
        "errors": [],
    }

    index_path = tmp_path / "index.json"
    write_index(index_path, [])
    assert TicketCatalog(index_path).snapshot()["tickets"] == []


def test_seed_project_is_visible_without_index_and_resolves_by_opaque_id(tmp_path):
    project = tmp_path / "new-project"
    project.mkdir()
    catalog = TicketCatalog(
        tmp_path / "missing.json", seed_projects=[project, project])
    snapshot = catalog.snapshot()

    assert snapshot["tickets"] == []
    assert len(snapshot["projects"]) == 1
    public = snapshot["projects"][0]
    assert public["name"] == "new-project"
    assert public["ticket_count"] == 0
    assert str(tmp_path) not in json.dumps(snapshot, ensure_ascii=False)
    assert catalog.resolve_project(public["project_id"]) == project.resolve()

    with pytest.raises(CatalogError, match="项目"):
        catalog.resolve_project("project-not-found")


def test_catalog_groups_filters_and_never_exposes_paths(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    make_ticket(alpha, 1, "A-1", "camera startup")
    make_ticket(beta, 2, "B-2", "serial recovery")
    index_path = tmp_path / "index.json"
    write_index(index_path, [
        entry(alpha, 1, "A-1", status="plan_done",
              requirement_summary="camera startup"),
        entry(beta, 2, "B-2", status="code_in_progress",
              requirement_summary="serial recovery"),
    ])

    catalog = TicketCatalog(index_path)
    snapshot = catalog.snapshot()
    encoded = json.dumps(snapshot, ensure_ascii=False)

    assert [item["name"] for item in snapshot["projects"]] == ["alpha", "beta"]
    assert [item["ticket_id"] for item in snapshot["tickets"]] == ["B-2", "A-1"]
    assert str(tmp_path) not in encoded
    assert catalog.snapshot(project_id=snapshot["projects"][0]["project_id"])["tickets"][0]["ticket_id"] == "A-1"
    assert catalog.snapshot(query="serial")["tickets"][0]["ticket_id"] == "B-2"
    assert catalog.snapshot(status="plan_done")["tickets"][0]["ticket_id"] == "A-1"


def test_resolve_requires_unique_v3_identity_and_matching_metadata(tmp_path):
    project = tmp_path / "project"
    out_dir = make_ticket(project, 1, "SAFE-1", "identity test")
    index_path = tmp_path / "index.json"
    write_index(index_path, [entry(project, 1, "SAFE-1")])

    resolved = TicketCatalog(index_path).resolve("SAFE-1")
    assert resolved.out_dir == out_dir.resolve()
    assert resolved.project_path == project.resolve()
    assert resolved.executable is True

    metadata_path = out_dir / ".ico_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["ticket_id"] = "OTHER"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(CatalogError, match="身份不一致"):
        TicketCatalog(index_path).resolve("SAFE-1")


def test_legacy_duplicate_and_escape_entries_fail_closed(tmp_path):
    project = tmp_path / "project"
    out_dir = make_ticket(project, 1, "SAFE-1", "catalog test")
    index_path = tmp_path / "index.json"

    write_index(index_path, [{
        "ticket_id": "LEGACY-1",
        "project_path": str(project),
        "out_dir": str(out_dir),
        "status": "completed",
    }])
    legacy_snapshot = TicketCatalog(index_path).snapshot()
    assert legacy_snapshot["tickets"][0]["generation"] == "legacy"
    assert legacy_snapshot["tickets"][0]["executable"] is False
    with pytest.raises(CatalogError, match="legacy"):
        TicketCatalog(index_path).resolve("LEGACY-1")

    write_index(index_path, [entry(project, 1, "SAFE-1"), entry(project, 1, "SAFE-1")])
    duplicate = TicketCatalog(index_path).snapshot()
    assert duplicate["tickets"][0]["executable"] is False
    assert duplicate["errors"][0]["code"] == "duplicate_ticket_id"
    with pytest.raises(CatalogError, match="不唯一"):
        TicketCatalog(index_path).resolve("SAFE-1")

    write_index(index_path, [entry(project, 1, "ESCAPE-1", out_dir="../outside")])
    escaped = TicketCatalog(index_path).snapshot()
    assert escaped["tickets"][0]["executable"] is False
    with pytest.raises(CatalogError, match="路径"):
        TicketCatalog(index_path).resolve("ESCAPE-1")


def test_settings_defaults_roundtrip_and_atomic_replacement(tmp_path):
    path = tmp_path / "settings.json"
    store = UISettingsStore(path)
    assert store.load() == DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["refresh_interval_seconds"] == 30

    saved = store.save({
        "host": "claude",
        "model": "sonnet",
        "mode": "advanced",
        "fallback": False,
        "show_next_step": False,
        "preferred_port": 9012,
        "refresh_interval_seconds": 60,
    })
    assert saved["host"] == "claude"
    assert saved["refresh_interval_seconds"] == 60
    assert store.load() == saved
    assert not path.with_suffix(".json.tmp").exists()
    assert os.stat(path).st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"host": "other"}, "host"),
        ({"preferred_port": 70000}, "preferred_port"),
        ({"refresh_interval_seconds": 4}, "refresh_interval_seconds"),
        ({"refresh_interval_seconds": 301}, "refresh_interval_seconds"),
        ({"refresh_interval_seconds": True}, "refresh_interval_seconds"),
        ({"fallback": "yes"}, "fallback"),
        ({"api_key": "secret"}, "未登记字段"),
        ({"dangerous_confirmation": False}, "未登记字段"),
    ],
)
def test_settings_reject_unknown_or_invalid_values(tmp_path, payload, message):
    with pytest.raises(SettingsError, match=message):
        UISettingsStore(tmp_path / "settings.json").save(payload)


def test_corrupt_settings_fail_closed_without_overwrite(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(SettingsError, match="JSON"):
        UISettingsStore(path).load()
    assert path.read_bytes() == before
