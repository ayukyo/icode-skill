"""Executable WebView2 host contract checks; no TCP listener is involved.

Set ICODE_NATIVE_HOST_EXE to a locally compiled Windows host executable.
"""

import base64
import json
import os
import queue
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "agent_runtime/icode_agent/windows_ui_host.cs"
ORIGIN = "https://icode.localhost"


@pytest.fixture
def host_exe():
    assert SOURCE.is_file(), "The native WebView2 pipe host has not been implemented"
    executable = os.environ.get("ICODE_NATIVE_HOST_EXE")
    if os.name != "nt" or not executable or os.environ.get("ICODE_RUN_NATIVE_UI_TESTS") != "1":
        pytest.skip("Requires Windows, ICODE_RUN_NATIVE_UI_TESTS=1 and ICODE_NATIVE_HOST_EXE")
    assert Path(executable).is_file()
    return executable


def test_invalid_origin_fails_before_creating_window(host_exe, tmp_path):
    message = {"type": "init", "origin": "https://example.com",
               "initial_path": "/", "user_data_dir": str(tmp_path / "profile"),
               "instance_id": "invalid-origin"}
    result = subprocess.run([host_exe], input=json.dumps(message) + "\n",
                            text=True, capture_output=True, timeout=15)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode != 0
    assert any(event.get("event") == "error" for event in events)
    assert not any(event.get("event") == "ready" for event in events)


def test_eof_before_init_exits_without_window(host_exe):
    result = subprocess.run([host_exe], input="", text=True,
                            capture_output=True, timeout=15)
    assert result.returncode != 0
    assert '"ready"' not in result.stdout


def test_oversize_jsonl_is_rejected_before_json_parse(host_exe):
    result = subprocess.run([host_exe], input=b"x" * (32 * 1024 * 1024 + 1) + b"\n",
                            capture_output=True, timeout=15)
    assert result.returncode != 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert any("32 MiB" in e.get("message", "") for e in events)
    assert not any(e.get("event") == "ready" for e in events)


def test_webview_environment_override_is_rejected_before_window(host_exe, tmp_path):
    environment = os.environ.copy()
    environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--disable-features=NativeHostTestSentinel"
    message = {"type": "init", "origin": ORIGIN, "initial_path": "/",
               "user_data_dir": str(tmp_path / "profile"), "instance_id": "override-test"}
    result = subprocess.run([host_exe], input=json.dumps(message) + "\n", env=environment,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert "WebView2 environment override is disabled" in result.stdout
    assert not (tmp_path / "profile").exists()


class PipeHost:
    def __init__(self, executable, tmp_path, steps):
        script = tmp_path / "test-script.json"
        script.write_text(json.dumps(steps), encoding="utf-8")
        self.process = subprocess.Popen(
            [executable, "--test-script=" + str(script)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.messages = queue.Queue()
        self.requests = []
        threading.Thread(target=self._read, daemon=True).start()
        self.send({"type": "init", "origin": ORIGIN, "initial_path": "/?view=docs",
                   "user_data_dir": str(tmp_path / "profile"), "instance_id": "native-test"})

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def run(self, routes, timeout=40):
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=0.1)
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            if message["type"] == "request":
                self.requests.append(message)
                path = message["url"].removeprefix(ORIGIN).split("?", 1)[0]
                route = routes.get(path, (b"missing", "text/plain"))
                if callable(route):
                    route = route(message)
                if route is None:
                    continue
                body, content_type = route[:2]
                headers = {"Content-Type": content_type,
                           "Content-Security-Policy": "default-src 'self'; script-src 'self'; frame-src 'self'",
                           "X-Content-Type-Options": "nosniff",
                           "Cross-Origin-Resource-Policy": "same-origin"}
                if len(route) > 2:
                    headers.update(route[2])
                self.send({"type": "response", "id": message["id"],
                           "status": 200 if path in routes else 404,
                           "headers": headers,
                           "body_base64": base64.b64encode(body).decode()})
            else:
                events.append(message)
        if self.process.poll() is None:
            self.process.kill()
            raise AssertionError("Native host did not exit within deadline")
        stderr = self.process.stderr.read()
        assert self.process.returncode == 0, stderr + repr(events)
        return events


def test_parent_eof_completes_inflight_navigation_and_exits(host_exe, tmp_path):
    host = PipeHost(host_exe, tmp_path, [{"action": "close"}])
    first = host.messages.get(timeout=15)
    assert first["type"] == "request" and first["url"].startswith(ORIGIN)
    host.process.stdin.close()
    host.process.wait(timeout=10)
    assert host.process.returncode == 0, host.process.stderr.read()
    events = []
    while not host.messages.empty():
        events.append(host.messages.get_nowait())
    assert any(e.get("event") == "closed" for e in events)
    assert not any(e.get("event") == "ready" for e in events)


def test_protocol_error_completes_inflight_navigation_and_exits(host_exe, tmp_path):
    host = PipeHost(host_exe, tmp_path, [{"action": "close"}])
    assert host.messages.get(timeout=15)["type"] == "request"
    host.send({"type": "unknown"})
    host.process.wait(timeout=10)
    assert host.process.returncode != 0
    events = []
    while not host.messages.empty():
        events.append(host.messages.get_nowait())
    assert any(e.get("event") == "error" for e in events)
    assert any(e.get("event") == "closed" for e in events)


def test_pipe_renders_iframe_and_post_and_captures_preview(host_exe, tmp_path):
    screenshot = tmp_path / "native.png"
    host = PipeHost(host_exe, tmp_path, [
        {"action": "wait", "milliseconds": 600},
        {"action": "script", "id": "dom", "script":
         "Promise.all([fetch('/echo', {method:'POST', headers:{'X-Test':'pipe'}, body:'中文'}).then(r=>r.text()), Promise.resolve(document.querySelector('iframe').contentDocument.body.textContent)]).then(x=>({echo:x[0],frame:x[1],origin:location.origin}))"},
        {"action": "screenshot", "path": str(screenshot)},
        {"action": "close"},
    ])
    events = host.run({
        "/": (b"<!doctype html><title>Pipe host</title><iframe src='/frame'></iframe>", "text/html; charset=utf-8"),
        "/frame": (b"<!doctype html><body>iframe-loaded</body>", "text/html; charset=utf-8"),
        "/echo": ("回声".encode(), "text/plain; charset=utf-8"),
    })
    assert any(e.get("event") == "ready" for e in events)
    result = next(e for e in events if e.get("event") == "test_result" and e.get("id") == "dom")
    value = json.loads(result["result"])
    assert value["echo"] == "回声"
    assert "iframe-loaded" in value["frame"]
    assert value["origin"] == ORIGIN
    post = next(r for r in host.requests if r["method"] == "POST")
    assert base64.b64decode(post["body_base64"]).decode() == "中文"
    assert any(k.lower() == "x-test" and v == "pipe" for k, v in post["headers"].items())
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert any(e.get("event") == "closed" for e in events)


def test_script_navigation_cannot_escape_fixed_origin(host_exe, tmp_path):
    host = PipeHost(host_exe, tmp_path, [
        {"action": "script", "script": "location.href='https://example.com/escape'; true"},
        {"action": "wait", "milliseconds": 200},
        {"action": "script", "id": "origin", "script": "location.origin"},
        {"action": "close"},
    ])
    events = host.run({"/": (b"<!doctype html><title>Safe origin</title>", "text/html")})
    result = next(e for e in events if e.get("id") == "origin")
    assert json.loads(result["result"]) == ORIGIN
    assert all(r["url"].startswith(ORIGIN + "/") for r in host.requests)


def test_oversize_request_rejected_locally_with_safe_headers(host_exe, tmp_path):
    host = PipeHost(host_exe, tmp_path, [
        {"action": "script", "id": "large", "script":
         "fetch('/large', {method:'POST',body:'x'.repeat(65537)}).then(r=>({status:r.status,csp:r.headers.get('Content-Security-Policy'),cache:r.headers.get('Cache-Control'),nosniff:r.headers.get('X-Content-Type-Options')}))"},
        {"action": "close"},
    ])
    events = host.run({"/": (b"<!doctype html><title>Bounded</title>", "text/html"),
                       "/large": (b"too large", "text/plain")})
    result = json.loads(next(e for e in events if e.get("id") == "large")["result"])
    assert result["status"] == 413
    assert result["cache"] == "no-store"
    assert result["nosniff"] == "nosniff"
    assert result["csp"] == "default-src 'none'; frame-ancestors 'none'"
    assert not any(r["url"].endswith("/large") for r in host.requests)


def test_real_mermaid_runs_inside_opaque_sandbox(host_exe, tmp_path):
    assets = SOURCE.parent / "ui_assets"
    host = PipeHost(host_exe, tmp_path, [
        {"action": "script", "id": "mermaid", "script":
         "ICODEDiagramRenderer.render('nativeDiagram','flowchart LR; A[Native] --> B[Private pipe]').then(x=>({svg:x.svg,styles:x.styles.length,opaque:document.querySelector('iframe').contentDocument===null}))"},
        {"action": "close"},
    ])
    routes = {"/": (b"<!doctype html><script defer src='/assets/diagram-host.js'></script>", "text/html")}
    for name in ("diagram-host.js", "diagram-renderer.html", "diagram-renderer.js", "vendor/mermaid.js"):
        policy = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                  "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts")
        headers = {}
        if name == "diagram-renderer.html":
            headers["Content-Security-Policy"] = policy
            headers["X-Frame-Options"] = "SAMEORIGIN"
        if name in ("diagram-renderer.js", "vendor/mermaid.js"):
            headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        routes["/assets/" + name] = ((assets / name).read_bytes(),
                                     "text/html" if name.endswith(".html") else "text/javascript", headers)
    events = host.run(routes)
    result = json.loads(next(e for e in events if e.get("id") == "mermaid")["result"])
    assert result["opaque"] is True
    assert "<svg" in result["svg"]
    assert "Private pipe" in " ".join("".join(ET.fromstring(result["svg"]).itertext()).split())
    assert result["styles"] > 0
    assert any(r["url"].endswith("/assets/vendor/mermaid.js") for r in host.requests)


def test_pending_request_limit_keeps_ui_responsive(host_exe, tmp_path):
    host = PipeHost(host_exe, tmp_path, [
        {"action": "script", "id": "concurrent", "script":
         "Promise.all(Array.from({length:18},(_,i)=>fetch('/hold?i='+i).then(r=>r.status)))"},
        {"action": "close"},
    ])
    held = []

    def hold(message):
        held.append(message)
        if len(held) == 16:
            # Let Chromium dispatch the two remaining fetches before releasing.
            time.sleep(0.3)
            for request in held:
                host.send({"type": "response", "id": request["id"], "status": 200,
                           "headers": {"Content-Type": "text/plain"}, "body_base64": ""})
        return None

    events = host.run({"/": (b"<!doctype html><link rel='icon' href='data:,'>", "text/html"),
                       "/hold": hold})
    statuses = json.loads(next(e for e in events if e.get("id") == "concurrent")["result"])
    assert len(held) == 16
    assert sorted(statuses) == [200] * 16 + [429] * 2


def test_unanswered_request_times_out_with_explicit_safe_response(host_exe, tmp_path):
    host = PipeHost(host_exe, tmp_path, [
        {"action": "script", "id": "timeout", "script":
         "fetch('/slow').then(r=>({status:r.status,csp:r.headers.get('Content-Security-Policy')}))"},
        {"action": "close"},
    ])
    events = host.run({"/": (b"<!doctype html><title>Timeout</title>", "text/html"),
                       "/slow": lambda _: None}, timeout=45)
    result = json.loads(next(e for e in events if e.get("id") == "timeout")["result"])
    assert result == {"status": 504, "csp": "default-src 'none'; frame-ancestors 'none'"}
