"""Bounded Windows native-UI instance probes over SID-protected named pipes.

Registration files are only hints. Reuse requires a pipe owned by the registered
process incarnation and an exact live host identity response. No TCP, file
mutation, process termination or window messaging occurs in this client.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import json
import os
import re
import threading
import time

NATIVE_PROBE_TIMEOUT_SECONDS = 3.0
MAX_CONTROL_MESSAGE_BYTES = 4096
_RECORD_FIELDS = frozenset({'schema_version', 'transport', 'instance_id', 'pid',
                            'process_started', 'window_id', 'pipe_name'})
_RESPONSE_FIELDS = frozenset({'ok', 'instance_id', 'pid', 'window_id', 'ready'})
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_ERROR_IO_PENDING = 997
_ERROR_PIPE_BUSY = 231
_ERROR_FILE_NOT_FOUND = 2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
_WAIT_OBJECT_0 = 0
_PIPE_RETRY_MILLISECONDS = 20


class _Overlapped(ctypes.Structure):
    # ULONG_PTR fields are pointer-sized on both x86 and x64 Windows. The offset
    # union has the same size/alignment as two DWORDs (or one pointer).
    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('hEvent', w.HANDLE)]


def _windows_api():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'GetProcessTimes': ([w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
        'GetExitCodeProcess': ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        'CloseHandle': ([w.HANDLE], w.BOOL),
        'CreateFileW': ([w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
        'WaitNamedPipeW': ([w.LPCWSTR, w.DWORD], w.BOOL),
        'GetNamedPipeServerProcessId': ([w.HANDLE, ctypes.POINTER(w.ULONG)], w.BOOL),
        'CreateEventW': ([w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
        'ReadFile': ([w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(_Overlapped)], w.BOOL),
        'WriteFile': ([w.HANDLE, w.LPCVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(_Overlapped)], w.BOOL),
        'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
        'GetOverlappedResult': ([w.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(w.DWORD), w.BOOL], w.BOOL),
        'CancelIoEx': ([w.HANDLE, ctypes.POINTER(_Overlapped)], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _valid_pid(pid):
    return type(pid) is int and 0 < pid < 2**32


def _identity(api, pid):
    handle = api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        status = w.DWORD()
        if (not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user))
                or not api.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value != _STILL_ACTIVE):
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        api.CloseHandle(handle)


def process_identity(pid) -> str | None:
    """Return a live Windows process's creation FILETIME, never only its PID."""
    if os.name != 'nt' or not _valid_pid(pid):
        return None
    try:
        return _identity(_windows_api(), pid)
    except (OSError, ValueError, AttributeError):
        return None


def _valid_record(record):
    if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
        return False
    instance_id, started, pipe_name = (record[key] for key in ('instance_id', 'process_started', 'pipe_name'))
    return (type(record['schema_version']) is int and record['schema_version'] == 1
            and record['transport'] == 'webview2' and _valid_pid(record['pid'])
            and type(record['window_id']) is int and 0 < record['window_id'] < 2**64
            and isinstance(instance_id, str) and 1 <= len(instance_id) <= 128
            and all(32 <= ord(char) <= 126 for char in instance_id)
            and isinstance(started, str) and bool(re.fullmatch(r'[1-9][0-9]{0,19}', started))
            and int(started) < 2**64 and isinstance(pipe_name, str)
            and bool(re.fullmatch(r'icode-ui-[A-Za-z0-9_.-]{1,119}', pipe_name)))


def _milliseconds(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    return max(1, int(remaining * 1000))


def _transfer(api, handle, value, *, writing, deadline):
    event = api.CreateEventW(None, True, False, None)
    if not event:
        raise OSError('control event unavailable')
    overlapped = _Overlapped(hEvent=event)
    buffer = ctypes.create_string_buffer(value, len(value)) if writing else ctypes.create_string_buffer(value)
    transferred = w.DWORD()
    try:
        _milliseconds(deadline)
        operation = api.WriteFile if writing else api.ReadFile
        completed = operation(handle, buffer, len(buffer), ctypes.byref(transferred), ctypes.byref(overlapped))
        if not completed:
            if ctypes.get_last_error() != _ERROR_IO_PENDING:
                raise OSError('control I/O failed')
            try:
                if api.WaitForSingleObject(event, _milliseconds(deadline)) != _WAIT_OBJECT_0:
                    raise TimeoutError()
            except BaseException:
                api.CancelIoEx(handle, ctypes.byref(overlapped))
                # CancelIoEx only requests cancellation. This worker retains the
                # buffer/OVERLAPPED/event until actual completion, even if a slow
                # driver outlives the caller's bounded three-second wait.
                api.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), True)
                raise
            if not api.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), False):
                raise OSError('control I/O completion failed')
        if not transferred.value:
            raise OSError('control pipe closed')
        return transferred.value if writing else buffer.raw[:transferred.value]
    finally:
        api.CloseHandle(event)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate control response key')
        result[key] = value
    return result


def _valid_response(response, record):
    return (isinstance(response, dict) and set(response) == _RESPONSE_FIELDS
            and response['ok'] is True and response['ready'] is True
            and response['instance_id'] == record['instance_id']
            and type(response['pid']) is int and response['pid'] == record['pid']
            and type(response['window_id']) is int and response['window_id'] == record['window_id'])


def _probe_windows(record, action, deadline):
    api = _windows_api()
    path = '\\\\.\\pipe\\' + record['pipe_name']
    handle = _INVALID_HANDLE
    try:
        while True:
            _milliseconds(deadline)
            # Identification-only SQOS prevents server impersonation of this client.
            flags = _FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION
            handle = api.CreateFileW(path, _GENERIC_READ | _GENERIC_WRITE, 0, None,
                                     _OPEN_EXISTING, flags, None)
            if handle not in (None, _INVALID_HANDLE):
                break
            error = ctypes.get_last_error()
            if error == _ERROR_PIPE_BUSY:
                api.WaitNamedPipeW(path, min(_PIPE_RETRY_MILLISECONDS, _milliseconds(deadline)))
            elif error == _ERROR_FILE_NOT_FOUND:
                # The host recreates its single-client server after each reply.
                # Retry only connection setup, never replay an action after I/O.
                time.sleep(min(_PIPE_RETRY_MILLISECONDS, _milliseconds(deadline)) / 1000)
            else:
                return None
        server_pid = w.ULONG()
        if (not api.GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid))
                or server_pid.value != record['pid'] or _identity(api, server_pid.value) != record['process_started']):
            return None
        raw = json.dumps({'action': action, 'instance_id': record['instance_id']}, separators=(',', ':')).encode('utf-8') + b'\n'
        offset = 0
        while offset < len(raw):
            offset += _transfer(api, handle, raw[offset:], writing=True, deadline=deadline)
        reply = bytearray()
        while b'\n' not in reply:
            if len(reply) >= MAX_CONTROL_MESSAGE_BYTES:
                return None
            reply.extend(_transfer(api, handle, MAX_CONTROL_MESSAGE_BYTES - len(reply), writing=False, deadline=deadline))
        if reply.count(b'\n') != 1 or not reply.endswith(b'\n'):
            return None
        response = json.loads(reply.decode('utf-8'), object_pairs_hook=_json_object)
        return response if _valid_response(response, record) else None
    finally:
        if handle not in (None, _INVALID_HANDLE):
            api.CloseHandle(handle)


def probe_native_instance(record, action='health') -> dict | None:
    """Validate a registration and live host identity within a total three seconds.

    The worker owns all native handles and I/O storage until completion. The
    caller never waits indefinitely for a cancellation acknowledgment; failed,
    stale or inaccessible instances simply return None without changing state.
    """
    if (not _valid_record(record) or not isinstance(action, str)
            or action not in {'health', 'focus'} or os.name != 'nt'):
        return None
    # Snapshot caller-owned metadata before starting any asynchronous work.
    entry = dict(record)
    deadline = time.monotonic() + NATIVE_PROBE_TIMEOUT_SECONDS
    finished = threading.Event()
    result = []
    def probe():
        try:
            result.append(_probe_windows(entry, action, deadline))
        except (OSError, ValueError, UnicodeError, AttributeError, RecursionError):
            result.append(None)
        finally:
            finished.set()
    threading.Thread(target=probe, daemon=True).start()
    if not finished.wait(max(0, deadline - time.monotonic())):
        return None
    return result[0]
