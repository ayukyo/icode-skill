"""Physical candidate storage preserves logical identity and fails closed."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import icode_candidate as candidate


def snapshot():
    root = str(Path(tempfile.gettempdir()).resolve())
    value = dict(version=1, identity=dict(common_git_dir=root, checkout_realpath=root,
        object_format="sha1", common_git_device=0, common_git_inode=0,
        checkout_device=0, checkout_inode=0), base="1" * 40, head="1" * 40,
        head_tree="2" * 40, code_files=["中文.py"], scope_contract=None,
        inspection_scope={}, excluded_side_effects=[],
        control_artifact_exclusions=list(candidate.CONTROL_DIRS),
        manifest=[dict(path="中文.py", kind="file", mode="100644", sha256="a" * 64,
                       size=1, git_oid="3" * 40)],
        index_manifest=[dict(path="中文.py", mode="100644", oid="3" * 40)],
        untracked_manifest=[])
    keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
            "excluded_side_effects", "control_artifact_exclusions", "manifest")
    value["content_id"] = candidate._digest({k: value[k] for k in keys})
    value["candidate_id"] = candidate._digest(value)
    return value


def event(number=1, previous="", value=None):
    result = dict(schema_version=1, event_id=number, ticket_id="fixture",
                  previous_event_hash=previous, payload=value or {"snapshot": snapshot()})
    result["event_hash"] = digest(result)
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def validate_event(value, previous):
    if (value.get("schema_version") != 1 or value.get("event_id") != len(previous) + 1
            or value.get("ticket_id") != "fixture"
            or value.get("previous_event_hash") != (previous[-1]["event_hash"] if previous else "")
            or value.get("event_hash") != digest({k: v for k, v in value.items() if k != "event_hash"})):
        raise ValueError("invalid logical event")


class CandidateStorageTests(unittest.TestCase):
    def setUp(self):
        # Missing implementation is a feature assertion, not an import error.
        self.assertTrue((TOOLS / "candidate_storage.py").is_file(), "candidate storage codec is missing")
        import candidate_storage
        self.api = candidate_storage

    def ledger(self, values):
        return self.api.decode_events(values, logical_event_validator=validate_event)

    def test_first_inline_and_later_references_keep_full_logical_identity(self):
        first = event()
        physical, prospective = self.api.encode_event(first, committed_registry=self.api.Registry())
        self.assertEqual(physical["storage_version"], 2)
        self.assertIsInstance(physical["value"]["payload"]["snapshot"]["manifest"], list)
        ledger = self.ledger([physical])
        second = event(2, first["event_hash"])
        encoded, _ = self.api.encode_event(second, committed_registry=ledger.registry)
        self.assertIn("$icode_manifest_ref", encoded["value"]["payload"]["snapshot"]["manifest"])
        decoded = self.ledger([physical, encoded])
        self.assertEqual(decoded.events, [first, second])
        self.assertEqual(len(decoded.registry), 3)
        self.assertEqual(len(prospective), 3)

    def test_same_event_backward_reference_and_no_mutable_aliases(self):
        logical = event(value={"a": snapshot(), "b": snapshot()})
        encoded, _ = self.api.encode_event(logical, committed_registry=self.api.Registry())
        self.assertIn("$icode_manifest_ref", encoded["value"]["payload"]["b"]["manifest"])
        actual = self.ledger([encoded]).events[0]
        actual["payload"]["a"]["manifest"][0]["size"] = 99
        self.assertEqual(actual["payload"]["b"]["manifest"][0]["size"], 1)
        self.assertEqual(logical["payload"]["a"]["manifest"][0]["size"], 1)

    def test_metadata_and_journal_never_register_new_inline_definitions(self):
        registry = self.api.Registry()
        logical = {"schema_version": 3, "a": snapshot(), "b": snapshot()}
        encoded = self.api.encode_document(logical, kind="metadata", committed_registry=registry)
        self.assertIsInstance(encoded["value"]["b"]["manifest"], list)
        self.assertEqual(self.api.decode_document(encoded, kind="metadata", registry=registry), logical)
        self.assertEqual(len(registry), 0)
        transaction = {"schema_version": 1, "before_metadata": logical, "after_metadata": logical,
                       "event": event()}
        encoded_txn = self.api.encode_document(transaction, kind="transaction", committed_registry=registry)
        self.assertEqual(self.api.decode_document(encoded_txn, kind="transaction", registry=registry), transaction)
        self.assertIsInstance(encoded_txn["value"]["after_metadata"]["b"]["manifest"], list)

    def test_legacy_inline_events_supply_definitions_without_changing_input(self):
        first = event()
        before = copy.deepcopy(first)
        ledger = self.ledger([first])
        logical = {"schema_version": 3, "current": snapshot()}
        encoded = self.api.encode_document(logical, kind="metadata", committed_registry=ledger.registry)
        self.assertIn("$icode_manifest_ref", encoded["value"]["current"]["manifest"])
        self.assertEqual(self.api.decode_document(encoded, kind="metadata", registry=ledger.registry), logical)
        self.assertEqual(first, before)

    def test_unknown_malformed_envelopes_and_reserved_markers_fail_closed(self):
        malformed = [dict(storage_version=x, kind="metadata", value={}) for x in (True, "2", 2.0, 3)]
        malformed += [dict(storage_version=2, kind="event", value={}),
                      dict(storage_version=2, kind="metadata", value={}, extra=1),
                      dict(storage_version=2, kind="metadata", value={"business": {"$icode_manifest_ref": {}}})]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(self.api.StorageError):
                self.api.decode_document(value, kind="metadata", registry=self.api.Registry())

    def test_missing_wrong_kind_extra_forward_and_corrupt_definitions_reject(self):
        physical, _ = self.api.encode_event(event(value={"a": snapshot(), "b": snapshot()}),
                                            committed_registry=self.api.Registry())
        for mode in ("missing", "kind", "extra", "forward", "corrupt"):
            damaged = copy.deepcopy(physical)
            first = damaged["value"]["payload"]["a"]
            second = damaged["value"]["payload"]["b"]
            if mode == "missing": second["manifest"]["$icode_manifest_ref"]["sha256"] = "f" * 64
            elif mode == "kind": second["manifest"]["$icode_manifest_ref"]["kind"] = "index_manifest"
            elif mode == "extra": second["manifest"]["$icode_manifest_ref"]["extra"] = True
            elif mode == "forward": first["manifest"], second["manifest"] = second["manifest"], first["manifest"]
            else: first["manifest"][0]["size"] = 2
            with self.subTest(mode=mode), self.assertRaises(self.api.StorageError): self.ledger([damaged])

    def test_failed_event_validation_does_not_mutate_committed_registry(self):
        registry = self.api.Registry()
        physical, _ = self.api.encode_event(event(), committed_registry=registry)
        physical["value"]["event_hash"] = "f" * 64
        with self.assertRaises(self.api.StorageError): self.ledger([physical])
        self.assertEqual(len(registry), 0)

    def test_ordinary_manifest_named_business_data_is_preserved(self):
        logical = {"schema_version": 3, "business": {"manifest": [1, 2], "version": 1}}
        encoded = self.api.encode_document(logical, kind="metadata", committed_registry=self.api.Registry())
        self.assertEqual(encoded["value"], logical)

    def test_independent_budgets_and_strict_json(self):
        encoded, _ = self.api.encode_event(event(value={"a": snapshot(), "b": snapshot()}),
                                            committed_registry=self.api.Registry())
        for key in ("max_events", "max_refs", "max_definitions", "max_manifest_rows", "max_expanded_bytes", "max_depth", "max_raw_bytes"):
            with self.subTest(key=key), self.assertRaises(self.api.StorageError):
                self.api.decode_events([encoded], limits=self.api.StorageLimits(**{key: 0}),
                                       logical_event_validator=validate_event)
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(self.api.StorageError): self.api.strict_json_loads(raw)

    def test_budget_boundaries_references_and_invalid_configuration(self):
        logical = {"schema_version": 3, "current": snapshot()}
        registry = self.ledger([event()]).registry
        encoded = self.api.encode_document(logical, kind="metadata", committed_registry=registry)
        logical_bytes = len(json.dumps(logical, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8"))
        actual = self.api.decode_document(encoded, kind="metadata", registry=registry,
            limits=self.api.StorageLimits(max_expanded_bytes=logical_bytes))
        self.assertEqual(actual, logical)
        for limits in (self.api.StorageLimits(max_expanded_bytes=logical_bytes - 1),
                       self.api.StorageLimits(max_manifest_rows=0),
                       self.api.StorageLimits(timeout_seconds=0)):
            with self.subTest(limits=limits), self.assertRaises(self.api.StorageError):
                self.api.decode_document(encoded, kind="metadata", registry=registry, limits=limits)
        for limit in (float("nan"), float("inf"), -1, True):
            with self.subTest(limit=limit), self.assertRaises(self.api.StorageError):
                self.api.decode_document({}, kind="metadata", registry=self.api.Registry(),
                    limits=self.api.StorageLimits(timeout_seconds=limit))
        for raw in ('{"x":1e999}', '[[[[0]]]]'):
            with self.subTest(raw=raw), self.assertRaises(self.api.StorageError):
                self.api.strict_json_loads(raw, limits=self.api.StorageLimits(max_depth=2))

    def test_event_validation_failure_cannot_publish_any_registry(self):
        first = event()
        second = event(2, first["event_hash"])
        encoded_first, registry = self.api.encode_event(first, committed_registry=self.api.Registry())
        encoded_second, _ = self.api.encode_event(second, committed_registry=registry)
        seen = []
        def reject_second(value, previous):
            validate_event(value, previous)
            seen.append(len(previous))
            if previous: raise ValueError("rejected second event")
        with self.assertRaisesRegex(self.api.StorageError, "rejected second event"):
            self.api.decode_events([encoded_first, encoded_second], logical_event_validator=reject_second)
        self.assertEqual(seen, [0, 1])

    def test_encoders_enforce_physical_budget_and_typed_errors(self):
        for kind in ("metadata", "transaction"):
            with self.subTest(kind=kind), self.assertRaises(self.api.StorageError):
                self.api.encode_document({"schema_version": 1}, kind=kind,
                    committed_registry=self.api.Registry(), limits=self.api.StorageLimits(max_raw_bytes=1))
        with self.assertRaises(self.api.StorageError):
            self.api.encode_event(event(), committed_registry=self.api.Registry(),
                                  limits=self.api.StorageLimits(max_raw_bytes=1))
        with self.assertRaises(self.api.StorageError):
            self.api.encode_document({}, kind="unknown", committed_registry=self.api.Registry())

    def test_canonical_order_retains_independent_candidate_identity(self):
        first = snapshot()
        second = copy.deepcopy(first)
        second["head"] = "4" * 40
        second["candidate_id"] = candidate._digest({k: v for k, v in second.items() if k != "candidate_id"})
        value = event(value={"z": first, "a": second})
        reordered = {k: value[k] for k in reversed(value)}
        encoded, _ = self.api.encode_event(value, committed_registry=self.api.Registry())
        encoded_reordered, _ = self.api.encode_event(reordered, committed_registry=self.api.Registry())
        self.assertEqual(encoded, encoded_reordered)
        decoded = self.ledger([encoded]).events[0]
        self.assertEqual(decoded, value)
        self.assertNotEqual(decoded["payload"]["a"]["candidate_id"], decoded["payload"]["z"]["candidate_id"])

    def test_validator_prior_events_are_readonly_and_do_not_grow_after_callback(self):
        first, retained = event(), []
        second = event(2, first["event_hash"])
        def validator(value, prior):
            validate_event(value, prior)
            retained.append(prior)
            if prior:
                with self.assertRaises(TypeError): prior[0]["ticket_id"] = "poisoned"
                with self.assertRaises(TypeError): prior[0]["payload"]["snapshot"]["manifest"][0]["size"] = 2
        ledger = self.api.decode_events([first, second], logical_event_validator=validator)
        self.assertEqual([len(prior) for prior in retained], [0, 1])
        self.assertEqual(ledger.events, [first, second])

    def test_timeout_after_final_snapshot_validation_still_rejects_document(self):
        registry = self.ledger([event()]).registry
        physical = self.api.encode_document({"schema_version": 3, "snapshot": snapshot()},
                                           kind="metadata", committed_registry=registry)
        clock = [0]
        original = self.api._valid_snapshot
        def validation(value):
            original(value)
            clock[0] = 2
        with mock.patch.object(self.api.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(self.api, "_valid_snapshot", side_effect=validation):
            with self.assertRaisesRegex(self.api.StorageError, "timeout_seconds"):
                self.api.decode_document(physical, kind="metadata", registry=registry,
                    limits=self.api.StorageLimits(timeout_seconds=1))


if __name__ == "__main__":
    unittest.main()
