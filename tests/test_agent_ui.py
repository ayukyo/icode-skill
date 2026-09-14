import http.client
import json
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))

from icode_agent.backends.base import BackendError
from icode_agent.backends.fake import FakeBackend
from icode_agent.ui_server import MAX_REQUEST_BYTES, create_ui_server


CONTROL = ROOT / "tools" / "icode_control.py"


def make_ticket(tmp_path: Path, name: str = "agent-ui-1") -> Path:
    out_dir = tmp_path / ".icode_output" / ".icode_output_1"
    subprocess.run(
        [
            sys.executable,
            str(CONTROL),
            "create",
            "--dir",
            str(out_dir),
            "--ticket-id",
            name,
            "--requirement",
            "agent ui test",
            "--birth",
            "init",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return out_dir


@contextmanager
def running_ui(ticket_dir: Path, backend_factory=None):
    server = create_ui_server(
        ticket_dir=ticket_dir,
        control_path=CONTROL,
        port=0,
        backend_factory=backend_factory,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(server, method, path, *, body=None, headers=None):
    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    final_headers = {"Host": f"{host}:{port}"}
    final_headers.update(headers or {})
    conn.request(method, path, body=body, headers=final_headers)
    response = conn.getresponse()
    raw = response.read()
    result = response.status, dict(response.getheaders()), raw
    conn.close()
    return result


def json_request(server, method, path, payload, *, token=True, headers=None):
    final_headers = {"Content-Type": "application/json"}
    if token:
        final_headers["X-ICODE-UI-Token"] = server.ui_token
    final_headers.update(headers or {})
    status, response_headers, raw = request(
        server,
        method,
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers=final_headers,
    )
    return status, response_headers, json.loads(raw)


def run_payload(**overrides):
    payload = {
        "backend": "fake",
        "model": "fake-v1",
        "capabilities": ["text"],
        "prompt": "Summarize bounded evidence",
        "instructions": "Return only evidence-backed facts.",
        "task_scope": "bounded UI test",
        "expected_artifact": "text response",
        "evidence_boundary": "synthetic ticket only",
        "join_condition": "non-empty response",
        "request_id": "ui-turn-1",
        "max_output_tokens": 512,
        "images": [],
        "fake_response": "offline UI result",
    }
    payload.update(overrides)
    return payload


def get_status(server):
    status, _, raw = request(server, "GET", "/api/v1/status")
    return status, json.loads(raw)


def test_index_assets_and_security_headers_are_local(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        status, headers, raw = request(server, "GET", "/")
        html = raw.decode("utf-8")

        assert status == 200
        assert 'meta name="icode-ui-token"' in html
        assert re.search(r'<meta name="icode-ui-token" content="[^"]+">', html)
        assert '<script src="/assets/app.js" defer></script>' in html
        assert '<link rel="stylesheet" href="/assets/style.css">' in html
        assert "http://" not in html and "https://" not in html
        assert headers["Cache-Control"] == "no-store"
        assert headers["Server"].startswith("ICODEAgentUI/0.4.0")
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert "https:" not in headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in headers

        js_status, js_headers, js = request(server, "GET", "/assets/app.js")
        css_status, css_headers, css = request(server, "GET", "/assets/style.css")
        assert js_status == 200 and js_headers["Content-Type"].startswith(
            "text/javascript"
        )
        assert css_status == 200 and css_headers["Content-Type"].startswith(
            "text/css"
        )
        assert b"innerHTML" not in js
        assert js and css


def test_status_and_health_use_versioned_trusted_projection(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        health_status, _, health_raw = request(server, "GET", "/api/v1/health")
        status_code, payload = get_status(server)

        health = json.loads(health_raw)
        assert health_status == 200
        assert health == {
            "ok": True,
            "schema_version": 1,
            "service": "icode-agent-ui",
            "instance_id": server.ui_instance_id,
            "mode": "fixed",
        }
        assert status_code == 200
        assert payload["ok"] is True
        assert payload["status"]["ticket_id"] == "agent-ui-1"
        assert payload["status"]["spawns"] == []
        assert "ticket_dir" not in json.dumps(payload)


def test_post_rejects_bad_host_origin_token_and_content_type(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        payload = run_payload()

        status, _, response = json_request(
            server, "POST", "/api/v1/run", payload, token=False
        )
        assert status == 403 and response["error_class"] == "UIAccessError"

        status, _, response = json_request(
            server,
            "POST",
            "/api/v1/run",
            payload,
            headers={"Host": "attacker.invalid"},
        )
        assert status == 421 and response["error_class"] == "UIAccessError"

        status, _, response = json_request(
            server,
            "POST",
            "/api/v1/run",
            payload,
            headers={"Origin": "https://attacker.invalid"},
        )
        assert status == 403 and response["error_class"] == "UIAccessError"

        status, _, raw = request(
            server,
            "POST",
            "/api/v1/run",
            body=b"backend=fake",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-ICODE-UI-Token": server.ui_token,
            },
        )
        assert status == 415
        assert json.loads(raw)["error_class"] == "UIRequestError"

        status, headers, _ = request(server, "OPTIONS", "/api/v1/run")
        assert status == 405
        assert "Access-Control-Allow-Origin" not in headers


def test_post_rejects_oversize_unknown_fields_and_ticket_path(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        status, _, raw = request(
            server,
            "POST",
            "/api/v1/run",
            body=b"x" * (MAX_REQUEST_BYTES + 1),
            headers={
                "Content-Type": "application/json",
                "X-ICODE-UI-Token": server.ui_token,
            },
        )
        assert status == 413
        assert json.loads(raw)["error_class"] == "UIRequestError"

        status, _, response = json_request(
            server,
            "POST",
            "/api/v1/run",
            run_payload(ticket_dir="/tmp/escape"),
        )
        assert status == 400
        assert "ticket_dir" in response["error"]

        status_code, payload = get_status(server)
        assert status_code == 200
        assert payload["status"]["spawns"] == []


def test_fake_run_succeeds_and_updates_status(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        status, headers, response = json_request(
            server, "POST", "/api/v1/run", run_payload()
        )

        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        assert response["ok"] is True
        assert response["backend"] == "fake"
        assert response["result"]["output_text"] == "offline UI result"
        assert "ticket_dir" not in json.dumps(response)

        _, status_payload = get_status(server)
        spawn = status_payload["status"]["spawns"][0]
        assert spawn["result"] == "joined"
        assert spawn["backend"] == "fake"


def test_text_only_image_is_rejected_before_spawn(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        status, _, response = json_request(
            server,
            "POST",
            "/api/v1/run",
            run_payload(images=["data:image/png;base64,AAAA"]),
        )

        assert status == 400
        assert response["error_class"] == "CapabilityError"
        _, status_payload = get_status(server)
        assert status_payload["status"]["spawns"] == []


def test_duplicate_request_is_conflict_and_never_replays(tmp_path):
    with running_ui(make_ticket(tmp_path)) as server:
        first, _, _ = json_request(
            server, "POST", "/api/v1/run", run_payload()
        )
        second, _, response = json_request(
            server, "POST", "/api/v1/run", run_payload()
        )

        assert first == 200
        assert second == 409
        assert response["error_class"] == "LifecycleAmbiguityError"
        _, status_payload = get_status(server)
        assert len(status_payload["status"]["spawns"]) == 1


def test_backend_failure_maps_to_502_and_records_terminal_failure(tmp_path):
    backend = FakeBackend(
        error=BackendError("synthetic provider timeout", code="provider_timeout")
    )

    def backend_factory(_backend_name, _payload):
        return backend

    with running_ui(
        make_ticket(tmp_path), backend_factory=backend_factory
    ) as server:
        status, _, response = json_request(
            server, "POST", "/api/v1/run", run_payload()
        )

        assert status == 502
        assert response["error_class"] == "BackendError"
        assert "synthetic provider timeout" not in response["error"]
        _, status_payload = get_status(server)
        spawn = status_payload["status"]["spawns"][0]
        assert spawn["result"] == "failed"
        assert spawn["adopted"] == "no"
        assert spawn["error_class"] == "provider_timeout"


@pytest.mark.parametrize("bad_port", [-1, 65536])
def test_server_rejects_invalid_port_before_binding(tmp_path, bad_port):
    with pytest.raises(ValueError, match="port"):
        create_ui_server(
            ticket_dir=make_ticket(tmp_path),
            control_path=CONTROL,
            port=bad_port,
        )
