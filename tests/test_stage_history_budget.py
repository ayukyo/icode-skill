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

    def write_ticket(self, payload_bytes):
        ticket_id = "budget-fixture"
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
        (self.ticket / self.api.METADATA_NAME).write_text(
            json.dumps(meta), encoding="utf-8")
        (self.ticket / self.api.EVENTS_NAME).write_text(
            json.dumps(event) + "\n", encoding="utf-8")

    def test_default_per_file_budget_uses_total_budget(self):
        self.assertEqual(self.stage.DISCOVERY_LIMITS["max_file_bytes"],
                         self.stage.DISCOVERY_LIMITS["max_total_bytes"])

    def test_single_file_above_old_default_is_accepted(self):
        # The previous default was 16 MiB; a legal single control file may use
        # the existing 64 MiB cumulative budget.
        self.write_ticket(16 * 1024 * 1024 + 1024)
        self.assertIsNone(self.stage.discover_stage_history(self.project))

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
