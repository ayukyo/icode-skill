"""Private generic scanner regressions; no native records or device operations."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "lint_workflow_contract.py"
SPEC = importlib.util.spec_from_file_location("permission_note_lint", PATH)
lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint)

def scan(text, parent="$.verification_runs"):
    """Exercise the trusted direct-run note context and its conservative path guard."""
    return lint.scan_sensitive({"note": text}, parent, direct_verification_run=True)

class PermissionAuthorizationTests(unittest.TestCase):
    def test_generic_negative_enumeration(self):
        self.assertEqual(scan("No new builds, service changes, visual review or deployment authorization."), [])

    def test_minimal_negative_permission(self):
        for text in ("No authorization.", "No service authorization.", "No deployment authorization"):
            with self.subTest(text=text):
                self.assertEqual(scan(text), [])

    def test_case_and_indexed_run(self):
        self.assertEqual(scan("NO SERVICE AUTHORIZATION.", "$.verification_runs[12]"), [])

    def test_prior_plain_sentence(self):
        self.assertEqual(scan("Checks completed. No service authorization."), [])

    def test_other_seven_categories_keep_old_rule(self):
        for word in lint.SENSITIVE_KEYWORDS:
            if word != "authorization":
                with self.subTest(word=word):
                    self.assertTrue(scan("No " + word + "."))

    def test_sensitive_keyword_elsewhere_is_not_hidden(self):
        for text in ("No password. No authorization.", "No authorization. No cookie.",
                     "Bearer sample. No service authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_sensitive_keys_remain_strict(self):
        self.assertTrue(lint.scan_sensitive({"authorization": "placeholder"}))

    def test_other_fields_remain_strict(self):
        self.assertTrue(lint.scan_sensitive({"evidence": "No service authorization."},
                                            "$.verification_runs"))

    def test_default_path_remains_strict(self):
        self.assertTrue(lint.scan_sensitive({"note": "No authorization."}))

    def test_default_scanner_matching_path_remains_strict(self):
        # Path text is diagnostic context, never a qualification token.
        for value, path in (({"note": "No authorization."}, "$.verification_runs"),
                            ({"note": "No authorization."}, "$.verification_runs[0]"),
                            ("No authorization.", "$.verification_runs.note"),
                            ({"verification_runs": {"note": "No authorization."}}, "$"),
                            ({"verification_runs.note": "No authorization."}, "$")):
            with self.subTest(value=value, path=path):
                self.assertTrue(lint.scan_sensitive(value, path))

    def test_direct_run_qualification_requires_literal_true(self):
        for qualification in (False, None, 0, 1, "true", [], [True], {"enabled": True}):
            with self.subTest(qualification=qualification):
                self.assertTrue(lint.scan_sensitive(
                    {"note": "No authorization."}, "$.verification_runs",
                    direct_verification_run=qualification))
        self.assertEqual(lint.scan_sensitive(
            {"note": "No authorization."}, "$.verification_runs",
            direct_verification_run=True), [])

    def test_direct_run_qualification_does_not_recurse(self):
        values = ({"details": {"note": "No authorization."}},
                  {"verification_runs": {"note": "No authorization."}},
                  {"verification_runs.note": "No authorization."},
                  {"note.child": "No authorization."},
                  {"Note": "No authorization."},
                  {"note": {"note": "No authorization."}},
                  {"note": ["No authorization."]},
                  {"evidence": "No authorization."},
                  [{"note": "No authorization."}], "No authorization.")
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(lint.scan_sensitive(
                    value, "$.verification_runs", direct_verification_run=True))

    def test_nested_notes_and_contracts_remain_strict(self):
        for parent in ("$.impact_contract", "$.verification_runs.details",
                       "$.verification_runs.artifact_identity", "$.verification_runs[0].details"):
            with self.subTest(parent=parent):
                self.assertTrue(scan("No authorization.", parent))

    def test_malformed_index_paths_remain_strict(self):
        for parent in ("$.verification_runs[-1]", "$.verification_runs[x]",
                       "$.verification_runs[1.0]", "$.verification_runs[1][2]"):
            with self.subTest(parent=parent):
                self.assertTrue(scan("No authorization.", parent))

    def test_embedded_or_suffixed_term_remains_strict(self):
        for text in ("No preauthorization.", "No authorization_token.",
                     "No authorization.invalid.", "No authorization-token.", "No authorization2."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_authorization_must_be_unique(self):
        for text in ("authorization. No authorization.",
                     "No authorization; No service authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_authorization_must_end_final_clause(self):
        for text in ("No authorization. Checks completed.", "No authorization required.",
                     "No authorization, checks complete."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_no_must_be_standalone_clause_leader(self):
        for text in ("Noerrors authorization.", "Checks no authorization.",
                     "Pending authorization.", "Without authorization.",
                     "No and authorization.", "No or authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_positive_or_predicate_phrases_remain_strict(self):
        for text in ("No errors; deployment needs authorization.",
                     "No errors, deployment requires authorization.",
                     "Not only deployment authorization.",
                     "No errors needs authorization.", "No requires authorization.",
                     "No errors, requires authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_assignments_anywhere_remain_strict(self):
        for text in ("token=secret.", "token: secret.", "token=x.secret.",
                     "token=\nauthorization.", "token=first. No service authorization.",
                     "token=x. No authorization.", "credential=authorization.",
                     "value=anything; No service authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_colon_headers_anywhere_remain_strict(self):
        for text in ("Authorization: Bearer sample", "token: sample. No authorization.",
                     "credential: sample. No authorization.", "No service: authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_prior_benign_colon_and_parenthetical_prose(self):
        self.assertEqual(scan("Checks: complete (service limits retained). No new builds, service changes, visual review or deployment authorization."), [])

    def test_sensitive_headers_in_prior_prose_still_rejected(self):
        for label in ("token", "credential", "X-Token", "api_key", "credentials"):
            with self.subTest(label=label):
                self.assertTrue(scan(label + ": sample. No service authorization."))

    def test_all_line_separators_remain_strict(self):
        for separator in ("\r", "\n", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
                          "\x85", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                self.assertTrue(scan("token:" + separator + "No authorization."))
                self.assertTrue(scan("No service authorization." + separator))

    def test_json_and_quotes_remain_strict(self):
        for text in (json.dumps({"note": "No authorization."}), '"No authorization."',
                     "'No authorization.'", "`No authorization.`", "[No authorization.]"):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_operators_remain_strict(self):
        for text in ("value+ No authorization.", "x | No authorization.",
                     "x && No authorization.", "No service > authorization.",
                     "No service%20authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_long_note_still_rejected(self):
        self.assertTrue(scan("x" * (lint.MAX_EVIDENCE_VALUE_CHARS + 1) + " No authorization."))

    def test_exact_note_budget_and_other_field_boundary(self):
        self.assertEqual(lint.MAX_EVIDENCE_VALUE_CHARS, 2048)
        prefix = "Checks complete. "
        suffix = "No service authorization."
        note = prefix + " " * (2048 - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(note), 2048)
        self.assertEqual(scan(note), [])
        oversized = note + " "
        self.assertEqual(len(oversized), 2049)
        self.assertTrue(scan(oversized))
        issues = lint.scan_sensitive({"evidence": note}, "$.verification_runs")
        self.assertTrue(issues)
        self.assertTrue(any("authorization" in issue for issue in issues))

    def test_ambiguous_labels_lock_only_finite_lexical_scope(self):
        """Lock token grammar only, not English meaning or action permission."""
        self.assertEqual(scan("No errors, request or enable authorization."), [])

    def test_multiple_or_negative_enumeration(self):
        self.assertEqual(scan("No builds or service changes or authorization."), [])

    def test_comma_without_final_or_is_not_permission_disclaimer(self):
        for text in ("No errors, request authorization.",
                     "No errors, enable authorization.",
                     "No errors, service authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_and_list_is_not_permission_disclaimer(self):
        for text in ("No errors and service authorization.",
                     "No errors, service changes and authorization."):
            with self.subTest(text=text):
                self.assertTrue(scan(text))

    def test_controls_and_nonascii_keep_original_rejection(self):
        characters = [chr(value) for value in range(32)] + [chr(127), "\x85", "\u00e9", "\u03b1", "\u2028", "\u4e2d", "\U0001f642"]
        for character in characters:
            with self.subTest(codepoint=ord(character)):
                self.assertTrue(scan("Checks" + character + " complete. No authorization."))

    def test_punctuation_in_credential_header_remains_strict(self):
        for label in ("token.v1", "token /v1", "token/v1", "prefix.token.v2",
                      "credential(v1)", "credentials /v2", "service credential.v1"):
            with self.subTest(label=label):
                self.assertTrue(scan(label + ": sample. No authorization."))

    def test_finite_large_non_note_is_fast_and_rejected(self):
        program = ("import importlib.util,sys; "
                   "s=importlib.util.spec_from_file_location('finite_scan',sys.argv[1]); "
                   "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
                   "assert m.scan_sensitive({'evidence':'authorization. '*16000},'$.verification_runs')")
        completed = subprocess.run([sys.executable, "-B", "-c", program, str(PATH)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_finite_large_note_is_fast_and_rejected(self):
        program = ("import importlib.util,sys; "
                   "s=importlib.util.spec_from_file_location('finite_scan',sys.argv[1]); "
                   "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
                   "assert m.scan_sensitive({'note':'authorization. '*16000},'$.verification_runs',direct_verification_run=True)")
        completed = subprocess.run([sys.executable, "-B", "-c", program, str(PATH)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(completed.returncode, 0, completed.stderr)

SCRIPT = PATH
ORDINARY_NOTE = "Checks: complete (service limits retained). No new service actions, deployment acceptance or authorization."
SENSITIVE_NOTE = "token=sample. No service authorization."

# These are actual data shapes, not strings pretending to be nested paths.
FORGED_CONTRACTS = (
    {"verification_runs": {"note": "No authorization."}},
    {"verification_runs.note": "No authorization."},
    {"verification_runs": [{"note": "No authorization."}]},
    {"verification_runs[0].note": "No authorization."},
)
STRICT_RUN_SHAPES = (
    {"details": {"note": "No authorization."}},
    {"verification_runs": {"note": "No authorization."}},
    {"verification_runs.note": "No authorization."},
    {"note.child": "No authorization."},
    {"Note": "No authorization."},
    {"note": {"note": "No authorization."}},
    {"note": ["No authorization."]},
    {"evidence": "No authorization."},
    {"note": ORDINARY_NOTE, "details": {"note": "No authorization."}},
    {"note": ORDINARY_NOTE, "evidence": "No authorization."},
)

def metadata(note):
    # A linter input, not a fabricated native ticket, event, or accepted run.
    return {"ticket_id": "generic-note-scan", "workflow_gate_schema_version": 1,
            "verification_runs": [{"note": note}]}

def frozen(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}

class PermissionNoteEntryPointTests(unittest.TestCase):
    def report(self, note, *, extra_metadata=None):
        data = metadata(note)
        if extra_metadata is not None:
            data.update(copy.deepcopy(extra_metadata))
        before = copy.deepcopy(data)
        catalog, error = lint.find_gates_catalog()
        self.assertIsNone(error)
        self.assertIsInstance(catalog, dict)
        with TemporaryDirectory() as temporary:
            report = lint.build_report(Path(temporary), "verify", data, catalog,
                                       legacy=False, strict=True)
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(data, before)
        self.assertEqual(report["blocked"], 0, report)
        self.assertTrue(any(row["gate"] == "execution_model" and row["status"] == "pass"
                            for row in report["gates"]), report)
        return report

    def test_build_report_ordinary_direct_run_note(self):
        report = self.report(ORDINARY_NOTE)
        self.assertEqual(report["sensitive_data"], 0, report)
        self.assertTrue(lint.report_is_pass(report, legacy=False, strict=True))

    def test_build_report_contract_note_context_cannot_be_forged(self):
        for key in ("semantic_decisions", "impact_contract", "requirement_deltas", "risk_profile"):
            for contract in FORGED_CONTRACTS:
                with self.subTest(key=key, contract=contract):
                    report = self.report(ORDINARY_NOTE, extra_metadata={key: contract})
                    self.assertGreater(report["sensitive_data"], 0, report)
                    self.assertFalse(lint.report_is_pass(report, legacy=False, strict=True))

    def test_build_report_actual_run_nested_and_other_fields_stay_strict(self):
        for run in STRICT_RUN_SHAPES:
            with self.subTest(run=run):
                report = self.report(ORDINARY_NOTE, extra_metadata={"verification_runs": [run]})
                self.assertGreater(report["sensitive_data"], 0, report)
                self.assertFalse(lint.report_is_pass(report, legacy=False, strict=True))

    def test_build_report_credential_note_stays_rejected(self):
        for text in (SENSITIVE_NOTE, "Authorization: Bearer sample",
                     "No secret. No service authorization.",
                     "No errors, request authorization.", "No errors, enable authorization.",
                     "token.v1: sample. No authorization.",
                     "Checks\0 complete. No authorization."):
            with self.subTest(text=text):
                report = self.report(text)
                self.assertGreater(report["sensitive_data"], 0, report)
                self.assertFalse(lint.report_is_pass(report, legacy=False, strict=True))

    def cli(self, note, expected, *, extra_metadata=None):
        data = metadata(note)
        if extra_metadata is not None:
            data.update(copy.deepcopy(extra_metadata))
        before_data = copy.deepcopy(data)
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "generic-ticket-input"
            directory.mkdir()
            (directory / ".ico_metadata.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
            before = frozen(directory)
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(SCRIPT), str(directory),
                 "--step", "verify", "--strict", "--json"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(result.stderr, b"")
            report = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(report["blocked"], 0, report)
            self.assertEqual(frozen(directory), before)
            self.assertEqual(set(before), {".ico_metadata.json"})
            self.assertEqual(data, before_data)
            return report

    def test_real_cli_ordinary_note_exit_zero(self):
        report = self.cli(ORDINARY_NOTE, 0)
        self.assertEqual(report["sensitive_data"], 0, report)

    def test_real_cli_contract_note_context_cannot_be_forged(self):
        for key in ("semantic_decisions", "impact_contract", "requirement_deltas", "risk_profile"):
            for contract in FORGED_CONTRACTS:
                with self.subTest(key=key, contract=contract):
                    report = self.cli(ORDINARY_NOTE, 1, extra_metadata={key: contract})
                    self.assertGreater(report["sensitive_data"], 0, report)

    def test_real_cli_actual_run_nested_and_other_fields_stay_strict(self):
        for run in STRICT_RUN_SHAPES:
            with self.subTest(run=run):
                report = self.cli(ORDINARY_NOTE, 1, extra_metadata={"verification_runs": [run]})
                self.assertGreater(report["sensitive_data"], 0, report)

    def test_real_cli_sensitive_note_exit_one(self):
        for note in (SENSITIVE_NOTE, "No errors, request authorization.",
                     "No errors, enable authorization.",
                     "token.v1: sample. No authorization.",
                     "Checks\0 complete. No authorization."):
            with self.subTest(note=note):
                report = self.cli(note, 1)
                self.assertGreater(report["sensitive_data"], 0, report)

if __name__ == "__main__":
    unittest.main()
