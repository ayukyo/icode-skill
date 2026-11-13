"""Actual process-exit transaction windows, using temporary control roots."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import test_candidate_storage_control as control_fixtures

ROOT = control_fixtures.ROOT


CHILD = r'''
import copy, json, os, pathlib, sys
from unittest import mock
sys.path[:0] = [str(pathlib.Path(sys.argv[1]) / 'tools'), str(pathlib.Path(sys.argv[1]) / 'tests')]
import icode_control as api
from test_candidate_storage import snapshot
directory, mode = pathlib.Path(sys.argv[2]), sys.argv[3]
real_write, real_append, real_unlink = api.atomic_write_json, api.atomic_append_line, pathlib.Path.unlink
metadata_writes = 0
def write(path, value):
    global metadata_writes
    real_write(path, value)
    if pathlib.Path(path).name == api.TXN_NAME and mode == 'journal': os._exit(73)
    if pathlib.Path(path).name == api.METADATA_NAME:
        metadata_writes += 1
        if (metadata_writes == 1 and mode == 'premetadata') or (metadata_writes == 2 and mode == 'compact'): os._exit(73)
def append(path, line):
    if mode == 'partial':
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line[:len(line)//2]); handle.flush(); os.fsync(handle.fileno())
        os._exit(73)
    real_append(path, line)
    if mode == 'append': os._exit(73)
def unlink(path, *args, **kwargs):
    if path.name == api.TXN_NAME and mode == 'unlink': os._exit(73)
    return real_unlink(path, *args, **kwargs)
before = api.load_metadata(directory)
after = copy.deepcopy(before)
after.setdefault('extensions', {})['storage_fixture'] = snapshot()
with mock.patch.object(api, 'atomic_write_json', side_effect=write), mock.patch.object(api, 'atomic_append_line', side_effect=append), mock.patch.object(pathlib.Path, 'unlink', new=unlink):
    api.commit_metadata_and_event(directory, before, after, 'metadata_updated', {'snapshot': snapshot()})
raise AssertionError('crash window was not reached')
'''


class StorageFaultTests(control_fixtures.StorageControlTests):
    def crash(self, mode):
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", CHILD, str(ROOT),
                                 str(self.directory), mode], capture_output=True, text=True,
                                encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 73, result.stdout + result.stderr)

    def test_actual_process_exit_at_each_durable_window(self):
        # Each subtest restores the original committed state via a new setUp.
        for index, mode in enumerate(("journal", "premetadata", "append", "compact", "unlink")):
            if index: self.setUp()
            with self.subTest(mode=mode):
                before = self.api.load_metadata(self.directory)
                prefix = (self.directory / self.api.EVENTS_NAME).read_bytes()
                self.crash(mode)
                self.assertTrue((self.directory / self.api.TXN_NAME).exists())
                with self.assertRaises(self.api.ControlError): self.api.read_metadata_readonly(self.directory)
                expected = "rolled_back" if mode in {"journal", "premetadata"} else "completed"
                self.assertEqual(self.api.recover_pending_transaction(self.directory), expected)
                current = self.api.load_metadata(self.directory)
                if expected == "rolled_back": self.assertEqual(current, before)
                else: self.assertIn("storage_fixture", current["extensions"])
                self.assertTrue((self.directory / self.api.EVENTS_NAME).read_bytes().startswith(prefix))
                self.assertEqual(self.api.verify_event_chain(self.directory, current)[1], [])
                self.assertIsNone(self.api.recover_pending_transaction(self.directory))

    def test_partial_append_is_not_absence_and_never_authorizes_rollback(self):
        self.crash("partial")
        metadata = (self.directory / self.api.METADATA_NAME).read_bytes()
        journal = (self.directory / self.api.TXN_NAME).read_bytes()
        with self.assertRaises(self.api.ControlError): self.api.recover_pending_transaction(self.directory)
        self.assertEqual((self.directory / self.api.METADATA_NAME).read_bytes(), metadata)
        self.assertEqual((self.directory / self.api.TXN_NAME).read_bytes(), journal)

    def test_append_success_then_fsync_error_completes_after(self):
        original = self.api.atomic_append_line
        def append(path, line):
            original(path, line)
            raise OSError("fsync response lost")
        with mock.patch.object(self.api, "atomic_append_line", side_effect=append):
            _, after, event = self.update()
        self.assertEqual(self.api.load_metadata(self.directory), after)
        self.assertEqual(self.api.read_events(self.directory)[0][-1], event)
        self.assertFalse((self.directory / self.api.TXN_NAME).exists())

    def test_schema1_pending_journal_is_still_recoverable(self):
        self.crash("premetadata")
        journal = self.api.load_json(self.directory / self.api.TXN_NAME, "fixture pending")
        self.api.atomic_write_json(self.directory / self.api.TXN_NAME, journal)
        self.assertEqual(self.api.recover_pending_transaction(self.directory), "rolled_back")
        self.assertNotIn("storage_fixture", self.api.load_metadata(self.directory).get("extensions", {}))

    def test_legacy_pending_recovery_validates_committed_ledger_before_rollback(self):
        metadata = self.api.load_metadata(self.directory)
        events, _ = self.api.read_events(self.directory)
        self.api.atomic_write_json(self.directory / self.api.METADATA_NAME, metadata)
        (self.directory / self.api.EVENTS_NAME).write_text(json.dumps(events[0]) + "\n", encoding="utf-8")
        self.crash("premetadata")
        event = json.loads((self.directory / self.api.EVENTS_NAME).read_text(encoding="utf-8"))
        event["event_hash"] = "f" * 64
        (self.directory / self.api.EVENTS_NAME).write_text(json.dumps(event) + "\n", encoding="utf-8")
        before = (self.directory / self.api.METADATA_NAME).read_bytes()
        with self.assertRaises(self.api.ControlError): self.api.recover_pending_transaction(self.directory)
        self.assertEqual((self.directory / self.api.METADATA_NAME).read_bytes(), before)
        self.assertTrue((self.directory / self.api.TXN_NAME).exists())

    def test_recovery_rejects_journal_after_not_bound_by_its_logical_event_hash(self):
        self.crash("append")
        journal = self.api.load_json(self.directory / self.api.TXN_NAME, "fixture journal")
        journal["before_metadata"] = self.api.load_metadata(self.directory)
        journal["after_metadata"]["requirement_summary"] = "forged after"
        self.api.atomic_write_json(self.directory / self.api.TXN_NAME, journal)
        original = (self.directory / self.api.METADATA_NAME).read_bytes()
        with self.assertRaises(self.api.ControlError): self.api.recover_pending_transaction(self.directory)
        self.assertEqual((self.directory / self.api.METADATA_NAME).read_bytes(), original)
        self.assertTrue((self.directory / self.api.TXN_NAME).exists())

    def test_recovery_reports_nonobject_event_payload_as_control_error(self):
        self.crash("premetadata")
        journal = self.api.load_json(self.directory / self.api.TXN_NAME, "fixture journal")
        journal["event"]["payload"] = [1]
        self.api.atomic_write_json(self.directory / self.api.TXN_NAME, journal)
        with self.assertRaises(self.api.ControlError): self.api.recover_pending_transaction(self.directory)
        self.assertTrue((self.directory / self.api.TXN_NAME).exists())

    def test_rollback_before_must_match_the_committed_metadata_projection(self):
        self.crash("premetadata")
        journal = self.api.load_json(self.directory / self.api.TXN_NAME, "fixture journal")
        journal["before_metadata"]["requirement_summary"] = "forged rollback state"
        physical = self.api._encode_control_document(journal, "transaction",
                    self.api._committed_ledger(self.directory).registry, True)
        self.api.atomic_write_json(self.directory / self.api.TXN_NAME, physical)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
        with self.assertRaises(self.api.ControlError): self.api.recover_pending_transaction(self.directory)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})
        self.assertTrue((self.directory / self.api.TXN_NAME).exists())

    def test_known_before_projection_cannot_be_removed_from_the_journal(self):
        self.crash("premetadata")
        journal = self.api.load_json(self.directory / self.api.TXN_NAME, "fixture journal")
        journal["before_metadata"] = None
        self.api.atomic_write_json(self.directory / self.api.TXN_NAME,
            self.api._encode_control_document(journal, "transaction", self.api._committed_ledger(self.directory).registry, True))
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
        with self.assertRaises(self.api.ControlError): self.api.recover_pending_transaction(self.directory)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})

    def test_birth_and_legacy_migration_without_prior_anchor_still_roll_back(self):
        after = self.api.load_metadata(self.directory)
        original_event = self.api.read_events(self.directory)[0][0]
        for name, before in (("birth", None), ("migration", {"schema_version": 2, "ticket_id": after["ticket_id"],
                                                            "project_path": after["project_path"]})):
            directory = self.directory.parent / ("fixture-" + name)
            directory.mkdir()
            event = copy.deepcopy(original_event)
            event["event_type"] = "ticket_created" if before is None else "migration_applied"
            event["event_hash"] = self.api.canonical_event_hash(event)
            journal = {"schema_version": 1, "before_metadata": before, "after_metadata": after, "event": event}
            self.api.atomic_write_json(directory / self.api.TXN_NAME, journal)
            self.api.atomic_write_json(directory / self.api.METADATA_NAME, after)
            with self.subTest(name=name):
                self.assertEqual(self.api.recover_pending_transaction(directory), "rolled_back")
                self.assertFalse((directory / self.api.TXN_NAME).exists())
                if before is None: self.assertFalse((directory / self.api.METADATA_NAME).exists())
                else: self.assertEqual(self.api.load_metadata(directory), before)


if __name__ == "__main__": unittest.main()
