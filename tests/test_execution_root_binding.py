import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/icode_control.py"
spec = importlib.util.spec_from_file_location("binding_control", SCRIPT)
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="icode-bind-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.control = self.root / "控制 空间"
        self.code = self.root / "代码 空间"
        self.control.mkdir()
        self.code.mkdir()
        self.ticket = self.control / ".icode_output/.icode_output_1"
        self.call("create", "--dir", self.ticket, "--ticket-id", "bind-1",
                  "--requirement", "binding test", "--birth", "plan",
                  "--request-id", "birth")

    def call(self, *args, ok=True):
        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPT),
                               *map(str, args)], capture_output=True,
                              text=True, encoding="utf-8", errors="strict", timeout=15)
        self.assertEqual(proc.returncode == 0, ok, (proc.stdout, proc.stderr))
        return json.loads(proc.stdout)

    def bind(self, path=None, request="binding", ticket_id="bind-1", ok=True):
        return self.call("bind-execution-root", "--dir", self.ticket,
                         "--ticket-id", ticket_id, "--execution-root", path or self.code,
                         "--request-id", request, ok=ok)

    def snapshot(self):
        return tuple((self.ticket / name).read_bytes()
                     for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME))

    def test_real_cli_projection_and_subprocess_replay(self):
        before = ctl.load_metadata(self.ticket)
        first = self.bind()
        frozen = self.snapshot()
        second = self.bind()
        self.assertTrue(second["already_applied"])
        self.assertEqual(set(second), {"ok", "already_applied", "event_id", "ticket_id", "request_id"})
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(frozen, self.snapshot())
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["project_path"], before["project_path"])
        self.assertEqual(ctl.execution_workspace(self.ticket, meta), self.code)
        self.assertEqual(ctl.trusted_execution_workspace(self.ticket, meta), self.code)
        policy = self.call("action-policy", "--dir", self.ticket)
        self.assertEqual(policy["execution_root"], str(self.code))
        self.assertFalse(ctl.verify_event_chain(self.ticket, meta)[1])
        (self.code / "source.txt").write_text("isolated source", encoding="utf-8")
        (self.control / "source.txt").write_text("wrong storage root", encoding="utf-8")
        self.call("metadata-update", "--dir", self.ticket,
                  "--set-json", json.dumps({"code_files": ["source.txt"]}),
                  "--request-id", "code-files")
        fact = ctl.resolve_port(self.ticket, ctl.load_metadata(self.ticket), {
            "id": "code_files", "kind": "metadata_files", "value": "/code_files",
            "base": "workspace"})
        self.assertTrue(fact["exists"])
        self.assertEqual(fact["items"][0]["sha256"], ctl.file_sha256(self.code / "source.txt"))
        self.assertNotEqual(fact["items"][0]["sha256"], ctl.file_sha256(self.control / "source.txt"))

    def test_wrong_ticket_and_overlap_and_missing_and_file_are_unchanged(self):
        file = self.root / "file"
        file.write_text("x", encoding="utf-8")
        before = self.snapshot()
        for path in (self.root, self.control, self.ticket, self.root / "missing", file):
            with self.subTest(path=path):
                self.bind(path, ok=False)
                self.assertEqual(before, self.snapshot())
        self.bind(ticket_id="wrong", ok=False)
        self.assertEqual(before, self.snapshot())

    def test_binding_is_immutable_and_request_cannot_change_root(self):
        self.bind()
        frozen = self.snapshot()
        other = self.root / "other"
        other.mkdir()
        for path, request in ((other, "binding"), (other, "new"), (self.code, "new")):
            with self.subTest(path=path, request=request):
                self.bind(path, request=request, ok=False)
                self.assertEqual(frozen, self.snapshot())

    def test_plain_metadata_and_birth_cannot_set_binding(self):
        value = {"execution_binding": {"version": 1}}
        before = self.snapshot()
        result = self.call("metadata-update", "--dir", self.ticket,
                           "--set-json", json.dumps(value), ok=False)
        self.assertEqual(result["gate_id"], "metadata_update_protected")
        self.assertEqual(before, self.snapshot())
        result = self.call("create", "--dir", self.control / ".icode_output/.icode_output_2",
                           "--ticket-id", "bind-2", "--requirement", "x", "--birth", "plan",
                           "--metadata-json", json.dumps(value), ok=False)
        self.assertEqual(result["gate_id"], "birth_protected_fields")

    def test_replaced_root_and_ancestor_rejected_but_trace_remains_readable(self):
        self.bind()
        meta = ctl.load_metadata(self.ticket)
        self.code.rename(self.root / "old-code")
        self.code.mkdir()
        for fn in (ctl.execution_workspace, ctl.trusted_execution_workspace):
            with self.assertRaises(ctl.ControlError):
                fn(self.ticket, meta)
        self.call("trace", "--dir", self.ticket)
        self.assertFalse(ctl.verify_event_chain(self.ticket, meta)[1])

    def test_old_unbound_ticket_keeps_old_root(self):
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(ctl.execution_workspace(self.ticket, meta), self.control)
        self.assertEqual(ctl.trusted_execution_workspace(self.ticket, meta), self.control)

    def test_pure_semantics_rejects_forged_markers_and_open_lifecycles(self):
        binding = ctl.capture_execution_binding(str(self.code))
        meta = dict(ctl.load_metadata(self.ticket), execution_binding=binding)
        event = {"event_type": "metadata_updated", "actor": "icode", "request_id": "b",
                 "payload": {"execution_root_binding": 1,
                             "set": {"execution_binding": binding}, "append": {},
                             "metadata_hash_after": ctl.metadata_hash(meta)}}
        bad = []
        no_marker = json.loads(json.dumps(event))
        del no_marker["payload"]["execution_root_binding"]
        bad.append(([no_marker], meta))
        wrong_marker = json.loads(json.dumps(event))
        wrong_marker["payload"]["execution_root_binding"] = True
        bad.append(([wrong_marker], meta))
        bad.extend((([event, event], meta), ([], meta), ([event], {})))
        for kind, key in (("step_started", "attempt"), ("operation_started", "attempt"),
                          ("agent_spawned", "spawn_id")):
            bad.append(([{"event_type": kind, "payload": {key: "a"}}, event], meta))
        bad.append(([{"event_type": "close_phase", "payload": {"to": "close_planned"}}, event], meta))
        changed = json.loads(json.dumps(meta))
        changed["execution_binding"]["ancestors"][-1]["inode"] += 1
        bad.append(([event], changed))
        for sequence, value in bad:
            with self.subTest(sequence=sequence):
                mirror = ctl.ExecutionBindingMirror(value)
                for item in sequence:
                    mirror.consume(item)
                self.assertTrue(mirror.finish())
        mirror = ctl.ExecutionBindingMirror(meta)
        mirror.consume(event)
        self.assertEqual(mirror.finish(), [])

    def test_boolean_ids_extra_fields_and_windows_junction_attribute_rejected(self):
        binding = ctl.capture_execution_binding(str(self.code))
        for key, value in (("device", True), ("inode", 0), ("inode", False)):
            broken = json.loads(json.dumps(binding))
            broken["ancestors"][-1][key] = value
            self.assertFalse(ctl.execution_binding_shape_ok(broken))
            with self.assertRaises(ctl.ControlError):
                ctl.require_valid_metadata(dict(ctl.load_metadata(self.ticket), execution_binding=broken))
        binding["extra"] = 1
        self.assertFalse(ctl.execution_binding_shape_ok(binding))
        from types import SimpleNamespace
        real = Path.lstat
        def reparse(path):
            value = real(path)
            if path == self.code:
                return SimpleNamespace(st_mode=value.st_mode, st_dev=value.st_dev,
                    st_ino=value.st_ino, st_file_attributes=ctl.stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return value
        with patch.object(Path, "lstat", reparse):
            with self.assertRaises(ctl.ControlError):
                ctl.capture_execution_binding(str(self.code))

    def test_symlink_path_refused_when_platform_can_create_it(self):
        link = self.root / "link"
        try:
            link.symlink_to(self.code, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                raise
            self.skipTest("Windows host cannot create symlink: " + str(exc.errno))
        before = self.snapshot()
        self.bind(link, ok=False)
        self.assertEqual(before, self.snapshot())

    def test_ancestor_replacement_detected_when_leaf_identity_is_preserved(self):
        parent = self.root / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        self.bind(child)
        meta = ctl.load_metadata(self.ticket)
        parent.rename(self.root / "old-parent")
        parent.mkdir()
        (self.root / "old-parent/child").rename(child)
        with self.assertRaises(ctl.ControlError):
            ctl.execution_workspace(self.ticket, meta)

    def test_all_writers_share_structural_checkout_conflict_guard(self):
        self.bind()
        meta = ctl.load_metadata(self.ticket)
        active = {"path": str(self.code), "state": "active"}
        for key, value in (("active_checkout", active), ("checkout_history", [active])):
            with self.subTest(key=key):
                self.assertFalse(ctl.execution_binding_topology_ok(dict(meta, **{key: value})))
                with self.assertRaises(ctl.ControlError):
                    ctl.require_valid_metadata(dict(meta, **{key: value}))
        self.assertTrue(ctl.execution_binding_topology_ok(
            dict(meta, checkout_history=[{"state": "released"}])))

    def test_append_failure_rolls_back_via_original_transaction(self):
        from argparse import Namespace
        args = Namespace(dir=str(self.ticket), ticket_id="bind-1",
                         execution_root=str(self.code), request_id="b")
        before = self.snapshot()
        with patch.object(ctl, "append_prepared_event", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                ctl.cmd_bind_execution_root(args)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.ticket / ctl.TXN_NAME).exists())

    def test_real_open_agent_blocks_new_binding_without_writes(self):
        self.call("record-agent-spawn", "--dir", self.ticket,
                  "--task-scope", "binding boundary", "--expected-artifact", "note",
                  "--evidence-boundary", "portable", "--join-condition", "done",
                  "--backend", "test", "--model", "test", "--capability", "text",
                  "--request-id", "agent-open")
        before = self.snapshot()
        result = self.bind(ok=False)
        self.assertEqual(result["gate_id"], "execution_binding_quiescence")
        self.assertEqual(before, self.snapshot())

    def test_exact_replay_during_open_agent_returns_only_existing_event(self):
        first = self.bind()
        self.call("record-agent-spawn", "--dir", self.ticket,
                  "--task-scope", "binding boundary", "--expected-artifact", "note",
                  "--evidence-boundary", "portable", "--join-condition", "done",
                  "--backend", "test", "--model", "test", "--capability", "text",
                  "--request-id", "agent-open")
        before = self.snapshot()
        result = self.bind()
        self.assertTrue(result["already_applied"])
        self.assertEqual(result["event_id"], first["event_id"])
        self.assertEqual(before, self.snapshot())

    def test_unpaired_step_and_operation_block_command_before_transaction(self):
        from argparse import Namespace
        args = Namespace(dir=str(self.ticket), ticket_id="bind-1",
                         execution_root=str(self.code), request_id="b")
        # This isolates the command's quiescence guard; it is not a real step receipt.
        for kind, payload in ((kind, payload)
                              for kind in ("step_started", "operation_started")
                              for payload in ({"attempt": "open"}, {}, None)):
            events = [{"event_type": kind, "payload": payload}]
            with self.subTest(kind=kind, payload=payload):
                with patch.object(ctl, "verify_event_chain", return_value=(events, [])):
                    with patch.object(ctl, "commit_metadata_and_event") as commit:
                        with self.assertRaises(ctl.ControlError) as raised:
                            ctl.cmd_bind_execution_root(args)
                        self.assertEqual(raised.exception.extra["gate_id"],
                                         "execution_binding_quiescence")
                        commit.assert_not_called()

    def test_real_step_and_operation_cli_block_binding(self):
        # Separate tickets keep the two real open-lifecycle checks independent.
        for number, args in enumerate((
            ("step", "--step", "plan", "--phase", "start", "--attempt", "open-step",
             "--request", "open-step"),
            ("operation", "--name", "read", "--opclass", "read_only", "--phase", "start",
             "--attempt", "open-operation", "--request", "open-operation"),
        ), 2):
            ticket = self.control / ".icode_output" / (".icode_output_" + str(number))
            ticket_id = "bind-" + str(number)
            self.call("create", "--dir", ticket, "--ticket-id", ticket_id,
                      "--requirement", "open execution", "--birth", "plan")
            self.call(args[0], "--dir", ticket, *args[1:])
            before = tuple((ticket / name).read_bytes()
                           for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME))
            result = self.call("bind-execution-root", "--dir", ticket,
                               "--ticket-id", ticket_id, "--execution-root", self.code,
                               "--request-id", "bind-open", ok=False)
            self.assertEqual(result["gate_id"], "execution_binding_quiescence")
            self.assertEqual(before, tuple((ticket / name).read_bytes()
                             for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME)))

    def test_real_checkout_update_blocks_binding_without_new_mutation(self):
        self.call("metadata-update", "--dir", self.ticket,
                  "--set-json", json.dumps({"active_checkout": {
                      "path": str(self.code), "state": "active"}}), "--request-id", "checkout")
        before = self.snapshot()
        result = self.bind(ok=False)
        self.assertEqual(result["gate_id"], "execution_binding_topology")
        self.assertEqual(before, self.snapshot())

    def test_close_guard_invokes_actual_command_before_transaction(self):
        from argparse import Namespace
        args = Namespace(dir=str(self.ticket), ticket_id="bind-1",
                         execution_root=str(self.code), request_id="b")
        # Explicit command unit fixture, not a completed workflow/close receipt.
        meta = dict(ctl.load_metadata(self.ticket), close_state="close_planned")
        with patch.object(ctl, "load_metadata", return_value=meta):
            with patch.object(ctl, "verify_event_chain", return_value=([], [])):
                with patch.object(ctl, "commit_metadata_and_event") as commit:
                    with self.assertRaises(ctl.ControlError) as raised:
                        ctl.cmd_bind_execution_root(args)
                    self.assertEqual(raised.exception.extra["gate_id"],
                                     "execution_binding_quiescence")
                    commit.assert_not_called()

    def test_mirror_markers_lifecycle_and_checkout_history(self):
        binding = ctl.capture_execution_binding(str(self.code))
        meta = {"execution_binding": binding}
        event = {"event_type": "metadata_updated", "actor": "icode", "request_id": "b",
                 "payload": {"execution_root_binding": 1,
                             "set": meta, "append": {},
                             "metadata_hash_after": ctl.metadata_hash(meta)}}
        bad = []
        for marker in (0, 2, "1", True, None):
            changed = copy.deepcopy(event)
            changed["payload"]["execution_root_binding"] = marker
            bad.append([changed])
        for field, value in (("actor", "user"), ("request_id", " "),
                             ("event_type", "external_note")):
            changed = copy.deepcopy(event)
            changed[field] = value
            bad.append([changed])
        changed = copy.deepcopy(event)
        changed["payload"]["extra"] = 1
        bad.append([changed])
        for verb in ("set", "append"):
            for field, value in (("active_checkout", {"state": "active"}),
                                 ("checkout_history", [{"state": "active"}])):
                bad.append([event, {"event_type": "metadata_updated", "payload": {
                    "set": {}, "append": {}, verb: {field: value}}}])
        bad.append([event, {"event_type": "ticket_reopened", "payload": {}}])
        for events in bad:
            with self.subTest(events=events):
                mirror = ctl.ExecutionBindingMirror(meta)
                for item in events:
                    mirror.consume(item)
                self.assertTrue(mirror.finish())
        paired = []
        for start, finish, key in (("step_started", "step_finished", "attempt"),
                                    ("operation_started", "operation_finished", "attempt"),
                                    ("agent_spawned", "agent_result", "spawn_id")):
            paired.extend(({"event_type": start, "payload": {key: "id"}},
                           {"event_type": finish, "payload": {key: "id"}}))
        paired.extend(({"event_type": "close_phase", "payload": {}},
                       {"event_type": "ticket_reopened", "payload": {}}, event))
        mirror = ctl.ExecutionBindingMirror(meta)
        for item in paired:
            mirror.consume(item)
        self.assertEqual(mirror.finish(), [])
        legacy = ctl.ExecutionBindingMirror({})
        legacy.consume({"event_type": "step_started", "payload": None})
        legacy.consume({"event_type": "step_finished", "payload": {"attempt": "x"}})
        self.assertEqual(legacy.open_steps, {None})
        self.assertEqual(legacy.finish(), [])

    def test_pure_shape_cross_platform_and_depth_without_filesystem_access(self):
        from pathlib import PurePosixPath, PureWindowsPath
        for raw, cls in (("/offline/代码 空间", PurePosixPath),
                         ("C:\\offline\\代码 空间", PureWindowsPath),
                         ("\\\\server\\share\\code", PureWindowsPath)):
            root = cls(raw)
            value = {"version": 1, "path": raw, "ancestors": [
                {"path": str(part), "device": 0, "inode": 1}
                for part in [*reversed(root.parents), root]]}
            with patch.object(Path, "lstat", side_effect=AssertionError("offline stat")):
                self.assertTrue(ctl.execution_binding_shape_ok(value))
                self.assertTrue(ctl.execution_binding_topology_ok({"execution_binding": value}))
            for field, invalid in (("version", True), ("path", raw + "/"),
                                   ("ancestors", list(reversed(value["ancestors"])) )):
                self.assertFalse(ctl.execution_binding_shape_ok(dict(value, **{field: invalid})))
            row_extra = copy.deepcopy(value)
            row_extra["ancestors"][-1]["extra"] = 0
            self.assertFalse(ctl.execution_binding_shape_ok(row_extra))
        deep = "/" + "/".join(["x"] * 256)
        with patch.object(Path, "lstat", side_effect=AssertionError("depth before stat")):
            with self.assertRaises(ctl.ControlError):
                ctl.capture_execution_binding(deep)
        before = self.snapshot()
        for raw in (str(self.code) + "/", str(self.code) + "/../" + self.code.name,
                    str(self.code) + "/.", "relative"):
            self.bind(raw, ok=False)
            self.assertEqual(before, self.snapshot())

    def test_missing_execution_directory_preserves_offline_evidence(self):
        self.bind()
        self.code.rmdir()
        meta = ctl.load_metadata(self.ticket)
        ctl.require_valid_metadata(meta)
        self.call("trace", "--dir", self.ticket)
        self.assertFalse(ctl.verify_event_chain(self.ticket, meta)[1])
        for consumer in (ctl.execution_workspace, ctl.trusted_execution_workspace):
            with self.assertRaises(ctl.ControlError):
                consumer(self.ticket, meta)

    def test_depth_limit_precedes_ancestor_materialization(self):
        from pathlib import PurePath
        depth = 256
        raw = "/" + "/".join(["component"] * depth)
        binding = {"version": 1, "path": raw, "ancestors": []}
        self.assertFalse(ctl.execution_binding_shape_ok(binding))

        class ParentsDepthDouble:
            def __len__(self):
                return depth

            def __getitem__(self, index):
                raise AssertionError("must reject depth before materializing ancestors")

        with patch.object(PurePath, "parents", property(lambda _: ParentsDepthDouble())):
            self.assertFalse(ctl.execution_binding_shape_ok(binding))
            with self.assertRaises(ctl.ControlError):
                ctl.capture_execution_binding(raw)

    def test_binding_hash_cannot_be_hidden_by_later_valid_metadata_hash(self):
        binding = ctl.capture_execution_binding(str(self.code))
        meta = dict(ctl.load_metadata(self.ticket), execution_binding=binding)
        birth = ctl.read_events(self.ticket)[0][0]
        event = {"event_type": "metadata_updated", "actor": "icode", "request_id": "b",
                 "payload": {"execution_root_binding": 1,
                             "set": {"execution_binding": binding}, "append": {},
                             "metadata_hash_after": ctl.metadata_hash(meta)}}
        later = {"event_type": "metadata_updated", "actor": "icode", "request_id": "later",
                 "payload": {"set": {}, "append": {},
                             "metadata_hash_after": ctl.metadata_hash(meta)}}
        self.assertEqual(ctl.validate_event_semantics([birth, event, later], meta), [])
        for invalid in (None, True, "not-a-hash", "A" * 64, "0" * 63, "0" * 65, 123):
            with self.subTest(invalid=invalid):
                broken = copy.deepcopy(event)
                if invalid is None:
                    del broken["payload"]["metadata_hash_after"]
                else:
                    broken["payload"]["metadata_hash_after"] = invalid
                problems = ctl.validate_event_semantics([birth, broken, later], meta)
                self.assertIn("binding_event_shape", problems)


if __name__ == "__main__":
    unittest.main()
