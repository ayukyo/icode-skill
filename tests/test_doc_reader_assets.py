"""The reader must ship reproducible local renderer resources without a CDN."""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))
from icode_agent.ui_server import create_ui_server


def test_reader_vendor_files_match_pinned_manifest():
    vendor = ROOT / "agent_runtime/icode_agent/ui_assets/vendor"
    assert (vendor / "manifest.json").is_file(), "reader renderer assets are not yet installed"
    manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    assert {package["name"] for package in manifest["packages"]} == {"marked", "dompurify", "mermaid"}
    for package in manifest["packages"]:
        assert package["version"] and package["integrity"].startswith("sha512-")
        assert package["source"].startswith("https://registry.npmjs.org/")
        assert package["license"]
        for name, digest in package["files"].items():
            assert Path(name).name == name
            assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest


def test_reader_assets_are_served_locally(tmp_path):
    server = create_ui_server(None, ROOT / "tools/icode_control.py", port=0,
                              index_path=tmp_path / "index.json",
                              settings_path=tmp_path / "settings.json")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name in ("doc-reader.js", "doc-reader.css", "vendor/marked.js",
                     "vendor/purify.js", "vendor/mermaid.js", "diagram-host.js",
                     "diagram-renderer.html", "diagram-renderer.js"):
            conn = http.client.HTTPConnection(*server.server_address, timeout=10)
            conn.request("GET", "/assets/" + name)
            response = conn.getresponse()
            body = response.read()
            assert response.status == 200, name
            assert body == (ROOT / "agent_runtime/icode_agent/ui_assets" / name).read_bytes()
            assert response.getheader("X-Content-Type-Options") == "nosniff"
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_document_reader_checks_are_mandatory_in_ci():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/public-site.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['build']['steps']
    browser = next((step for step in steps if step.get('name') == 'Document reader browser acceptance'), None)
    assert browser is not None, 'real browser acceptance must run in CI'
    assert browser.get('env', {}).get('ICODE_RUN_BROWSER_TESTS') == '1'
    assert not browser.get('continue-on-error', False)
    assert 'tests/test_doc_reader_browser.py' in browser['run']
    assert 'tests/test_doc_reader_integration.py' in browser['run']
    regression = next(step for step in steps if step.get('name') == 'Brand and UI regression tests')
    assert 'tests/test_doc_library.py' in regression['run']
    assert 'tests/test_doc_reader_assets.py' in regression['run']
    budget = next((step for step in steps if step.get('name') == 'Validate actual import package budgets'), None)
    assert budget is not None, 'the exact committed source must be built under the import budget'
    assert 'tools/build_release_assets.py' in budget['run']
    assert 'CORE_MAX_FILES' in budget['run']
    assert not budget.get('continue-on-error', False)


def test_windows_native_reader_acceptance_gates_public_build():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/public-site.yml').read_text(encoding='utf-8'))
    events = workflow.get('on', workflow.get(True))  # PyYAML also parses YAML 1.1's boolean "on".
    assert 'tools/icode_agent.py' in events['pull_request']['paths']
    job = workflow['jobs'].get('windows-ui')
    assert job is not None, 'Windows native UI acceptance must run before public builds'
    assert job['runs-on'] == 'windows-latest'
    assert 'windows-ui' in workflow['jobs']['build']['needs']
    assert not job.get('if') and not job.get('continue-on-error', False)
    browser = next(step for step in job['steps'] if step.get('name') == 'Windows HTTP document reader compatibility')
    assert browser.get('env', {}).get('ICODE_RUN_BROWSER_TESTS') == '1'
    assert not browser.get('if') and not browser.get('continue-on-error', False)
    for name in ('test_doc_library.py', 'test_doc_reader_assets.py',
                 'test_doc_reader_browser.py', 'test_doc_reader_integration.py'):
        assert name in browser['run']
    native = next(step for step in job['steps'] if step.get('name') == 'Windows WebView2 transport and actual CLI acceptance')
    assert native.get('env', {}).get('ICODE_RUN_NATIVE_UI_TESTS') == '1'
    assert not native.get('if') and not native.get('continue-on-error', False)
    for name in ('test_agent_ui_native_host.py', 'test_agent_ui_native_instance.py',
                 'test_agent_ui_native_integration.py'):
        assert name in native['run']
    bootstrap = next(step for step in job['steps'] if 'ensure_native_runtime' in step.get('run', ''))
    assert 'ICODE_NATIVE_HOST_EXE' in bootstrap['run'] and '$LASTEXITCODE' in bootstrap['run']
