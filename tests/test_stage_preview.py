"""Report-bound HTTP previews use frozen evidence, never browser file paths."""
import copy
import base64
from contextlib import contextmanager
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))
from icode_agent import ui_server
from test_agent_ui_manager_v2 import dashboard as base_dashboard
from test_stage_ui import StageControl


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def png(width=4, height=2):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * width) * height))
            + chunk(b"IEND", b""))


def request(server, path, *, token=True, host=None, origin=None):
    hostname, port = server.server_address
    headers = {"Host": host or f"{hostname}:{port}"}
    if token:
        headers["X-ICODE-UI-Token"] = server.ui_token
    if origin:
        headers["Origin"] = origin
    conn = http.client.HTTPConnection(hostname, port, timeout=5)
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


class FrozenStageControl(StageControl):
    """Fake protected ledger resolver; report/preview bytes use real Reader."""
    def __init__(self, root, *, image=None, image_path="frames/preview.png"):
        super().__init__()
        self.root = Path(root).resolve()
        spec = importlib.util.spec_from_file_location("preview_test_control", ROOT / "tools/icode_control.py")
        self.core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.core)
        self.stage = self.core.stage_module()
        self.image = png() if image is None else image
        self.image_path = image_path
        self.bundle = {
            "run_id": "run-1", "candidate_id": "a" * 64,
            "items": [{"id": "preview-1", "path": image_path, "sha256": sha(self.image), "bytes": len(self.image)}],
            "visual": {"frame_sets": [{"id": "set-1", "frames": [
                {"id": "frame-1", "preview_item_id": "preview-1", "monotonic": 1.0,
                 "review_refs": [{"review_id": "visual-1"}]}]}]},
        }
        self.receipt = {"review_id": "visual-1", "run_id": "run-1", "candidate_id": "a" * 64,
                        "frame_results": [{"frame_set_id": "set-1", "frame_id": "frame-1",
                                           "input_sha256": sha(self.image), "outcome": "pass",
                                           "channel": "native", "conclusion": "Visible red field <script>untrusted</script>"}]}
        self.save(write=False)

    def save(self, *, write=True):
        if write and self.image_path == "frames/preview.png":
            target = self.root / self.image_path
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(self.image)
        bundle = json.dumps(self.bundle).encode()
        self.snapshot = {
            "accepted_runs": [{"run_id": "run-1", "candidate_id": "a" * 64,
                               "evidence_bundle": {"path": "bundle.json", "sha256": sha(bundle)}}],
            "evidence_metadata": {"extensions": {"verification": {"visual_reviews": [self.receipt]}}},
        }
        report = b"# Acceptance\n" + b"b" * 64
        source = json.dumps(self.snapshot).encode()
        if write:
            (self.root / "bundle.json").write_bytes(bundle)
            (self.root / "report.md").write_bytes(report)
            (self.root / "review.json").write_bytes(source)
        self.review = {"report_path": "report.md", "report_sha256": sha(report)[7:],
                       "review_path": "review.json", "review_sha256": sha(source)[7:],
                       "facts_digest": "b" * 64, "snapshot": copy.deepcopy(self.snapshot)}
        self.program = {"reviews": {"first": [self.review]}}

    def stage_module(self):
        return self

    def verification_evidence_module(self):
        return self.core.verification_evidence_module()

    def _resolve(self, directory):
        assert Path(directory).resolve() == self.root
        self.calls.append(("resolve", self.root))
        return {"directory": self.root, "program": self.program, "stage_id": "first"}

    def _latest_review(self, program, sid):
        return self.stage._latest_review(program, sid)

    def _check_report(self, directory, review):
        return self.stage._check_report(directory, review)

    def stage_projection(self, out_dir):
        result = super().stage_projection(out_dir)
        result.update(can_confirm=True, report={"text": "# Acceptance", "report_sha256": self.review["report_sha256"]})
        result["challenge"]["report_sha256"] = self.review["report_sha256"]
        return result


def setup_frozen(tmp_path, **kwargs):
    return FrozenStageControl(tmp_path / "project/.icode_output/.icode_output_1", **kwargs)


@contextmanager
def dashboard(tmp_path):
    with base_dashboard(tmp_path) as server:
        control = server.service._stage_control()
        if isinstance(control, FrozenStageControl):
            control.save()
        yield server


def preview_url(fake, index=0):
    return f"/api/v1/stage-previews/M2-UI-1/{index}?report_sha256={fake.review['report_sha256']}"


def test_report_lists_safe_indices_and_endpoint_serves_exact_png(tmp_path):
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, _, body = request(server, "/api/v1/tickets/M2-UI-1")
        assert status == 200
        previews = json.loads(body)["ticket"]["stage"]["report"]["previews"]
        assert len(previews) == 1
        assert set(previews[0]) == {"index", "title", "sha256", "report_sha256"}
        assert previews[0]["index"] == 0 and previews[0]["sha256"] == sha(fake.image)
        assert "Visible red field" in previews[0]["title"]
        assert str(tmp_path) not in json.dumps(previews)
        assert "preview.png" not in json.dumps(previews)
        status, headers, body = request(server, preview_url(fake))
        assert status == 200 and body == fake.image
        assert headers["Content-Type"] == "image/png"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "blob:" in headers["Content-Security-Policy"]
        assert any(call[0] == "resolve" for call in fake.calls)


def test_real_jpeg_is_served_by_content_even_with_png_file_extension(tmp_path):
    # Small synthetic red field encoded once as JPEG; no runtime media tool.
    image = base64.b64decode(
        "/9j//gAPTGF2YzYzLjEuMTAyAP/bAEMACAQEBAQEBQUFBQUFBgYGBgYGBgYGBgYGBgcHBwgICAcHBwYGBwcICAgICQkJCAgICAkJCgoKDAwLCw4ODhERFP/EAE0AAQEAAAAAAAAAAAAAAAAAAAAGAQEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAACAAQDARIAAhIAAxIA/9oADAMBAAIRAxEAPwCLEmN/H//Z")
    fake = setup_frozen(tmp_path, image=image)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, headers, body = request(server, preview_url(fake))
        assert status == 200 and body == image
        assert headers["Content-Type"] == "image/jpeg"


def test_preview_list_has_bounded_representative_frames(tmp_path):
    fake = setup_frozen(tmp_path)
    frames = fake.bundle["visual"]["frame_sets"][0]["frames"]
    for index in range(1, 8):
        frames.append({**copy.deepcopy(frames[0]), "id": f"frame-{index + 1}"})
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, _, body = request(server, "/api/v1/tickets/M2-UI-1")
        assert status == 200
        previews = json.loads(body)["ticket"]["stage"]["report"]["previews"]
        assert [entry["index"] for entry in previews] == list(range(5))
        assert request(server, preview_url(fake, index=5))[0] == 400


def test_preview_refuses_an_oversize_file_before_serving_bytes(tmp_path):
    fake = setup_frozen(tmp_path, image=b"x" * (16 * 1024 * 1024 + 1))
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        assert request(server, preview_url(fake))[0] == 409


@pytest.mark.parametrize("token,host,expected", [(False, None, 403), (True, "foreign.invalid", 421)])
def test_preview_keeps_http_token_and_host_checks(tmp_path, token, host, expected):
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, _, _ = request(server, preview_url(fake), token=token, host=host)
        assert status == expected


@pytest.mark.parametrize("suffix", ["", "?report_sha256=" + "c" * 64,
    "?report_sha256=" + "c" * 64 + "&path=frames/preview.png",
    "?report_sha256=" + "c" * 64 + "&report_sha256=" + "d" * 64])
def test_preview_rejects_missing_stale_or_injected_report_query(tmp_path, suffix):
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, _, body = request(server, "/api/v1/stage-previews/M2-UI-1/0" + suffix)
        assert status in {400, 409}
        assert fake.image not in body


@pytest.mark.parametrize("ticket,index", [("unknown", "0"), ("M2-UI-1", "-1"), ("M2-UI-1", "1"),
    ("M2-UI-1", "0%2f..%2fframes%2fpreview.png"), ("M2-UI-1", "%2e%2e"), ("M2-UI-1", "0.png")])
def test_preview_is_catalog_scoped_and_accepts_only_an_index(tmp_path, ticket, index):
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, _, _ = request(server, preview_url(fake).replace("M2-UI-1/0", ticket + "/" + index))
        assert status in {400, 409}


@pytest.mark.parametrize("mutation", ["replace", "delete", "bundle", "report", "review", "ledger"])
def test_preview_rechecks_current_bytes_and_protected_review(tmp_path, mutation):
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        if mutation == "replace": (fake.root / fake.image_path).write_bytes(png(6, 2))
        elif mutation == "delete": (fake.root / fake.image_path).unlink()
        elif mutation == "bundle": (fake.root / "bundle.json").write_text("{}")
        elif mutation == "report": (fake.root / "report.md").write_text("other")
        elif mutation == "review": (fake.root / "review.json").write_text("{}")
        else: fake._resolve = lambda _directory: (_ for _ in ()).throw(ValueError("invalid ledger"))
        status, _, body = request(server, preview_url(fake))
        assert status == 409
        assert str(tmp_path).encode() not in body


def test_review_change_during_preview_read_is_rejected(tmp_path):
    fake = setup_frozen(tmp_path)
    resolve = fake._resolve
    calls = 0
    def changing_resolve(directory):
        nonlocal calls
        calls += 1
        if calls == 2:
            fake.program["reviews"]["first"][-1]["report_sha256"] = "d" * 64
        return resolve(directory)
    fake._resolve = changing_resolve
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        assert request(server, preview_url(fake))[0] == 409


def test_preview_rejects_frozen_observation_for_other_image(tmp_path):
    fake = setup_frozen(tmp_path)
    fake.receipt["frame_results"][0]["input_sha256"] = "sha256:" + "d" * 64
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        assert request(server, preview_url(fake))[0] == 409
        status, _, body = request(server, "/api/v1/tickets/M2-UI-1")
        stage = json.loads(body)["ticket"]["stage"]
        assert status == 200 and stage["can_confirm"] is False and stage["preview_error"]


@pytest.mark.parametrize("path", ["../external.png", ".ssh/key.png", "frames/preview.png:secret", "frames/../preview.png"])
def test_frozen_item_still_cannot_read_private_ads_or_escape_paths(tmp_path, path):
    fake = setup_frozen(tmp_path, image_path=path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        status, _, _ = request(server, preview_url(fake))
        assert status == 409


def test_frozen_absolute_external_path_is_rejected(tmp_path):
    outside = tmp_path / "external.png"
    outside.write_bytes(png())
    fake = setup_frozen(tmp_path, image_path=str(outside))
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        assert request(server, preview_url(fake))[0] == 409


@pytest.mark.skipif(os.name != "nt", reason="Windows junction-specific boundary")
def test_preview_refuses_windows_parent_junction(tmp_path):
    import _winapi
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        external = tmp_path / "junction-target"
        external.mkdir()
        (external / "preview.png").write_bytes(fake.image)
        target = fake.root / fake.image_path
        target.unlink()
        target.parent.rmdir()
        _winapi.CreateJunction(str(external), str(target.parent))
        try:
            assert request(server, preview_url(fake))[0] == 409
        finally:
            # Remove only the junction, never traverse into its target.
            target.parent.rmdir()
        assert (external / "preview.png").read_bytes() == fake.image


@pytest.mark.parametrize("link_parent", [False, True])
def test_preview_refuses_file_or_parent_symlink(tmp_path, link_parent):
    fake = setup_frozen(tmp_path)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        external = tmp_path / "outside"
        external.mkdir()
        (external / "preview.png").write_bytes(fake.image)
        target = fake.root / fake.image_path
        target.unlink()
        try:
            if link_parent:
                target.parent.rmdir()
                target.parent.symlink_to(external, target_is_directory=True)
            else:
                target.symlink_to(external / "preview.png")
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable; exercised on Linux")
            raise
        assert request(server, preview_url(fake))[0] == 409


@pytest.mark.parametrize("image", [b"<svg onload=alert(1)></svg>", b"raw UYVY", b"\x89PNG\r\n\x1a\n", png(5000, 4000)],
                         ids=["svg", "raw", "short-png", "pixel-budget"])
def test_preview_only_serves_bounded_real_png_jpeg(tmp_path, image):
    fake = setup_frozen(tmp_path, image=image)
    with patch.object(ui_server.AgentUIService, "_stage_control", return_value=fake), dashboard(tmp_path) as server:
        assert request(server, preview_url(fake))[0] == 409


def test_non_stage_ticket_stays_compatible_and_has_no_previews(tmp_path):
    with dashboard(tmp_path) as server:
        status, _, body = request(server, "/api/v1/tickets/M2-UI-1")
        assert status == 200 and json.loads(body)["ticket"]["stage"]["enabled"] is False
        assert request(server, "/api/v1/stage-previews/M2-UI-1/0?report_sha256=" + "a" * 64)[0] == 409


def test_frontend_uses_token_blob_image_and_text_without_markdown_links():
    html = (ROOT / "agent_runtime/icode_agent/ui_assets/index.html").read_text(encoding="utf-8")
    js = (ROOT / "agent_runtime/icode_agent/ui_assets/app.js").read_text(encoding="utf-8")
    assert 'id="stage-previews"' in html
    assert 'document.createElement("img")' in js
    assert 'document.createElement("figure")' in js
    assert 'document.createElement("figcaption")' in js
    assert "URL.createObjectURL" in js and "URL.revokeObjectURL" in js
    assert 'headers: { "X-ICODE-UI-Token": token }' in js
    assert "report_sha256=" in js and "/api/v1/stage-previews/" in js
    assert 'byId("stage-report").textContent' in js
    assert "stagePreviewPending" in js and "stagePreviewFailed" in js
    assert ".innerHTML" not in js and "marked(" not in js


@pytest.mark.parametrize("failure", [False, True], ids=["decode-success", "decode-failure"])
def test_frontend_preview_load_controls_confirmation_and_releases_blob(tmp_path, failure):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; frontend runtime test not executed")
    script = (ROOT / "agent_runtime/icode_agent/ui_assets/app.js").read_text(encoding="utf-8")
    # Exercise the real renderer without starting bootstrap/network polling.
    # No confirmation action or claimed human confirmation is produced here.
    script = script.replace("  bootstrap();", "  globalThis.previewUI = { state, renderStage, resetStagePreviews };")
    harness = r'''
const vm = require("node:vm");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const elements = new Map();
let releaseFetch;
const calls = [], urls = [], revoked = [];
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.hidden = false; }
  addEventListener() {}
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  set src(value) {
    this.actualSrc = value;
    queueMicrotask(() => config.failure ? this.onerror() : this.onload());
  }
}
const document = {
  querySelector: () => ({content: "session-secret"}),
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, new Element("div"));
    return elements.get(id);
  },
  createElement: (tag) => new Element(tag),
};
const context = {
  document, console, Promise, queueMicrotask, encodeURIComponent,
  URL: {
    createObjectURL: (blob) => { assert.equal(blob.safe, true); urls.push("blob:trusted"); return "blob:trusted"; },
    revokeObjectURL: (url) => revoked.push(url),
  },
  fetch: (path, options) => {
    calls.push({path, options});
    return new Promise((resolve) => { releaseFetch = resolve; });
  },
};
vm.createContext(context);
vm.runInContext(config.script, context);
const {state, renderStage, resetStagePreviews} = context.previewUI;
state.ticket = {ticket_id: "ticket-one"};
state.stale = false;
const digest = "a".repeat(64);
const title = '<img src="https://foreign.invalid/" onerror="alert(1)"> red field';
const stage = { enabled: true, can_confirm: true, challenge: {challenge_id: "challenge"},
  report: {text: '<script>alert(1)</script>', report_sha256: digest,
    previews: [{index: 0, title, sha256: "sha256:" + "b".repeat(64), report_sha256: digest}]} };
state.ticket.stage = stage;
renderStage(stage);
assert.equal(elements.get("stage-confirm-button").disabled, true);
assert.equal(state.stagePreviewPending, true);
assert.equal(calls.length, 1);
assert.equal(calls[0].path, `/api/v1/stage-previews/ticket-one/0?report_sha256=${digest}`);
assert.equal(calls[0].options.headers["X-ICODE-UI-Token"], "session-secret");
assert.equal(calls[0].path.includes("session-secret"), false);
assert.equal(elements.get("stage-report").textContent, stage.report.text);
releaseFetch({ok: true, headers: {get: () => "image/png"}, blob: async () => ({safe: true})});
setImmediate(() => {
  assert.equal(state.stagePreviewPending, false);
  assert.equal(state.stagePreviewFailed, config.failure);
  assert.equal(elements.get("stage-confirm-button").disabled, config.failure);
  const figure = elements.get("stage-previews").children[0];
  assert.equal(figure.tagName, "figure");
  assert.equal(figure.children[0].tagName, "img");
  assert.equal(figure.children[0].actualSrc, "blob:trusted");
  assert.equal(figure.children[1].textContent, title);
  resetStagePreviews();
  assert.deepEqual(revoked, urls);
  assert.equal(elements.get("stage-previews").children.length, 0);
});
'''
    config = tmp_path / "preview-render.json"
    runner = tmp_path / "preview-render.cjs"
    config.write_text(json.dumps({"script": script, "failure": failure}), encoding="utf-8")
    runner.write_text(harness, encoding="utf-8")
    result = subprocess.run([node, str(runner), str(config)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
