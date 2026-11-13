from contextlib import contextmanager
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from test_agent_ui_manager_v2 import make_ticket, DashboardRunner, ROOT, CONTROL
from icode_agent.ui_server import AgentUIService, AgentUIInMemoryDispatcher
from icode_agent.doc_catalog import DocCatalog, DocLibraryError, MAX_CHAPTER_BYTES


@contextmanager
def dashboard(tmp_path):
    project = tmp_path / "project"
    out = make_ticket(project)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"version": 1, "tickets": [{"control_schema_version": 3,
        "ticket_id": "M2-UI-1", "project_path": str(project),
        "out_dir": out.relative_to(project).as_posix(), "status": "init_in_progress"}]}), encoding="utf-8")
    service = AgentUIService(None, CONTROL, index_path=index,
        settings_path=tmp_path / "settings.json", host_runner=DashboardRunner(), seed_project_paths=[project])
    yield AgentUIInMemoryDispatcher(service)


def request(server, method, path):
    response = server.dispatch(method, "https://icode.localhost" + path,
        {"X-ICODE-UI-Token": server.service.token})
    return response.status, json.loads(response.body)


def test_overview_first_and_material_reader_preserves_untrusted_markdown_as_text(tmp_path):
    with dashboard(tmp_path) as server:
        out = tmp_path / "project/.icode_output/.icode_output_1"
        (out / "delivery_validation_report.md").write_text('# 工单概览\n<script>PRIVATE_CODE</script>', encoding="utf-8")
        (out / "03_plan_final.md").write_text('# Final plan', encoding="utf-8")
        status, detail = request(server, "GET", "/api/v1/tickets/M2-UI-1")
        assert status == 200
        artifacts = detail["ticket"]["cockpit"]["artifacts"]
        assert artifacts[0]["name"] == "delivery_validation_report.md"
        assert artifacts[0]["title"] == "工单概览"
        status, payload = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/delivery_validation_report.md")
        assert status == 200 and "<script>PRIVATE_CODE</script>" in payload["material"]["markdown"]
        assert payload["material"]["freshness"]["state"] == "untracked"
        assert str(tmp_path) not in json.dumps(payload)
    script = (ROOT / "agent_runtime/icode_agent/ui_assets/app.js").read_text(encoding="utf-8")
    assert "工单概览" in script and "ticket-materials" in script and "preview.textContent" in script


def test_ticket_material_route_rejects_unlisted_paths_encodings_json_and_missing_auth(tmp_path):
    with dashboard(tmp_path) as server:
        out = tmp_path / "project/.icode_output/.icode_output_1"
        (out / "private.md").write_text("PRIVATE_CONTENT")
        for name in ("private.md", ".ico_metadata.json", "ticket_snapshot.json", "../private.md", "%2e%2e%2fprivate.md", "%2e%2e%5cprivate.md", "x%2f03_plan_final.md", "x%5c03_plan_final.md", "%252e%252e%252fprivate.md"):
            status, result = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/" + name)
            assert status in {400, 404}, (name, status, result)
            assert "PRIVATE_CONTENT" not in json.dumps(result)
        response = server.dispatch("GET", "https://icode.localhost/api/v1/ticket-materials/M2-UI-1/00_init.md", {})
        assert response.status == 403
        response = server.dispatch("GET", "https://icode.localhost/api/v1/ticket-materials/M2-UI-1/00_init.md",
            {"X-ICODE-UI-Token": server.service.token, "Origin": "https://untrusted.invalid"})
        assert response.status == 403


def test_ticket_material_reader_rejects_hardlink_and_oversize(tmp_path):
    with dashboard(tmp_path) as server:
        out = tmp_path / "project/.icode_output/.icode_output_1"
        outside = tmp_path / "private.md"
        outside.write_text("PRIVATE_CONTENT")
        (out / "03_plan_final.md").hardlink_to(outside)
        status, _ = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/03_plan_final.md")
        assert status == 404
        (out / "08_patch.md").write_bytes(b'x' * (MAX_CHAPTER_BYTES + 1))
        status, _ = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/08_patch.md")
        assert status == 413


def test_ticket_material_reader_rejects_symlink(tmp_path):
    with dashboard(tmp_path) as server:
        out = tmp_path / "project/.icode_output/.icode_output_1"
        outside = tmp_path / "private.md"
        outside.write_text("PRIVATE_CONTENT")
        try:
            (out / "06_audit.md").symlink_to(outside)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable; real link case runs on Linux")
            raise
        status, _ = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/06_audit.md")
        assert status == 404


def test_ticket_material_reader_rejects_reparse_and_hardlink_created_during_read(tmp_path):
    from types import SimpleNamespace
    with dashboard(tmp_path) as server:
        source = tmp_path / "project/.icode_output/.icode_output_1/00_init.md"
        real_stat = Path.lstat
        def reparse(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path == source else info
        with patch.object(Path, "lstat", reparse):
            status, payload = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/00_init.md")
            assert status == 404 and "初稿" not in json.dumps(payload, ensure_ascii=False)
        real_read = DocCatalog._read
        def link_during_read(reader, path, limit):
            content = real_read(reader, path, limit)
            (tmp_path / "new-alias.md").hardlink_to(path)
            return content
        with patch.object(DocCatalog, "_read", link_during_read):
            status, payload = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/00_init.md")
            assert status == 404 and "初稿" not in json.dumps(payload, ensure_ascii=False)


def test_ticket_material_reader_reuses_safe_reader_and_refuses_race(tmp_path):
    with dashboard(tmp_path) as server:
        with patch.object(DocCatalog, "_read", side_effect=DocLibraryError("读取期间发生变化", 409)) as reader:
            status, _ = request(server, "GET", "/api/v1/ticket-materials/M2-UI-1/00_init.md")
        assert status == 409 and reader.call_count == 1
