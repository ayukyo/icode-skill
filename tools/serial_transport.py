#!/usr/bin/env python3
"""Optional, single-owner serial transport. Importing this module needs no extras.

All ports are caller supplied. ``budgets`` is the single configuration location
for finite timing/byte limits; DEFAULT_BUDGETS documents their units/defaults.
Read/write calls are capped to half shutdown_grace_seconds so cancellation is
observed between calls. OS/driver open/close and failing storage are outside a
Python hard-real-time guarantee. DTR/RTS false does not promise glitch-free open.

Third-party notice: the Windows open adapter is adapted from pyserial 3.5
serialwin32.Serial.open (https://github.com/pyserial/pyserial/tree/v3.5), with
buffer purging/resizing removed. Upstream BSD-3-Clause notice follows:

Copyright (c) 2001-2020 Chris Liechti <cliechti@gmx.net>
All Rights Reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
* Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.
* Neither the name of the copyright holder nor the names of its contributors
  may be used to endorse or promote products derived from this software
  without specific prior written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

DEFAULT_BUDGETS = {
    "listen_seconds": 15.0,
    "exec_seconds": 30.0,
    "read_timeout": 0.1,
    "write_timeout": 0.5,
    "ready_timeout": 5.0,
    "identity_timeout": 5.0,
    "shutdown_grace_seconds": 2.0,
    "matcher_poll_seconds": 0.02,
    "matcher_cleanup_seconds": 0.5,
    "max_raw_bytes": 16 * 1024 * 1024,
    "max_script_bytes": 1024 * 1024,
}
# Each octal printf source line stays below POSIX's minimum MAX_CANON (255).
# Chunking serial.write alone does not shorten a canonical terminal input line.
PROTOCOL_SOURCE_BYTES_PER_LINE = 32
_BOOL_FIELDS = ("xonxoff", "rtscts", "dsrdtr", "dtr", "rts")
_COM = re.compile(r"^(?:\\\\\.\\)?COM0*([1-9][0-9]*|0)$", re.IGNORECASE)


class PortBusyError(OSError):
    """A live owner holds the advisory lock; never terminate that owner."""


def canonical_port(port):
    """Map Windows spelling and POSIX symlink/device aliases to one lock key."""
    if not isinstance(port, str) or not port.strip() or "\x00" in port:
        raise ValueError("serial port must be supplied explicitly")
    port = port.strip()
    match = _COM.fullmatch(port)
    if match:
        return "windows:COM" + str(int(match.group(1)))
    if "://" in port:
        raise ValueError("serial URL/network transports are not supported")
    real = os.path.realpath(os.path.expanduser(port))
    try:
        info = os.stat(real)
    except OSError:
        return "path:" + os.path.normcase(real)
    if stat.S_ISCHR(info.st_mode):
        return "device:" + str(info.st_rdev)
    return "path:" + os.path.normcase(real)


def _positive(value, field, integer=False):
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        finite = False
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not finite or value <= 0 or (integer and not isinstance(value, int)):
        raise ValueError(field + " must be a finite positive " + ("integer" if integer else "number"))


def load_connection(config, connection):
    """Load a named serial connection from a JSON path, wrapper, or object.

    Accepted wrappers: device_config.connections, connections; a connections
    list must contain exactly one entry with the requested name. Unknown fields
    are retained and bound by the operation configuration digest.
    """
    if not isinstance(connection, str) or not connection.strip():
        raise ValueError("connection name is required")
    if isinstance(config, (str, os.PathLike)):
        with open(config, encoding="utf-8-sig") as source:
            config = json.load(source)
    if not isinstance(config, dict):
        raise ValueError("serial configuration must be an object")
    config = config.get("device_config", config)
    if not isinstance(config, dict):
        raise ValueError("device_config must be an object")
    if "connections" in config:
        connections = config["connections"]
        if not isinstance(connections, list):
            raise ValueError("connections must be an array")
        matches = [item for item in connections if isinstance(item, dict) and item.get("name") == connection]
        if len(matches) != 1:
            raise ValueError("connection name must identify exactly one configured connection")
        config = matches[0]
    cfg = copy.deepcopy(config)
    if cfg.get("type") != "serial" or cfg.get("name") != connection:
        raise ValueError("requested connection must be a named serial connection")
    canonical_port(cfg.get("port"))
    cfg["port"] = cfg["port"].strip()
    if "<" in cfg["port"] or ">" in cfg["port"]:
        raise ValueError("replace the serial port placeholder explicitly")
    match = _COM.fullmatch(cfg["port"])
    if match:
        cfg["port"] = "COM" + str(int(match.group(1)))
    _positive(cfg.get("baud"), "baud", integer=True)
    cfg.setdefault("bytesize", 8)
    cfg.setdefault("parity", "N")
    cfg.setdefault("stopbits", 1)
    if type(cfg["bytesize"]) is not int or cfg["bytesize"] not in (5, 6, 7, 8):
        raise ValueError("bytesize must be 5, 6, 7, or 8")
    if cfg["parity"] not in ("N", "E", "O", "M", "S"):
        raise ValueError("parity must be N, E, O, M, or S")
    if isinstance(cfg["stopbits"], bool) or cfg["stopbits"] not in (1, 1.5, 2):
        raise ValueError("stopbits must be 1, 1.5, or 2")
    for field in _BOOL_FIELDS:
        cfg.setdefault(field, False)
        if not isinstance(cfg[field], bool):
            raise ValueError(field + " must be boolean")
    budgets = cfg.get("budgets", {})
    if not isinstance(budgets, dict) or set(budgets) - set(DEFAULT_BUDGETS):
        raise ValueError("budgets contains unknown limits or is not an object")
    cfg["budgets"] = dict(DEFAULT_BUDGETS, **budgets)
    for field, value in cfg["budgets"].items():
        _positive(value, "budgets." + field, integer=field.startswith("max_"))
    # Ensures canonical hashing/evidence will not silently encode NaN or objects.
    json.dumps(cfg, allow_nan=False)
    return cfg


def transport_settings(config):
    """The effective settings actually passed to the backend, including clamps."""
    budgets = config["budgets"]
    quantum = min(1.0, budgets["shutdown_grace_seconds"] / 2)
    return {"port": config["port"], "baudrate": config["baud"],
            **{field: config[field] for field in (*_BOOL_FIELDS, "bytesize", "parity", "stopbits")},
            "read_timeout": min(budgets["read_timeout"], quantum),
            "write_timeout": min(budgets["write_timeout"], quantum),
            "exclusive_requested": os.name != "nt", "preserve_queued_input": True}


class PortLock:
    """Nonblocking process lock, stable across releases (lock file is not deleted)."""
    def __init__(self, port, lock_dir=None):
        self.key = canonical_port(port)
        directory = Path(lock_dir) if lock_dir else Path(tempfile.gettempdir()) / "icode-serial-locks"
        self.path = directory / (hashlib.sha256(self.key.encode()).hexdigest() + ".lock")
        self.file = None

    def __enter__(self):
        directory = self.path.parent.absolute()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for parent in (directory, *directory.parents):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("serial lock directory must not traverse links/reparse points")
        info = directory.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("serial lock parent must be a directory")
        if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise OSError("serial lock directory must be owned by this user and not writable by others")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        if self.path.is_symlink():
            raise OSError("serial lock file must not be a link")
        descriptor = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            path_info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                    or stat.S_ISLNK(path_info.st_mode) or info.st_ino != path_info.st_ino \
                    or getattr(path_info, "st_file_attributes", 0) & 0x400:
                raise OSError("serial lock must be an unlinked regular file at the expected path")
            if os.name != "nt" and info.st_uid != os.getuid():
                raise OSError("serial lock file is owned by another user")
            self.file = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, 2)
                if not self.file.tell():
                    self.file.write(b"\0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise PortBusyError("serial port is owned by another session: " + self.key) from exc
        return self

    def __exit__(self, *args):
        if self.file is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


def serial_factory():
    """Load pinned pyserial only on demand, preserving queued RX during open.

    pyserial 3.5's normal open clears input on both supported platforms. POSIX
    offers an overridable hook; Windows needs its handle initialization without
    the normal PurgeComm call. No process-global backend function is patched.
    """
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("optional serial tools require tools/serial-requirements.txt (pyserial==3.5)") from exc
    if serial.VERSION != "3.5":
        raise RuntimeError("serial backend must be pyserial==3.5; passive open adapter is version bounded")
    if os.name != "nt":
        class PreserveInputSerial(serial.Serial):
            def _reset_input_buffer(self):
                # The session never invokes reset; suppress only open's hook.
                return None
        return PreserveInputSerial
    import ctypes
    from serial import serialwin32
    win32 = serialwin32.win32

    class PreserveInputSerial(serial.Serial):
        def open(self):
            if self._port is None or self.is_open:
                raise serial.SerialException("port missing or already open")
            name = self.name
            match = _COM.fullmatch(name)
            if match:
                name = "\\\\.\\COM" + str(int(match.group(1)))
            handle = win32.CreateFile(name, win32.GENERIC_READ | win32.GENERIC_WRITE,
                                      0, None, win32.OPEN_EXISTING,
                                      win32.FILE_ATTRIBUTE_NORMAL | win32.FILE_FLAG_OVERLAPPED, 0)
            if handle == win32.INVALID_HANDLE_VALUE:
                raise serial.SerialException("serial open failed: " + str(ctypes.WinError()))
            self._port_handle = handle
            self._overlapped_read = self._overlapped_write = None
            self._orgTimeouts = win32.COMMTIMEOUTS()
            try:
                if not win32.GetCommTimeouts(handle, ctypes.byref(self._orgTimeouts)):
                    raise serial.SerialException("cannot read original serial timeouts")
                self._overlapped_read = win32.OVERLAPPED()
                self._overlapped_read.hEvent = win32.CreateEvent(None, 1, 0, None)
                self._overlapped_write = win32.OVERLAPPED()
                self._overlapped_write.hEvent = win32.CreateEvent(None, 0, 0, None)
                if not self._overlapped_read.hEvent or not self._overlapped_write.hEvent:
                    raise serial.SerialException("cannot allocate serial completion events")
                self._reconfigure_port()
                # Deliberately no SetupComm, PurgeComm, reset or BREAK.
                self.is_open = True
            except BaseException:
                try:
                    self._close()
                finally:
                    self._port_handle = None
                raise
    return PreserveInputSerial


class SerialTransport:
    """Own the lock and one configured handle. No TX occurs during construction."""
    def __init__(self, config, factory=None):
        self.config = config
        self.factory = factory
        self.lock = PortLock(config["port"])
        self.serial = None

    def __enter__(self):
        self.lock.__enter__()
        try:
            self.serial = (self.factory or serial_factory())(port=None)
            cfg = self.config
            for field in _BOOL_FIELDS:
                setattr(self.serial, field, cfg[field])
            self.serial.baudrate = cfg["baud"]
            for field in ("bytesize", "parity", "stopbits"):
                setattr(self.serial, field, cfg[field])
            settings = transport_settings(cfg)
            self.serial.timeout = settings["read_timeout"]
            self.serial.write_timeout = settings["write_timeout"]
            if os.name != "nt" and hasattr(self.serial, "exclusive"):
                self.serial.exclusive = True
            self.serial.port = cfg["port"]
            self.serial.open()
            return self.serial
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self.serial is not None:
                self.serial.close()
        finally:
            self.lock.__exit__(*args)
