"""Real control-file I/O and crash recovery with physical candidate storage."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_candidate_storage import snapshot

ROOT = Path(__file__).resolve().parents[1]


def load_control():
    spec = importlib.util.spec_from_file_location("storage_control_test", ROOT / "tools/icode_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageControlTests(unittest.TestCase):
    def setUp(self):
        self.api = load_control()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve() / ".icode_output/.icode_output_1"
        args = self.api.build_parser().parse_args(["create", "--dir", str(self.directory),
            "--ticket-id", "storage-fixture", "--requirement", "storage fixture", "--birth", "plan"])
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(args.func(args), 0)

    def physical(self, name):
        return json.loads((self.directory / name).read_text(encoding="utf-8"))

    def update(self):
        before = self.api.load_metadata(self.directory)
        after = copy.deepcopy(before)
        after.setdefault("extensions", {})["storage_fixture"] = snapshot()
        event = self.api.commit_metadata_and_event(self.directory, before, after,
                    "metadata_updated", {"snapshot": snapshot()})
        return before, after, event

    def test_new_ticket_uses_typed_storage_and_keeps_logical_hash(self):
        self.assertEqual(self.physical(self.api.METADATA_NAME).get("storage_version"), 2)
        _, after, event = self.update()
        actual = self.api.load_metadata(self.directory)
        self.assertEqual(actual, after)
        self.assertEqual(self.api.metadata_hash(actual), event["payload"]["metadata_hash_after"])
        physical = self.physical(self.api.METADATA_NAME)
        self.assertIn("$icode_manifest_ref", physical["value"]["extensions"]["storage_fixture"]["manifest"])
        events, problems = self.api.verify_event_chain(self.directory, after)
        self.assertEqual(problems, [])
        self.assertEqual(events[-1], event)
        self.assertFalse((self.directory / self.api.TXN_NAME).exists())

    def test_legacy_prefix_bytes_survive_v2_event_append(self):
        events, _ = self.api.read_events(self.directory)
        prefix = (json.dumps(events[0], ensure_ascii=False, indent=None) + "\r\n").encode("utf-8")
        (self.directory / self.api.EVENTS_NAME).write_bytes(prefix)
        _, _, event = self.update()
        self.assertTrue((self.directory / self.api.EVENTS_NAME).read_bytes().startswith(prefix))
        self.assertEqual(self.api.read_events(self.directory)[0][-1], event)

    def test_postappend_compact_failure_never_rolls_back_committed_metadata(self):
        original = self.api.atomic_write_json
        count = 0
        def write(path, value):
            nonlocal count
            if Path(path).name == self.api.METADATA_NAME:
                count += 1
                if count >= 2: raise OSError("postcompact failure")
            return original(path, value)
        with mock.patch.object(self.api, "atomic_write_json", side_effect=write):
            with self.assertRaises(OSError): self.update()
        self.assertTrue((self.directory / self.api.TXN_NAME).exists())
        current = self.api.load_metadata(self.directory)
        self.assertIn("storage_fixture", current["extensions"])
        self.assertEqual(self.api.recover_pending_transaction(self.directory), "completed")
        self.assertFalse((self.directory / self.api.TXN_NAME).exists())
        self.assertIn("$icode_manifest_ref", self.physical(self.api.METADATA_NAME)["value"]["extensions"]["storage_fixture"]["manifest"])

    def test_observation_remembers_first_ledger_bytes_across_metadata_and_events(self):
        self.update()
        observation = self.api._ControlReadObservation(self.directory)
        self.api.load_metadata(self.directory, _observation=observation)
        path = self.directory / self.api.EVENTS_NAME
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(self.api.ControlError):
            self.api.read_events(self.directory, _observation=observation)

    def test_readonly_adapter_rejects_pending_without_any_write(self):
        self.assertTrue(hasattr(self.api, "read_metadata_readonly"), "readonly metadata adapter is missing")
        self.update()
        txn = self.directory / self.api.TXN_NAME
        txn.write_text("{}", encoding="utf-8")
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()}
        with self.assertRaises(self.api.ControlError): self.api.read_metadata_readonly(self.directory)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir() if p.is_file()})

    def test_new_metadata_arrays_must_be_defined_by_the_transaction_event(self):
        before = self.api.load_metadata(self.directory)
        after = copy.deepcopy(before)
        after.setdefault("extensions", {})["unmirrored_snapshot"] = snapshot()
        original = {p.name: p.read_bytes() for p in self.directory.iterdir() if p.is_file()}
        with self.assertRaisesRegex(self.api.ControlError, "definition"):
            self.api.commit_metadata_and_event(self.directory, before, after, "metadata_updated", {})
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.directory.iterdir() if p.is_file()})

    def test_only_typed_control_documents_use_compact_atomic_json(self):
        current = (self.directory / self.api.METADATA_NAME).read_bytes()
        self.assertEqual(current.count(b"\n"), 1)
        artifact = self.directory / "ordinary.json"
        self.api.atomic_write_json(artifact, {"business": {"value": 1}})
        self.assertGreater(artifact.read_bytes().count(b"\n"), 1)
        seen = []
        original = self.api.atomic_write_json
        def write(path, value):
            original(path, value)
            if Path(path).name == self.api.TXN_NAME:
                seen.append(Path(path).read_bytes())
        with mock.patch.object(self.api, "atomic_write_json", side_effect=write): self.update()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].count(b"\n"), 1)

    def test_raw_budget_counts_whitespace_bytes_not_only_canonical_json(self):
        path = self.directory / self.api.EVENTS_NAME
        original = path.read_bytes()
        path.write_bytes(original + b" " * 4096 + b"\n")
        storage = self.api.storage_module()
        limits = storage.StorageLimits(max_raw_bytes=len(original) + 1)
        with mock.patch.object(storage, "StorageLimits", return_value=limits):
            with self.assertRaisesRegex(self.api.ControlError, "max_raw_bytes"):
                self.api.read_events(self.directory)


if __name__ == "__main__": unittest.main()
