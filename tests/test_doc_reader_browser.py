"""Exercise the document reader in a real browser with an isolated HTTP fixture.

Set ICODE_RUN_BROWSER_TESTS=1, optionally ICODE_TEST_BROWSER to a Chromium
executable, then run python -m pytest tests/test_doc_reader_browser.py -q.
No installed document library or ticket state is changed by this fixture.
"""
import json
import os
from pathlib import Path
import unittest
import struct
import zlib

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ASSETS = Path(__file__).resolve().parents[1] / "agent_runtime/icode_agent/ui_assets"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


@unittest.skipIf(sync_playwright is None, "Playwright required for document reader browser acceptance")
@unittest.skipUnless(os.environ.get("ICODE_RUN_BROWSER_TESTS") == "1", "Set ICODE_RUN_BROWSER_TESTS=1 for explicit browser acceptance")
class DocReaderBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        executable = os.environ.get("ICODE_TEST_BROWSER")
        if not executable and EDGE.exists():
            executable = str(EDGE)
        cls.browser = cls.playwright.chromium.launch(
            executable_path=executable, headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 960})
        self.requests = []
        self.pending_chapter = None
        self.hold_book = None
        self.hold_detail = None
        self.pending_book = None
        self.hold_search = False
        self.pending_search = None
        self.hold_catalog = False
        self.pending_catalog = None
        self.hold_asset = False
        self.pending_asset = None
        self.project_chapters = [dict(id="intro", name="intro.md", title="概览"), dict(id="next", name="next.md", title="目标章节")]
        self.books = [
            dict(id="project-main", kind="project", name="同名工程", branch="main", version="main", project_id="p1", linked_project_id="p1", status="verified", chapter_count=2),
            dict(id="project-dev", kind="project", name="同名工程", branch="dev", version="dev", project_id="p1", linked_project_id="p1", status="partial", chapter_count=1),
            dict(id="other-main", kind="project", name="同名工程", branch="main", version="main", project_id="p2", linked_project_id="p2", status="legacy", chapter_count=1),
            dict(id="module-v1", kind="module", name="相机模块", branch="", version="v1", module_key="camera", status="legacy", chapter_count=1),
        ]
        self.markdown = "# 中文标题\n\n## 同一标题\n\n[下一章](next.md#目标)\n\n## 同一标题\n\n| 字段 | 值 |\n|---|---|\n| 频率 | 30 |\n\n```cpp\nint count = 3;\n```\n\n" + "\n\n".join(f"阅读段落 {i}" for i in range(100))
        self.page.route("**/*", self.route)

    def tearDown(self):
        self.page.close()

    def route(self, route):
        from urllib.parse import urlparse, parse_qs
        request = route.request
        url = urlparse(request.url)
        query = parse_qs(url.query)
        self.requests.append(request)
        if url.path == "/":
            html = (ASSETS / "index.html").read_text(encoding="utf-8").replace("{{ICODE_UI_TOKEN}}", "fixture-secret")
            return route.fulfill(content_type="text/html", body=html)
        if url.path.startswith("/assets/"):
            path = ASSETS / url.path[len("/assets/"):]
            if path.is_file():
                content_type = "text/javascript" if path.suffix == ".js" else "text/css" if path.suffix == ".css" else "text/html" if path.suffix == ".html" else "image/svg+xml"
                return route.fulfill(content_type=content_type, body=path.read_bytes())
            return route.fulfill(status=404, body="missing fixture asset")
        if url.path == "/api/v1/docs/catalog":
            payload = dict(ok=True, books=self.books, errors=[])
            if self.hold_catalog:
                self.pending_catalog = (route, payload)
                return
        elif url.path == "/api/v1/docs/book":
            book = next(b for b in self.books if b["id"] == query["id"][0])
            chapters = self.project_chapters if book["kind"] == "project" else [dict(id="intro", name="intro.md", title="模块概览"), dict(id="next", name="next.md", title="模块细节")]
            payload = dict(ok=True, book={**book, "chapters": chapters, "dependencies": [dict(name="相机模块", book_id="module-v1", status="legacy"), dict(name="缺失模块", book_id=None, status="missing")], "metadata": dict(source_status="unverified", integrity="verified", commit="123abc", scope="full")})
            if book["id"] == self.hold_detail:
                self.pending_book = (route, payload)
                return
        elif url.path == "/api/v1/docs/chapter":
            chapter_id = query["chapter_id"][0]
            payload = dict(ok=True, chapter=dict(id=chapter_id, name=chapter_id + ".md", title="概览" if chapter_id == "intro" else "目标章节", markdown=self.markdown if chapter_id == "intro" else "# 目标\n\n正文目标", integrity="verified", links={"next.md#目标": dict(book_id=query["book_id"][0], chapter_id="next", fragment="目标")}, assets={"tall.png": "tall"}))
            if query["book_id"][0] == self.hold_book:
                self.pending_chapter = (route, payload)
                return
        elif url.path == "/api/v1/docs/search":
            payload = dict(ok=True, results=[dict(book_id=query["book_id"][0], chapter_id="next", title="目标章节", snippet="正文含有检索词")], truncated=False)
            if self.hold_search:
                self.pending_search = (route, payload)
                return
        elif url.path == "/api/v1/docs/asset":
            if self.hold_asset:
                self.pending_asset = route
                return
            return self.fulfill_tall_image(route)
        else:
            # A broken ticket index must never block independent document reading.
            return route.fulfill(status=503, content_type="application/json", body=json.dumps(dict(ok=False, error="fixture ticket index unavailable")))
        self.assertEqual(request.headers.get("x-icode-ui-token"), "fixture-secret")
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    @staticmethod
    def fulfill_tall_image(route):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 400, 4000, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * 1200) * 4000)) + chunk(b"IEND", b"")
        route.fulfill(content_type="image/png", body=image)

    def open_reader(self, suffix=""):
        self.page.goto("http://reader.test/" + suffix)
        self.page.locator("#workspace-docs").click(timeout=3000)
        self.page.locator("#doc-body h1").wait_for(timeout=10000)
        self.page.wait_for_function("document.querySelector('#doc-content').getAttribute('aria-busy') === 'false'", timeout=10000)

    def test_independent_reader_renders_markdown_navigation_and_identity(self):
        self.open_reader()
        self.assertEqual(self.page.locator("#doc-body h1").inner_text(), "中文标题")
        self.assertEqual(self.page.locator("#doc-body table").count(), 1)
        self.assertEqual(self.page.locator("#doc-body pre code").inner_text().strip(), "int count = 3;")
        ids = self.page.locator("#doc-body h2").evaluate_all("nodes => nodes.map(n => n.id)")
        self.assertEqual(ids, ["同一标题", "同一标题-1"])
        self.assertIn("未核验", self.page.locator("#doc-provenance").inner_text())
        self.assertIn("缺失模块", self.page.locator("#doc-tree").inner_text())
        self.assertEqual(self.page.locator("#doc-book option").count(), 2)
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.wait_for_function("location.search.includes('project-dev')")

    def test_panel_toggles_are_independent_preserve_reading_and_focus_restores_snapshot(self):
        self.open_reader()
        self.page.locator("#doc-content").evaluate("el => { el.scrollTop = 640; el.dispatchEvent(new Event('scroll')); }")
        before_url, before_history = self.page.url, self.page.evaluate("history.length")
        before_requests = len([r for r in self.requests if '/api/v1/docs/' in r.url])
        self.assertEqual(self.page.locator('#doc-tree-toggle').count(), 1, 'chapter tree needs an independent, always reachable toggle')
        self.page.locator('#doc-tree-toggle').click()
        self.assertTrue(self.page.locator('#doc-tree').is_hidden())
        self.assertTrue(self.page.locator('.doc-outline').is_visible())
        self.assertEqual(self.page.locator('#doc-tree-toggle').get_attribute('aria-expanded'), 'false')
        self.page.locator('#sidebar-toggle').click()
        self.assertLess(self.page.locator('.sidebar').bounding_box()['width'], 64)
        self.assertTrue(self.page.locator('#sidebar-toggle').is_visible())
        self.page.locator('#doc-focus').click()
        self.assertTrue(self.page.locator('.doc-outline').is_hidden())
        self.page.locator('#doc-focus').click()
        self.assertTrue(self.page.locator('#doc-tree').is_hidden())
        self.assertTrue(self.page.locator('.doc-outline').is_visible())
        self.assertEqual(self.page.locator('#sidebar-toggle').get_attribute('aria-expanded'), 'false')
        self.page.locator('#doc-outline-toggle').focus()
        self.page.keyboard.press('Enter')
        self.assertTrue(self.page.locator('.doc-outline').is_hidden())
        self.page.keyboard.press('Enter')
        self.assertTrue(self.page.locator('.doc-outline').is_visible())
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'doc-outline-toggle')
        self.assertEqual(self.page.url, before_url)
        self.assertEqual(self.page.evaluate('history.length'), before_history)
        self.assertEqual(len([r for r in self.requests if '/api/v1/docs/' in r.url]), before_requests)
        self.assertAlmostEqual(self.page.locator('#doc-content').evaluate('el => el.scrollTop'), 640, delta=3)
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('icode-doc-panels:v1'))"), dict(sidebar=True, tree=True, outline=False))
        self.page.reload()
        self.page.locator('#doc-body h1').wait_for()
        self.assertTrue(self.page.locator('#doc-tree').is_hidden())
        self.assertEqual(self.page.locator('#sidebar-toggle').get_attribute('aria-expanded'), 'false')

    def test_native_toolbar_is_compact_and_settings_handler_remains_connected(self):
        self.page.goto('https://icode.localhost/?view=docs')
        self.page.locator('#doc-body h1').wait_for()
        self.assertEqual(self.page.locator('html').get_attribute('data-transport'), 'native')
        self.assertTrue(self.page.locator('.topbar').is_hidden(), 'native reading must reclaim the empty 67px header')
        self.page.locator('#settings-button').evaluate('el => el.click()')
        self.assertTrue(self.page.locator('#settings-drawer').is_visible())
        self.page.locator('#settings-close').click()
        self.page.locator('#workspace-tickets').click()
        self.assertTrue(self.page.locator('.crumbs').is_visible())
        self.assertLess(self.page.locator('.topbar').bounding_box()['height'], 50)
        self.assertTrue(self.page.locator('.top-actions').is_hidden())
        self.page.goto('https://icode.localhost.evil.test/?view=docs')
        self.page.locator('#doc-body h1').wait_for()
        self.assertEqual(self.page.locator('html').get_attribute('data-transport'), 'browser')
        self.assertTrue(self.page.locator('#settings-button').is_visible())

    def test_native_connection_status_survives_refresh_errors_workspace_and_collapsed_navigation(self):
        project_name = '状态验证工程的较长名称' * 8
        payload = dict(ok=True, projects=[dict(project_id='p0', name=project_name, ticket_count=0)], tickets=[], jobs=[], hosts={}, initial_project_id=None, initial_ticket_id=None,
                       refreshed_at='2026-10-31T08:00:00Z', settings=dict(host='auto', model='', mode='simple', preferred_port=8765,
                       refresh_interval_seconds=30, fallback=True, show_next_step=True))
        self.page.route('**/api/v1/bootstrap', lambda route: route.fulfill(content_type='application/json', body=json.dumps(payload)))
        self.page.goto('https://icode.localhost/?view=docs')
        self.page.locator('#doc-body h1').wait_for()
        self.page.wait_for_function("document.getElementById('connection-state').textContent === '本地已连接'")
        self.assertTrue(self.page.locator('#connection-state').is_visible(), 'native reading must retain the existing connection status')
        self.assertTrue(self.page.locator('#last-refresh').is_visible())
        self.page.locator('#project-list button').filter(has_text=project_name).click()
        self.page.evaluate("window.originalConnection = document.getElementById('connection-state'); window.originalRefresh = document.getElementById('last-refresh')")
        self.page.locator('#sidebar-toggle').click()
        for width in (1440, 390):
            self.page.set_viewport_size(dict(width=width, height=960))
            self.assertTrue(self.page.locator('#connection-state').is_visible())
            self.assertTrue(self.page.locator('#last-refresh').is_visible())
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#refresh-button').evaluate('el => el.click()')
        self.page.wait_for_function("document.getElementById('connection-state').classList.contains('error')")
        self.assertEqual(self.page.locator('#connection-state').inner_text(), '连接或状态异常')
        self.assertTrue(self.page.locator('#connection-state').is_visible())
        self.page.locator('#sidebar-toggle').click()
        self.page.locator('#workspace-tickets').click()
        self.assertTrue(self.page.locator('#connection-state').is_visible())
        self.assertTrue(self.page.locator('#last-refresh').is_visible())
        self.assertTrue(self.page.evaluate('originalConnection === document.getElementById("connection-state") && originalRefresh === document.getElementById("last-refresh")'))
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertLess(self.page.locator('.topbar').bounding_box()['height'], 80)

    def test_mobile_expanded_navigation_exposes_real_project_buttons(self):
        payload = dict(ok=True, projects=[dict(project_id=f'p{i}', name=f'工程{i}较长名称', ticket_count=0) for i in range(12)],
                       tickets=[], jobs=[], hosts={}, initial_project_id=None, initial_ticket_id=None, refreshed_at=None, settings=None)
        self.page.route('**/api/v1/bootstrap', lambda route: route.fulfill(content_type='application/json', body=json.dumps(payload)))
        self.page.set_viewport_size(dict(width=390, height=844))
        self.open_reader()
        project = self.page.locator('#project-list button').filter(has_text='工程0较长名称')
        self.assertTrue(project.is_visible(), 'expanded mobile navigation must expose project selection')
        project.click()
        self.assertEqual(self.page.locator('body').get_attribute('data-project-id'), 'p0')
        self.assertLess(self.page.locator('.sidebar').bounding_box()['height'], 130)
        self.assertGreater(self.page.locator('#doc-content').bounding_box()['height'], 100)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#sidebar-toggle').click()
        self.assertTrue(project.is_hidden())
        self.page.locator('#sidebar-toggle').click()
        self.assertTrue(project.is_visible())

    def test_collapsed_panels_stay_collapsed_at_breakpoints_and_storage_denied(self):
        self.page.add_init_script("for (const key of ['sessionStorage', 'localStorage']) Object.defineProperty(window, key, {get() {throw new DOMException('disabled', 'SecurityError')}})")
        self.open_reader()
        self.assertEqual(self.page.locator('#doc-tree-toggle').count(), 1)
        for selector in ('#sidebar-toggle', '#doc-tree-toggle', '#doc-outline-toggle'):
            self.page.locator(selector).click()
        for width in (1280, 980, 720, 390, 320, 1440):
            self.page.set_viewport_size(dict(width=width, height=844))
            self.assertTrue(self.page.locator('#doc-tree').is_hidden())
            self.assertTrue(self.page.locator('.doc-outline').is_hidden())
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            for selector in ('#sidebar-toggle', '#doc-tree-toggle', '#doc-outline-toggle'):
                self.assertTrue(self.page.locator(selector).is_visible())
            self.assertGreater(self.page.locator('#doc-content').bounding_box()['height'], 100)
        self.page.locator('#doc-tree-toggle').click()
        self.assertTrue(self.page.locator('#doc-tree').is_visible())
        self.assertEqual(self.page.locator('#doc-focus').get_attribute('aria-pressed'), 'false')

    def test_wide_diagram_keeps_natural_labels_and_prose_keeps_comfortable_measure(self):
        self.markdown = '# 宽图\n\n正文保持舒适行宽。\n\n```mermaid\nflowchart LR\n' + ' --> '.join(f'N{i}[节点{i}包含较长中文标签]' for i in range(12)) + '\n```\n\n```cpp\n' + 'x' * 250 + '\n```'
        self.open_reader()
        self.page.locator('#doc-body svg').wait_for()
        self.page.locator('#doc-focus').click()
        sizes = self.page.locator('#doc-body').evaluate('''el => {
const svg=el.querySelector('svg'),diagram=el.querySelector('.doc-diagram');
return {svg:svg.getBoundingClientRect().width,natural:svg.viewBox.baseVal.width,diagram:diagram.clientWidth,
scroll:diagram.scrollWidth,prose:el.querySelector('p').getBoundingClientRect().width,code:el.querySelector('pre').getBoundingClientRect().width};}''')
        self.assertGreater(sizes['natural'], 1500)
        self.assertGreaterEqual(sizes['svg'], sizes['natural'] - 1, 'wide diagrams must scroll instead of shrinking labels')
        self.assertGreater(sizes['scroll'], sizes['diagram'])
        self.assertLessEqual(sizes['prose'], 820)
        self.assertGreater(sizes['code'], sizes['prose'])
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def test_malformed_panel_preferences_and_hidden_focus_do_not_break_reader(self):
        self.page.add_init_script("localStorage.setItem('icode-doc-panels:v1', '{bad json')")
        self.open_reader()
        self.page.locator('#doc-tree .doc-tree-item').first.focus()
        self.page.locator('#doc-tree-toggle').evaluate('el => el.click()')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'doc-tree-toggle')
        self.page.locator('#doc-focus').click()
        self.page.locator('#doc-outline-toggle').click()
        self.assertEqual(self.page.locator('#doc-focus').get_attribute('aria-pressed'), 'false')
        self.assertTrue(self.page.locator('.doc-outline').is_visible())
        self.assertTrue(self.page.locator('#doc-tree').is_hidden())

    def test_panel_layout_preserves_visible_paragraph_when_text_reflows(self):
        self.markdown = '# 长段落\n\n' + '\n\n'.join(f'段落{i} ' + '连续中文正文用于验证栏宽变化后的阅读位置。' * 20 for i in range(40))
        self.open_reader()
        self.page.locator('#doc-content').evaluate('''el => {
const p=document.querySelectorAll('#doc-body > p')[12];
el.scrollTop += p.getBoundingClientRect().top - el.getBoundingClientRect().top + 16;
}''')
        offset = self.page.locator('#doc-content').evaluate("el => document.querySelectorAll('#doc-body > p')[12].getBoundingClientRect().top - el.getBoundingClientRect().top")
        self.page.locator('#doc-focus').click()
        actual = self.page.locator('#doc-content').evaluate("el => document.querySelectorAll('#doc-body > p')[12].getBoundingClientRect().top - el.getBoundingClientRect().top")
        self.assertAlmostEqual(actual, offset, delta=2)
        self.page.locator('#doc-focus').click()
        restored = self.page.locator('#doc-content').evaluate("el => document.querySelectorAll('#doc-body > p')[12].getBoundingClientRect().top - el.getBoundingClientRect().top")
        self.assertAlmostEqual(restored, offset, delta=2)

    def test_ticket_sidebar_toggle_does_not_overwrite_hidden_reader_position(self):
        self.open_reader()
        self.page.locator('#doc-content').evaluate("el => { el.scrollTop = 640; el.dispatchEvent(new Event('scroll')); }")
        key = self.page.evaluate("Object.keys(sessionStorage).find(key => key.startsWith('icode-doc-scroll:') && key.includes('intro'))")
        self.page.locator('#workspace-tickets').click()
        self.page.locator('#sidebar-toggle').click()
        self.assertEqual(self.page.evaluate('key => sessionStorage.getItem(key)', key), '640', 'ticket-only panel changes must not save a hidden reader scrollTop')
        self.page.locator('#sidebar-toggle').click()
        self.page.locator('#workspace-docs').click()
        self.assertGreaterEqual(self.page.locator('#doc-content').evaluate('el => el.scrollTop'), 600)

    def test_layout_change_during_deferred_image_restore_keeps_restore_intent(self):
        self.prepare_delayed_image_history()
        self.page.locator('#doc-focus').click()
        self.fulfill_tall_image(self.pending_asset)
        self.page.wait_for_function("document.querySelector('#doc-content').getAttribute('aria-busy') === 'false'")
        self.assertAlmostEqual(self.page.locator('#doc-content').evaluate('el => el.scrollTop'), 6000, delta=3)

    def test_medium_width_reader_does_not_squeeze_prose_between_three_columns(self):
        self.page.set_viewport_size(dict(width=900, height=960))
        self.open_reader()
        self.assertGreater(self.page.locator('#doc-content').bounding_box()['width'], 420)
        self.assertTrue(self.page.locator('#doc-tree').is_visible())
        self.assertTrue(self.page.locator('.doc-outline').is_visible())

    def test_low_height_reader_keeps_text_space_with_expanded_and_collapsed_navigation(self):
        self.page.add_init_script("localStorage.removeItem('icode-doc-panels:v1')")
        payload = dict(ok=True, projects=[dict(project_id=f'p{i}', name=f'工程{i}', ticket_count=0) for i in range(12)],
                       tickets=[], jobs=[], hosts={}, initial_project_id=None, initial_ticket_id=None,
                       refreshed_at='2026-10-31T08:00:00Z', settings=None)
        self.page.route('**/api/v1/bootstrap', lambda route: route.fulfill(content_type='application/json', body=json.dumps(payload)))
        toggles = ('#sidebar-toggle', '#doc-tree-toggle', '#doc-outline-toggle')
        for origin, width, height in (('https://icode.localhost', 624, 394), ('https://icode.localhost', 640, 480), ('http://reader.test', 640, 480)):
            self.page.set_viewport_size(dict(width=width, height=height))
            self.page.goto(origin + '/?view=docs')
            self.page.locator('#doc-body h1').wait_for()
            self.page.wait_for_function("document.getElementById('doc-content').getAttribute('aria-busy') === 'false'")
            for collapsed in (False, True, False):
                with self.subTest(origin=origin, css_size=(width, height), collapsed=collapsed):
                    if self.page.locator('#sidebar-toggle').get_attribute('aria-expanded') != str(not collapsed).lower():
                        for selector in toggles:
                            self.page.locator(selector).click()
                    geometry = self.page.locator('#doc-content').evaluate('''el => {
const style=getComputedStyle(el),top=parseFloat(style.paddingTop),bottom=parseFloat(style.paddingBottom);
const heading=document.querySelector('#doc-body h1');el.scrollTop += heading.getBoundingClientRect().top-el.getBoundingClientRect().top-top;
const content=el.getBoundingClientRect(),text=heading.getBoundingClientRect(),grid=document.querySelector('.doc-reader-grid').getBoundingClientRect();
return {inner:el.clientHeight-top-bottom,bottom:content.bottom,gridBottom:grid.bottom,
visibleText:Math.min(text.bottom,content.bottom-bottom)-Math.max(text.top,content.top+top),overflow:document.documentElement.scrollWidth>innerWidth};}''')
                    self.assertGreater(geometry['inner'], 50, geometry)
                    self.assertLessEqual(geometry['bottom'], geometry['gridBottom'] + 1, geometry)
                    self.assertGreater(geometry['visibleText'], 15, geometry)
                    self.assertFalse(geometry['overflow'], geometry)
                    for selector in toggles:
                        self.assertTrue(self.page.locator(selector).is_visible())
                        self.assertEqual(self.page.locator(selector).get_attribute('aria-expanded'), str(not collapsed).lower())

    def test_simulated_dpr_matrix_preserves_reader_layout_and_pixel_density(self):
        """Browser DPR emulation supplements, but does not replace, native monitor DPI checks."""
        self.markdown = '# 缩放矩阵\n\n正文保持16px。\n\n```mermaid\nflowchart LR\n' + ' --> '.join(f'N{i}[节点{i}较长中文标签]' for i in range(12)) + '\n```'
        original_page = self.page
        toggles = ('#sidebar-toggle', '#doc-tree-toggle', '#doc-outline-toggle')
        for scale in (1, 1.25, 1.5, 1.75, 2):
            context = self.browser.new_context(viewport=dict(width=1280, height=960), device_scale_factor=scale)
            try:
                self.page = context.new_page()
                self.page.route('**/*', self.route)
                self.open_reader()
                self.page.locator('#doc-body svg').wait_for()
                expanded_width = self.page.locator('#doc-content').bounding_box()['width']
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                for selector in toggles:
                    self.page.locator(selector).click()
                self.assertGreater(self.page.locator('#doc-content').bounding_box()['width'], expanded_width + 400)
                for width, height in ((1280, 960), (390, 844)):
                    with self.subTest(simulated_dpr=scale, css_width=width):
                        self.page.set_viewport_size(dict(width=width, height=height))
                        if width == 390:
                            for selector in toggles:
                                self.page.locator(selector).click()
                            expanded_height = self.page.locator('#doc-content').bounding_box()['height']
                            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                            for selector in toggles:
                                self.page.locator(selector).click()
                            self.assertGreater(self.page.locator('#doc-content').bounding_box()['height'], expanded_height + 150)
                        metrics = self.page.evaluate('''() => {
const svg=document.querySelector('#doc-body svg'),diagram=svg.parentElement;
const unscaled=['html','body','.app-shell','.main-stage','#doc-library','#doc-content','#doc-body'].every(selector=>{
  const style=getComputedStyle(document.querySelector(selector));return Number(style.zoom||1)===1&&style.transform==='none';
});
return {dpr:devicePixelRatio,width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,
font:getComputedStyle(document.getElementById('doc-body')).fontSize,unscaled,
svg:svg.getBoundingClientRect().width,natural:svg.viewBox.baseVal.width,diagram:diagram.clientWidth,scroll:diagram.scrollWidth};}''')
                        self.assertAlmostEqual(metrics['dpr'], scale, delta=0.01)
                        self.assertEqual((metrics['width'], metrics['height']), (width, height))
                        self.assertFalse(metrics['overflow'])
                        self.assertEqual(metrics['font'], '16px')
                        self.assertTrue(metrics['unscaled'], 'reading must not compensate using CSS zoom or transforms')
                        self.assertGreater(metrics['natural'], 1500)
                        self.assertGreaterEqual(metrics['svg'], metrics['natural'] - 1)
                        self.assertGreater(metrics['scroll'], metrics['diagram'])
                        png = self.page.screenshot()
                        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
                        pixel_width, pixel_height = struct.unpack('>II', png[16:24])
                        self.assertAlmostEqual(pixel_width, width * scale, delta=1)
                        self.assertAlmostEqual(pixel_height, height * scale, delta=1)
            finally:
                self.page = original_page
                context.close()

    def test_cross_chapter_history_and_scroll_restore(self):
        self.open_reader()
        self.page.locator("#doc-content").evaluate("el => { el.scrollTop = 640; el.dispatchEvent(new Event('scroll')); }")
        self.page.locator('#doc-body a').evaluate("el => el.click()")
        self.page.wait_for_function("location.search.includes('doc_chapter=next')")
        self.assertEqual(self.page.locator("#doc-body h1").inner_text(), "目标")
        self.page.go_back()
        self.page.locator("#doc-body h1").filter(has_text="中文标题").wait_for()
        self.page.wait_for_function("document.querySelector('#doc-content').scrollTop >= 600")
        self.assertEqual(self.page.locator("#doc-version").input_value(), "project-main")

    def test_search_clears_previous_results_when_switching_book(self):
        self.open_reader()
        self.page.locator("#doc-search").fill("检索词")
        self.page.locator("#doc-search-results button").wait_for()
        self.assertIn("正文含有检索词", self.page.locator("#doc-search-results").inner_text())
        self.page.locator("#doc-version").select_option("project-dev")
        self.assertEqual(self.page.locator("#doc-search").input_value(), "")
        self.assertEqual(self.page.locator("#doc-search-results").inner_text(), "")

    def test_untrusted_html_links_and_remote_images_cannot_execute(self):
        self.markdown = '# 安全\n\n<script>window.readerXss=1</script>\n\n<img src=x onerror="window.readerXss=2">\n\n[危险](javascript:alert(1))\n\n![远程](https://evil.invalid/a.png)\n\n<iframe src="https://evil.invalid/"></iframe>\n\n```html\n<script>literal()</script>\n```'
        self.open_reader()
        self.assertIsNone(self.page.evaluate("window.readerXss"))
        self.assertEqual(self.page.locator("#doc-body script, #doc-body iframe, #doc-body [onerror]").count(), 0)
        self.assertEqual(self.page.locator('#doc-body a[href^="javascript:"]').count(), 0)
        self.assertFalse(any("evil.invalid" in request.url for request in self.requests))
        self.assertIn("<script>literal()</script>", self.page.locator("#doc-body pre code").inner_text())

    def test_deep_link_without_ticket_bootstrap(self):
        self.page.goto("http://reader.test/?view=docs&doc_book=project-dev&doc_chapter=next#目标")
        self.page.locator("#doc-body h1").wait_for(timeout=10000)
        self.assertFalse(self.page.locator("#doc-library").is_hidden())
        self.assertEqual(self.page.locator("#doc-version").input_value(), "project-dev")
        self.assertEqual(self.page.locator("#doc-body h1").inner_text(), "目标")

    def test_late_chapter_cannot_replace_new_book(self):
        self.open_reader()
        self.hold_book = "project-main"
        self.page.locator("#doc-next").click()
        self.page.wait_for_timeout(60)
        self.assertIsNotNone(self.pending_chapter)
        self.assertEqual(self.page.locator("#doc-body").inner_text(), "")
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.locator("#doc-body h1").wait_for()
        route, payload = self.pending_chapter
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.locator("#doc-version").input_value(), "project-dev")
        self.assertEqual(self.page.locator("#doc-body h1").inner_text(), "中文标题")

    def test_late_book_response_cannot_select_old_branch(self):
        self.open_reader()
        self.hold_detail = "project-dev"
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.wait_for_timeout(60)
        self.assertIsNotNone(self.pending_book)
        self.page.locator("#doc-version").select_option("project-main")
        self.page.locator("#doc-body h1").wait_for()
        route, payload = self.pending_book
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.locator("#doc-version").input_value(), "project-main")
        self.assertIn("doc_book=project-main", self.page.url)

    def test_late_search_results_cannot_reappear_after_branch_switch(self):
        self.open_reader()
        self.hold_search = True
        self.page.locator("#doc-search").fill("旧检索")
        self.page.wait_for_timeout(280)
        self.assertIsNotNone(self.pending_search)
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.locator("#doc-body h1").wait_for()
        route, payload = self.pending_search
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.locator("#doc-search-results").inner_text(), "")
        self.assertTrue(self.page.locator("#doc-search-results").is_hidden())

    def test_mobile_layout_keeps_article_scrollable_without_page_overflow(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.open_reader()
        self.assertTrue(self.page.locator("#doc-content").evaluate("el => el.clientHeight > 100 && el.scrollHeight > el.clientHeight"))
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

    def test_late_catalog_refresh_cannot_override_empty_scope_selection(self):
        self.open_reader()
        self.hold_catalog = True
        self.page.locator("#doc-refresh").click()
        self.page.wait_for_timeout(60)
        self.assertIsNotNone(self.pending_catalog)
        self.page.locator("#doc-scope").select_option("project")
        route, payload = self.pending_catalog
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(120)
        self.assertEqual(self.page.locator("#doc-scope").input_value(), "project")
        self.assertEqual(self.page.locator("#doc-body").inner_text(), "")

    def test_late_first_catalog_cannot_reopen_docs_after_ticket_switch(self):
        self.hold_catalog = True
        self.page.goto("http://reader.test/")
        self.page.locator("#workspace-docs").click()
        self.page.wait_for_timeout(60)
        self.assertIsNotNone(self.pending_catalog)
        self.page.locator("#workspace-tickets").click()
        route, payload = self.pending_catalog
        self.hold_catalog = False
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(120)
        self.assertNotIn("view=docs", self.page.url)
        self.assertTrue(self.page.locator("#doc-library").is_hidden())
        self.page.locator("#workspace-docs").click()
        self.page.locator("#doc-body h1").wait_for()

    def test_late_book_cannot_rewrite_ticket_url_and_pending_branch_resumes(self):
        self.open_reader()
        self.hold_detail = "project-dev"
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.wait_for_timeout(60)
        self.assertIsNotNone(self.pending_book)
        self.page.locator("#workspace-tickets").click()
        route, payload = self.pending_book
        self.hold_detail = None
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(120)
        self.assertNotIn("view=docs", self.page.url)
        self.page.locator("#workspace-docs").click()
        self.page.locator("#doc-body h1").wait_for()
        self.assertEqual(self.page.locator("#doc-version").input_value(), "project-dev")

    def test_late_chapter_cannot_rewrite_ticket_url_and_pending_chapter_resumes(self):
        self.open_reader()
        self.hold_book = "project-main"
        self.page.locator("#doc-next").click()
        self.page.wait_for_timeout(60)
        self.assertIsNotNone(self.pending_chapter)
        self.page.locator("#workspace-tickets").click()
        route, payload = self.pending_chapter
        self.hold_book = None
        route.fulfill(content_type="application/json", body=json.dumps(payload))
        self.page.wait_for_timeout(120)
        self.assertNotIn("view=docs", self.page.url)
        self.page.locator("#workspace-docs").click()
        self.page.locator("#doc-body h1").filter(has_text="目标").wait_for()
        self.assertIn("doc_chapter=next", self.page.url)

    def test_completed_reader_dom_and_position_survive_workspace_roundtrip(self):
        self.open_reader()
        self.page.locator("#doc-content").evaluate("el => { el.scrollTop = 640; el.dispatchEvent(new Event('scroll')); }")
        self.page.locator("#doc-body").evaluate("el => { el.dataset.roundtrip = 'preserved'; }")
        self.page.locator("#workspace-tickets").click()
        self.page.locator("#workspace-docs").click()
        self.assertEqual(self.page.locator("#doc-body").get_attribute("data-roundtrip"), "preserved")
        self.assertGreaterEqual(self.page.locator("#doc-content").evaluate("el => el.scrollTop"), 600)

    def prepare_delayed_image_history(self):
        self.markdown = "# 中文标题\n\n![长图](tall.png)\n\n" + "\n\n".join(f"正文段落 {i}" for i in range(100))
        self.open_reader()
        self.page.wait_for_function("document.querySelector('#doc-body img').naturalHeight === 4000")
        self.page.wait_for_function("document.querySelector('#doc-content').getAttribute('aria-busy') === 'false'")
        self.page.locator("#doc-content").evaluate("el => { el.scrollTop = 6000; el.dispatchEvent(new Event('scroll')); }")
        self.page.wait_for_function("Object.entries(sessionStorage).some(([key,value]) => key.startsWith('icode-doc-scroll:') && key.includes('intro') && value === '6000')")
        self.page.locator("#doc-next").evaluate("el => el.click()")
        self.page.locator("#doc-body h1").filter(has_text="目标").wait_for()
        self.hold_asset = True
        with self.page.expect_request("**/api/v1/docs/asset?id=tall"):
            self.page.go_back()
        self.page.locator("#doc-body h1").filter(has_text="中文标题").wait_for()
        self.page.wait_for_function("document.querySelector('#doc-content').getAttribute('aria-busy') === 'true'")
        self.assertIsNotNone(self.pending_asset)

    def test_delayed_image_restores_unclamped_history_position_after_layout(self):
        self.prepare_delayed_image_history()
        self.fulfill_tall_image(self.pending_asset)
        self.page.wait_for_function("document.querySelector('#doc-body img').naturalHeight === 4000")
        self.page.wait_for_function("document.querySelector('#doc-content').getAttribute('aria-busy') === 'false'")
        actual = self.page.locator("#doc-content").evaluate("el => el.scrollTop")
        self.assertAlmostEqual(actual, 6000, delta=3)

    def test_user_wheel_cancels_deferred_history_restoration(self):
        self.prepare_delayed_image_history()
        self.page.locator("#doc-content").evaluate("el => { el.dispatchEvent(new WheelEvent('wheel', {deltaY: 100})); el.scrollTop = 777; el.dispatchEvent(new Event('scroll')); }")
        self.fulfill_tall_image(self.pending_asset)
        self.page.wait_for_function("document.querySelector('#doc-body img').naturalHeight === 4000")
        self.page.wait_for_function("document.querySelector('#doc-content').getAttribute('aria-busy') === 'false'")
        actual = self.page.locator("#doc-content").evaluate("el => el.scrollTop")
        self.assertAlmostEqual(actual, 777, delta=3)

    def test_repeated_asset_is_fetched_once_and_uses_one_object_url(self):
        self.markdown = "# 重复图片\n\n" + "\n\n".join(f"![图片{i}](tall.png)" for i in range(100))
        self.open_reader()
        self.page.wait_for_function("document.querySelectorAll('#doc-body img').length === 100 && [...document.querySelectorAll('#doc-body img')].every(img => img.naturalHeight === 4000)")
        requests = [request for request in self.requests if "/api/v1/docs/asset?" in request.url]
        self.assertEqual(len(requests), 1)
        urls = self.page.locator("#doc-body img").evaluate_all("images => [...new Set(images.map(img => img.currentSrc))]")
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("blob:"))

    def test_mermaid_uses_opaque_sandbox_and_sanitized_host_svg(self):
        self.markdown = "# 图示\n\n```mermaid\nflowchart LR\n  A[输入] --> B[输出]\n```"
        self.open_reader()
        self.page.locator("#doc-body .doc-diagram svg").wait_for()
        self.assertEqual(self.page.locator("#doc-body [style]").count(), 0)
        self.assertEqual(self.page.locator("iframe.doc-diagram-frame").get_attribute("sandbox"), "allow-scripts")
        self.assertIsNone(self.page.evaluate("window.mermaid"))
        self.assertIn("输入", self.page.locator("#doc-body svg").text_content())

    def test_long_project_tree_keeps_selected_bound_module_visible(self):
        self.project_chapters = [self.project_chapters[0]] + [dict(id=f"chapter-{i}", name=f"chapter-{i}.md", title=f"工程章节 {i}") for i in range(49)]
        self.open_reader()
        self.page.locator("#doc-content").evaluate("el => { el.scrollTop = 640; el.dispatchEvent(new Event('scroll')); }")
        self.page.locator('[data-dependency-book="module-v1"]').click()
        self.page.locator('[data-module-chapter="intro"]').wait_for()
        self.page.locator('[data-module-chapter="intro"]').click()
        self.page.wait_for_function("location.search.includes('doc_book=module-v1') && document.querySelector('[data-module-chapter=intro][aria-current=page]')")
        selected_visible = "() => { const selected = document.querySelector('[data-module-chapter=intro][aria-current=page]').getBoundingClientRect(); const tree = document.querySelector('#doc-tree').getBoundingClientRect(); return selected.top >= tree.top && selected.bottom <= tree.bottom; }"
        self.assertTrue(self.page.evaluate(selected_visible))
        self.page.locator("#doc-return-project").click()
        self.page.wait_for_function("location.search.includes('doc_book=project-main') && document.querySelector('#doc-content').getAttribute('aria-busy') === 'false'")
        self.assertGreater(self.page.locator("#doc-tree").evaluate("el => el.scrollTop"), 500)
        self.assertGreaterEqual(self.page.locator("#doc-content").evaluate("el => el.scrollTop"), 600)

    def test_module_family_prefers_recorded_snapshot_name_over_legacy_directory_name(self):
        legacy = self.books[-1]
        legacy.update(name="camera_abcdef123456", version="legacy")
        self.books.append({**legacy, "id": "module-v2", "version": "snapshot-v2", "name": "相机模块", "status": "verified"})
        self.open_reader()
        self.page.locator("#doc-scope").select_option("module")
        self.assertEqual(self.page.locator("#doc-book option").inner_text(), "相机模块")
        self.assertEqual(self.page.locator("#doc-book option").get_attribute("title"), "camera")

    def test_failed_book_clears_previous_document(self):
        self.open_reader()
        self.page.route("**/api/v1/docs/book?id=project-dev", lambda route: route.fulfill(status=409, content_type="application/json", body=json.dumps(dict(ok=False, error="文档已变化，请刷新"))))
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.wait_for_function("document.querySelector('#doc-message').textContent.includes('文档已变化')")
        self.assertEqual(self.page.locator("#doc-body").inner_text(), "")
        self.assertEqual(self.page.locator("#doc-toc").inner_text(), "")
        self.assertTrue(self.page.locator("#doc-copy-link").is_disabled())

    def test_template_metadata_blocks_fold_without_hiding_body(self):
        self.markdown = '# 工程概览\n\n> **项目元信息**\n> 分支：main\n> 提交：abc\n\n正文仍然显示。\n\n> **KEYS**\n> owner: reader\n'
        self.open_reader()
        self.assertEqual(self.page.locator("#doc-body details").count(), 2)
        self.assertFalse(self.page.locator("#doc-body details").first.evaluate("el => el.open"))
        self.assertIn("正文仍然显示", self.page.locator("#doc-body").inner_text())

    def test_expand_bound_module_and_return_to_exact_project_branch(self):
        self.open_reader()
        self.page.locator("#doc-version").select_option("project-dev")
        self.page.locator("#doc-body h1").wait_for()
        self.page.locator('[data-dependency-book="module-v1"]').click()
        self.page.locator('[data-module-chapter="intro"]').wait_for()
        self.page.locator('[data-module-chapter="intro"]').click()
        self.page.wait_for_function("location.search.includes('doc_book=module-v1')")
        self.assertIn("doc_origin=project-dev", self.page.url)
        self.page.locator("#doc-return-project").click()
        self.page.wait_for_function("location.search.includes('doc_book=project-dev')")
        self.assertEqual(self.page.locator("#doc-version").input_value(), "project-dev")


def test_native_reader_panel_behavior_without_http_listener(tmp_path):
    """Use the shipped native test driver when HTTP browser automation is unavailable."""
    import subprocess
    import sys
    import pytest
    if sys.platform != 'win32' or os.environ.get('ICODE_RUN_NATIVE_UI_TESTS') != '1':
        pytest.skip('opt-in Windows WebView2 acceptance')
    home = tmp_path / 'isolated-home'
    book = home / '.claude/icode_data/project_docs/reader/main'
    book.mkdir(parents=True)
    (book / '_meta.json').write_text(json.dumps(dict(branch='main')), encoding='utf-8')
    (book / '00_overview.md').write_text('# 阅读测试\n\n## 章节\n\n' + '\n\n'.join('阅读段落 ' + str(i) for i in range(160)), encoding='utf-8')
    script = tmp_path / 'panel-test.json'
    script.write_text(json.dumps([
        {'action': 'resize', 'width': 1600, 'height': 1000},
        {'action': 'script', 'id': 'panels', 'script': r'''(async()=>{
const wait=async(f)=>{const end=Date.now()+20000;while(!f()){if(Date.now()>end)throw Error('reader timeout');await new Promise(r=>setTimeout(r,40));}};
document.getElementById('workspace-docs').click();await wait(()=>document.querySelector('#doc-body h1')&&document.getElementById('doc-content').getAttribute('aria-busy')==='false');
const ids=['sidebar-toggle','doc-tree-toggle','doc-outline-toggle'];
if(ids.some(id=>!document.getElementById(id)))return {controls:false};
const get=id=>document.getElementById(id),shown=el=>!!el.getClientRects().length;
const content=get('doc-content');content.scrollTop=640;content.dispatchEvent(new Event('scroll'));
const url=location.href,historyCount=history.length,body=get('doc-body');body.dataset.preserved='yes';
get('doc-tree-toggle').click();get('sidebar-toggle').click();
const independent=!shown(get('doc-tree'))&&shown(get('doc-outline'))&&get('doc-outline-toggle').getAttribute('aria-expanded')==='true';
get('doc-focus').click();const focused=ids.every(id=>get(id).getAttribute('aria-expanded')==='false');get('doc-focus').click();
const snapshot=!shown(get('doc-tree'))&&shown(get('doc-outline'))&&get('sidebar-toggle').getAttribute('aria-expanded')==='false';
get('doc-outline-toggle').focus();get('doc-outline-toggle').click();get('doc-outline-toggle').click();
const prefs=JSON.parse(localStorage.getItem('icode-doc-panels:v1'));
return {controls:true,independent,focused,snapshot,prefs,scroll:content.scrollTop,unchanged:location.href===url&&history.length===historyCount&&body.dataset.preserved==='yes',focus:document.activeElement.id,transport:document.documentElement.dataset.transport,topbar:shown(document.querySelector('.topbar')),connection:shown(get('connection-state')),refresh:shown(get('last-refresh')),overflow:document.documentElement.scrollWidth>innerWidth};})()'''},
        {'action': 'close'},
    ]), encoding='utf-8')
    result = subprocess.run([sys.executable, str(ASSETS.parents[2] / 'tools/icode_agent.py'), 'ui', '--native-test-script', str(script)],
                            cwd=tmp_path, env=dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8='1'),
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    value = json.loads(next(event['result'] for event in events if event.get('id') == 'panels'))
    assert value.get('controls'), 'independent panel restore controls are missing'
    assert value['independent'] and value['focused'] and value['snapshot'], value
    assert value['prefs'] == dict(sidebar=True, tree=True, outline=False)
    assert value['unchanged'] and abs(value['scroll'] - 640) < 3, value
    assert value['focus'] == 'doc-outline-toggle'
    assert value['transport'] == 'native' and not value['topbar'] and not value['overflow'], value
    assert value['connection'] and value['refresh'], value


if __name__ == "__main__":
    unittest.main()
