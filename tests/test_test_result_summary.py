"""Test counts are derived from bounded report bytes, never runner exit alone."""
import copy
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=None)
def load_tool(name):
    path = ROOT / "tools" / (name + ".py")
    assert path.exists(), "bounded test report parser is missing"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icode-test-report-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def report(self, xml, **kwargs):
        path = self.root / "report.xml"
        path.write_bytes(xml.encode("utf-8") if isinstance(xml, str) else xml)
        return load_tool("test_result_summary").summarize_report(path, self.root, **kwargs)

    def assert_rejected(self, xml, message, **kwargs):
        api = load_tool("test_result_summary")
        with self.assertRaisesRegex(api.ReportError, message):
            self.report(xml, **kwargs)

    def test_zero_cases_is_inconclusive(self):
        result = self.report('<testsuite tests="0" failures="0"/>')
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["total"], 0)

    def test_all_skipped_is_inconclusive(self):
        result = self.report('<testsuite tests="2" skipped="2"><testcase><skipped/></testcase>'
                             '<testcase><skipped/></testcase></testsuite>')
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual((result["total"], result["passed"], result["skipped"]), (2, 0, 2))

    def test_unexecuted_status_markers_cannot_be_passing_cases(self):
        for attrs in ('status="notrun"', 'status="notrun" result="suppressed"', 'result="skipped"'):
            with self.subTest(attrs=attrs):
                result = self.report('<testsuite><testcase ' + attrs + '/></testsuite>')
                self.assertEqual((result["passed"], result["skipped"], result["outcome"]), (0, 1, "inconclusive"))

    def test_disabled_and_runtime_skipped_counters_stay_distinct(self):
        result = self.report('<testsuites tests="3" disabled="1"><testsuite tests="3" disabled="1" skipped="1">'
                             '<testcase status="run" result="completed"/>'
                             '<testcase status="notrun" result="suppressed"/>'
                             '<testcase status="run" result="skipped"><skipped/></testcase>'
                             '</testsuite></testsuites>')
        self.assertEqual((result["total"], result["passed"], result["skipped"]), (3, 1, 2))
        ctest = self.report('<testsuite tests="1" disabled="0" skipped="1">'
                            '<testcase status="notrun"><skipped/></testcase></testsuite>')
        self.assertEqual(ctest["outcome"], "inconclusive")

    def test_unknown_and_conflicting_status_markers_are_rejected(self):
        for attrs in ('status="unknown"', 'result="unknown"', 'status="notrun" result="completed"'):
            with self.subTest(attrs=attrs):
                self.assert_rejected('<testsuite><testcase ' + attrs + '/></testsuite>', "status|result")

    def test_disabled_counter_conflicts_are_rejected(self):
        self.assert_rejected('<testsuite tests="1" disabled="0">'
                             '<testcase status="notrun" result="suppressed"/></testsuite>', "count mismatch")

    def test_disabled_metadata_does_not_hide_forced_execution(self):
        result = self.report('<testsuite tests="1" disabled="1" skipped="0">'
                             '<testcase status="run" result="completed"/></testsuite>')
        self.assertEqual((result["passed"], result["skipped"], result["outcome"]), (1, 0, "pass"))
        self.assert_rejected('<testsuite tests="0" disabled="1"/>', "count mismatch")

    def test_actual_case_counts_and_original_report_identity(self):
        xml = '<testsuite><testcase name="ok"/><testcase><failure/></testcase>' \
              '<testcase><error/></testcase><testcase><skipped/></testcase></testsuite>'
        result = self.report(xml)
        self.assertEqual({key: result[key] for key in ("total", "passed", "failed", "errors", "skipped")},
                         {"total": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1})
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["report"], {"path": str((self.root / "report.xml").resolve()),
                         "sha256": hashlib.sha256(xml.encode()).hexdigest(), "bytes": len(xml.encode())})

    def test_nested_suite_aggregates_are_not_double_counted(self):
        result = self.report('<testsuites tests="3" failures="1" skipped="1"><testsuite tests="3" failures="1" skipped="1">'
                             '<testsuite tests="2" failures="1"><testcase/><testcase><failure/></testcase></testsuite>'
                             '<testcase><skipped/></testcase></testsuite></testsuites>')
        self.assertEqual((result["total"], result["passed"], result["failed"], result["skipped"]), (3, 1, 1, 1))

    def test_namespace_and_utf16_reports_are_counted(self):
        xml = '<?xml version="1.0" encoding="utf-16"?><testsuites xmlns="urn:junit"><testsuite tests="1">' \
              '<testcase name="ok"/></testsuite></testsuites>'
        self.assertEqual(self.report(xml.encode("utf-16"))["outcome"], "pass")

    def test_partial_skips_with_passing_cases_is_report_scoped_pass(self):
        result = self.report('<testsuite><testcase/><testcase><skipped/></testcase></testsuite>')
        self.assertEqual((result["outcome"], result["passed"], result["skipped"]), ("pass", 1, 1))

    def test_missing_case_details_cannot_authenticate_claimed_pass(self):
        self.assert_rejected('<testsuite tests="10" failures="0"/>', "count mismatch")

    def test_suite_and_root_counter_conflicts_are_rejected(self):
        for xml in ('<testsuite tests="0"><testcase/></testsuite>',
                    '<testsuite failures="0"><testcase><failure/></testcase></testsuite>',
                    '<testsuites tests="2"><testsuite><testcase/></testsuite></testsuites>',
                    '<testsuite skipped="1"><testcase/></testsuite>'):
            with self.subTest(xml=xml):
                self.assert_rejected(xml, "count mismatch")

    def test_invalid_declared_counts_are_rejected(self):
        for value in ("-1", "1.0", "NaN", "true", "", "9" * 100):
            with self.subTest(value=value):
                self.assert_rejected('<testsuite tests="' + value + '"/>', "count")

    def test_malformed_and_unsupported_roots_are_rejected(self):
        self.assert_rejected('<testsuite><testcase></testsuite>', "malformed")
        self.assert_rejected('<html/>', "root")

    def test_unsupported_xml_encodings_are_controlled_errors(self):
        for encoding in ('unrecognized-icode-encoding', 'utf-32', 'shift_jis', 'utf-7'):
            with self.subTest(encoding=encoding):
                self.assert_rejected('<?xml version="1.0" encoding="' + encoding + '"?><testsuite/>', "encoding|malformed")

    def test_supported_single_byte_encoding_remains_parseable(self):
        xml = '<?xml version="1.0" encoding="iso-8859-1"?><testsuite><testcase name="café"/></testsuite>'
        self.assertEqual(self.report(xml.encode('iso-8859-1'))["outcome"], "pass")

    def test_conflicting_case_outcomes_cannot_hide_failure(self):
        self.assert_rejected('<testsuite><testcase><failure/><skipped/></testcase></testsuite>', "conflicting")

    def test_misplaced_cases_and_failure_tags_are_rejected(self):
        for xml in ('<testsuite><properties><testcase/></properties></testsuite>',
                    '<testsuite><failure/></testsuite>',
                    '<testsuite><testcase><testsuite/></testcase></testsuite>'):
            with self.subTest(xml=xml):
                self.assert_rejected(xml, "structure")

    def test_dtd_and_entity_declarations_are_rejected_in_all_encodings(self):
        xml = '<?xml version="1.0"?><!DOCTYPE testsuite [<!ENTITY x "expanded">]>' \
              '<testsuite><testcase name="&x;"/></testsuite>'
        for data in (xml.encode(), xml.replace('version="1.0"', 'version="1.0" encoding="utf-16"').encode("utf-16"),
                     b'<!DOCTYPE testsuite SYSTEM "https://invalid.example/report.dtd"><testsuite/>'):
            with self.subTest(data=data[:20]):
                self.assert_rejected(data, "DTD|entity")

    def test_file_element_depth_and_case_budgets_are_enforced(self):
        self.assert_rejected('<testsuite><testcase/></testsuite>', "byte", limits={"max_bytes": 10})
        self.assert_rejected('<testsuite><testcase/></testsuite>', "element", limits={"max_elements": 1})
        self.assert_rejected('<testsuites><testsuite><testcase/></testsuite></testsuites>', "depth", limits={"max_depth": 2})
        self.assert_rejected('<testsuite><testcase/><testcase/></testsuite>', "test count", limits={"max_tests": 1})
        self.assert_rejected('<testsuite tests="2"/>', "test count", limits={"max_tests": 1})

    def test_timeout_and_invalid_budgets_are_controlled_errors(self):
        api = load_tool("test_result_summary")
        # Exercise elapsed time explicitly, independent of the host clock resolution.
        with patch.object(api.time, "monotonic", side_effect=(0.0, 2.0)):
            self.assert_rejected('<testsuite/>', "timeout", limits={"timeout_seconds": 1.0})
        for limits in ({"max_bytes": 0}, {"max_depth": True}, {"timeout_seconds": float("nan")}, {"other": 1}):
            with self.subTest(limits=limits):
                self.assert_rejected('<testsuite/>', "limit", limits=limits)

    def test_directory_missing_and_private_paths_are_rejected(self):
        api = load_tool("test_result_summary")
        for path in (self.root, self.root / "missing.xml"):
            with self.subTest(path=path), self.assertRaises(api.ReportError):
                api.summarize_report(path, self.root)
        private = self.root / ".git"
        private.mkdir()
        (private / "report.xml").write_text('<testsuite/>')
        with self.assertRaisesRegex(api.ReportError, "private"):
            api.summarize_report(private / "report.xml", self.root)

    def test_symlink_reports_are_rejected_when_supported(self):
        path = self.root / "real.xml"
        path.write_text('<testsuite/>')
        link = self.root / "link.xml"
        try:
            link.symlink_to(path)
        except OSError as exc:
            self.skipTest("host cannot create symlinks: " + str(exc))
        api = load_tool("test_result_summary")
        with self.assertRaisesRegex(api.ReportError, "symlink"):
            api.summarize_report(link, self.root)


class VerificationReportTests(unittest.TestCase):
    def setUp(self):
        self.ctl = load_tool("icode_control")
        self.temp = tempfile.TemporaryDirectory(prefix="icode-report-control-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Report Test")
        (self.repo / ".gitignore").write_text('.icode_output/\nreports/\n')
        (self.repo / "source.py").write_text('value = 1\n')
        (self.repo / "README.md").write_text('Initial documentation\n')
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.ticket = self.repo / ".icode_output/.icode_output_1"
        contract = {"required": True, "required_layers": ["host"], "required_consumers": ["suite"], "required_scenarios": ["unit"]}
        self.call("create", "--dir", self.ticket, "--ticket-id", "report-1", "--birth", "plan",
                  "--requirement", "report evidence", "--metadata-json", json.dumps({"verification_contract": contract}))
        (self.repo / "reports").mkdir()
        self.path = self.repo / "reports/report.xml"
        self.path.write_text('<testsuite tests="1"><testcase/></testsuite>')

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def call(self, *args, ok=True):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"), *map(str, args)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode == 0, ok, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}

    def frozen(self):
        return tuple((self.ticket / name).read_bytes() for name in (self.ctl.METADATA_NAME, self.ctl.EVENTS_NAME))

    def record(self, outcome="pass", report=True, request=None, ok=True, reuse=None, direct=None, evidence=None):
        meta = self.ctl.load_metadata(self.ticket)
        args = ["record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", outcome,
                "--candidate-id", self.ctl.candidate_snapshot(self.ticket, meta)["candidate_id"],
                "--environment", "host", "--layer", "host", "--consumer", "suite", "--scenario", "unit",
                "--baseline", "fixture source", "--evidence", evidence if evidence is not None else "reports/report.xml"]
        if report:
            args.extend(["--test-report", "reports/report.xml"])
        if request:
            args.extend(["--request-id", request])
        if reuse:
            args.extend(["--reuse-json", json.dumps(reuse)])
        if direct:
            args.extend(["--direct-test-json", json.dumps(direct)])
        return self.call(*args, ok=ok)

    def test_writer_records_machine_derived_summary_in_event_and_metadata(self):
        run = self.record(request="pass")["run"]
        summary = run["test_summary"]
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["report"]["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        meta = self.ctl.load_metadata(self.ticket)
        events, problems = self.ctl.verify_event_chain(self.ticket, meta)
        self.assertFalse(problems)
        self.assertEqual(events[-1]["payload"]["test_summary"], summary)
        self.assertTrue(self.ctl.verification_applicability(self.ticket)["ok"])

    def test_zero_all_skipped_and_failed_reports_cannot_be_pass(self):
        for xml in ('<testsuite tests="0"/>', '<testsuite><testcase><skipped/></testcase></testsuite>',
                    '<testsuite><testcase><failure/></testcase></testsuite>'):
            with self.subTest(xml=xml):
                self.path.write_text(xml)
                before = self.frozen()
                self.record(ok=False)
                self.assertEqual(before, self.frozen())

    def test_unrecognized_report_encoding_is_controlled_atomic_cli_failure(self):
        self.path.write_text('<?xml version="1.0" encoding="unrecognized-icode-encoding"?><testsuite/>')
        before = self.frozen()
        error = self.record(ok=False)
        self.assertEqual(error.get("gate_id"), "verification_evidence")
        self.assertEqual(before, self.frozen())

    def test_reported_failure_cannot_be_downgraded_to_inconclusive(self):
        self.path.write_text('<testsuite><testcase><error/></testcase></testsuite>')
        self.record("inconclusive", ok=False)
        run = self.record("fail")["run"]
        self.assertEqual(run["test_summary"]["outcome"], "fail")

    def test_successful_subset_report_does_not_override_process_failure(self):
        direct = {"target_built": True, "binary_executed": True, "binary_exit_code": 2,
                  "test_runner_discovered": True, "ci_registered": False}
        self.record(direct=direct, ok=False)
        self.assertEqual(self.record("fail", direct=direct)["run"]["outcome"], "fail")

    def test_nonzero_binary_exit_cannot_be_downgraded_to_inconclusive(self):
        direct = {"target_built": True, "binary_executed": True, "binary_exit_code": 2,
                  "test_runner_discovered": True, "ci_registered": False}
        before = self.frozen()
        for report in (True, False):
            with self.subTest(report=report):
                self.record("inconclusive", report=report, direct=direct, ok=False)
        self.assertEqual(before, self.frozen())

    def test_old_direct_inconclusive_event_remains_readable_and_unverified(self):
        # Synthetic history in the shape accepted before strict writer rules;
        # the controlled primitive retains its real metadata/event hash chain.
        before_meta = self.ctl.load_metadata(self.ticket)
        candidate = self.ctl.candidate_snapshot(self.ticket, before_meta)
        legacy = {"run_id": "legacy-direct-inconclusive", "at": self.ctl.now_iso(), "kind": "device_test",
                  "outcome": "inconclusive", "build_source": "unknown", "candidate_id": candidate["candidate_id"],
                  "candidate": candidate, "environment": "host", "layer": "host", "consumer": "suite", "scenario": "unit",
                  "baseline": "fixture source", "evidence": "historic direct process output",
                  "direct_test": {"target_built": True, "binary_executed": True, "binary_exit_code": 2,
                                  "test_runner_discovered": True, "ci_registered": False}}
        after_meta = copy.deepcopy(before_meta)
        after_meta["verification_runs"] = [legacy]
        with self.ctl.DirLock(self.ticket):
            self.ctl.commit_metadata_and_event(self.ticket, before_meta, after_meta, "verification_recorded", legacy,
                                               request_id="old-direct-receipt")
        before = self.frozen()
        _events, problems = self.ctl.verify_event_chain(self.ticket, self.ctl.load_metadata(self.ticket))
        self.assertFalse(problems)
        result = self.ctl.verification_applicability(self.ticket)
        self.assertFalse(result["ok"])
        self.assertIn("latest_outcome_inconclusive", result["reasons"])
        self.assertEqual(before, self.frozen())
        replay = self.record("inconclusive", report=False, request="old-direct-receipt", direct=legacy["direct_test"],
                             evidence=legacy["evidence"])
        self.assertTrue(replay["already_applied"])
        self.assertEqual(before, self.frozen())
        self.record("fail", report=False, direct=legacy["direct_test"])
        self.assertEqual(self.ctl.load_metadata(self.ticket)["verification_runs"][0], legacy)

    def test_zero_binary_exit_can_have_overall_inconclusive_result(self):
        direct = {"target_built": True, "binary_executed": True, "binary_exit_code": 0,
                  "test_runner_discovered": True, "ci_registered": False}
        self.assertEqual(self.record("inconclusive", direct=direct)["run"]["outcome"], "inconclusive")

    def test_later_zero_case_attempt_keeps_verification_pending(self):
        self.record()
        self.path.write_text('<testsuite/>')
        self.record("inconclusive")
        result = self.ctl.verification_applicability(self.ticket)
        self.assertFalse(result["ok"])
        self.assertIn("latest_outcome_inconclusive", result["reasons"])

    def test_runner_only_pass_cannot_replace_report_backed_failure(self):
        self.path.write_text('<testsuite><testcase><failure/></testcase></testsuite>')
        self.record("fail")
        self.record(report=False)
        result = self.ctl.verification_applicability(self.ticket)
        self.assertFalse(result["ok"])
        self.assertIn("latest_outcome_fail", result["reasons"])
        self.assertIn("test_report_required", {item["reason"] for item in result["rejected_runs"]})

    def test_report_requirement_is_filtered_by_current_candidate_first(self):
        self.path.write_text('<testsuite><testcase><failure/></testcase></testsuite>')
        self.record("fail")
        (self.repo / "source.py").write_text('value = 2\n')
        self.record(report=False)
        result = self.ctl.verification_applicability(self.ticket)
        self.assertTrue(result["ok"])
        self.assertIn("candidate_drift", {item["reason"] for item in result["rejected_runs"]})

    def test_negative_attempt_without_report_is_preserved(self):
        self.record()
        self.record("fail", report=False)
        result = self.ctl.verification_applicability(self.ticket)
        self.assertFalse(result["ok"])
        self.assertIn("latest_outcome_fail", result["reasons"])

    def test_reuse_cannot_resurrect_unobserved_counts_after_report_observation(self):
        origin = self.record(report=False)["run"]
        old = self.ctl.load_metadata(self.ticket)["verification_runs"][0]["candidate"]
        self.record()
        (self.repo / "README.md").write_text('Clarified documentation\n')
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only documentation changed",
                 "scope_paths": ["source.py"], "scope_hash": self.ctl.verification_evidence_module().scope_hash(old, ["source.py"]),
                 "comparison_ref": "README.md"}
        before = self.frozen()
        self.record(report=False, reuse=reuse, ok=False)
        self.assertEqual(before, self.frozen())

    def test_historical_identity_and_idempotency_survive_report_removal(self):
        run = self.record(request="receipt")["run"]
        self.path.unlink()
        before = self.frozen()
        self.assertTrue(self.record(request="receipt")["already_applied"])
        self.assertTrue(self.ctl.verification_applicability(self.ticket)["ok"])
        self.assertEqual(self.ctl.load_metadata(self.ticket)["verification_runs"][-1]["test_summary"], run["test_summary"])
        self.assertEqual(before, self.frozen())

    def test_report_parameter_cannot_be_omitted_from_exact_replay(self):
        self.record(request="report-receipt")
        before = self.frozen()
        self.record(report=False, request="report-receipt", ok=False)
        self.assertEqual(before, self.frozen())

    def test_legacy_request_without_report_still_replays_exactly(self):
        self.record(report=False, request="legacy-receipt")
        before = self.frozen()
        self.assertTrue(self.record(report=False, request="legacy-receipt")["already_applied"])
        self.assertEqual(before, self.frozen())

    def test_new_record_reads_new_report_bytes_instead_of_cached_counts(self):
        first = self.record()["run"]["test_summary"]
        self.path.write_text('<testsuite><testcase/><testcase/></testsuite>')
        second = self.record()["run"]["test_summary"]
        self.assertNotEqual(first["report"]["sha256"], second["report"]["sha256"])
        self.assertEqual(second["total"], 2)

    def test_report_identity_cannot_be_dropped_or_changed_during_reuse(self):
        origin = self.record()["run"]
        old = self.ctl.load_metadata(self.ticket)["verification_runs"][0]["candidate"]
        (self.repo / "README.md").write_text("Clarified documentation\n")
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only documentation changed",
                 "scope_paths": ["source.py"], "scope_hash": self.ctl.verification_evidence_module().scope_hash(old, ["source.py"]),
                 "comparison_ref": "README.md"}
        self.record(report=False, reuse=reuse, ok=False)
        self.record(reuse=reuse)
        self.path.write_text('<testsuite><testcase/><testcase/></testsuite>')
        self.record(reuse=reuse, ok=False)

    def test_self_reported_summary_json_is_not_an_input_port(self):
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                  "--evidence", "fixture", "--test-summary-json", '{"total":99}', ok=False)

    def test_legacy_no_summary_still_uses_existing_contract_boundary(self):
        run = self.record(report=False)["run"]
        self.assertNotIn("test_summary", run)
        self.assertTrue(self.ctl.verification_applicability(self.ticket)["ok"])

    def test_stored_summary_shape_and_outcome_are_checked_offline(self):
        run = self.record()["run"]
        api = self.ctl.verification_evidence_module()
        for field, value in (("total", 0), ("passed", True), ("outcome", "inconclusive")):
            bad = copy.deepcopy(run)
            bad["test_summary"][field] = value
            with self.subTest(field=field), self.assertRaises(api.EvidenceError):
                api.validate_test_summary(bad)
        bad = copy.deepcopy(run)
        bad["test_summary"].update(total=0, passed=0, outcome="inconclusive")
        ctx = {"enabled": False, "current": None, "events": [], "reasons": [], "candidate_api": None}
        selected = api.eligible_latest_runs({"verification_runs": [bad]}, ctx)
        self.assertEqual(selected["latest"], {})
        self.assertTrue(selected["rejected"])
        self.assertTrue(api.event_issues([{"event_type": "verification_recorded", "payload": bad}], {}, self.ctl.candidate_module()))

    def test_schema_accepts_machine_summary_and_rejects_invalid_types(self):
        self.record()
        meta = self.ctl.load_metadata(self.ticket)
        schema = json.loads((ROOT / "schemas/ticket-metadata.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(self.ctl.check_schema(meta, schema), [])
        for key, value in (("total", -1), ("passed", True), ("version", 2)):
            invalid = copy.deepcopy(meta)
            invalid["verification_runs"][-1]["test_summary"][key] = value
            with self.subTest(key=key):
                self.assertTrue(self.ctl.check_schema(invalid, schema))


if __name__ == "__main__":
    unittest.main()
