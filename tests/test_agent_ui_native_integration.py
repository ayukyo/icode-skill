"""Actual Windows CLI and production UI through WebView2, without TCP/CDP ports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import queue
import threading
import ctypes
from ctypes import wintypes
import struct

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ICODE_RUN_NATIVE_UI_TESTS') != '1',
                                reason='opt-in Windows WebView2 acceptance')
ROOT = Path(__file__).resolve().parents[1]


def test_native_reader_uses_monitor_dpi_and_preserves_panels_across_restart(tmp_path):
    """Measure the real HWND and capture buffer; don't emulate DPI with page zoom."""
    assert sys.platform == 'win32'
    home = tmp_path / 'dpi reader home'
    book = home / '.claude/icode_data/project_docs/reader/main'
    book.mkdir(parents=True)
    (book / '_meta.json').write_text('{"branch":"main"}', encoding='utf-8')
    chapter = book / '00_overview.md'
    source = '# 通用缩放阅读\n\n## 正文\n\n' + '\n\n'.join(
        f'第 {i} 段：正文与矢量图随显示器适配，保持清晰阅读。' for i in range(120))
    chapter.write_text(source, encoding='utf-8')
    screenshot = tmp_path / 'dpi-reader.png'
    first_script = tmp_path / 'first.json'
    first_script.write_text(json.dumps([
        {'action': 'script', 'id': 'read-panels', 'script': r'''(async()=>{
const wait=async(f)=>{const end=Date.now()+25000;while(!f()){if(Date.now()>end)throw Error('DOM timeout');await new Promise(r=>setTimeout(r,60));}};
await wait(()=>document.getElementById('workspace-docs'));document.getElementById('workspace-docs').click();
await wait(()=>document.querySelector('#doc-body h1'));
await new Promise(r=>setTimeout(r,650));
const root=document.getElementById('doc-library');
const controls=['sidebar-toggle','doc-tree-toggle','doc-outline-toggle'];
if(!controls.every(id=>document.getElementById(id)))throw Error('Missing independent panel controls');
const route=location.href,body=document.getElementById('doc-body'),historyLength=history.length;
const pane=document.getElementById('doc-content');pane.scrollTop=640;
pane.dispatchEvent(new Event('scroll'));await new Promise(r=>setTimeout(r,150));
if(pane.scrollTop<635)throw Error('Fixture did not establish a real reading position');
const anchor=[...body.children].find(el=>el.getBoundingClientRect().bottom>pane.getBoundingClientRect().top);
const anchorOffset=anchor.getBoundingClientRect().top-pane.getBoundingClientRect().top;
for(const [id,element,key] of [['sidebar-toggle',document.body,'sidebarCollapsed'],['doc-tree-toggle',root,'treeCollapsed'],['doc-outline-toggle',root,'outlineCollapsed']]){
 if(element.dataset[key]!=='true')document.getElementById(id).click();
}
if(document.body.dataset.sidebarCollapsed!=='true'||root.dataset.treeCollapsed!=='true'||root.dataset.outlineCollapsed!=='true')throw Error('Panels did not collapse');
document.getElementById('doc-focus').click();document.getElementById('doc-focus').click();
if(document.body.dataset.sidebarCollapsed!=='true'||root.dataset.treeCollapsed!=='true'||root.dataset.outlineCollapsed!=='true')throw Error('Focus overwrote manual panel preferences');
if(route!==location.href||body!==document.getElementById('doc-body')||historyLength!==history.length)throw Error('Layout changed routing or document');
if(Math.abs(anchor.getBoundingClientRect().top-pane.getBoundingClientRect().top-anchorOffset)>3)throw Error('Panel layout lost the visible paragraph');
if(getComputedStyle(document.querySelector('.topbar')).display!=='none')throw Error('Redundant native docs topbar');
if(getComputedStyle(document.documentElement).zoom!=='1')throw Error('Unexpected page zoom compensation');
return {dpr:devicePixelRatio,width:innerWidth,height:innerHeight,viewportScale:visualViewport.scale,
 transport:document.documentElement.dataset.transport,scroll:pane.scrollTop,route};})()'''},
        {'action': 'settings'},
        {'action': 'script', 'id': 'native-settings', 'script': r'''(async()=>{
const drawer=document.getElementById('settings-drawer');
if(drawer.hidden||!document.body.classList.contains('drawer-open'))throw Error('Upper native settings did not open the actual drawer');
document.getElementById('settings-close').click();
const end=Date.now()+3000;while(!drawer.hidden){if(Date.now()>end)throw Error('Settings did not close');await new Promise(r=>setTimeout(r,30));}
return {settings:true};})()'''},
        {'action': 'screenshot', 'path': str(screenshot)},
        {'action': 'toolbar_layout'},
        {'action': 'close'},
    ]), encoding='utf-8')
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8='1')
    command = [sys.executable, str(ROOT / 'tools/icode_agent.py'), 'ui', '--native-test-script']
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    user.GetWindowDpiAwarenessContext.restype = wintypes.HANDLE
    user.GetAwarenessFromDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    user.GetDpiForWindow.argtypes = [wintypes.HWND]
    user.GetDpiForWindow.restype = wintypes.UINT
    process = subprocess.Popen(command + [str(first_script)], cwd=tmp_path, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8')
    events, dpi, awareness = [], None, None
    try:
        while True:
            row = process.stdout.readline()
            if not row:
                break
            event = json.loads(row)
            events.append(event)
            if event.get('transport') == 'webview2':
                dpi = user.GetDpiForWindow(event['window_id'])
                awareness = user.GetAwarenessFromDpiAwarenessContext(
                    user.GetWindowDpiAwarenessContext(event['window_id']))
        process.wait(timeout=10)
        assert process.returncode == 0, repr(events) + process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
    assert awareness == 2, 'The real window must be per-monitor DPI aware'
    reading = json.loads(next(e['result'] for e in events if e.get('id') == 'read-panels'))
    assert reading['transport'] == 'native'
    assert abs(reading['dpr'] - dpi / 96) < .02
    assert reading['viewportScale'] == 1
    png = screenshot.read_bytes()
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    width, height = struct.unpack('>II', png[16:24])
    assert abs(width - reading['width'] * reading['dpr']) <= 2
    assert abs(height - reading['height'] * reading['dpr']) <= 2

    second_script = tmp_path / 'reopen.json'
    reopen_steps = [
        {'action': 'script', 'id': 'restored', 'script': r'''(async()=>{
const end=Date.now()+20000;while(!document.getElementById('doc-library')){if(Date.now()>end)throw Error('DOM timeout');await new Promise(r=>setTimeout(r,60));}
const root=document.getElementById('doc-library');
return {sidebar:document.body.dataset.sidebarCollapsed,tree:root.dataset.treeCollapsed,outline:root.dataset.outlineCollapsed};})()'''},
        {'action': 'script', 'id': 'short-ready', 'script': r'''(async()=>{
document.getElementById('workspace-docs').click();
const end=Date.now()+20000;while(!document.querySelector('#doc-body h1')){if(Date.now()>end)throw Error('DOC timeout');await new Promise(r=>setTimeout(r,50));}
const root=document.getElementById('doc-library');
for(const [id,element,key] of [['sidebar-toggle',document.body,'sidebarCollapsed'],['doc-tree-toggle',root,'treeCollapsed'],['doc-outline-toggle',root,'outlineCollapsed']]){
 if(element.dataset[key]==='true')document.getElementById(id).click();
}return {ready:true};})()'''},
    ]
    chrome = json.loads(next(e['result'] for e in events if e.get('id') == 'toolbar_layout'))
    for css_width, css_height in ((624, 394), (640, 480), (900, 480)):
        # The native runner sizes the Form client, which includes ToolStrip.
        # Derive physical pixels from this live window's actual monitor DPI.
        reopen_steps.extend([
            {'action': 'resize', 'width': round(css_width * dpi / 96),
             'height': round(css_height * dpi / 96) + chrome['toolbar_bottom']},
            {'action': 'script', 'id': f'short-{css_width}', 'script': r'''(async()=>{
await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
const content=document.getElementById('doc-content'),grid=document.querySelector('.doc-reader-grid');
const style=getComputedStyle(content),rect=content.getBoundingClientRect(),bounds=grid.getBoundingClientRect();
return {width:innerWidth,height:innerHeight,inner:content.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom),
bottom:rect.bottom,gridBottom:bounds.bottom,overflow:document.documentElement.scrollWidth>innerWidth};})()'''},
        ])
    reopen_steps.append({'action': 'close'})
    second_script.write_text(json.dumps(reopen_steps), encoding='utf-8')
    reopened = subprocess.run(command + [str(second_script)], cwd=tmp_path, env=env,
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    restored = json.loads(next(json.loads(row)['result'] for row in reopened.stdout.splitlines()
                               if json.loads(row).get('id') == 'restored'))
    assert restored == dict(sidebar='true', tree='true', outline='true')
    short_layouts = [json.loads(event['result']) for row in reopened.stdout.splitlines()
                     if (event := json.loads(row)).get('id', '').startswith('short-')
                     and event.get('id') != 'short-ready']
    assert len(short_layouts) == 3
    assert all(item['inner'] > 50 and item['bottom'] <= item['gridBottom'] + 1
               and not item['overflow'] for item in short_layouts), short_layouts
    assert chapter.read_text(encoding='utf-8') == source
    evidence = ROOT / 'docs/nbl/verification'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'reading-clarity-dpi.png').write_bytes(png)
    (evidence / 'reading-clarity-integrated.json').write_text(json.dumps(
        dict(dpi=dpi, awareness=awareness, page=reading, capture=dict(width=width, height=height),
             restored=restored, short_layouts=short_layouts, doc_unchanged=True), ensure_ascii=False, indent=2), encoding='utf-8')


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
    evidence = ROOT / 'docs/nbl/verification'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'native-cli-document.png').write_bytes(screenshot.read_bytes())


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
        audit = json.dumps({
            'parent_pid': process.pid, 'ready': ready,
            'processes': [p for p in network['processes'] if p['ProcessId'] in owned],
            'listeners': listeners}, indent=2)
        (tmp_path / 'native-process-listeners.json').write_text(audit, encoding='utf-8')
        evidence = ROOT / 'docs/nbl/verification'
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'native-process-listeners.json').write_text(audit, encoding='utf-8')
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
