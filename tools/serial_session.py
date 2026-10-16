#!/usr/bin/env python3
"""Raw serial evidence CLI: list / listen / exec (no new /icode command).

Install tools/serial-requirements.txt only when using serial capabilities.
listen/exec require --config, --connection and a NEW --output-dir. Exec also
requires --script-file and --operation-ref '{"dir":"<ticket>","attempt":"<id>"}'.

Exec requires console_type=posix_shell, ready_pattern, and explicit identity
{script,pattern}, or a trusted API identity_validator(config, ready_bytes).
There is no built-in identity command. Identity scripts are remote actions and
are covered by the same operation authorization as the requested script.

operation_input(normalized_config, script_bytes) returns the EXACT string to
pass as icode_control operation --input: UTF-8 JSON with ensure_ascii=False,
sort_keys=True,separators=(',',':'). The control plane hashes that STRING via
canonical_digest(string), i.e. JSON string serialization before SHA-256, not
just SHA-256 of the binding object. action=serial_exec, class must be
external_side_effect. The full normalized config and script are digest-bound.
An operation record proves registered controlled intent; actual human authority
comes from the host/step boundary. Arbitrary scripts are not classified for
destructive/hardware risk by text inspection. Such actions need their own
appropriate authority and must not be smuggled through a generic serial intent.

Production first TX requires claim_operation's control-plane writer. No local
file, --authorized bool, or metadata supplied by a caller is authorization.
T5 integrates stage checks in authorize_operation and claim_serial_operation.
The caller must finish the operation with the result reference; this tool does
not claim a missing remote marker means the remote process has stopped.

The output is session.json, rx.bin (authoritative bytes), rx.txt (lossy view),
io_trace.jsonl (base64 bytes + UTC/monotonic clocks), result.json. Limits live in
serial_transport.DEFAULT_BUDGETS and config.budgets. RX limit exhaustion is
partial/inconclusive. Only a complete nonce protocol yields an exit code.
There is one serial owner, no host shell interpolation, retry, login, interrupt,
buffer reset, BREAK, or automatic remote cleanup. A transport error/cancel after
TX leaves remote_state=unknown, exit_code=null. DTR/RTS false cannot exclude an
OS/driver glitch, and capture covers the window AFTER successful open only.
Remote scripts inherit /dev/null as stdin so read/cat cannot consume UART
protocol lines. Explicit script redirections, including heredocs, still work.
Configured regex compilation/search runs in a killable isolated local Python
process using fixed argv and JSON stdin; pattern/text never appears in argv or
diagnostics. Deadline/cancellation stops matching and leaves evidence inconclusive.
config.budgets matcher_poll_seconds (default .02, capped .05) and
matcher_cleanup_seconds (default .5, capped .5) bound polling and each reap wait.
One exec_seconds deadline starts at run_exec entry and covers configuration,
script preparation, authorization, regex preflight and capture. Only listen's
capture window starts after actual open. OS/driver open/close cannot offer a
Python hard real-time guarantee; elapsed open time still consumes exec's budget.
Process startup counts toward matching budgets, so short budgets may refuse
before opening. No phase receives a fresh total exec budget.
Evidence execution_start_* and execution_deadline_monotonic describe that total
attempt; host_start/monotonic_start describe evidence creation, and capture_open_*
describe the successful port-open boundary.
Structured metadata redacts sensitive fields/probe source; the complete config
digest still binds authorization. Raw RX/TX can contain secrets: keep originals
under controlled storage. POSIX new directories/files request 0700/0600;
Windows ACL hardening and filesystems that ignore POSIX modes are not promised.
"""
import argparse
import base64
import codecs
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

try:
    from .serial_transport import (SerialTransport, canonical_port, load_connection,
                                   transport_settings, PROTOCOL_SOURCE_BYTES_PER_LINE)
except ImportError:
    from serial_transport import (SerialTransport, canonical_port, load_connection,
                                  transport_settings, PROTOCOL_SOURCE_BYTES_PER_LINE)


class AuthorizationError(ValueError):
    """Missing, stale, mismatched, or already consumed controlled authority."""


class SessionStopped(Exception):
    """Finite deadline, cancellation or incomplete evidence; never remote stop."""


class CaptureLimit(SessionStopped):
    pass


# This source is fixed. User patterns/text enter only through piped JSON stdin;
# the child inherits neither the UART, lock nor host stdin (close_fds=True).
_MATCH_WORKER = """import json, re, sys
try:
    request = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    if request['mode'] == 'validate':
        empty = [index for index, pattern in enumerate(request['patterns'])
                 if re.compile(pattern).search('') is not None]
        response = {'empty': empty}
    else:
        response = {'matched': re.compile(request['pattern']).search(request['text']) is not None}
except Exception:
    response = {'error': 'invalid regular expression'}
sys.stdout.write(json.dumps(response))
"""


def _isolated_match(request, deadline, cancel_event, budgets):
    """Terminate CPU-bound regex work without blocking the sole serial owner."""
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise SessionStopped("cancelled during isolated pattern matching")
        if time.monotonic() >= deadline:
            raise SessionStopped("isolated pattern matching deadline reached")

    check()
    payload = _canonical(request).encode("utf-8")
    check()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    poll_seconds = min(0.05, budgets["matcher_poll_seconds"])
    cleanup_seconds = min(0.5, budgets["matcher_cleanup_seconds"])
    try:
        process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", _MATCH_WORKER],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   close_fds=True, creationflags=flags)
    except OSError:
        raise SessionStopped("isolated pattern matcher is unavailable") from None
    pending_input = payload
    try:
        while True:
            check()
            try:
                output, _ = process.communicate(input=pending_input,
                                                timeout=min(poll_seconds, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pending_input = None
                continue
            check()
            if process.returncode != 0:
                raise SessionStopped("isolated pattern matcher failed")
            try:
                response = json.loads(output)
            except (ValueError, UnicodeError):
                raise SessionStopped("isolated pattern matcher returned an invalid response") from None
            if not isinstance(response, dict):
                raise SessionStopped("isolated pattern matcher returned an invalid response")
            if response.get("error"):
                raise ValueError("configured regular expression is invalid")
            return response
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=cleanup_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=cleanup_seconds)
            except subprocess.TimeoutExpired:
                # No UART/lock is inherited even if an OS fails to reap promptly.
                pass
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact(value):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            sensitive = any(word in normalized for word in
                            ("password", "passwd", "token", "cookie", "secret", "credential", "authorization", "apikey"))
            if sensitive or normalized in ("script", "privatekey", "auth"):
                output[key] = {"redacted": True, "sha256": hashlib.sha256(_canonical(item).encode()).hexdigest()}
            else:
                output[key] = _redact(item)
        return output
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _private_opener(path, flags):
    return os.open(path, flags, 0o600)


def operation_input(config, script):
    """Canonical input string for the *existing* control-plane operation start."""
    cfg = load_connection(config, config.get("name"))
    return _canonical({"action": "serial_exec", "operation_class": "external_side_effect",
                       "connection": cfg["name"], "port_identity": canonical_port(cfg["port"]),
                       "config_sha256": hashlib.sha256(_canonical(cfg).encode()).hexdigest(),
                       "script_sha256": hashlib.sha256(script).hexdigest()})


def _control():
    try:
        from . import icode_control
    except ImportError:
        import icode_control
    return icode_control


def authorize_operation(operation_ref, binding, *, session_id=None):
    """Read the real event chain, not an input object's assertions of authority.

    Kept as the explicit integration boundary for the control-plane stage guard.
    Injected API authorizers are trusted host code; the CLI never substitutes one.
    """
    if not isinstance(operation_ref, dict) or set(operation_ref) != {"dir", "attempt"} \
            or not all(isinstance(operation_ref.get(k), str) and operation_ref[k].strip()
                       for k in ("dir", "attempt")):
        raise AuthorizationError("operation-ref must be {dir:<ticket control dir>,attempt:<open attempt>}")
    try:
        ctl = _control()
        directory = Path(operation_ref["dir"]).resolve(strict=True)
        metadata = ctl.load_metadata(directory)
        ctl.require_vnext(metadata, directory)
        ctl.require_valid_metadata(metadata)
        ctl.ensure_execution_writable(metadata)
        ctl.require_stage_action(directory, "serial_exec")
        events, problems = ctl.verify_event_chain(directory, metadata)
        if problems:
            raise AuthorizationError("operation event chain is invalid: " + "; ".join(problems))
        event = ctl.find_operation_start(events, operation_ref["attempt"])
        payload = event.get("payload") or {}
        ctl.require_stage_action(directory, "serial_exec", expected_generation=payload.get("stage_generation"))
        if ctl.operation_finished(events, operation_ref["attempt"]) is not None:
            raise AuthorizationError("operation attempt is already finished")
        if payload.get("class") != "external_side_effect" \
                or payload.get("input_digest") != ctl.canonical_digest(binding):
            raise AuthorizationError("operation class or canonical serial input digest does not match")
        claims = ((metadata.get("extensions") or {}).get("verification") or {}).get("serial_claims") or []
        if not isinstance(claims, list) or any(not isinstance(claim, dict) for claim in claims):
            raise AuthorizationError("controlled serial claims are invalid")
        matching_claims = [claim for claim in claims if claim.get("attempt") == operation_ref["attempt"]]
        if matching_claims:
            # A fresh call has no session context and must reject a consumed
            # attempt. Only the owning live session can recheck stage/operation
            # authority after its identity probe, without acquiring a second claim.
            expected = {"attempt": operation_ref["attempt"], "session_id": session_id,
                        "operation_event_id": event["event_id"], "ticket_id": metadata["ticket_id"],
                        "input_digest": payload["input_digest"], "operation_class": payload["class"],
                        "stage_generation": payload.get("stage_generation")}
            if session_id is None or len(matching_claims) != 1 or any(
                    matching_claims[0].get(key) != value for key, value in expected.items()):
                raise AuthorizationError("operation attempt was already claimed; refusing replay")
        return {"dir": str(directory), "attempt": operation_ref["attempt"],
                "event_id": event["event_id"], "ticket_id": metadata["ticket_id"],
                "input_digest": payload["input_digest"], "operation_class": payload["class"]}
    except AuthorizationError:
        raise
    except Exception as exc:
        raise AuthorizationError("controlled operation could not be verified: " + str(exc)) from exc


def claim_operation(proof, session_info):
    """First TX uses the control-plane writer; unavailable integration fails shut.

    The writer must atomically revalidate authority, reject consumed attempts and
    record serial_claims through the existing event chain. Local files are never
    a replacement. A claim is consumed even when the ensuing TX outcome is unknown.
    """
    writer = getattr(_control(), "claim_serial_operation", None)
    if writer is None:
        raise AuthorizationError("control-plane claim_serial_operation writer is unavailable; refusing TX")
    try:
        return writer(proof, session_info)
    except Exception as exc:
        raise AuthorizationError("controlled serial operation claim rejected: " + str(exc)) from exc


def list_ports(provider=None):
    """Enumeration is descriptive only: it never opens, probes, or transmits."""
    if provider is None:
        try:
            from serial.tools.list_ports import comports
        except ImportError as exc:
            raise RuntimeError("optional serial tools require tools/serial-requirements.txt") from exc
        provider = comports
    return [{key: getattr(port, key, None) for key in
             ("device", "description", "hwid", "vid", "pid", "serial_number", "location")}
            for port in provider()]


class EvidenceWriter:
    """Append-only per-session evidence. Raw binary is the source of truth."""
    def __init__(self, directory, config, mode, operation_ref=None, script_sha256=None,
                 execution_start=None, execution_deadline=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.files = []
        self.raw = self.text = self.trace_file = None
        self.raw_bytes = self.tx_bytes = 0
        self.raw_hash = hashlib.sha256()
        self.truncated = False
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.start = {"schema_version": 1, "session_id": str(uuid.uuid4()), "mode": mode,
                      "connection": config["name"], "actual_config": _redact(config),
                      "config_sha256": hashlib.sha256(_canonical(config).encode()).hexdigest(),
                      "transport_settings": transport_settings(config),
                      "operation_ref": operation_ref, "command_id": str(uuid.uuid4()) if mode == "exec" else None,
                      "script_sha256": script_sha256, "host_start": _utc(),
                      "monotonic_start": time.monotonic(), "capture_open_utc": None,
                      "capture_open_monotonic": None,
                      "execution_start_host_utc": execution_start["host_utc"] if execution_start else None,
                      "execution_start_monotonic": execution_start["monotonic"] if execution_start else None,
                      "execution_deadline_monotonic": execution_deadline}
        self.limit = config["budgets"]["max_raw_bytes"]
        try:
            self._json("session.json", self.start)
            self.raw = open(self.directory / "rx.bin", "xb", buffering=0, opener=_private_opener)
            self.files.append(self.raw)
            self.text = open(self.directory / "rx.txt", "x", encoding="utf-8", newline="", opener=_private_opener)
            self.files.append(self.text)
            self.trace_file = open(self.directory / "io_trace.jsonl", "x", encoding="utf-8", newline="\n", opener=_private_opener)
            self.files.append(self.trace_file)
        except BaseException:
            self.close()
            raise

    def _json(self, filename, value):
        with open(self.directory / filename, "x", encoding="utf-8", newline="\n", opener=_private_opener) as target:
            target.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def trace(self, direction, data, **extra):
        event = {"direction": direction, "host_utc": _utc(), "monotonic": time.monotonic(),
                 "bytes": len(data), "base64": base64.b64encode(data).decode("ascii"), **extra}
        self.trace_file.write(_canonical(event) + "\n")
        self.trace_file.flush()

    def receive(self, data):
        remaining = self.limit - self.raw_bytes
        kept = data[:remaining]
        if kept:
            written = self.raw.write(kept)
            if written is None:
                written = 0
            self.raw_hash.update(kept[:written])
            self.raw_bytes += written
            if written != len(kept):
                raise OSError("short raw evidence write")
            self.text.write(self.decoder.decode(kept))
            self.text.flush()
            self.trace("rx", kept, raw_offset=self.raw_bytes - len(kept))
        if len(data) >= remaining:
            self.truncated = True
            raise CaptureLimit("max_raw_bytes reached; capture is partial")

    def transmit(self, data, accepted):
        if isinstance(accepted, int) and not isinstance(accepted, bool) and 0 <= accepted <= len(data):
            self.tx_bytes += accepted
        self.trace("tx", data, accepted_bytes=accepted)

    def close(self):
        errors = []
        if self.text is not None and not self.text.closed:
            try:
                self.text.write(self.decoder.decode(b"", final=True))
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        for file in self.files:
            try:
                file.close()
            except OSError as exc:
                errors.append(str(exc))
        return errors

    def finish(self, result):
        errors = self.close()
        result.update({"schema_version": 1, "session_id": self.start["session_id"],
                       "command_id": self.start["command_id"], "operation_ref": self.start["operation_ref"],
                       "host_start": self.start["host_start"], "host_end": _utc(),
                       "monotonic_start": self.start["monotonic_start"], "monotonic_end": time.monotonic(),
                       "capture_open_utc": self.start["capture_open_utc"],
                       "capture_open_monotonic": self.start["capture_open_monotonic"],
                       "execution_start_host_utc": self.start["execution_start_host_utc"],
                       "execution_start_monotonic": self.start["execution_start_monotonic"],
                       "execution_deadline_monotonic": self.start["execution_deadline_monotonic"],
                       "rx_bytes": self.raw_bytes, "rx_sha256": self.raw_hash.hexdigest(),
                       "tx_bytes": self.tx_bytes, "truncated": self.truncated})
        if errors:
            result.update(complete=False, outcome="inconclusive", evidence_errors=errors)
        result = _redact(result)
        try:
            self._json("result.json", result)
        except OSError as exc:
            result.update(complete=False, outcome="inconclusive")
            result.setdefault("evidence_errors", []).append("result.json: " + str(exc))
        return result


class _Frame:
    """Strict whole-line nonce protocol; echoed source lines never match."""
    def __init__(self, nonce):
        self.begin = b"__ICODE_BEGIN_" + nonce.encode("ascii") + b"__"
        self.end = re.compile(rb"__ICODE_END_" + nonce.encode("ascii") + rb"__:([0-9]{1,3})")
        self.pending = b""
        self.started = False
        self.exit_code = None
        self.body = bytearray()

    def feed(self, data):
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line = line[:-1] if line.endswith(b"\r") else line
            if not self.started:
                if line == self.begin:
                    self.started = True
                continue
            matched = self.end.fullmatch(line)
            if matched and int(matched.group(1)) <= 255:
                self.exit_code = int(matched.group(1))
                return
            self.body.extend(line + b"\n")


def _wrapper(script, nonce, identity_probe=False):
    # The script lives in a subshell so an ordinary exit doesn't skip the final
    # status marker. A reboot/exec/closed UART may still make completion unknown.
    marker = nonce.encode("ascii")
    # POSIX %b accepts \0ooo. Encode every source byte so an echoed identity
    # query cannot itself contain a literal expected identifier. The trailing
    # dot sentinel prevents command substitution from stripping source LFs.
    encoded_lines = []
    for offset in range(0, len(script), PROTOCOL_SOURCE_BYTES_PER_LINE):
        encoded = "".join("\\0%03o" % byte for byte in
                          script[offset:offset + PROTOCOL_SOURCE_BYTES_PER_LINE]).encode("ascii")
        encoded_lines.append(b"printf '%b' '" + encoded + b"'\n")
    # The outer shell must retain the UART exclusively for protocol commands.
    # A user read/cat must not swallow the following rc assignment and let END
    # reuse an earlier status; explicit script heredocs/redirections still work.
    evaluate = (b"(\n__icode_serial_source=\"$(\n" + b"".join(encoded_lines) +
                b"printf '.'\n)\"\neval \"${__icode_serial_source%.}\"\n) </dev/null\n")
    if identity_probe:
        # Emit BEGIN only AFTER the complete probe source has been read and its
        # stdout captured. Even interleaved terminal source echo precedes this
        # marker, and cannot satisfy an identity regex inside the framed output.
        return (b"__icode_serial_probe_output=$(\n" + evaluate.rstrip(b"\n") + b" 2>&1\n)\n"
                b"__icode_serial_rc=$?\nprintf '\\n__ICODE_BEGIN_" + marker + b"__\\n%s\\n"
                b"__ICODE_END_" + marker + b"__:%s\\n' \"$__icode_serial_probe_output\" \"$__icode_serial_rc\"\n")
    return (b"printf '\\n__ICODE_BEGIN_" + marker + b"__\\n'\n" + evaluate +
            b"__icode_serial_rc=$?\nprintf '\\n__ICODE_END_" + marker + b"__:%s\\n' \"$__icode_serial_rc\"\n")


class _Owner:
    def __init__(self, port, evidence, result, deadline, cancel_event):
        self.port, self.evidence, self.result = port, evidence, result
        self.deadline, self.cancel_event = deadline, cancel_event
        self.observed = bytearray()

    def check(self, deadline=None):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise SessionStopped("cancelled; no remote interrupt was sent")
        if time.monotonic() >= min(self.deadline, deadline or self.deadline):
            raise SessionStopped("deadline reached; remote completion is unknown")

    def read(self, deadline=None):
        self.check(deadline)
        remaining = min(self.deadline, deadline or self.deadline) - time.monotonic()
        original_timeout = self.port.timeout
        self.port.timeout = max(0.001, min(original_timeout, remaining))
        try:
            data = self.port.read(4096)
        finally:
            self.port.timeout = original_timeout
        if data:
            self.evidence.receive(data)
            self.observed.extend(data)
            # Readiness uses only a bounded tail. All bytes remain in rx.bin.
            del self.observed[:-65536]
        return data

    def wait_ready(self, pattern, seconds):
        deadline = min(self.deadline, time.monotonic() + seconds)
        while True:
            self.read(deadline)
            text = bytes(self.observed).decode("utf-8", "replace")
            # Only the current final line can establish readiness, never an old
            # prompt buried in boot logs. No wake-up/login/return is sent.
            current = text.split("\n")[-1].rstrip("\r")
            if self.match(pattern, current, deadline):
                if re.search(r"(?i)(?:\blogin\s*:|\bpassword\s*:|\bbootloader\b|^\s*=>)", current):
                    raise SessionStopped("console is login/bootloader, refusing TX")
                return bytes(self.observed)

    def match(self, pattern, text, deadline=None):
        self.check(deadline)
        response = _isolated_match({"mode": "search", "pattern": pattern, "text": text},
                                   min(self.deadline, deadline or self.deadline), self.cancel_event,
                                   self.evidence.start["actual_config"]["budgets"])
        self.check(deadline)
        return response.get("matched") is True

    def frame(self, script, seconds, command_id, identity_probe=False):
        nonce = secrets.token_hex(24)
        frame = _Frame(nonce)
        wrapped = _wrapper(script, nonce, identity_probe=identity_probe)
        deadline = min(self.deadline, time.monotonic() + seconds)
        self.evidence.trace("protocol", b"", command_id=command_id, nonce=nonce)
        for offset in range(0, len(wrapped), 1024):
            self.check(deadline)
            chunk = wrapped[offset:offset + 1024]
            # A write can succeed just before raising: treat any attempt as
            # unknown until the authenticated complete line protocol is seen.
            self.result["remote_state"] = "unknown"
            accepted = None
            original_timeout = self.port.write_timeout
            self.port.write_timeout = max(0.001, min(original_timeout, deadline - time.monotonic()))
            try:
                accepted = self.port.write(chunk)
            finally:
                try:
                    self.evidence.transmit(chunk, accepted)
                finally:
                    self.port.write_timeout = original_timeout
            if accepted != len(chunk):
                raise SessionStopped("short serial write; no retransmission attempted")
            # One owner alternates writes and reads even while loading a large
            # script, preserving output and avoiding a second competing reader.
            frame.feed(self.read(deadline))
        while frame.exit_code is None:
            frame.feed(self.read(deadline))
        return frame


def _execution_contract(config, identity_validator):
    if config.get("console_type") != "posix_shell":
        raise ValueError("exec requires console_type=posix_shell")
    pattern = config.get("ready_pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("exec requires an explicit ready_pattern")
    identity = config.get("identity")
    if identity_validator is None:
        if not isinstance(identity, dict) or not isinstance(identity.get("script"), str) \
                or not identity["script"].strip() or not isinstance(identity.get("pattern"), str) \
                or not identity["pattern"]:
            raise ValueError("exec requires a configured identity script/pattern or trusted caller validator")
        if "\x00" in identity["script"]:
            raise ValueError("identity script must not contain NUL")
        if len(identity["script"].encode("utf-8")) > config["budgets"]["max_script_bytes"]:
            raise ValueError("identity script exceeds max_script_bytes")


def _run(config, connection, output_dir, mode, *, script=None, operation_ref=None,
         serial_factory=None, cancel_event=None, authorizer=None, identity_validator=None,
         claim_callback=None, execution_deadline=None, execution_start=None):
    entered = time.monotonic()
    entered_host = _utc()
    cfg = load_connection(config, connection)
    binding = proof = None
    preflight_stopped = None
    production_authorizer = authorizer is None or authorizer is authorize_operation
    if mode == "exec":
        if execution_deadline is None:
            execution_deadline = entered + cfg["budgets"]["exec_seconds"]
        if execution_start is None:
            execution_start = {"host_utc": entered_host, "monotonic": entered}
        _execution_contract(cfg, identity_validator)
        if not isinstance(script, bytes) or not script or b"\x00" in script:
            raise ValueError("script must be nonempty POSIX shell bytes without NUL")
        if len(script) > cfg["budgets"]["max_script_bytes"]:
            raise ValueError("script exceeds max_script_bytes")
        binding = operation_input(cfg, script)
        authorizer = authorizer or authorize_operation
        patterns = [cfg["ready_pattern"]]
        if identity_validator is None:
            patterns.append(cfg["identity"]["pattern"])
        try:
            if time.monotonic() >= execution_deadline:
                raise SessionStopped("total exec deadline reached before authorization")
            proof = authorizer(operation_ref, binding)
            if not isinstance(proof, dict):
                raise AuthorizationError("authorizer did not return a verified operation proof")
            validation = _isolated_match({"mode": "validate", "patterns": patterns},
                                         execution_deadline,
                                         cancel_event, cfg["budgets"])
            if validation.get("empty"):
                raise ValueError("configured patterns must not match an empty observation")
        except SessionStopped as exc:
            preflight_stopped = str(exc)
        except KeyboardInterrupt:
            preflight_stopped = "cancelled during isolated pattern preflight"
    evidence = EvidenceWriter(output_dir, cfg, mode, operation_ref,
                              hashlib.sha256(script).hexdigest() if script else None,
                              execution_start=execution_start, execution_deadline=execution_deadline)
    result = {"outcome": "inconclusive", "complete": False, "remote_state": "not_started",
              "exit_code": None, "identity": {"matched": False, "method": None}, "reason": None,
              "execution_deadline_monotonic": execution_deadline}
    try:
        if preflight_stopped is not None:
            raise SessionStopped(preflight_stopped)
        if cancel_event is not None and cancel_event.is_set():
            raise SessionStopped("cancelled before opening port")
        if mode == "exec" and time.monotonic() >= execution_deadline:
            raise SessionStopped("total exec deadline reached before opening port")
        with SerialTransport(cfg, factory=serial_factory) as port:
            opened = time.monotonic()
            evidence.start["capture_open_utc"] = _utc()
            evidence.start["capture_open_monotonic"] = opened
            deadline = opened + cfg["budgets"]["listen_seconds"] if mode == "listen" else execution_deadline
            owner = _Owner(port, evidence, result, deadline, cancel_event)
            owner.check()
            if mode == "listen":
                while time.monotonic() < owner.deadline:
                    try:
                        owner.read()
                    except CaptureLimit:
                        raise
                    except SessionStopped:
                        if time.monotonic() >= owner.deadline and not (
                                cancel_event is not None and cancel_event.is_set()):
                            break
                        raise
                result.update(outcome="pass", complete=True, reason="configured passive capture window ended")
            else:
                ready = owner.wait_ready(cfg["ready_pattern"], cfg["budgets"]["ready_timeout"])
                proof = authorizer(operation_ref, binding)
                if not isinstance(proof, dict):
                    raise AuthorizationError("authorizer did not return a proof before TX")
                owner.check()
                claim = (claim_callback or claim_operation)(proof, {
                    "session_id": evidence.start["session_id"], "output_dir": str(evidence.directory),
                    "operation_input": binding})
                result["operation_proof"] = proof
                result["operation_claim"] = claim
                if identity_validator is not None:
                    verdict = identity_validator(cfg, ready)
                    matched = verdict.get("matched") is True if isinstance(verdict, dict) else verdict is True
                    result["identity"] = {"matched": matched, "method": "caller_validator",
                                          "observation_sha256": hashlib.sha256(_canonical(verdict).encode()).hexdigest()}
                else:
                    identity = cfg["identity"]
                    identity_deadline = min(owner.deadline, time.monotonic() + cfg["budgets"]["identity_timeout"])
                    probe = owner.frame(identity["script"].encode("utf-8"),
                                        cfg["budgets"]["identity_timeout"], "identity-probe", identity_probe=True)
                    observation = bytes(probe.body).decode("utf-8", "replace")
                    matched = probe.exit_code == 0 and owner.match(identity["pattern"], observation, identity_deadline)
                    result["identity"] = {"matched": matched, "method": "configured_probe",
                                          "probe_exit_code": probe.exit_code,
                                          "observation_sha256": hashlib.sha256(probe.body).hexdigest(),
                                          "observation_bytes": len(probe.body)}
                if not result["identity"]["matched"]:
                    raise SessionStopped("device identity did not match; requested script was not transmitted")
                # Authority may have changed while waiting for the probe.
                if production_authorizer:
                    authorize_operation(operation_ref, binding, session_id=evidence.start["session_id"])
                else:
                    authorizer(operation_ref, binding)
                frame = owner.frame(script, cfg["budgets"]["exec_seconds"], evidence.start["command_id"])
                result.update(exit_code=frame.exit_code, remote_state="completed", complete=True,
                              outcome="pass" if frame.exit_code == 0 else "fail",
                              reason="remote command completed with an explicit nonce exit marker")
    except KeyboardInterrupt:
        result.update(outcome="inconclusive", complete=False,
                      reason="cancelled by host; no remote interrupt was sent")
    except Exception as exc:
        result.update(outcome="inconclusive", complete=False, reason=type(exc).__name__ + ": " + str(exc))
    finally:
        result = evidence.finish(result)
    return result


def run_listen(config, connection, output_dir, *, serial_factory=None, cancel_event=None):
    return _run(config, connection, output_dir, "listen", serial_factory=serial_factory, cancel_event=cancel_event)


def run_exec(config, connection, output_dir, script_file, operation_ref, *, serial_factory=None,
             cancel_event=None, authorizer=None, identity_validator=None, claim_callback=None):
    entered = time.monotonic()
    entered_host = _utc()
    cfg = load_connection(config, connection)
    execution_deadline = entered + cfg["budgets"]["exec_seconds"]
    with open(script_file, "rb") as source:
        script = source.read(cfg["budgets"]["max_script_bytes"] + 1)
    if len(script) > cfg["budgets"]["max_script_bytes"]:
        raise ValueError("script exceeds max_script_bytes")
    return _run(cfg, connection, output_dir, "exec", script=script, operation_ref=operation_ref,
                serial_factory=serial_factory, cancel_event=cancel_event, authorizer=authorizer,
                identity_validator=identity_validator, claim_callback=claim_callback,
                execution_deadline=execution_deadline,
                execution_start={"host_utc": entered_host, "monotonic": entered})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="enumerate without opening or transmitting")
    for mode in ("listen", "exec"):
        sub = commands.add_parser(mode)
        sub.add_argument("--config", required=True)
        sub.add_argument("--connection", required=True)
        sub.add_argument("--output-dir", required=True)
        if mode == "exec":
            sub.add_argument("--script-file", required=True)
            sub.add_argument("--operation-ref", required=True, help="JSON object containing dir and attempt")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result = {"ports": list_ports(), "opened": False, "tx_bytes": 0}
        elif args.command == "listen":
            result = run_listen(args.config, args.connection, args.output_dir)
        else:
            result = run_exec(args.config, args.connection, args.output_dir, args.script_file,
                              json.loads(args.operation_ref))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command == "list" or result.get("outcome") == "pass" else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2 if not isinstance(exc, AuthorizationError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
