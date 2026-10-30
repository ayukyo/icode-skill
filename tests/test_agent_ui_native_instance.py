"""Native instance identity and named-pipe probes; no TCP or user metadata writes."""
import ctypes
import importlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent_runtime'))


def test_native_instance_client_is_available():
    assert importlib.util.find_spec('icode_agent.native_instance') is not None, 'native instance client is missing'


@pytest.fixture
def native():
    return importlib.import_module('icode_agent.native_instance')


def record(**extra):
    value = {'schema_version':1, 'transport':'webview2', 'instance_id':'test-instance',
             'pid':os.getpid(), 'process_started':'123456', 'window_id':123,
             'pipe_name':'icode-ui-' + uuid.uuid4().hex}
    value.update(extra)
    return value


@pytest.mark.parametrize('bad', [None, [], {}, record(token='must-not-be-accepted'),
    record(schema_version=True), record(transport='http'), record(pid=True), record(pid=-1),
    record(window_id=False), record(process_started='not-filetime'), record(process_started='0'),
    record(instance_id=''), record(instance_id='bad\nline'), record(pipe_name='other-prefix'),
    record(pipe_name='icode-ui-bad/path'), record(pipe_name='icode-ui-' + 'x'*120)])
def test_bad_metadata_never_reaches_windows_api(native, monkeypatch, bad):
    monkeypatch.setattr(native, '_probe_windows', lambda *args: pytest.fail('invalid metadata reached OS'))
    assert native.probe_native_instance(bad) is None


@pytest.mark.parametrize('pid', [None, True, 0, -1, 2**32, '123'])
def test_invalid_process_identity_returns_none(native, pid):
    assert native.process_identity(pid) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows process identity API')
def test_current_process_identity_is_stable_filetime(native):
    started = native.process_identity(os.getpid())
    assert isinstance(started, str) and started.isdigit() and int(started) > 0
    assert native.process_identity(os.getpid()) == started


@pytest.fixture
def pipe_server(native):
    if os.name != 'nt':
        pytest.skip('Windows named-pipe API')
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateNamedPipeW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.LPVOID]
    api.CreateNamedPipeW.restype = w.HANDLE
    api.ConnectNamedPipe.argtypes = [w.HANDLE,w.LPVOID]
    api.ConnectNamedPipe.restype = w.BOOL
    api.ReadFile.argtypes = [w.HANDLE,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD),w.LPVOID]
    api.ReadFile.restype = w.BOOL
    api.WriteFile.argtypes = [w.HANDLE,w.LPCVOID,w.DWORD,ctypes.POINTER(w.DWORD),w.LPVOID]
    api.WriteFile.restype = w.BOOL
    api.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    servers = []
    def start(response=None, delay=0):
        entry = record(process_started=native.process_identity(os.getpid()))
        handle = api.CreateNamedPipeW('\\\\.\\pipe\\' + entry['pipe_name'], 3, 0, 1, 4096, 4096, 1000, None)
        assert handle not in (None, ctypes.c_void_p(-1).value)
        requests = []
        def serve():
            try:
                if not api.ConnectNamedPipe(handle, None) and ctypes.get_last_error() != 535:
                    return
                buf, count = ctypes.create_string_buffer(4096), w.DWORD()
                if not api.ReadFile(handle, buf, 4096, ctypes.byref(count), None):
                    return
                requests.append(json.loads(buf.raw[:count.value]))
                time.sleep(delay)
                reply = response if response is not None else {
                    'ok':True,'instance_id':entry['instance_id'],'pid':entry['pid'],
                    'window_id':entry['window_id'],'ready':True}
                raw = reply if isinstance(reply, bytes) else json.dumps(reply).encode() + b'\n'
                api.WriteFile(handle, raw, len(raw), ctypes.byref(count), None)
            finally:
                api.CloseHandle(handle)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        servers.append((thread, entry))
        return entry, requests
    yield start
    for thread, entry in servers:
        if thread.is_alive():
            # Release an unused ConnectNamedPipe/ReadFile without closing a
            # possibly reused server HANDLE from a different thread.
            client = api.CreateFileW('\\\\.\\pipe\\' + entry['pipe_name'], 0xC0000000, 0, None, 3, 0, None)
            if client not in (None, ctypes.c_void_p(-1).value):
                api.CloseHandle(client)
        thread.join(timeout=1.5)
        assert not thread.is_alive(), 'owned test pipe server failed to exit'


@pytest.mark.parametrize('action', ['health', 'focus'])
def test_real_named_pipe_binds_owner_identity_and_response(native, pipe_server, action):
    entry, requests = pipe_server()
    response = native.probe_native_instance(entry, action=action)
    assert response == {'ok':True,'instance_id':entry['instance_id'],'pid':entry['pid'],
                        'window_id':entry['window_id'],'ready':True}
    assert requests == [{'action':action,'instance_id':entry['instance_id']}]


@pytest.mark.parametrize('change', [{'process_started':'123'}, {'pid':1}])
def test_real_pipe_rejects_metadata_process_identity(native, pipe_server, change):
    entry, requests = pipe_server()
    entry.update(change)
    assert native.probe_native_instance(entry) is None
    assert not requests


@pytest.mark.parametrize('reply', [b'not-json\n', b'{}\n', b'x'*4097,
    b'{"ok":true,"ok":false}\n', {'ok':True,'instance_id':'forged','pid':1,'window_id':1,'ready':True}])
def test_real_pipe_rejects_bad_response(native, pipe_server, reply):
    entry, _ = pipe_server(reply)
    assert native.probe_native_instance(entry) is None


def test_real_pipe_timeout_is_bounded(native, pipe_server, monkeypatch):
    entry, _ = pipe_server(delay=1)
    monkeypatch.setattr(native, 'NATIVE_PROBE_TIMEOUT_SECONDS', .2)
    started = time.monotonic()
    assert native.probe_native_instance(entry) is None
    assert time.monotonic() - started < .5


@pytest.mark.parametrize('action', ['execute', None, [], {}, False])
def test_probe_rejects_unknown_action_without_io(native, monkeypatch, action):
    monkeypatch.setattr(native, '_probe_windows', lambda *args: pytest.fail('bad action reached OS'))
    assert native.probe_native_instance(record(), action=action) is None


@pytest.mark.skipif(os.name != 'nt' or os.environ.get('ICODE_RUN_NATIVE_UI_TESTS') != '1'
                    or not os.environ.get('ICODE_NATIVE_HOST_EXE'), reason='explicit real native host test')
def test_actual_host_control_pipe_health_and_focus(native, tmp_path, monkeypatch):
    import icode_agent.native_ui as launcher
    from icode_agent.ui_server import AgentUIService
    service = AgentUIService(None, ROOT/'tools/icode_control.py', index_path=tmp_path/'index.json',
                             settings_path=tmp_path/'settings.json', docs_root=tmp_path/'docs')
    monkeypatch.setattr(launcher, 'AgentUIService', lambda **kwargs: service)
    pipe_name = 'icode-ui-' + uuid.uuid4().hex
    events, results = queue.Queue(), []
    def launch():
        try:
            results.append(launcher.run_native_ui(os.environ['ICODE_NATIVE_HOST_EXE'], ticket_dir=None,
                control_path=ROOT/'tools/icode_control.py',seed_project_paths=[],initial_ticket_id=None,
                initial_project_id=None,user_data_dir=tmp_path/'profile',control_pipe_name=pipe_name,
                on_ready=events.put))
        except Exception as exc:
            events.put(exc)
    thread = threading.Thread(target=launch, daemon=True)
    thread.start()
    info = events.get(timeout=35)
    assert isinstance(info, dict), info
    try:
        entry = record(instance_id=info['instance_id'],pid=info['pid'],window_id=info['window_id'],
                       process_started=native.process_identity(info['pid']),pipe_name=pipe_name)
        assert native.probe_native_instance(entry)['ready'] is True
        assert native.probe_native_instance(entry, action='focus')['window_id'] == info['window_id']
    finally:
        from ctypes import wintypes as w
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.PostMessageW.argtypes = [w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        user.PostMessageW.restype = w.BOOL
        user.PostMessageW(info['window_id'], 0x10, 0, 0)
        thread.join(timeout=10)
    assert not thread.is_alive() and results == [0]
