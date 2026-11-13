import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from test_delivery_inventory import ROOT, inventory_repo
from test_delivery_report import digest


def context_tool():
    path = ROOT / "tools/ticket_context.py"
    assert path.is_file(), "missing compact read-only ticket context"
    spec = importlib.util.spec_from_file_location("ticket_context_test", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_fresh_context_never_opens_old_crosscheck_or_emits_manifest(inventory_repo, monkeypatch):
    repo, path, meta = inventory_repo
    old = repo / ".icode_output/.crosscheck/old/findings.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"verdict":"PRIVATE_OLD_VERDICT","findings":["PRIVATE_OLD_FINDING"]}')
    meta["verification_runs"] = [{"run_id": "history", "evidence": "LARGE_OLD_BODY", "outcome": "pass"}]
    path.write_text(json.dumps(meta))
    before = digest(repo)
    tool = context_tool()
    real_open = Path.open
    def no_old_cc(item, *args, **kwargs):
        assert ".crosscheck" not in item.parts, "fresh context opened prior crosscheck"
        return real_open(item, *args, **kwargs)
    with monkeypatch.context() as isolated:
        isolated.setattr(Path, "open", no_old_cc)
        report = tool.build_ticket_context(path.parent, purpose="crosscheck_fresh")
    body = json.dumps(report)
    assert report["crosscheck"]["state"] == "withheld_for_fresh"
    assert all(value not in body for value in ("manifest", "LARGE_OLD_BODY", "PRIVATE_OLD", "verification_runs"))
    assert before == digest(repo)


def test_compact_context_rejects_unknown_purpose(inventory_repo):
    _, path, _ = inventory_repo
    with pytest.raises(ValueError, match="purpose"):
        context_tool().build_ticket_context(path.parent, purpose="typo")


def test_compact_context_bounds_latest_patch_file_list(inventory_repo):
    _, path, meta = inventory_repo
    meta["patch_history"] = [{"summary": "large patch", "files": [f"src/{i}.py" for i in range(1000)]}]
    path.write_text(json.dumps(meta))
    result = context_tool().build_ticket_context(path.parent, purpose="crosscheck_fresh")
    assert len(result["latest_patch"]["files"]) == 20
    assert result["latest_patch"]["file_count"] == 1000


def test_saved_overview_detects_metadata_and_live_candidate_staleness(inventory_repo):
    _, path, meta = inventory_repo
    tool = context_tool()
    snapshot = tool.build_ticket_context(path.parent)
    assert tool.overview_freshness(path.parent, snapshot)["state"] == "current"
    meta["patch_history"] = [{"description": "new patch"}]
    path.write_text(json.dumps(meta))
    assert tool.overview_freshness(path.parent, snapshot)["state"] == "stale"
    current = tool.build_ticket_context(path.parent)
    current["candidate"]["candidate_id"] = "changed-live-candidate"
    with patch.object(tool, "build_ticket_context", return_value=current):
        result = tool.overview_freshness(path.parent, snapshot)
    assert result["state"] == "stale"


def test_unknown_old_report_revision_is_untracked(inventory_repo):
    _, path, _ = inventory_repo
    assert context_tool().overview_freshness(path.parent, {"schema_version": 1})["state"] == "untracked"


def test_general_context_reports_persisted_overview_stale_after_patch(inventory_repo):
    _, path, meta = inventory_repo
    tool = context_tool()
    assert tool.build_ticket_context(path.parent)["overview"]["state"] == "missing"
    report = tool.delivery.build_delivery_report(path.parent)
    (path.parent / "delivery_validation_report.md").write_text(tool.delivery.markdown_report(report), encoding="utf-8")
    assert tool.build_ticket_context(path.parent)["overview"]["state"] == "current"
    meta["patch_history"] = [{"summary": "patch after completed", "at": "2026-11-13"}]
    path.write_text(json.dumps(meta))
    assert tool.build_ticket_context(path.parent)["overview"]["state"] == "stale"


def test_fresh_context_does_not_open_saved_overview(inventory_repo, monkeypatch):
    _, path, _ = inventory_repo
    overview = path.parent / "delivery_validation_report.md"
    overview.write_text("old crosscheck verdict/findings contaminate this report")
    tool = context_tool()
    real_open = Path.open
    def no_overview(item, *args, **kwargs):
        assert item != overview, "fresh context opened saved overview"
        return real_open(item, *args, **kwargs)
    with monkeypatch.context() as isolated:
        isolated.setattr(Path, "open", no_overview)
        result = tool.build_ticket_context(path.parent, purpose="crosscheck_fresh")
    assert result["overview"]["state"] == "withheld_for_fresh"


def test_saved_overview_detects_real_source_change_without_metadata_edit(inventory_repo):
    repo, _, _ = inventory_repo
    ticket = repo / ".icode_output/.icode_output_2"
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"), "create",
        "--dir", str(ticket), "--ticket-id", "context-live", "--birth", "plan",
        "--requirement", "current source identity"], check=True, capture_output=True)
    tool = context_tool()
    report = tool.delivery.build_delivery_report(ticket)
    saved = ticket / "delivery_validation_report.md"
    saved.write_text(tool.delivery.markdown_report(report), encoding="utf-8")
    assert tool.build_ticket_context(ticket)["overview"]["state"] == "current"
    (repo / "source.py").write_text("value = 2\n")
    context = tool.build_ticket_context(ticket)
    assert context["metadata_revision"] == report["metadata_revision"]
    assert context["candidate"]["candidate_id"] != report["candidate"]["candidate_id"]
    assert context["overview"]["state"] == "stale"
