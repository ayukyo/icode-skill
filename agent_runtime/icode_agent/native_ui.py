"""WebView2 host transport: UTF-8 JSONL over private process pipes, never TCP."""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.parse import urlsplit

from .ui_server import AgentUIInMemoryDispatcher, AgentUIService, NATIVE_UI_ORIGIN

MAX_NATIVE_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_NATIVE_INFLIGHT = 16
NATIVE_TIMEOUT_SECONDS = 30


class NativeUIError(RuntimeError):
    """A bounded, path-free failure safe for the CLI startup error response."""


def decode_native_request(message):
    """Validate framing before any request can reach the shared UI handler."""
    fields = {'type', 'id', 'method', 'url', 'headers', 'body_base64'}
    if not isinstance(message, dict) or set(message) != fields or message['type'] != 'request':
        raise NativeUIError('原生 UI 请求消息无效')
    request_id = message['id']
    if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
            or any(ord(c) < 32 or ord(c) == 127 for c in request_id)):
        raise NativeUIError('原生 UI 请求 ID 无效')
    if (not isinstance(message['method'], str) or not isinstance(message['url'], str)
            or not isinstance(message['headers'], dict) or not isinstance(message['body_base64'], str)):
        raise NativeUIError('原生 UI 请求字段无效')
    try:
        body = base64.b64decode(message['body_base64'], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise NativeUIError('原生 UI 请求 base64 无效') from exc
    return request_id, message['method'], message['url'], message['headers'], body


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NativeUIError('原生 UI JSON 含重复字段')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('JSON cannot contain non-finite numbers')


def _encode(message):
    try:
        raw = json.dumps(message, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
    except (ValueError, TypeError, UnicodeError) as exc:
        raise NativeUIError('原生 UI 响应 JSON 无效') from exc
    if len(raw) > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeUIError('原生 UI 消息过大')
    return raw


class _NativeSession:
    def __init__(self, process, dispatcher, *, test_mode=False):
        self.process = process
        self.dispatcher = dispatcher
        self.stopped = threading.Event()
        self.events = queue.Queue(maxsize=MAX_NATIVE_INFLIGHT * 3 + 8)
        self.writes = queue.Queue(maxsize=MAX_NATIVE_INFLIGHT + 1)
        self.threads = []
        self.pending = {}
        self.test_mode = test_mode

    def _put(self, target, value):
        while not self.stopped.is_set():
            try:
                target.put(value, timeout=.1)
                return
            except queue.Full:
                continue

    def _start(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        self.threads.append(thread)
        thread.start()

    def _read(self):
        try:
            while not self.stopped.is_set():
                raw = self.process.stdout.readline(MAX_NATIVE_MESSAGE_BYTES + 1)
                if not raw:
                    raise NativeUIError('原生 UI 宿主管道已关闭')
                if len(raw) > MAX_NATIVE_MESSAGE_BYTES:
                    raise NativeUIError('原生 UI 消息过大')
                if not raw.endswith(b'\n'):
                    raise NativeUIError('原生 UI JSONL 消息不完整')
                try:
                    message = json.loads(raw.decode('utf-8'), object_pairs_hook=_json_object,
                                         parse_constant=_reject_constant)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise NativeUIError('原生 UI 宿主 JSON 无效') from exc
                self._put(self.events, ('message', message))
        except (NativeUIError, OSError, ValueError) as exc:
            self._put(self.events, ('error', exc if isinstance(exc, NativeUIError)
                                    else NativeUIError('原生 UI 管道读取失败')))

    def _write(self):
        while not self.stopped.is_set():
            try:
                request_id, raw = self.writes.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (OSError, ValueError):
                self._put(self.events, ('error', NativeUIError('原生 UI 管道写入失败')))
                return
            if request_id is not None:
                self._put(self.events, ('written', request_id))

    def _request(self, request):
        request_id, method, url, headers, body = request
        try:
            response = self.dispatcher.dispatch(method, url, headers, body)
            raw = _encode({'type': 'response', 'id': request_id, 'status': response.status,
                           'headers': response.headers,
                           'body_base64': base64.b64encode(response.body).decode('ascii')})
            self._put(self.writes, (request_id, raw))
        except Exception:
            # Never include a request body, URL, document path or provider error in logs.
            self._put(self.events, ('error', NativeUIError('原生 UI 请求分派失败')))

    def run(self, init):
        self._start(self._read)
        self._start(self._write)
        self._put(self.writes, (None, _encode(init)))
        ready = False
        ready_deadline = time.monotonic() + NATIVE_TIMEOUT_SECONDS
        while True:
            now = time.monotonic()
            if not ready and now >= ready_deadline:
                raise NativeUIError('原生 UI 宿主就绪超时')
            if any(now >= deadline for deadline in self.pending.values()):
                raise NativeUIError('原生 UI 请求超时')
            try:
                kind, value = self.events.get(timeout=.05)
            except queue.Empty:
                continue
            if kind == 'error':
                raise value
            if kind == 'written':
                self.pending.pop(value, None)
                continue
            if not isinstance(value, dict):
                raise NativeUIError('原生 UI 消息必须是对象')
            if value.get('type') == 'request':
                request = decode_native_request(value)
                request_id = request[0]
                if request_id in self.pending:
                    raise NativeUIError('原生 UI 请求 ID 重复')
                if len(self.pending) >= MAX_NATIVE_INFLIGHT:
                    raise NativeUIError('原生 UI 请求超过并发上限')
                self.pending[request_id] = time.monotonic() + NATIVE_TIMEOUT_SECONDS
                self._start(self._request, request)
                # Finished threads do not accumulate for the lifetime of the window.
                self.threads = [thread for thread in self.threads if thread.is_alive()]
                continue
            if value.get('type') != 'event':
                raise NativeUIError('原生 UI 宿主消息类型无效')
            event = value.get('event')
            if event == 'ready':
                pid, window_id = value.get('pid'), value.get('window_id')
                if (set(value) != {'type', 'event', 'pid', 'window_id'} or ready
                        or type(pid) is not int or pid != self.process.pid
                        or type(window_id) is not int or window_id <= 0):
                    raise NativeUIError('原生 UI ready 事件无效')
                ready = True
                print(json.dumps({'ok': True, 'transport': 'webview2', 'origin': NATIVE_UI_ORIGIN,
                                  'instance_id': self.dispatcher.service.instance_id,
                                  'pid': pid, 'window_id': window_id}), flush=True)
            elif event == 'closed' and set(value) == {'type', 'event'}:
                if not ready:
                    raise NativeUIError('原生 UI 宿主在就绪前关闭')
                return 0
            elif event == 'error':
                # Host detail is untrusted and might contain private paths. The CLI
                # receives a stable bounded class; diagnostic detail stays in stderr.
                message = value.get('message')
                if (set(value) != {'type', 'event', 'message'} or not isinstance(message, str)
                        or len(message) > 2048):
                    raise NativeUIError('原生 UI 错误事件无效')
                raise NativeUIError('原生 UI 宿主错误')
            elif self.test_mode and event in {'test_result', 'test_screenshot'}:
                valid = ready and set(value) == {'type', 'event'}
                if event == 'test_result':
                    step_id = value.get('id')
                    valid = (ready and set(value) == {'type', 'event', 'id', 'result'}
                             and (step_id is None or (isinstance(step_id, str) and len(step_id) <= 128
                                  and not any(ord(c) < 32 or ord(c) == 127 for c in step_id)))
                             and isinstance(value.get('result'), str))
                    if valid:
                        try:
                            json.loads(value['result'], parse_constant=_reject_constant)
                        except (ValueError, RecursionError):
                            valid = False
                if not valid:
                    raise NativeUIError('原生 UI 测试事件无效')
                print(json.dumps(value, ensure_ascii=False), flush=True)
            else:
                raise NativeUIError('原生 UI 宿主事件无效')

    def close(self):
        self.stopped.set()
        self.pending.clear()
        # Only this Popen handle is ever terminated. No process-name/PID sweeps.
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        for thread in self.threads:
            thread.join(timeout=.1)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def run_native_ui(host_executable, *, ticket_dir, control_path, seed_project_paths,
                  initial_ticket_id, initial_project_id, initial_path='/',
                  user_data_dir=None, test_script=None):
    """Own a single host until its window closes; announce success only after ready.

    Errors raise NativeUIError for the caller's normal CLI error response. Host
    diagnostics inherit stderr; stdout is reserved for one truthful ready receipt.
    """
    if not isinstance(initial_path, str) or len(initial_path) > 4096 or any(ord(c) < 32 for c in initial_path):
        raise NativeUIError('原生 UI 初始路径无效')
    parsed = urlsplit(initial_path)
    if (not initial_path.startswith('/') or initial_path.startswith('//') or '\\' in initial_path
            or parsed.scheme or parsed.netloc or parsed.fragment or '#' in initial_path):
        raise NativeUIError('原生 UI 初始路径必须位于应用内')
    service = AgentUIService(ticket_dir=ticket_dir, control_path=control_path,
                             seed_project_paths=seed_project_paths,
                             initial_ticket_id=initial_ticket_id,
                             initial_project_id=initial_project_id)
    profile = Path(user_data_dir).expanduser().resolve() if user_data_dir else (
        Path.home() / '.claude' / 'icode_data' / 'webview2' / service.instance_id).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    command = [str(Path(host_executable).resolve())]
    if test_script is not None:
        command.append('--test-script=' + str(Path(test_script).expanduser().resolve()))
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=None,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except OSError as exc:
        raise NativeUIError('原生 UI 宿主启动失败') from exc
    session = _NativeSession(process, AgentUIInMemoryDispatcher(service), test_mode=test_script is not None)
    try:
        return session.run({'type': 'init', 'origin': NATIVE_UI_ORIGIN,
                            'initial_path': initial_path, 'user_data_dir': str(profile),
                            'instance_id': service.instance_id})
    finally:
        session.close()
