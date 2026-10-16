"""Hardware-free contracts for the optional serial evidence tool.

No fixture initializes Git, opens a physical port, or sends to a real device.
"""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    session = importlib.import_module("serial_session")
    transport = importlib.import_module("serial_transport")
except ModuleNotFoundError:
    session = transport = None


class FakeSerial:
    """Byte-level UART substitute; assertions exercise the real session owner."""
    def __init__(self, factory, port=None):
        self.factory = factory
        self.port = port
        self.timeout = 0.002
        self.write_timeout = 0.01
        self.is_open = False
        self.pending = [factory.initial]
        self.writes = []
        self.seen = set()
        self.scripts = []
        self.request = bytearray()
        self.close_count = 0
        self.fail_read = False
        factory.instances.append(self)

    def open(self):
        self.factory.open_snapshots.append(dict(self.__dict__))
        if self.factory.open_error:
            raise OSError("port busy")
        self.is_open = True

    def read(self, size):
        if self.pending:
            if getattr(self.factory, "cancel_after_read", None) is not None:
                self.factory.cancel_after_read()
                self.factory.cancel_after_read = None
            data = self.pending.pop(0)
            chunk, rest = data[:size], data[size:]
            if rest:
                self.pending.insert(0, rest)
            return chunk
        if self.fail_read:
            raise OSError("disconnected")
        if self.factory.read_error:
            raise self.factory.read_error
        time.sleep(min(self.timeout, 0.002))
        return b""

    def write(self, data):
        self.writes.append(bytes(data))
        if self.factory.short_write:
            return max(0, len(data) - 1)
        if self.factory.write_error:
            raise self.factory.write_error
        if self.factory.cancel_on_write:
            self.factory.cancel_on_write.set()
        # Source printf lines contain quoted/escaped markers; the real receiver
        # must ignore that echo and recognize only actual whole marker lines.
        self.request.extend(data)
        if not self.request.endswith(b'"$__icode_serial_rc"\n'):
            return len(data)
        source = bytes(self.request)
        self.request.clear()
        fragments = re.findall(rb"printf '%b' '((?:\\0[0-7]{3})*)'", source)
        encoded = b"".join(fragments)
        decoded = bytes(int(encoded[i + 2:i + 5], 8)
                        for i in range(0, len(encoded), 5)) if fragments else source
        self.scripts.append(decoded)
        for nonce in re.findall(rb"__ICODE_BEGIN_([0-9a-f]+)__", source):
            if nonce in self.seen:
                continue
            self.seen.add(nonce)
            probe = b"unit-probe" in decoded
            begin = b"__ICODE_BEGIN_" + nonce + b"__\n"
            end = b"__ICODE_END_" + nonce + b"__"
            body = getattr(self.factory, "identity_body", b"unit-id\n") if probe else b"user-output\n"
            if probe and self.factory.bad_identity:
                body = b"other-id\n"
            if probe and getattr(self.factory, "cancel_after_probe", None) is not None:
                self.factory.cancel_after_probe()
            if not probe and self.factory.mode == "echo_only":
                self.pending.append(source)
                continue
            response = source + b"\r\n" + begin
            if probe and self.factory.mode == "interleaved_echo":
                response += source + b"\r\n"
            response += body
            if probe or self.factory.mode not in ("no_end", "disconnect"):
                code = 0 if probe or self.factory.mode != "nonzero" else 7
                response += end + b":" + str(code).encode() + b"\r\n"
            self.pending.append(response)
            if not probe and self.factory.mode == "disconnect":
                self.fail_read = True
        return len(data)

    def close(self):
        self.close_count += 1
        self.is_open = False
        if self.factory.close_error:
            raise OSError("driver close failed")

    def reset_input_buffer(self):
        raise AssertionError("passive capture must not discard bytes")

    def send_break(self, *args, **kwargs):
        raise AssertionError("must never send BREAK")


class Factory:
    def __init__(self, initial=b"unit$ ", mode="normal", **options):
        self.initial, self.mode = initial, mode
        self.instances, self.open_snapshots = [], []
        self.open_error = self.short_write = self.bad_identity = self.close_error = False
        self.read_error = self.write_error = self.cancel_on_write = None
        self.__dict__.update(options)

    def __call__(self, port=None):
        if port is not None:
            raise AssertionError("Serial must be constructed with port=None")
        return FakeSerial(self, port)


class SerialContracts(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(session, "optional serial session implementation is missing")
        self.assertIsNotNone(transport, "optional serial transport implementation is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "capture"
        self.script = self.root / "request.sh"
        self.script.write_bytes(b"printf 'user-script\\n'\n")
        self.cfg = {
            "name": "console", "type": "serial", "port": str(self.root / "virtual-uart"), "baud": 115200,
            "console_type": "posix_shell", "ready_pattern": r"unit\$ $",
            "identity": {"script": "printf 'unit-probe\\n'", "pattern": r"(?m)^unit-id$"},
            # Semantic cases use product budgets, not a Python startup SLA.
            # Deadline/cancel/no-end cases below set their own explicit limits.
            "budgets": {"listen_seconds": 0.02,
                        "exec_seconds": transport.DEFAULT_BUDGETS["exec_seconds"],
                        "read_timeout": 0.002, "write_timeout": 0.01,
                        "ready_timeout": transport.DEFAULT_BUDGETS["ready_timeout"],
                        "identity_timeout": transport.DEFAULT_BUDGETS["identity_timeout"],
                        "max_raw_bytes": 32768},
        }

    def listen(self, factory=None, **kw):
        return session.run_listen(self.cfg, "console", self.out,
                                  serial_factory=factory or Factory(), **kw)

    def execute(self, factory=None, **kw):
        kw.setdefault("authorizer", lambda ref, binding: {"event_id": "test-event"})
        kw.setdefault("claim_callback", lambda proof, info: {"claimed": True})
        return session.run_exec(self.cfg, "console", self.out, self.script,
                                {"dir": "test-control", "attempt": "test-attempt"},
                                serial_factory=factory or Factory(), **kw)

    def assertReleased(self, factory):
        self.assertTrue(factory.instances)
        self.assertFalse(factory.instances[0].is_open)
        self.assertGreaterEqual(factory.instances[0].close_count, 1)
        with transport.PortLock(self.cfg["port"]):
            pass

    def test_list_enumerates_without_open_or_write(self):
        port = types.SimpleNamespace(device="mock-port", description="virtual", hwid="unit")
        ports = session.list_ports(provider=lambda: [port])
        self.assertEqual(ports[0]["device"], "mock-port")

    def test_optional_module_import_does_not_require_pyserial(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "sys.modules['serial']=None; import serial_session; "
                "assert 'serial' not in serial_session.__dict__")
        proc = subprocess.run([sys.executable, "-B", "-c", code, str(TOOLS)],
                              capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_passive_capture_preserves_non_utf8_and_has_zero_tx(self):
        raw = b"boot\x00\xff\xfe\r\n\xe4\xb8\xad\n"
        factory = Factory(initial=raw)
        result = self.listen(factory)
        self.assertEqual((self.out / "rx.bin").read_bytes(), raw)
        self.assertEqual(factory.instances[0].writes, [])
        self.assertEqual(result["rx_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(result["complete"])
        self.assertEqual(result["tx_bytes"], 0)
        self.assertEqual(result["remote_state"], "not_started")
        self.assertIn("\ufffd", (self.out / "rx.txt").read_text(encoding="utf-8"))
        self.assertReleased(factory)

    def test_control_lines_and_flow_are_set_before_open(self):
        self.cfg.update(dtr=False, rts=False, rtscts=True, xonxoff=False, dsrdtr=False,
                        bytesize=7, parity="E", stopbits=2)
        factory = Factory()
        self.listen(factory)
        at_open = factory.open_snapshots[0]
        for key in ("dtr", "rts", "rtscts", "xonxoff", "dsrdtr", "bytesize", "parity", "stopbits"):
            self.assertEqual(at_open[key], self.cfg[key], key)

    def test_config_wrapper_and_named_connection_are_supported(self):
        wrapped = {"device_config": {"connections": [self.cfg, {"name": "other", "type": "ssh"}]}}
        path = self.root / "config.json"
        path.write_text(json.dumps(wrapped), encoding="utf-8")
        actual = transport.load_connection(path, "console")
        self.assertEqual(actual["port"], self.cfg["port"])
        with self.assertRaises(ValueError):
            transport.load_connection(path, "missing")

    def test_config_rejects_implicit_ports_and_unbounded_or_nonfinite_budgets(self):
        for key, value in (("port", ""), ("baud", True), ("bytesize", 9), ("parity", "?")):
            cfg = dict(self.cfg, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                transport.load_connection(cfg, "console")
        for value in (None, 0, -1, float("inf"), float("nan"), True):
            cfg = dict(self.cfg, budgets=dict(self.cfg["budgets"], read_timeout=value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                transport.load_connection(cfg, "console")

    def test_oversized_numeric_configuration_is_explicit_value_error(self):
        configs = [dict(self.cfg, baud=10 ** 400)]
        configs += [dict(self.cfg, budgets=dict(self.cfg["budgets"], **{field: 10 ** 400}))
                    for field in ("read_timeout", "max_raw_bytes")]
        for cfg in configs:
            with self.subTest(field="baud" if cfg["baud"] != self.cfg["baud"] else "budgets"):
                try:
                    transport.load_connection(cfg, "console")
                except ValueError:
                    pass
                except Exception as exc:
                    self.fail("expected ValueError, got " + type(exc).__name__)
                else:
                    self.fail("oversized numeric configuration was accepted")

    def test_cli_oversized_baud_and_budget_return_json_error_without_traceback(self):
        for index, cfg in enumerate((dict(self.cfg, baud=10 ** 400),
                                     dict(self.cfg, budgets=dict(self.cfg["budgets"], read_timeout=10 ** 400)))):
            path = self.root / ("oversized-" + str(index) + ".json")
            path.write_text(json.dumps(cfg), encoding="utf-8")
            proc = subprocess.run([sys.executable, "-B", str(TOOLS / "serial_session.py"), "listen",
                                   "--config", str(path), "--connection", "console",
                                   "--output-dir", str(self.root / ("bad-" + str(index)))],
                                  capture_output=True, text=True, timeout=5)
            with self.subTest(index=index):
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(json.loads(proc.stderr)["error"], "ValueError")

    def test_catastrophic_readiness_matching_respects_total_deadline(self):
        self.cfg["ready_pattern"] = "(a+)+$"
        self.cfg["budgets"].update(exec_seconds=0.01, ready_timeout=0.01)
        factory = Factory(initial=b"a" * 27 + b"!")
        started = time.monotonic()
        result = self.execute(factory)
        self.assertLess(time.monotonic() - started, 2.01)
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertIsNone(result["exit_code"])
        if factory.instances:
            self.assertEqual(factory.instances[0].writes, [])
            self.assertReleased(factory)
        with transport.PortLock(self.cfg["port"]):
            pass

    def test_fake_port_is_fixture_scoped_and_same_port_still_excludes(self):
        self.assertTrue(Path(self.cfg["port"]).is_absolute())
        self.assertEqual(Path(self.cfg["port"]).parent, self.root)
        with transport.PortLock(self.cfg["port"]):
            with self.assertRaises(transport.PortBusyError):
                with transport.PortLock(self.cfg["port"]):
                    self.fail("same configured port was not exclusive")

    def test_slow_valid_preflight_does_not_reset_total_exec_budget(self):
        self.cfg["ready_pattern"] = "different-prompt"
        self.cfg["budgets"].update(exec_seconds=0.3, ready_timeout=0.3)
        original = session._isolated_match
        validation_completed = []
        def slow_valid(request, deadline, cancel, budgets):
            if request["mode"] == "validate":
                time.sleep(0.25)
                validation_completed.append(True)
                return {"empty": []}
            return original(request, deadline, cancel, budgets)
        factory = Factory()
        started = time.monotonic()
        with mock.patch.object(session, "_isolated_match", slow_valid):
            result = self.execute(factory)
        self.assertTrue(validation_completed)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertIsNone(result["exit_code"])
        if factory.instances:
            self.assertEqual(factory.instances[0].writes, [])
            self.assertReleased(factory)

    def test_real_slow_valid_preflight_and_ready_wait_share_one_budget(self):
        self.cfg["ready_pattern"] = "(?:a?|a?){26}(?!)"
        self.cfg["budgets"].update(exec_seconds=5, ready_timeout=5)
        factory = Factory()
        started = time.monotonic()
        result = self.execute(factory)
        self.assertLess(time.monotonic() - started, 7)
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(result["tx_bytes"], 0)
        if factory.instances:
            self.assertReleased(factory)

    def test_initial_authorization_time_is_deducted_before_open(self):
        self.cfg["budgets"]["exec_seconds"] = 0.05
        factory = Factory()
        def slow_authorize(ref, binding):
            time.sleep(0.1)
            return {"event_id": "slow-authority"}
        result = self.execute(factory, authorizer=slow_authorize)
        self.assertEqual(factory.instances, [])
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["tx_bytes"], 0)
        self.assertIn("execution_start_monotonic", result)
        self.assertIn("execution_start_host_utc", result)
        self.assertAlmostEqual(result["execution_deadline_monotonic"] - result["execution_start_monotonic"], 0.05)
        self.assertGreaterEqual(result["monotonic_start"], result["execution_start_monotonic"])
        metadata = json.loads((self.out / "session.json").read_text(encoding="utf-8"))
        for key in ("execution_start_monotonic", "execution_start_host_utc", "execution_deadline_monotonic"):
            self.assertEqual(metadata[key], result[key])

    def test_evidence_setup_time_is_deducted_before_open(self):
        self.cfg["budgets"]["exec_seconds"] = 0.2
        factory = Factory()
        original = session.EvidenceWriter.__init__
        def slow_setup(writer, *args, **kwargs):
            original(writer, *args, **kwargs)
            time.sleep(0.25)
        with mock.patch.object(session.EvidenceWriter, "__init__", slow_setup):
            result = self.execute(factory)
        self.assertEqual(factory.instances, [])
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["tx_bytes"], 0)

    def test_listen_window_still_starts_after_slow_open(self):
        factory = Factory(initial=b"passive")
        original = FakeSerial.open
        def slow_open(port):
            time.sleep(0.06)
            original(port)
        with mock.patch.object(FakeSerial, "open", slow_open):
            result = self.listen(factory)
        self.assertTrue(result["complete"], result)
        self.assertGreaterEqual(result["monotonic_end"] - result["capture_open_monotonic"],
                                self.cfg["budgets"]["listen_seconds"] * 0.8)
        self.assertEqual(factory.instances[0].writes, [])

    def test_catastrophic_identity_matching_respects_deadline_and_never_sends_script(self):
        self.cfg["identity"]["pattern"] = "(a+)+$"
        self.cfg["budgets"].update(exec_seconds=1.5, ready_timeout=0.5, identity_timeout=0.3)
        factory = Factory(identity_body=b"a" * 27 + b"!\n")
        started = time.monotonic()
        result = self.execute(factory)
        self.assertLess(time.monotonic() - started, 3.5)
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertIsNone(result["exit_code"])
        self.assertFalse(any(b"user-script" in script for script in factory.instances[0].scripts))
        self.assertReleased(factory)

    def test_cancel_during_catastrophic_ready_and_identity_matching_is_bounded(self):
        for stage in ("ready", "identity"):
            self.out = self.root / ("cancel-matcher-" + stage)
            cancel = threading.Event()
            timers = []
            def arm_cancel():
                timer = threading.Timer(0.05, cancel.set)
                timer.start()
                timers.append(timer)
            self.cfg["budgets"].update(exec_seconds=2, ready_timeout=1, identity_timeout=1)
            self.cfg["ready_pattern"] = "(a+)+$" if stage == "ready" else r"unit\$ $"
            self.cfg["identity"]["pattern"] = "(a+)+$" if stage == "identity" else "unit-id"
            factory = (Factory(initial=b"a" * 27 + b"!", cancel_after_read=arm_cancel)
                       if stage == "ready" else
                       Factory(identity_body=b"a" * 27 + b"!\n", cancel_after_probe=arm_cancel))
            started = time.monotonic()
            with self.subTest(stage=stage):
                result = self.execute(factory, cancel_event=cancel)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(result["outcome"], "inconclusive")
                self.assertIsNone(result["exit_code"])
                self.assertReleased(factory)
            for timer in timers:
                timer.join(timeout=1)

    def test_isolated_matcher_utf8_and_errors_do_not_leak_inputs_or_inherit_stdin(self):
        cfg = transport.load_connection(self.cfg, "console")
        original = subprocess.Popen
        children = []
        def launch(argv, **kwargs):
            child = original(argv, **kwargs)
            children.append((argv, kwargs, child))
            return child
        with mock.patch.object(session.subprocess, "Popen", launch):
            response = session._isolated_match(
                {"mode": "search", "pattern": "^[一-龥]+$", "text": "汉字"},
                time.monotonic() + transport.DEFAULT_BUDGETS["exec_seconds"], None, cfg["budgets"])
            self.assertTrue(response["matched"])
            with self.assertRaises(ValueError) as caught:
                session._isolated_match(
                    {"mode": "search", "pattern": "private-pattern-credential[", "text": "private-device-input"},
                    time.monotonic() + transport.DEFAULT_BUDGETS["exec_seconds"], None, cfg["budgets"])
        self.assertNotIn("private-pattern-credential", str(caught.exception))
        self.assertNotIn("private-device-input", str(caught.exception))
        for argv, kwargs, child in children:
            self.assertEqual(argv, [sys.executable, "-I", "-S", "-B", "-c", session._MATCH_WORKER])
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.assertIsNotNone(child.returncode)
            self.assertTrue(child.stdin.closed)
            self.assertTrue(child.stdout.closed)

    def test_timed_out_matcher_is_reaped_and_cannot_keep_port_lock(self):
        cfg = transport.load_connection(self.cfg, "console")
        original = subprocess.Popen
        children = []
        with transport.PortLock(self.cfg["port"]) as owned:
            os.set_inheritable(owned.file.fileno(), True)
            def launch(argv, **kwargs):
                child = original(argv, **kwargs)
                children.append(child)
                if os.name != "nt":
                    targets = []
                    for fd in Path("/proc", str(child.pid), "fd").iterdir():
                        try:
                            targets.append(fd.readlink())
                        except FileNotFoundError:
                            pass
                    self.assertNotIn(owned.path.resolve(), targets)
                return child
            started = time.monotonic()
            with mock.patch.object(session.subprocess, "Popen", launch):
                with self.assertRaises(session.SessionStopped):
                    session._isolated_match({"mode": "search", "pattern": "(a+)+$", "text": "a" * 29 + "!"},
                                             started + 0.3, None, cfg["budgets"])
            self.assertLess(time.monotonic() - started, 2.3)
        self.assertTrue(children)
        self.assertTrue(all(child.returncode is not None for child in children))
        with transport.PortLock(self.cfg["port"]):
            pass

    def test_catastrophic_empty_pattern_validation_is_bounded_before_open(self):
        self.cfg["ready_pattern"] = "(a?){1000000000}$"
        self.cfg["budgets"]["exec_seconds"] = 0.05
        factory = Factory()
        started = time.monotonic()
        result = self.execute(factory)
        self.assertLess(time.monotonic() - started, 2.05)
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertFalse(result["complete"])
        self.assertIsNone(result["exit_code"])
        self.assertEqual(factory.instances, [])

    def test_matcher_start_failure_and_preflight_interrupt_preserve_inconclusive_result(self):
        for index, failure in enumerate((OSError("private-matcher-path"), KeyboardInterrupt())):
            self.out = self.root / ("matcher-start-" + str(index))
            factory = Factory()
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(session.subprocess, "Popen", side_effect=failure):
                    try:
                        result = self.execute(factory)
                    except (OSError, KeyboardInterrupt) as exc:
                        self.fail("matcher preflight escaped instead of preserving evidence: " + type(exc).__name__)
                self.assertEqual(result["outcome"], "inconclusive")
                self.assertFalse(result["complete"])
                self.assertIsNone(result["exit_code"])
                self.assertEqual(factory.instances, [])
                self.assertNotIn("private-matcher-path", json.dumps(result))
                self.assertTrue((self.out / "result.json").is_file())

    def test_alias_lock_is_nonblocking_and_does_not_remove_owner(self):
        self.assertEqual(transport.canonical_port("COM007"), transport.canonical_port(r"\\.\com7"))
        locks = self.root / "locks"
        with transport.PortLock("COM007", lock_dir=locks):
            started = time.monotonic()
            with self.assertRaises(transport.PortBusyError):
                with transport.PortLock(r"\\.\com7", lock_dir=locks):
                    self.fail("alias lock was bypassed")
            self.assertLess(time.monotonic() - started, 0.5)
        with transport.PortLock("COM7", lock_dir=locks):
            pass

    def test_cross_process_lock_exclusion(self):
        locks = self.root / "locks"
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from serial_transport import *; "
                "\ntry:\n with PortLock('mock-port', lock_dir=sys.argv[2]): pass"
                "\nexcept PortBusyError: sys.exit(23)\n")
        with transport.PortLock("mock-port", lock_dir=locks):
            proc = subprocess.run([sys.executable, "-B", "-c", code, str(TOOLS), str(locks)],
                                  capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 23, proc.stderr)

    def test_open_busy_preserves_evidence_and_closes(self):
        factory = Factory(open_error=True)
        result = self.listen(factory)
        self.assertNotEqual(result["outcome"], "pass")
        self.assertFalse(result["complete"])
        self.assertEqual(factory.instances[0].writes, [])
        self.assertReleased(factory)

    def test_exec_success_ignores_wrapper_echo(self):
        factory = Factory()
        result = self.execute(factory)
        self.assertEqual(result["outcome"], "pass")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["remote_state"], "completed")
        self.assertTrue(result["identity"]["matched"])
        self.assertTrue(result["complete"])
        self.assertEqual(len(factory.instances[0].seen), 2)
        self.assertReleased(factory)

    def test_exec_nonzero_is_failure_not_transport_success(self):
        result = self.execute(Factory(mode="nonzero"))
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["remote_state"], "completed")

    def test_echo_alone_cannot_complete(self):
        self.cfg["budgets"].update(exec_seconds=5, ready_timeout=2, identity_timeout=2)
        result = self.execute(Factory(mode="echo_only"))
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["remote_state"], "unknown")
        self.assertIsNone(result["exit_code"])

    def test_missing_end_and_disconnect_never_replay_or_claim_stopped(self):
        self.cfg["budgets"].update(exec_seconds=5, ready_timeout=2, identity_timeout=2)
        for mode in ("no_end", "disconnect"):
            self.out = self.root / mode
            factory = Factory(mode=mode)
            with self.subTest(mode=mode):
                result = self.execute(factory)
                self.assertEqual(result["remote_state"], "unknown")
                self.assertIsNone(result["exit_code"])
                self.assertFalse(result["complete"])
                self.assertEqual(len(factory.instances), 1)
                self.assertEqual(len(factory.instances[0].seen), 2)
                self.assertReleased(factory)

    def test_cancellation_is_bounded_and_sends_no_interrupt(self):
        self.cfg["budgets"].update(exec_seconds=1.5, ready_timeout=0.5, identity_timeout=0.5)
        cancel = threading.Event()
        factory = Factory(cancel_on_write=cancel)
        started = time.monotonic()
        result = self.execute(factory, cancel_event=cancel)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result["remote_state"], "unknown")
        self.assertIsNone(result["exit_code"])
        tx = b"".join(factory.instances[0].writes)
        self.assertNotIn(b"\x03", tx)
        self.assertReleased(factory)

    def test_short_write_is_partial_and_does_not_retry(self):
        factory = Factory(short_write=True)
        result = self.execute(factory)
        self.assertEqual(result["remote_state"], "unknown")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(len(factory.instances[0].writes), 1)
        self.assertFalse(result["complete"])
        self.assertReleased(factory)

    def test_write_error_and_keyboard_interrupt_keep_partial_evidence_and_release(self):
        self.cfg["budgets"].update(exec_seconds=1.5, ready_timeout=0.5, identity_timeout=0.5)
        for name, factory, execute in (
                ("write-error", Factory(write_error=TimeoutError("finite write timeout")), True),
                ("interrupt", Factory(initial=b"", read_error=KeyboardInterrupt()), False)):
            self.out = self.root / name
            started = time.monotonic()
            with self.subTest(name=name):
                result = self.execute(factory) if execute else self.listen(factory)
                self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(result["complete"])
                self.assertIsNone(result["exit_code"])
                self.assertNotEqual(result["outcome"], "pass")
                self.assertReleased(factory)
                self.assertTrue((self.out / "result.json").is_file())
                if execute:
                    self.assertEqual(result["remote_state"], "unknown")
                    self.assertEqual(len(factory.instances[0].writes), 1)

    def test_raw_limit_is_partial_and_not_pass(self):
        self.cfg["budgets"]["max_raw_bytes"] = 5
        factory = Factory(initial=b"123456789")
        result = self.listen(factory)
        self.assertEqual((self.out / "rx.bin").read_bytes(), b"12345")
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertReleased(factory)

    def test_raw_limit_at_window_boundary_cannot_be_mistaken_for_normal_end(self):
        self.cfg["budgets"].update(max_raw_bytes=3, listen_seconds=0.01)
        original = session.EvidenceWriter.receive
        def late_limit(writer, data):
            try:
                return original(writer, data)
            except session.CaptureLimit:
                time.sleep(0.02)
                raise
        with mock.patch.object(session.EvidenceWriter, "receive", late_limit):
            result = self.listen(Factory(initial=b"12345"))
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])
        self.assertNotEqual(result["outcome"], "pass")

    def test_disk_full_retains_partial_raw_and_releases(self):
        factory = Factory(initial=b"raw-data")
        original = session.EvidenceWriter.receive
        def full(writer, data):
            original(writer, data[:3])
            raise OSError(28, "No space left on device")
        with mock.patch.object(session.EvidenceWriter, "receive", full):
            result = self.listen(factory)
        self.assertEqual((self.out / "rx.bin").read_bytes(), b"raw")
        self.assertFalse(result["complete"])
        self.assertNotEqual(result["outcome"], "pass")
        self.assertReleased(factory)

    def test_existing_output_is_never_overwritten(self):
        self.out.mkdir()
        sentinel = self.out / "session.json"
        sentinel.write_bytes(b"existing")
        factory = Factory()
        with self.assertRaises(FileExistsError):
            self.listen(factory)
        self.assertEqual(sentinel.read_bytes(), b"existing")
        self.assertEqual(factory.instances, [])

    def test_wrong_identity_never_sends_user_script(self):
        factory = Factory(bad_identity=True)
        result = self.execute(factory)
        self.assertNotEqual(result["outcome"], "pass")
        self.assertFalse(any(b"user-script" in script for script in factory.instances[0].scripts))
        self.assertFalse(result["identity"]["matched"])

    def test_login_bootloader_and_unready_console_send_nothing(self):
        self.cfg["budgets"].update(exec_seconds=5, ready_timeout=0.5, identity_timeout=2)
        for initial in (b"login: ", b"bootloader> ", b"=> ", b"garbage"):
            self.out = self.root / hashlib.sha256(initial).hexdigest()
            factory = Factory(initial=initial)
            with self.subTest(initial=initial):
                result = self.execute(factory)
                self.assertNotEqual(result["outcome"], "pass")
                self.assertEqual(factory.instances[0].writes, [])

    def test_missing_identity_or_console_contract_blocks_before_open(self):
        for key in ("identity", "ready_pattern", "console_type"):
            cfg = dict(self.cfg)
            del cfg[key]
            factory = Factory()
            with self.subTest(key=key), self.assertRaises(ValueError):
                session.run_exec(cfg, "console", self.out, self.script, {}, serial_factory=factory,
                                 authorizer=lambda r, b: {})
            self.assertEqual(factory.instances, [])

    def test_empty_matching_patterns_and_nul_identity_source_block_before_open(self):
        for change in ({"ready_pattern": ".*"},
                       {"identity": {"script": "unit-probe", "pattern": ".*"}},
                       {"identity": {"script": "unit-probe\x00", "pattern": "unit-id"}}):
            factory = Factory()
            cfg = dict(self.cfg, **change)
            self.out = self.root / str(len(change["identity"]["script"]) if "identity" in change else "ready")
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    session.run_exec(cfg, "console", self.out, self.script, {}, serial_factory=factory,
                                     authorizer=lambda r, b: {}, claim_callback=lambda p, s: {"claimed": True})
                self.assertEqual(factory.instances, [])

    def test_no_authorization_blocks_before_open_or_probe(self):
        factory = Factory()
        with self.assertRaises(session.AuthorizationError):
            session.run_exec(self.cfg, "console", self.out, self.script,
                             {"dir": str(self.root), "attempt": "unregistered"}, serial_factory=factory)
        self.assertEqual(factory.instances, [])

    def test_authorization_is_rechecked_before_transmission(self):
        calls = []
        def authorize(ref, binding):
            calls.append(binding)
            if len(calls) > 1:
                raise session.AuthorizationError("operation was closed")
            return {"event_id": "open-at-start"}
        factory = Factory()
        result = self.execute(factory, authorizer=authorize)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(factory.instances[0].writes, [])
        self.assertNotEqual(result["outcome"], "pass")

    def test_claim_is_required_before_any_transmission(self):
        factory = Factory()
        def refuse(proof, info):
            raise session.AuthorizationError("claim writer unavailable")
        result = self.execute(factory, claim_callback=refuse)
        self.assertEqual(factory.instances[0].writes, [])
        self.assertNotEqual(result["outcome"], "pass")

    def test_close_error_cannot_leave_a_successful_complete_result(self):
        factory = Factory(close_error=True)
        result = self.execute(factory)
        self.assertEqual(result["exit_code"], 0, result)  # actual remote marker is retained
        self.assertEqual(result["remote_state"], "completed")
        self.assertNotEqual(result["outcome"], "pass")
        self.assertFalse(result["complete"])
        self.assertReleased(factory)

    def test_oversized_script_is_not_read_unbounded(self):
        self.cfg["budgets"]["max_script_bytes"] = 2
        factory = Factory()
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded input read")):
            with self.assertRaises(ValueError):
                self.execute(factory)
        self.assertEqual(factory.instances, [])

    def test_effective_timeouts_are_recorded_and_allow_bounded_cancellation(self):
        self.cfg["budgets"].update(read_timeout=50, write_timeout=50)
        factory = Factory()
        self.listen(factory)
        meta = json.loads((self.out / "session.json").read_text(encoding="utf-8"))
        self.assertIn("transport_settings", meta)
        settings = meta["transport_settings"]
        self.assertLessEqual(settings["read_timeout"], 1)
        self.assertLessEqual(settings["write_timeout"], 1)
        self.assertEqual(factory.open_snapshots[0]["timeout"], settings["read_timeout"])

    def test_structured_metadata_redacts_secrets_and_probe_source_but_keeps_raw(self):
        self.cfg["password"] = "config-credential"
        self.cfg["nested"] = {"api_token": "nested-credential", "headers": {"Authorization": "header-credential"}}
        self.cfg["identity"]["script"] += "; : 'source-credential'"
        factory = Factory(identity_body=b"unit-id\nraw-credential\n")
        result = self.execute(factory)
        self.assertEqual(result["outcome"], "pass")
        structured = (self.out / "session.json").read_text(encoding="utf-8") + json.dumps(result)
        for value in ("config-credential", "nested-credential", "header-credential", "source-credential", "raw-credential"):
            self.assertNotIn(value, structured)
        self.assertIn(b"raw-credential", (self.out / "rx.bin").read_bytes())
        self.assertTrue(any(b"source-credential" in script for script in factory.instances[0].scripts))

    def test_probe_source_echo_cannot_satisfy_identity_match(self):
        self.cfg["identity"] = {"script": "printf 'unit-id\\n'; # unit-probe", "pattern": "unit-id"}
        factory = Factory(mode="interleaved_echo", bad_identity=True)
        result = self.execute(factory)
        self.assertFalse(result["identity"]["matched"])
        self.assertFalse(any(b"user-script" in script for script in factory.instances[0].scripts))

    @unittest.skipIf(os.name == "nt", "POSIX modes; do not claim Windows ACL hardening")
    def test_raw_evidence_directory_and_files_are_private_on_posix(self):
        self.listen(Factory(initial=b"credential"))
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o700)
        for file in self.out.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600, file.name)

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_lock_refuses_symlink_file_and_directory(self):
        real = self.root / "real-locks"
        real.mkdir()
        linked = self.root / "linked-locks"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            with transport.PortLock("mock-port", lock_dir=linked):
                self.fail("symlink lock directory accepted")
        lock = transport.PortLock("mock-port", lock_dir=real)
        victim = self.root / "unrelated"
        victim.write_bytes(b"preserve")
        lock.path.symlink_to(victim)
        with self.assertRaises(OSError):
            with lock:
                self.fail("symlink lock file accepted")
        self.assertEqual(victim.read_bytes(), b"preserve")

    def test_passive_backend_rejects_unreviewed_pyserial_version(self):
        import serial
        with mock.patch.object(serial, "VERSION", "future-version"):
            with self.assertRaises(RuntimeError):
                transport.serial_factory()

    def test_nonce_parser_requires_begin_and_exact_complete_lines(self):
        frame = session._Frame("ab12")
        frame.feed(b"__ICODE_END_ab12__:0\n")
        self.assertIsNone(frame.exit_code)
        frame.feed(b"printf '__ICODE_BEGIN_ab12__\\n'\n__ICODE_BEGIN_ab12__\r\n")
        self.assertTrue(frame.started)
        for line in (b"x__ICODE_END_ab12__:0\n", b"__ICODE_END_ab12__:0x\n",
                     b"__ICODE_END_other__:0\n", b"__ICODE_END_ab12__:256\n"):
            frame.feed(line)
            self.assertIsNone(frame.exit_code)
        frame.feed(b"__ICODE_END_ab12__:0")
        self.assertIsNone(frame.exit_code)
        frame.feed(b"\r\n")
        self.assertEqual(frame.exit_code, 0)

    def test_maximum_declared_script_encodes_losslessly_into_bounded_lines(self):
        size = transport.DEFAULT_BUDGETS["max_script_bytes"]
        source = b":#" + b"x" * (size - 4) + b"\n\n"
        wrapped = session._wrapper(source, "a" * 48)
        self.assertLess(max(map(len, wrapped.splitlines())), 255)
        chunks = re.findall(rb"printf '%b' '((?:\\0[0-7]{3})*)'", wrapped)
        encoded = b"".join(chunks)
        decoded = bytes(int(encoded[i + 2:i + 5], 8) for i in range(0, len(encoded), 5))
        self.assertEqual(len(decoded), size)
        self.assertEqual(hashlib.sha256(decoded).digest(), hashlib.sha256(source).digest())

    @unittest.skipIf(os.name == "nt", "Linux PTY; never a physical port")
    def test_linux_pty_prequeued_binary_is_preserved_without_tx(self):
        import tty
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        raw = b"first-byte\x00\xff\xfe\r\n"
        os.write(master, raw)
        self.cfg["port"] = os.ttyname(slave)
        self.cfg["budgets"]["listen_seconds"] = 0.05
        result = session.run_listen(self.cfg, "console", self.out)
        self.assertTrue(result["complete"], result)
        self.assertEqual((self.out / "rx.bin").read_bytes(), raw)
        os.set_blocking(master, False)
        with self.assertRaises(BlockingIOError):
            os.read(master, 4096)

    @unittest.skipIf(os.name == "nt", "Linux PTY shell; never a device")
    def test_linux_pty_real_posix_shell_reports_actual_exit_status(self):
        import tty
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        child = subprocess.Popen(["/bin/sh", "-i"], stdin=master, stdout=master, stderr=master,
                                 env=dict(os.environ, PS1="unit$ ", PS2=""), close_fds=True)
        def stop_shell():
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        self.addCleanup(stop_shell)
        self.cfg["port"] = os.ttyname(slave)
        self.cfg["identity"] = {"script": "printf 'unit-id\\n'", "pattern": "unit-id"}
        self.script.write_bytes("printf '%s\\n' \"a'b\" '汉字'\nprintf 'actual-shell-output\\n'\nexit 7\n\n".encode())
        result = session.run_exec(self.cfg, "console", self.out, self.script,
                                 {"dir": "test-control", "attempt": "pty-attempt"},
                                 authorizer=lambda r, b: {"event_id": "pty-test"},
                                 claim_callback=lambda p, s: {"claimed": True})
        self.assertEqual(result["exit_code"], 7, result)
        self.assertEqual(result["outcome"], "fail", result)
        self.assertIn(b"actual-shell-output", (self.out / "rx.bin").read_bytes())
        self.assertIn("汉字".encode(), (self.out / "rx.bin").read_bytes())
        self.assertIn(b"a'b", (self.out / "rx.bin").read_bytes())

    @unittest.skipIf(os.name == "nt", "POSIX printf shell semantics")
    def test_octal_wrapper_preserves_tail_newline_and_quote_semantics(self):
        source = b"printf '%s\\n' tail\\\n\n"
        expected = subprocess.run(["/bin/sh"], input=source, capture_output=True, timeout=2)
        wrapped = subprocess.run(["/bin/sh"], input=session._wrapper(source, "ab12"),
                                 capture_output=True, timeout=2)
        frame = session._Frame("ab12")
        frame.feed(wrapped.stdout)
        self.assertEqual(frame.exit_code, expected.returncode)
        self.assertEqual(bytes(frame.body).strip(b"\n"), expected.stdout.strip(b"\n"))
        self.assertNotIn(source, session._wrapper(source, "ab12"))

    def run_canonical_tty_script(self, source):
        """Execute against an independent real remote ICANON terminal."""
        import select
        import termios
        import tty
        import fcntl
        host_master, host_slave = os.openpty()
        remote_master, remote_slave = os.openpty()
        for fd in (host_master, host_slave, remote_master, remote_slave):
            self.addCleanup(os.close, fd)
        tty.setraw(host_slave)
        attrs = termios.tcgetattr(remote_slave)
        self.assertTrue(attrs[3] & termios.ICANON)
        def remote_terminal_session():
            os.setsid()
            fcntl.ioctl(remote_slave, termios.TIOCSCTTY, 0)
        child = subprocess.Popen(["/bin/sh", "-i"], stdin=remote_slave, stdout=remote_slave,
                                 stderr=remote_slave, close_fds=True,
                                 preexec_fn=remote_terminal_session,
                                 env=dict(os.environ, PS1="unit$ ", PS2=""))
        def stop_child():
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        self.addCleanup(stop_child)
        stop = threading.Event()
        failures = []
        bridge_state = {"read": 0, "write": 0, "sample": b""}
        def bridge():
            # A second PTY models an independent remote UART terminal. Configuring
            # host pyserial raw must not disable the remote ICANON discipline.
            peers = {host_master: remote_master, remote_master: host_master}
            pending = {host_master: bytearray(), remote_master: bytearray()}
            for fd in peers:
                os.set_blocking(fd, False)
            try:
                while not stop.is_set():
                    readable, writable, _ = select.select(list(peers), [fd for fd in peers if pending[fd]], [], 0.02)
                    for fd in readable:
                        try:
                            data = os.read(fd, 4096)
                        except BlockingIOError:
                            continue
                        bridge_state["read"] += len(data)
                        bridge_state["sample"] = data[:100]
                        pending[peers[fd]].extend(data)
                    for fd in writable:
                        try:
                            sent = os.write(fd, pending[fd])
                        except BlockingIOError:
                            continue
                        bridge_state["write"] += sent
                        del pending[fd][:sent]
            except OSError as exc:
                if not stop.is_set():
                    failures.append(exc)
        thread = threading.Thread(target=bridge, daemon=True)
        thread.start()
        def stop_bridge():
            stop.set()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
        self.addCleanup(stop_bridge)
        self.cfg["port"] = os.ttyname(host_slave)
        self.cfg["budgets"].update(max_raw_bytes=1024 * 1024)
        self.cfg["identity"] = {"script": "printf 'unit-id\\n'", "pattern": "(?m)^unit-id$"}
        self.script.write_bytes(source)
        result = session.run_exec(self.cfg, "console", self.out, self.script,
                                 {"dir": "test-control", "attempt": "canonical-pty"},
                                 authorizer=lambda r, b: {"event_id": "canonical-test"},
                                 claim_callback=lambda p, s: {"claimed": True})
        self.assertFalse(failures, failures)
        return result, (self.out / "rx.bin").read_bytes(), (failures, bridge_state, child.poll())

    @unittest.skipIf(os.name == "nt", "Linux double PTY with remote canonical line discipline")
    def test_linux_remote_canonical_tty_accepts_script_larger_than_max_canon(self):
        source = b": #" + b"x" * 6000 + b"\nprintf '%s\\n' canonical-tail\\\n\n"
        result, raw, diagnostic = self.run_canonical_tty_script(source)
        self.assertEqual(result["exit_code"], 0, (result, diagnostic))
        self.assertEqual(result["outcome"], "pass", result)
        self.assertIn(b"canonical-tail", raw)

    @unittest.skipIf(os.name == "nt", "Linux double PTY with remote canonical line discipline")
    def test_linux_remote_script_stdin_cannot_consume_exit_protocol(self):
        source = b'read stolen_line\nprintf "STOLEN=[%s]\\n" "$stolen_line"\nfalse\n'
        result, raw, diagnostic = self.run_canonical_tty_script(source)
        self.assertEqual(result["exit_code"], 1, (result, raw[-1500:], diagnostic))
        self.assertEqual(result["outcome"], "fail", result)
        self.assertEqual(result["remote_state"], "completed", result)
        self.assertIn(b"STOLEN=[]", raw)
        self.assertNotIn(b"STOLEN=[__icode_serial_rc=$?]", raw)

    @unittest.skipIf(os.name == "nt", "Linux double PTY with remote canonical line discipline")
    def test_linux_remote_script_explicit_heredoc_still_supplies_stdin(self):
        source = ("read supplied <<'ICODE_TEST_INPUT'\nheredoc-'汉字\nICODE_TEST_INPUT\n"
                  "printf 'HEREDOC=[%s]\\n' \"$supplied\"\n").encode("utf-8")
        result, raw, diagnostic = self.run_canonical_tty_script(source)
        self.assertEqual(result["exit_code"], 0, (result, diagnostic))
        self.assertEqual(result["outcome"], "pass", result)
        self.assertIn("HEREDOC=[heredoc-'汉字]".encode("utf-8"), raw)

    def test_passive_factory_suppresses_backend_input_purge(self):
        # pyserial 3.5 itself clears RX on open on both platforms. Our adapter
        # must suppress that backend behavior, not merely avoid public reset().
        if os.name != "nt":
            self.skipTest("Windows adapter mock; POSIX adapter is checked by PTY")
        import serial.serialwin32 as backend
        original_open = backend.Serial.open
        with mock.patch.object(backend.win32, "CreateFile", return_value=123), \
             mock.patch.object(backend.win32, "CreateEvent", return_value=456), \
             mock.patch.object(backend.win32, "GetCommTimeouts", return_value=True), \
             mock.patch.object(backend.win32, "CloseHandle", return_value=True), \
             mock.patch.object(backend.win32, "SetCommTimeouts", return_value=True), \
             mock.patch.object(backend.win32, "PurgeComm") as purge:
            port = transport.serial_factory()(port=None)
            port.port = "mock-port"
            with mock.patch.object(port, "_reconfigure_port"), \
                 mock.patch.object(port, "cancel_read"), mock.patch.object(port, "cancel_write"):
                port.open()
                port.close()
            purge.assert_not_called()
        self.assertIs(backend.Serial.open, original_open)

    @unittest.skipUnless(os.name == "nt", "Windows handle cleanup mock")
    def test_windows_adapter_open_failure_closes_owned_handles(self):
        import serial.serialwin32 as backend
        with mock.patch.object(backend.win32, "CreateFile", return_value=123), \
             mock.patch.object(backend.win32, "CreateEvent", return_value=456), \
             mock.patch.object(backend.win32, "GetCommTimeouts", return_value=True), \
             mock.patch.object(backend.win32, "CloseHandle", return_value=True) as close_handle, \
             mock.patch.object(backend.win32, "SetCommTimeouts", return_value=True), \
             mock.patch.object(backend.win32, "PurgeComm") as purge:
            port = transport.serial_factory()(port=None)
            port.port = "mock-port"
            with mock.patch.object(port, "_reconfigure_port", side_effect=OSError("configure failed")), \
                 mock.patch.object(port, "cancel_read"), mock.patch.object(port, "cancel_write"):
                with self.assertRaises(OSError):
                    port.open()
            self.assertFalse(port.is_open)
            self.assertIsNone(port._port_handle)
            self.assertEqual(close_handle.call_count, 3)
            purge.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Linux PTY external OS exclusive owner")
    def test_linux_pty_external_exclusive_owner_is_not_terminated(self):
        import serial
        import tty
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        self.cfg["port"] = os.ttyname(slave)
        with serial.Serial(self.cfg["port"], exclusive=True, timeout=0.01) as external_owner:
            result = session.run_listen(self.cfg, "console", self.out)
            self.assertTrue(external_owner.is_open)
            self.assertEqual(result["tx_bytes"], 0)
            self.assertFalse(result["complete"])
            self.assertNotEqual(result["outcome"], "pass")

    def test_operation_input_binds_script_configuration_and_connection(self):
        cfg = transport.load_connection(self.cfg, "console")
        original = session.operation_input(cfg, self.script.read_bytes())
        binding = json.loads(original)
        self.assertEqual(binding["action"], "serial_exec")
        self.assertEqual(binding["operation_class"], "external_side_effect")
        self.assertEqual(binding["connection"], "console")
        self.assertEqual(binding["script_sha256"], hashlib.sha256(self.script.read_bytes()).hexdigest())
        self.assertNotEqual(original, session.operation_input(cfg, b"changed"))
        self.assertNotEqual(original, session.operation_input(dict(cfg, baud=9600), self.script.read_bytes()))

    def test_evidence_has_correlatable_clocks_configuration_and_raw_trace(self):
        result = self.listen(Factory(initial=b"a\x00b"))
        for name in ("session.json", "rx.bin", "rx.txt", "io_trace.jsonl", "result.json"):
            self.assertTrue((self.out / name).is_file(), name)
        meta = json.loads((self.out / "session.json").read_text(encoding="utf-8"))
        saved = json.loads((self.out / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, result)
        self.assertEqual(meta["actual_config"]["port"], self.cfg["port"])
        self.assertEqual(meta["session_id"], result["session_id"])
        self.assertIn("host_start", meta)
        self.assertGreaterEqual(result["monotonic_end"], meta["monotonic_start"])
        trace = [json.loads(line) for line in (self.out / "io_trace.jsonl").read_text().splitlines()]
        self.assertTrue(trace)
        self.assertEqual(trace[0]["direction"], "rx")
        self.assertIn("host_utc", trace[0])
        self.assertIn("monotonic", trace[0])

    def test_cli_has_only_list_listen_exec_and_rejects_authorized_switch(self):
        for argv in (["--help"], ["exec", "--authorized"]):
            proc = subprocess.run([sys.executable, "-B", str(TOOLS / "serial_session.py"), *argv],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 0 if argv == ["--help"] else 2)
            if argv == ["--help"]:
                self.assertIn("list", proc.stdout)
                self.assertIn("listen", proc.stdout)
                self.assertIn("exec", proc.stdout)


if __name__ == "__main__":
    unittest.main()
