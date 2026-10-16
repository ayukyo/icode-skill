"""HTTP confirmation origin and payload boundary; no device or Git mutation."""
import http.client
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))
from icode_agent import ui_server
from test_agent_ui_manager_v2 import dashboard


class StageControl:
    def __init__(self):
        self.calls = []

    def stage_projection(self, out_dir):
        self.calls.append(("projection", Path(out_dir)))
        return {"enabled": True, "program_id": "program", "stage_id": "first",
                "state": "awaiting_confirmation", "blocked": False, "reasons": [],
                "revision": "a" * 64, "generation": 1,
                "challenge": {"challenge_id": "challenge", "facts_digest": "b" * 64,
                              "report_sha256": "c" * 64},
                "report_markdown": "# Acceptance\n<script>untrusted report text</script>"}

    def confirm_stage(self, out_dir, request, *, trusted_confirmation):
        receipt = trusted_confirmation(request)
        self.calls.append(("confirm", Path(out_dir), request, receipt))
        assert receipt["source"] == "ui"
        assert receipt["intent"] == {"confirm": "确认本阶段验收", "reject": "拒绝本阶段验收",
                                    "revoke": "撤销本阶段验收"}[request["decision"]]
        return {"ok": True, "decision": request["decision"]}


def payload(**updates):
    return {"ticket_id": "M2-UI-1", "program_id": "program", "stage_id": "first",
            "challenge_id": "challenge", "facts_digest": "b" * 64,
            "report_sha256": "c" * 64, "revision": "a" * 64,
            "decision": "confirm", "request_id": "stage-ui-1", **updates}


def request(server, method, path, data=None, *, origin=True, token=True):
    host, port = server.server_address
    headers = {"Host": f"{host}:{port}", "Content-Type": "application/json"}
    if token:
        headers["X-ICODE-UI-Token"] = server.ui_token
    if origin:
        headers["Origin"] = f"http://{host}:{port}" if origin is True else origin
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
    response = conn.getresponse()
    result = response.status, json.loads(response.read())
    conn.close()
    return result


def test_stage_projection_and_report_are_available_for_review(tmp_path):
    fake = StageControl()
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake, create=True), dashboard(tmp_path) as server:
        status, result = request(server, "GET", "/api/v1/tickets/M2-UI-1")
        assert status == 200
        assert result["ticket"]["stage"]["challenge"]["challenge_id"] == "challenge"
        assert result["ticket"]["stage"]["report_markdown"].startswith("# Acceptance")


@pytest.mark.parametrize("origin,token", [(False, True), ("https://foreign.invalid", True), (True, False)])
def test_confirmation_requires_explicit_loopback_origin_and_session_token(tmp_path, origin, token):
    fake = StageControl()
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake, create=True), dashboard(tmp_path) as server:
        status, _ = request(server, "POST", "/api/v1/stages/decision", payload(), origin=origin, token=token)
        assert status == 403
        assert not any(call[0] == "confirm" for call in fake.calls)


def test_confirmation_uses_catalog_root_and_server_generated_source(tmp_path):
    fake = StageControl()
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake, create=True), dashboard(tmp_path) as server:
        status, result = request(server, "POST", "/api/v1/stages/decision", payload())
        assert status == 200 and result["ok"]
        call = next(call for call in fake.calls if call[0] == "confirm")
        assert call[1] == (tmp_path / "project/.icode_output/.icode_output_1").resolve()
        assert call[3]["source_id"] == f"ui:{server.ui_instance_id}:stage-ui-1"
        assert server.ui_token not in json.dumps(call[2:])


@pytest.mark.parametrize("extra", [{"dir": "/external"}, {"actor": "human"}, {"source_id": "forged"},
                                   {"decision": "override"}, {"request_id": ""}])
def test_browser_cannot_inject_authority_or_paths(tmp_path, extra):
    fake = StageControl()
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake, create=True), dashboard(tmp_path) as server:
        status, _ = request(server, "POST", "/api/v1/stages/decision", payload(**extra))
        assert status == 400
        assert not any(call[0] == "confirm" for call in fake.calls)


@pytest.mark.parametrize("decision", ["reject", "revoke"])
def test_reject_and_revoke_keep_distinct_ui_intent(tmp_path, decision):
    fake = StageControl()
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake, create=True), dashboard(tmp_path) as server:
        status, result = request(server, "POST", "/api/v1/stages/decision", payload(decision=decision))
        assert status == 200 and result["decision"] == decision


def test_report_is_rendered_as_text_and_confirmation_is_explicit():
    html = (ROOT / "agent_runtime/icode_agent/ui_assets/index.html").read_text(encoding="utf-8")
    script = (ROOT / "agent_runtime/icode_agent/ui_assets/app.js").read_text(encoding="utf-8")
    assert 'id="stage-report"' in html
    assert 'id="stage-confirm-button"' in html
    assert 'byId("stage-report").textContent' in script
    assert '"/api/v1/stages/decision"' in script
    assert "确认本阶段验收" in script
