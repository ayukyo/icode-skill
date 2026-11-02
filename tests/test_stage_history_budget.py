"""Pure filesystem fixtures for stage-history discovery budgets.

No Git repository, device, or real control ledger is touched.
"""
import importlib.util
import itertools
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
GENESIS = "0" * 64


class HistoryDiscoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="stage-budget-"))
        self.project = self.root / "project"
        self.ticket = self.project / ".icode_output" / ".icode_output_1"
        self.ticket.mkdir(parents=True)
        spec = importlib.util.spec_from_file_location(
            "stage_budget_under_test", ROOT / "tools/stage_acceptance.py")
        self.stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.stage)
        self.api = self.stage.ctl()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_ticket(self, payload_bytes, *, ticket=None):
        ticket = ticket or self.ticket
        ticket.mkdir(parents=True, exist_ok=True)
        ticket_id = "budget-fixture-" + ticket.name
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "request_id": "fixture-birth",
            "timestamp": "2026-01-01T00:00:00",
            "actor": "icode",
            "event_type": "ticket_created",
            "payload": {"birth_kind": "plan", "padding": "x" * payload_bytes},
            "previous_event_hash": GENESIS,
        }
        event["event_hash"] = self.api.canonical_event_hash(event)
        meta = {
            "schema_version": 3,
            "ticket_id": ticket_id,
            "status": "completed",
            "completed_steps": [],
            "created_at": "2026-01-01T00:00:00",
            "project_path": str(self.project),
            "requirement": "fixture",
        }
        (ticket / self.api.METADATA_NAME).write_text(
            json.dumps(meta), encoding="utf-8")
        (ticket / self.api.EVENTS_NAME).write_text(
            json.dumps(event) + "\n", encoding="utf-8")

    def test_default_budget_admits_metadata_beside_valid_large_ledger(self):
        self.write_ticket(5500)
        path = self.ticket / self.api.METADATA_NAME
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["ordinary_notes"] = "x" * 3500
        path.write_text(json.dumps(meta), encoding="utf-8")
        # Keep the default cumulative/per-file relationship, scaled down so
        # this authenticated coexistence case needs no huge allocation.
        per_file = 8192
        cumulative = per_file * self.stage.DISCOVERY_LIMITS["max_total_bytes"] // self.stage.DISCOVERY_LIMITS["max_file_bytes"]
        with mock.patch.dict(self.stage.DISCOVERY_LIMITS,
                             {"max_file_bytes": per_file, "max_total_bytes": cumulative}):
            self.assertIsNone(self.stage.discover_stage_history(self.project))

    def test_default_single_file_budget_admits_grown_authenticated_history(self):
        # Scale both default byte caps equally while retaining an actual,
        # authenticated metadata/ledger pair and all ordinary read checks.
        self.write_ticket(1100)
        scale = 256 * 1024
        bounds = {key: self.stage.DISCOVERY_LIMITS[key] // scale
                  for key in ("max_file_bytes", "max_total_bytes")}
        with mock.patch.dict(self.stage.DISCOVERY_LIMITS, bounds):
            try:
                result = self.stage.discover_stage_history(self.project)
            except self.api.ControlError as error:
                self.fail("Grown authenticated history inside the supported budget was rejected: " + str(error))
        self.assertIsNone(result)

    def test_single_file_above_old_default_is_accepted(self):
        # A legitimate ledger can outgrow the previous 64 MiB limit while
        # remaining within the bounded discovery budget.
        self.write_ticket(64 * 1024 * 1024 + 1024)
        self.assertIsNone(self.stage.discover_stage_history(self.project))

    def test_default_budget_admits_candidate_history_beyond_previous_file_cap(self):
        self.write_ticket(3000)
        scale = 256 * 1024
        bounds = {key: self.stage.DISCOVERY_LIMITS[key] // scale
                  for key in ("max_file_bytes", "max_total_bytes")}
        with mock.patch.dict(self.stage.DISCOVERY_LIMITS, bounds):
            try:
                result = self.stage.discover_stage_history(self.project)
            except self.api.ControlError as error:
                self.fail("Authenticated history inside supported file capacity rejected: " + str(error))
        self.assertIsNone(result)

    def test_default_budget_admits_multiple_grown_candidate_histories(self):
        for index in range(3):
            self.write_ticket(1400, ticket=self.ticket.with_name(".icode_output_" + str(index + 1)))
        scale = 256 * 1024
        bounds = {key: self.stage.DISCOVERY_LIMITS[key] // scale
                  for key in ("max_file_bytes", "max_total_bytes")}
        with mock.patch.dict(self.stage.DISCOVERY_LIMITS, bounds):
            try:
                result = self.stage.discover_stage_history(self.project)
            except self.api.ControlError as error:
                self.fail("Authenticated histories inside supported total capacity rejected: " + str(error))
        self.assertIsNone(result)

    def test_cumulative_history_above_old_default_is_accepted(self):
        self.write_ticket(33 * 1024 * 1024)
        self.write_ticket(33 * 1024 * 1024,
                          ticket=self.ticket.with_name(".icode_output_2"))
        self.assertIsNone(self.stage.discover_stage_history(self.project))

    def test_larger_budget_does_not_accept_damaged_ledger(self):
        self.write_ticket(256)
        path = self.ticket / self.api.EVENTS_NAME
        path.write_bytes(path.read_bytes().replace(b'"padding": "x',
                                                   b'"padding": "y', 1))
        with self.assertRaisesRegex(self.api.ControlError, "damaged"):
            self.stage.discover_stage_history(self.project)

    def test_single_file_cannot_exceed_configured_cap(self):
        self.write_ticket(256)
        with mock.patch.dict(self.stage.DISCOVERY_LIMITS,
                             {"max_file_bytes": 100, "max_total_bytes": 200}):
            with self.assertRaisesRegex(self.api.ControlError, "budget"):
                self.stage.discover_stage_history(self.project)

    def test_cumulative_budget_still_rejects_multiple_files(self):
        self.write_ticket(150)
        with mock.patch.dict(self.stage.DISCOVERY_LIMITS,
                             {"max_file_bytes": 500, "max_total_bytes": 300}):
            with self.assertRaisesRegex(self.api.ControlError, "budget"):
                self.stage.discover_stage_history(self.project)

    def test_explicit_limit_still_tightens(self):
        self.write_ticket(100)
        with self.assertRaisesRegex(self.api.ControlError, "budget"):
            self.stage.discover_stage_history(
                self.project, limits={"max_file_bytes": 50})

    def test_default_time_budget_admits_complete_scan_beyond_five_seconds(self):
        self.write_ticket(100)
        clock = itertools.chain([0], itertools.repeat(6))
        with mock.patch.object(self.stage.time, "monotonic", side_effect=clock):
            try:
                result = self.stage.discover_stage_history(self.project)
            except self.api.ControlError as error:
                self.fail("A complete scan inside the revised budget was rejected: " + str(error))
        self.assertIsNone(result)

    def test_default_time_budget_still_has_finite_upper_bound(self):
        self.write_ticket(100)
        clock = itertools.chain([0], itertools.repeat(self.stage.DISCOVERY_LIMITS["timeout_seconds"] + 1))
        with mock.patch.object(self.stage.time, "monotonic", side_effect=clock):
            with self.assertRaisesRegex(self.api.ControlError, "time budget") as error:
                self.stage.discover_stage_history(self.project)
        self.assertEqual(error.exception.extra["discovery"]["limit"], "timeout_seconds")

    def test_default_time_budget_admits_large_history_authentication(self):
        self.write_ticket(100)
        # A complete authenticated scan can exceed 30 seconds on Windows
        # while remaining inside the declared 1GiB/file, 4GiB total budget.
        clock = itertools.chain([0], itertools.repeat(61))
        with mock.patch.object(self.stage.time, "monotonic", side_effect=clock):
            self.assertIsNone(self.stage.discover_stage_history(self.project))

    def test_explicit_five_second_limit_still_rejects_slow_scan(self):
        self.write_ticket(100)
        clock = itertools.chain([0], itertools.repeat(6))
        with mock.patch.object(self.stage.time, "monotonic", side_effect=clock):
            with self.assertRaisesRegex(self.api.ControlError, "time budget"):
                self.stage.discover_stage_history(self.project, limits={"timeout_seconds": 5})


if __name__ == "__main__":
    unittest.main(verbosity=2)
