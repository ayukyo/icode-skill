"""Socket-free UI routes and bounded native-host RPC using owned fixtures."""
import base64
import importlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent_runtime'))
from icode_agent.ui_server import AgentUIService, MAX_REQUEST_BYTES

ORIGIN = 'https://icode.localhost'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC')


def test_native_dispatcher_is_available_without_http_server():
    import icode_agent.ui_server as ui
    assert hasattr(ui, 'AgentUIInMemoryDispatcher'), 'socket-free dispatcher is missing'


@pytest.fixture
def dispatcher(tmp_path, monkeypatch):
    import icode_agent.ui_server as ui
    assert hasattr(ui, 'AgentUIInMemoryDispatcher'), 'socket-free dispatcher is missing'
    book = tmp_path / 'docs/project_docs/demo/main'
    book.mkdir(parents=True)
    (book / '00_overview.md').write_text('# 中文书籍\n\n![像素](pixel.png)', encoding='utf-8')
    (book / 'pixel.png').write_bytes(PNG)
    service = AgentUIService(None, ROOT / 'tools/icode_control.py',
                             index_path=tmp_path / 'index.json',
                             settings_path=tmp_path / 'settings.json', docs_root=tmp_path / 'docs')
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('native transport bound a socket'))
    return ui.AgentUIInMemoryDispatcher(service)


def dispatch(target, method='GET', path='/', *, token=True, body=b'', headers=None):
    final = {'X-ICODE-UI-Token': target.service.token} if token else {}
    final.update(headers or {})
    return target.dispatch(method, ORIGIN + path, final, body)


def test_native_home_preserves_token_nonce_and_local_assets(dispatcher):
    response = dispatch(dispatcher, path='/?view=docs')
    assert response.status == 200
    html = response.body.decode()
    assert f'content="{dispatcher.service.token}"' in html
    assert dispatcher.service.style_nonce in response.headers['Content-Security-Policy']
    assert '<script src="/assets/app.js" defer></script>' in html
    assert response.headers['Cache-Control'] == 'no-store'
    for path in ('/assets/app.js', '/assets/doc-reader.js', '/assets/vendor/mermaid.js'):
        assert dispatch(dispatcher, path=path).status == 200


def test_native_docs_routes_preserve_opaque_ids_and_binary(dispatcher):
    catalog = dispatch(dispatcher, path='/api/v1/docs/catalog')
    book_id = json.loads(catalog.body)['books'][0]['id']
    assert re.fullmatch(r'book-[0-9a-f]{32}', book_id)
    book = json.loads(dispatch(dispatcher, path='/api/v1/docs/book?id=' + book_id).body)['book']
    chapter_id = book['chapters'][0]['id']
    chapter = json.loads(dispatch(dispatcher, path=f'/api/v1/docs/chapter?book_id={book_id}&chapter_id={chapter_id}').body)['chapter']
    asset = dispatch(dispatcher, path='/api/v1/docs/asset?id=' + chapter['assets']['pixel.png'])
    assert asset.status == 200 and asset.body == PNG
    assert asset.headers['Content-Type'] == 'image/png'
    invalid = dispatch(dispatcher, path='/api/v1/docs/book?id=../secret')
    assert invalid.status == 404 and json.loads(invalid.body)['error_class'] == 'DocLibraryError'
    assert str(dispatcher.service.doc_catalog.root).encode() not in invalid.body


def test_native_diagram_keeps_opaque_sandbox_policy(dispatcher):
    response = dispatch(dispatcher, path='/assets/diagram-renderer.html')
    assert response.status == 200
    assert 'sandbox allow-scripts' in response.headers['Content-Security-Policy']
    assert 'allow-same-origin' not in response.headers['Content-Security-Policy']
    assert dispatcher.service.token.encode() not in response.body


@pytest.mark.parametrize('method,path', [('GET', '/api/v1/docs/catalog'), ('POST', '/api/v1/run')])
def test_native_missing_token_is_403(dispatcher, method, path):
    response = dispatch(dispatcher, method, path, token=False)
    assert response.status == 403 and json.loads(response.body)['error_class'] == 'UIAccessError'


@pytest.mark.parametrize('origin', ['https://attacker.invalid', 'https://icode.localhost.evil', 'null', 'https://icode.localhost/'])
def test_native_origin_spoof_is_403(dispatcher, origin):
    response = dispatch(dispatcher, 'POST', '/api/v1/run', headers={'Origin': origin})
    assert response.status == 403


def test_native_read_api_rejects_spoofed_origin_with_token(dispatcher):
    assert dispatch(dispatcher, path='/api/v1/docs/catalog', headers={'Origin': 'null'}).status == 403


def test_native_host_spoof_is_421(dispatcher):
    assert dispatch(dispatcher, headers={'Host': 'attacker.invalid'}).status == 421


def test_native_unknown_route_and_existing_validation(dispatcher):
    assert dispatch(dispatcher, path='/missing').status == 404
    response = dispatch(dispatcher, 'POST', '/api/v1/run', body=b'no json',
                        headers={'Content-Type': 'text/plain'})
    assert response.status == 415 and json.loads(response.body)['error_class'] == 'UIRequestError'
    response = dispatch(dispatcher, 'POST', '/api/v1/run', body=b'{',
                        headers={'Content-Type': 'application/json'})
    assert response.status == 400
    response = dispatch(dispatcher, 'POST', '/api/v1/run', body=b'x' * (MAX_REQUEST_BYTES + 1),
                        headers={'Content-Type': 'application/json'})
    assert response.status == 413


@pytest.mark.parametrize('method', ['HEAD', 'PATCH', 'DELETE'])
def test_native_unknown_method_keeps_security_headers(dispatcher, method):
    response = dispatch(dispatcher, method)
    assert response.status == 501
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert "script-src 'self'" in response.headers['Content-Security-Policy']
    if method == 'HEAD':
        assert response.body == b''
    else:
        assert json.loads(response.body)['error_class'] == 'UIRequestError'


@pytest.mark.parametrize('url', ['http://icode.localhost/', 'https://evil.invalid/',
                                'https://user@icode.localhost/', 'https://icode.localhost/#fragment'])
def test_native_rejects_nonlocal_request_url(dispatcher, url):
    with pytest.raises(ValueError):
        dispatcher.dispatch('GET', url, {}, b'')


def test_native_rejects_header_injection_and_length_mismatch(dispatcher):
    with pytest.raises(ValueError):
        dispatch(dispatcher, headers={'X-Test': 'ok\r\nHost: evil.invalid'})
    with pytest.raises(ValueError):
        dispatch(dispatcher, 'POST', '/api/v1/run', body=b'{}', headers={'Content-Length': '100'})


def test_native_runner_is_available():
    assert importlib.util.find_spec('icode_agent.native_ui') is not None, 'native host runner is missing'


def fake_host(tmp_path, monkeypatch, dispatcher, source):
    native = importlib.import_module('icode_agent.native_ui')
    host = tmp_path / 'fake_host.py'
    host.write_text('import sys,json,os,time\n'
                    'init=json.loads(sys.stdin.readline())\n'
                    'def emit(v):\n print(json.dumps(v),flush=True)\n' + source, encoding='utf-8')
    real_popen = subprocess.Popen
    children = []
    commands = []
    def start(command, **kwargs):
        commands.append(command)
        child = real_popen([sys.executable, '-u', str(host)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(native.subprocess, 'Popen', start)
    monkeypatch.setattr(native, 'AgentUIService', lambda **kwargs: dispatcher.service)
    monkeypatch.setattr(native, 'NATIVE_TIMEOUT_SECONDS', .4)
    return native, children, commands


def run_host(native, tmp_path, **extra):
    return native.run_native_ui(tmp_path / 'host.exe', ticket_dir=None,
                               control_path=ROOT / 'tools/icode_control.py',
                               seed_project_paths=[], initial_ticket_id=None,
                               initial_project_id=None, user_data_dir=tmp_path / 'profile', **extra)


def test_native_runner_round_trip_ready_and_clean_close(dispatcher, tmp_path, monkeypatch, capsys):
    capture = tmp_path / 'init.json'
    source = (f'open({str(capture)!r},"w").write(json.dumps(init))\n'
              'emit({"type":"request","id":"home","method":"GET","url":init["origin"]+"/", "headers":{},"body_base64":""})\n'
              'response=json.loads(sys.stdin.readline())\n'
              'assert response["status"]==200 and response["id"]=="home"\n'
              'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
              'emit({"type":"event","event":"closed"})\n')
    native, children, commands = fake_host(tmp_path, monkeypatch, dispatcher, source)
    assert run_host(native, tmp_path, initial_path='/?view=docs', test_script=tmp_path / 'test.json') == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready['ok'] and ready['transport'] == 'webview2' and ready['window_id'] == 123
    assert 'url' not in ready and ready['pid'] == children[0].pid
    init = json.loads(capture.read_text())
    assert init == {'type':'init', 'origin':ORIGIN, 'initial_path':'/?view=docs',
                    'user_data_dir':str((tmp_path/'profile').resolve()),
                    'instance_id':dispatcher.service.instance_id}
    assert '--test-script=' + str((tmp_path/'test.json').resolve()) in commands[0]
    assert children[0].poll() is not None


@pytest.mark.parametrize('source,match', [
    ('time.sleep(5)\n', '就绪超时'),
    ('pass\n', '管道已关闭'),
    ('print("not-json",flush=True)\ntime.sleep(5)\n', 'JSON'),
    ('emit({"type":"mystery"})\ntime.sleep(5)\n', '消息'),
    ('emit({"type":"event","event":"error","message":"failed"})\ntime.sleep(5)\n', '宿主错误'),
    ('emit({"type":"event","event":"ready","pid":1,"window_id":123})\ntime.sleep(5)\n', 'ready'),
])
def test_native_runner_fails_closed_and_reaps_only_owned_child(dispatcher, tmp_path, monkeypatch, capsys, source, match):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher, source)
    with pytest.raises(native.NativeUIError, match=match):
        run_host(native, tmp_path)
    assert capsys.readouterr().out == ''
    assert children[0].poll() is not None


@pytest.mark.parametrize('change', [{'body_base64':'%%%invalid'}, {'id':''}, {'type':'other'},
                                  {'headers':[]}, {'method':False}])
def test_native_rpc_rejects_malformed_request(dispatcher, change):
    native = importlib.import_module('icode_agent.native_ui')
    message = {'type':'request', 'id':'1', 'method':'GET', 'url':ORIGIN+'/', 'headers':{}, 'body_base64':''}
    message.update(change)
    with pytest.raises(native.NativeUIError):
        native.decode_native_request(message)


def test_native_runner_bounds_message_size(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
                                     'print("x"*1025,flush=True)\ntime.sleep(5)\n')
    monkeypatch.setattr(native, 'MAX_NATIVE_MESSAGE_BYTES', 1024)
    with pytest.raises(native.NativeUIError, match='消息过大'):
        run_host(native, tmp_path)
    assert children[0].poll() is not None


def test_native_request_timeout_cleans_pending_and_child(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"request","id":"slow","method":"GET","url":init["origin"]+"/", "headers":{},"body_base64":""})\n'
        'time.sleep(5)\n')
    monkeypatch.setattr(native.AgentUIInMemoryDispatcher, 'dispatch', lambda *a: time.sleep(2))
    with pytest.raises(native.NativeUIError, match='请求超时'):
        run_host(native, tmp_path)
    assert children[0].poll() is not None


def test_native_concurrency_limit_is_fail_closed(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'for n in range(17):\n'
        ' emit({"type":"request","id":str(n),"method":"GET","url":init["origin"]+"/", "headers":{},"body_base64":""})\n'
        'time.sleep(5)\n')
    monkeypatch.setattr(native.AgentUIInMemoryDispatcher, 'dispatch', lambda *a: time.sleep(2))
    with pytest.raises(native.NativeUIError, match='并发'):
        run_host(native, tmp_path)
    assert children[0].poll() is not None


def test_native_pipe_returns_large_binary_without_truncation(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'import base64\n'
        'emit({"type":"request","id":"binary","method":"GET","url":init["origin"]+"/", "headers":{},"body_base64":""})\n'
        'response=json.loads(sys.stdin.readline())\n'
        'assert base64.b64decode(response["body_base64"])==bytes(range(256))*8192\n'
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"event","event":"closed"})\n')
    from icode_agent.ui_server import UIResponse
    monkeypatch.setattr(native, 'NATIVE_TIMEOUT_SECONDS', 5)
    monkeypatch.setattr(native.AgentUIInMemoryDispatcher, 'dispatch',
                        lambda *a: UIResponse(200, {'Content-Type':'application/octet-stream'}, bytes(range(256))*8192))
    assert run_host(native, tmp_path) == 0
    assert children[0].poll() is not None


def test_native_duplicate_inflight_id_is_fail_closed(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'for n in range(2):\n'
        ' emit({"type":"request","id":"duplicate","method":"GET","url":init["origin"]+"/", "headers":{},"body_base64":""})\n'
        'time.sleep(5)\n')
    monkeypatch.setattr(native.AgentUIInMemoryDispatcher, 'dispatch', lambda *a: time.sleep(2))
    with pytest.raises(native.NativeUIError, match='ID 重复'):
        run_host(native, tmp_path)
    assert children[0].poll() is not None


@pytest.mark.parametrize('path', ['https://evil.invalid/', '//evil.invalid/', '/\\evil.invalid/', '/#fragment'])
def test_native_initial_navigation_is_local(dispatcher, tmp_path, monkeypatch, path):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher, 'pass\n')
    with pytest.raises(native.NativeUIError, match='初始路径'):
        run_host(native, tmp_path, initial_path=path)
    assert not children


def test_native_blocked_pipe_write_obeys_request_timeout(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"request","id":"blocked","method":"GET","url":init["origin"]+"/", "headers":{},"body_base64":""})\n'
        'time.sleep(5)\n')
    from icode_agent.ui_server import UIResponse
    monkeypatch.setattr(native.AgentUIInMemoryDispatcher, 'dispatch',
                        lambda *a: UIResponse(200, {}, b'x' * 2 * 1024 * 1024))
    with pytest.raises(native.NativeUIError, match='请求超时'):
        run_host(native, tmp_path)
    assert children[0].poll() is not None


def test_native_test_events_only_forwarded_in_explicit_test_mode(dispatcher, tmp_path, monkeypatch, capsys):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"event","event":"test_result","id":"dom","result":"{\\\"ok\\\":true}"})\n'
        'emit({"type":"event","event":"test_screenshot"})\n'
        'emit({"type":"event","event":"closed"})\n')
    assert run_host(native, tmp_path, test_script=tmp_path / 'test.json') == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[1] == {'type':'event','event':'test_result','id':'dom','result':'{"ok":true}'}
    assert lines[2] == {'type':'event','event':'test_screenshot'}
    assert children[0].poll() is not None


@pytest.mark.parametrize('event', [
    {'type':'event','event':'test_result','id':'dom','result':'{}'},
    {'type':'event','event':'test_screenshot'},
])
def test_native_test_events_rejected_in_normal_mode(dispatcher, tmp_path, monkeypatch, event):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
                                  f'emit({event!r})\ntime.sleep(5)\n')
    with pytest.raises(native.NativeUIError, match='事件'):
        run_host(native, tmp_path)
    assert children[0].poll() is not None


@pytest.mark.parametrize('event', [
    {'type':'event','event':'test_result','id':12,'result':'{}'},
    {'type':'event','event':'test_result','id':'x'*129,'result':'{}'},
    {'type':'event','event':'test_result','id':'dom','result':'not-json'},
    {'type':'event','event':'test_screenshot','path':'private-path'},
])
def test_native_test_events_validate_payload(dispatcher, tmp_path, monkeypatch, event):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
                                  f'emit({event!r})\ntime.sleep(5)\n')
    with pytest.raises(native.NativeUIError, match='测试事件'):
        run_host(native, tmp_path, test_script=tmp_path / 'test.json')
    assert children[0].poll() is not None


def test_native_ready_callback_runs_after_validation_before_receipt(dispatcher, tmp_path, monkeypatch, capsys):
    capture = tmp_path / 'control-init.json'
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        f'open({str(capture)!r},"w").write(json.dumps(init))\n'
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"event","event":"closed"})\n')
    calls = []
    def ready_callback(info):
        assert capsys.readouterr().out == ''
        assert info['pid'] == children[0].pid and info['window_id'] == 123
        calls.append(dict(info))
        info['pid'] = -1  # Metadata hooks cannot change the trusted public receipt.
    assert run_host(native, tmp_path, control_pipe_name='icode-ui-test', on_ready=ready_callback) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert calls == [receipt]
    assert json.loads(capture.read_text())['control_pipe_name'] == 'icode-ui-test'
    assert children[0].poll() is not None


def test_native_ready_callback_failure_is_error_without_success_receipt(dispatcher, tmp_path, monkeypatch, capsys):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'time.sleep(5)\n')
    def broken_callback(_info):
        raise OSError('private metadata path must not leak')
    with pytest.raises(native.NativeUIError, match='ready 回调失败') as result:
        run_host(native, tmp_path, on_ready=broken_callback)
    assert isinstance(result.value.__cause__, OSError)
    assert 'private metadata' not in str(result.value)
    assert capsys.readouterr().out == ''
    assert children[0].poll() is not None


def test_native_invalid_ready_never_calls_callback(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'emit({"type":"event","event":"ready","pid":1,"window_id":123})\n'
        'time.sleep(5)\n')
    calls = []
    with pytest.raises(native.NativeUIError, match='ready 事件无效'):
        run_host(native, tmp_path, on_ready=calls.append)
    assert not calls and children[0].poll() is not None


def test_native_host_environment_cannot_override_webview_policy(dispatcher, tmp_path, monkeypatch):
    overrides = ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', 'WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER',
                 'WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER', 'WEBVIEW2_USER_DATA_FOLDER']
    for key in overrides:
        monkeypatch.setenv(key, 'unsafe-inherited-override')
    monkeypatch.setenv('webview2_additional_browser_arguments', 'unsafe-case-variant')
    monkeypatch.setenv('ICODE_TEST_ENV_PRESERVED', 'preserved')
    capture = tmp_path / 'child-env.json'
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        f'keys={overrides!r}\n'
        f'open({str(capture)!r},"w").write(json.dumps({{"overrides":[k for k in os.environ if k.upper() in keys],"preserved":os.environ["ICODE_TEST_ENV_PRESERVED"]}}))\n'
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"event","event":"closed"})\n')
    start = native.subprocess.Popen
    def assert_flags(command, **kwargs):
        assert kwargs['creationflags'] == (subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        return start(command, **kwargs)
    monkeypatch.setattr(native.subprocess, 'Popen', assert_flags)
    assert run_host(native, tmp_path) == 0
    assert json.loads(capture.read_text()) == {'overrides':[], 'preserved':'preserved'}
    assert all(key in os.environ for key in overrides)
    assert children[0].poll() is not None


@pytest.mark.parametrize('pipe_name', [None, ''])
def test_native_optional_control_pipe_keeps_original_init(dispatcher, tmp_path, monkeypatch, pipe_name):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher,
        'assert "control_pipe_name" not in init\n'
        'emit({"type":"event","event":"ready","pid":os.getpid(),"window_id":123})\n'
        'emit({"type":"event","event":"closed"})\n')
    assert run_host(native, tmp_path, control_pipe_name=pipe_name) == 0
    assert children[0].poll() is not None


@pytest.mark.parametrize('pipe_name', [False, 1, 'bad/name', 'bad\\name', 'x' * 129, 'bad\nname'])
def test_native_control_pipe_name_is_validated_before_spawn(dispatcher, tmp_path, monkeypatch, pipe_name):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher, 'pass\n')
    with pytest.raises(native.NativeUIError, match='管道名称无效'):
        run_host(native, tmp_path, control_pipe_name=pipe_name)
    assert not children


def test_native_ready_hook_must_be_callable_before_spawn(dispatcher, tmp_path, monkeypatch):
    native, children, _ = fake_host(tmp_path, monkeypatch, dispatcher, 'pass\n')
    with pytest.raises(native.NativeUIError, match='回调必须可调用'):
        run_host(native, tmp_path, on_ready=False)
    assert not children
