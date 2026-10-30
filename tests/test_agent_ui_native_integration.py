"""Actual Windows CLI and production UI through WebView2, without TCP/CDP ports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import queue
import threading

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ICODE_RUN_NATIVE_UI_TESTS') != '1',
                                reason='opt-in Windows WebView2 acceptance')
ROOT = Path(__file__).resolve().parents[1]


def test_actual_native_cli_renders_doc_and_sandboxed_mermaid(tmp_path):
    assert sys.platform == 'win32', 'native acceptance requires Windows'
    home = tmp_path / '中文 profile'
    book = home / '.claude/icode_data/project_docs/demo/main'
    book.mkdir(parents=True)
    (book / '_meta.json').write_text(json.dumps({'branch': 'main'}), encoding='utf-8')
    source = '# 原生文档阅读\n\n## 流程\n\n```mermaid\nflowchart LR\n A[开始] --> B[完成]\n```\n\n' + '\n\n'.join('正文段落 ' + str(i) for i in range(80))
    chapter = book / '00_overview.md'
    chapter.write_text(source, encoding='utf-8')
    screenshot = tmp_path / 'native-document.png'
    script = tmp_path / 'steps.json'
    script.write_text(json.dumps([
        {'action': 'script', 'id': 'read', 'script': r'''(async()=>{
const wait=async(f)=>{const end=Date.now()+25000;while(!f()){if(Date.now()>end)throw Error('DOM timeout');await new Promise(r=>setTimeout(r,80));}};
await wait(()=>document.getElementById('workspace-docs'));document.getElementById('workspace-docs').click();
await wait(()=>document.querySelector('#doc-body .doc-diagram svg'));
const frame=document.querySelector('iframe.doc-diagram-frame');
const svg=document.querySelector('#doc-body .doc-diagram svg');
let sameOrigin=false;try{sameOrigin=!!frame.contentWindow.document;}catch(_){}
const content=document.getElementById('doc-content');content.scrollTop=500;content.dispatchEvent(new Event('scroll'));
await new Promise(r=>setTimeout(r,150));document.getElementById('workspace-tickets').click();document.getElementById('workspace-docs').click();
await new Promise(r=>setTimeout(r,300));
return {title:document.querySelector('#doc-body h1').textContent,origin:location.origin,platform:navigator.platform,
sandbox:frame.getAttribute('sandbox'),sameOrigin,diagramHeight:svg.getBoundingClientRect().height,scroll:content.scrollTop,
hostObjects:!!(window.chrome&&chrome.webview&&chrome.webview.hostObjects),url:location.href};})()'''},
        {'action': 'screenshot', 'path': str(screenshot)},
        {'action': 'close'},
    ]), encoding='utf-8')
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8='1')
    # Exercise the Windows default, rather than selecting the transport in test code.
    result = subprocess.run([sys.executable, str(ROOT / 'tools/icode_agent.py'), 'ui',
                             '--native-test-script', str(script)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]['ok'] and events[0]['transport'] == 'webview2'
    assert 'url' not in events[0]  # A ready HWND is not an HTTP listener receipt.
    reading = json.loads(next(e['result'] for e in events if e.get('id') == 'read'))
    assert reading['title'] == '原生文档阅读'
    assert reading['origin'] == 'https://icode.localhost' and reading['platform'].startswith('Win')
    assert reading['sandbox'] == 'allow-scripts' and not reading['sameOrigin']
    assert reading['diagramHeight'] > 20 and reading['scroll'] >= 490
    assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert chapter.read_text(encoding='utf-8') == source


def test_actual_duplicate_cli_focuses_window_and_recovers_stale_registration(tmp_path):
    assert sys.platform == 'win32'
    home = tmp_path / 'isolated home'
    home.mkdir()
    script = tmp_path / 'hold.json'
    script.write_text(json.dumps([{'action': 'wait', 'milliseconds': 10000}, {'action': 'close'}]))
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8='1')
    command = [sys.executable, str(ROOT / 'tools/icode_agent.py'), 'ui', '--native-test-script', str(script)]
    process = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        rows = queue.Queue()
        threading.Thread(target=lambda: rows.put(process.stdout.readline()), daemon=True).start()
        ready = json.loads(rows.get(timeout=45))
        assert ready['ok'] and ready['transport'] == 'webview2'
        second = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=15)
        assert second.returncode == 0, second.stdout + second.stderr
        reused = json.loads(second.stdout)
        assert reused['reused'] and reused['pid'] == ready['pid']
        assert reused['window_id'] == ready['window_id'] and reused['instance_id'] == ready['instance_id']
        snapshot = subprocess.run(['powershell', '-NoProfile', '-Command',
            '@{processes=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name);'
            'listeners=@(Get-NetTCPConnection -State Listen | Select-Object OwningProcess,LocalAddress,LocalPort)}'
            ' | ConvertTo-Json -Depth 4 -Compress'], capture_output=True, text=True, timeout=20)
        assert snapshot.returncode == 0, snapshot.stderr
        network = json.loads(snapshot.stdout)
        owned = {process.pid}
        while True:
            more = {p['ProcessId'] for p in network['processes'] if p['ParentProcessId'] in owned}
            if more <= owned:
                break
            owned |= more
        assert ready['pid'] in owned, 'observe the actual live native host, not a stopped PID'
        assert any(p['ProcessId'] in owned and p['Name'] == 'msedgewebview2.exe' for p in network['processes'])
        listeners = [row for row in network['listeners'] if row['OwningProcess'] in owned]
        assert not listeners, listeners
        (tmp_path / 'native-process-listeners.json').write_text(json.dumps({
            'parent_pid': process.pid, 'ready': ready,
            'processes': [p for p in network['processes'] if p['ProcessId'] in owned],
            'listeners': listeners}, indent=2), encoding='utf-8')
        registration = next((home / '.claude/icode_data').glob('ui_native_*.json'))
        old_record = json.loads(registration.read_text(encoding='utf-8'))
        assert 'token' not in old_record
        output, error = process.communicate(timeout=15)
        assert process.returncode == 0, output + error
        assert not registration.exists(), 'normal window close must remove its own registration'
        # Reproduce a process killed before its owner-bound finally cleanup.
        registration.write_text(json.dumps(old_record), encoding='utf-8')
        replacement = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                                     text=True, encoding='utf-8', timeout=30)
        assert replacement.returncode == 0, replacement.stdout + replacement.stderr
        current = json.loads(replacement.stdout.splitlines()[0])
        assert current['instance_id'] != old_record['instance_id']
        assert not current.get('reused') and not registration.exists()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
