"""Opt-in real HTTP/CSP/browser acceptance; no user library writes."""
import json
import os
from pathlib import Path
import struct
import sys
import threading
import zlib

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ICODE_RUN_BROWSER_TESTS") != "1",
                                reason="set ICODE_RUN_BROWSER_TESTS=1 with a local Playwright browser")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))
from icode_agent.ui_server import create_ui_server


def test_real_http_reader_renders_graph_image_and_preserves_position(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    docs_root = tmp_path / "library"
    book_path = docs_root / "project_docs" / "demo" / "main"
    book_path.mkdir(parents=True)
    (book_path / "_meta.json").write_text(json.dumps({"branch": "main"}), encoding="utf-8")
    markdown = ("# 真实阅读验证\n\n> **项目元信息**\n> - 工程名：demo\n\n"
                "## 流程图\n\n```mermaid\nflowchart LR\n A[开始] --> B[结束]\n```\n\n"
                "![像素](pixel.png)\n\n| 字段 | 值 |\n|---|---|\n| 状态 | 正常 |\n\n"
                "## 详细内容\n\n" + "\n\n".join("阅读正文 " + str(i) for i in range(70)))
    (book_path / "00_overview.md").write_text(markdown, encoding="utf-8")
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    (book_path / "pixel.png").write_bytes(png)
    server = create_ui_server(None, ROOT / "tools/icode_control.py", port=0,
                              docs_root=docs_root, index_path=tmp_path / "index.json",
                              settings_path=tmp_path / "settings.json")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get("ICODE_TEST_BROWSER"),
                                               headless=True, args=["--no-sandbox", "--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 1600, "height": 1000})
                errors = []
                console_errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: console_errors.append(message.text)
                        if message.type == 'error' else None)
                response = page.goto("http://127.0.0.1:%d/?view=docs" % server.server_port)
                assert "script-src 'self'" in response.headers["content-security-policy"]
                assert "'unsafe-inline'" not in response.headers['content-security-policy']
                page.locator("#doc-body table").wait_for()
                page.locator("#doc-body .doc-diagram svg").wait_for(timeout=20000)
                playwright.expect(page.locator('#doc-body img')).to_have_js_property('naturalWidth', 1)
                graph = page.locator("#doc-body .doc-diagram svg")
                assert graph.bounding_box()["height"] > 20
                fill = graph.locator(".node rect").first.evaluate("node => getComputedStyle(node).fill")
                assert fill != "rgb(0, 0, 0)", "Mermaid styles blocked by the production CSP"
                playwright.expect(page.locator('iframe.doc-diagram-frame')).to_have_attribute('sandbox', 'allow-scripts')
                assert page.locator('iframe.doc-diagram-frame').evaluate('frame => {try {return !!frame.contentWindow.document;} catch (_) {return false;}}') is False
                assert graph.locator('[style]').count() == 0
                assert page.evaluate('async () => {try {await ICODEDiagramRenderer.render("reject-config", "%%{init: {securityLevel: loose}}%%\\nflowchart LR\\nA-->B"); return false;} catch (_) {return true;}}')
                assert not console_errors, console_errors[:2]
                page.locator("#doc-content").evaluate("el => {el.scrollTop = 500; el.dispatchEvent(new Event('scroll'));}")
                page.locator("#refresh-button").click()
                playwright.expect(page.locator('#refresh-button')).to_be_enabled()
                assert page.locator("#doc-content").evaluate("el => el.scrollTop") >= 490
                page.locator("#doc-focus").click()
                assert page.locator("#doc-library").evaluate("el => el.classList.contains('doc-focused')")
                page.locator("#doc-focus").click()
                output = ROOT / "docs/nbl/verification"
                output.mkdir(parents=True, exist_ok=True)
                page.locator("#doc-content").evaluate("el => {el.scrollTop = 0;}")
                page.screenshot(path=str(output / "doc-reader-desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "doc-reader-mobile.png"))
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
