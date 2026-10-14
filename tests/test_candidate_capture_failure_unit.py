"""Portable propagation checks; real tickets and readers, one capture boundary."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

import test_candidate_verification as fixtures

ROOT = Path(__file__).resolve().parents[1]


def load_reader(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("required reader module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortableCaptureFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerificationTests(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def assert_capture_failure(self, required):
        fixture = self.fixture
        fixture.record(request="prior-pass")
        if not required:
            fixture.call("metadata-update", "--dir", fixture.ticket,
                         "--set-json", '{"verification_contract":null}')
        metadata = fixture.meta()
        self.assertEqual(metadata["candidate_tracking"]["mode"], "enabled")
        if required:
            self.assertIs(metadata["verification_contract"]["required"], True)
        else:
            self.assertIsNone(metadata["verification_contract"])
        before = fixture.frozen()
        debt = load_reader("portable_capture_debt", "verification_debt.py")
        lint = load_reader("portable_capture_lint", "lint_workflow_contract.py")
        catalog, error = lint.find_gates_catalog()
        self.assertIsNone(error)
        self.assertIsInstance(catalog, dict)

        def delivery_gate(report):
            gates = [row for row in report["gates"] if row["gate"] == "delivery_evidence"]
            self.assertEqual(len(gates), 1)
            return gates[0]

        # Healthy controls exercise the same readers before injecting capture failure.
        self.assertTrue(fixtures.ctl.verification_applicability(fixture.ticket, metadata)["ok"])
        self.assertFalse(debt.analyze_ticket(metadata, fixture.ticket)["ticket_blocked"])
        healthy = lint.build_report(fixture.ticket, "audit-verified", metadata, catalog, False, True)
        self.assertEqual(delivery_gate(healthy)["status"], "pass")
        self.assertEqual(before, fixture.frozen())

        readers = (("applicability", fixtures.ctl),
                   ("debt", debt.evidence_module().control_module()),
                   ("linter", lint.evidence_module().control_module()))
        for path, api in readers:
            with self.subTest(reader=path):
                failure = api.ControlError("fixture capture unavailable", gate_id="candidate_capture")
                with mock.patch.object(api, "candidate_snapshot", autospec=True,
                                       side_effect=failure) as capture:
                    if path == "applicability":
                        result = fixtures.ctl.verification_applicability(fixture.ticket, metadata)
                        self.assertFalse(result["ok"])
                        self.assertEqual(result["state"], "blocked")
                        self.assertEqual(result["reasons"], ["candidate_capture_unavailable"])
                        self.assertEqual(result["eligible_runs"], [])
                    elif path == "debt":
                        result = debt.analyze_ticket(metadata, fixture.ticket)
                        self.assertTrue(result["ticket_blocked"])
                        self.assertEqual(result["candidate_status"], "blocked")
                        self.assertEqual(result["blocking_reason"], "candidate_capture_unavailable")
                        self.assertEqual(result["eligibility_reasons"], ["candidate_capture_unavailable"])
                    else:
                        result = lint.build_report(fixture.ticket, "audit-verified", metadata,
                                                   catalog, False, True)
                        gate = delivery_gate(result)
                        self.assertGreater(result["blocked"], 0)
                        self.assertFalse(lint.report_is_pass(result, False, True))
                        self.assertEqual(gate["status"], "blocked")
                        self.assertEqual(gate["issues"], ["delivery_evidence candidate_capture_unavailable"])
                    capture.assert_called_once_with(fixture.ticket, metadata)
                self.assertEqual(before, fixture.frozen())
        self.assertTrue(fixtures.ctl.verification_applicability(fixture.ticket, metadata)["ok"])
        self.assertEqual(before, fixture.frozen())

    def test_required_capture_failure_blocks_all_readers(self):
        self.assert_capture_failure(True)

    def test_null_contract_capture_failure_blocks_all_readers(self):
        self.assert_capture_failure(False)


if __name__ == "__main__":
    unittest.main()
