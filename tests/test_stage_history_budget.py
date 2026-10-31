"""Pure filesystem fixtures for stage-history discovery budgets.

No Git repository, device, or real control ledger is touched.
"""
import importlib.util
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

    def test_single_file_above_old_default_is_accepted(self):
        # A legitimate ledger can outgrow the previous 64 MiB limit while
        # remaining within the bounded discovery budget.
        self.write_ticket(64 * 1024 * 1024 + 1024)
        self.assertIsNone(self.stage.discover_stage_history(self.project))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
