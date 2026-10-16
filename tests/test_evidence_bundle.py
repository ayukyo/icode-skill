"""Raw evidence contracts, using only temporary files; no hardware or Git writes."""
import copy
import hashlib
import importlib.util
import json
import os
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location("bundle_test_" + name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class EvidenceBundleTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / "tools/evidence_bundle.py").is_file(), "raw bundle validator is missing")
        self.api = module("evidence_bundle")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = {"required": True, "required_layers": ["host"], "required_consumers": ["consumer"],
                         "required_scenarios": ["steady"], "evidence_contract": {
            "schema_version": 1, "required_items": [{"id": "raw", "kind": "log", "layer": "host",
                "consumer": "consumer", "scenario": "steady", "min_count": 1, "complete": True, "method": "machine"}],
            "required_identity": ["device_id", "boot_id"], "visual": None}}
        self.run = {"run_id": "run-1", "candidate_id": "a" * 64, "layer": "host", "consumer": "consumer",
                    "scenario": "steady", "outcome": "pass", "evidence": "raw records", "baseline": "source"}
        self.bundle = {"schema_version": 1, "ticket_id": "ticket-1", "stage_id": None, "run_id": "run-1",
            "candidate_id": "a" * 64, "contract_sha256": self.api.contract_digest(self.contract), "baseline_ref": None,
            "device": {"device_id": "device-1", "boot_id": "boot-1"},
            "window": {"host_start": "2026-10-15T00:00:00Z", "host_end": "2026-10-15T00:00:10Z",
                       "monotonic_start": 1, "monotonic_end": 11}, "items": [], "measurements": [], "visual": None}
        self.add_item("raw-1", "raw", b"complete original log\n")

    def add_item(self, identifier, requirement, raw, kind="log", sources=None, metadata=None):
        path = self.root / (identifier + ".json")
        path.write_bytes(raw)
        item = {"id": identifier, "requirement_id": requirement, "kind": kind, "path": path.name,
                "sha256": digest(raw), "bytes": len(raw), "complete": True, "source_ids": sources or [], "metadata": metadata or {}}
        self.bundle["items"].append(item)
        return item

    def reference(self):
        self.bundle["contract_sha256"] = self.api.contract_digest(self.contract)
        path = self.root / "bundle.json"
        path.write_text(json.dumps(self.bundle), encoding="utf-8")
        return self.api.make_reference(path, self.root)

    def validate(self, reference=None, **kw):
        return self.api.validate_bundle(self.reference() if reference is None else reference, self.contract, self.run,
                                        ticket_id="ticket-1", evidence_root=self.root, **kw)

    def assertRejected(self, pattern, **kw):
        with self.assertRaisesRegex(self.api.EvidenceBundleError, pattern):
            self.validate(**kw)

    def test_complete_bundle_and_digest_are_machine_verified(self):
        result = self.validate()
        self.assertEqual(result["bundle"]["run_id"], self.run["run_id"])
        self.assertEqual(len(result["verified_files"]), 2)

    def test_visual_callback_negative_or_missing_outcome_cannot_satisfy_pass(self):
        self.contract["evidence_contract"]["visual"] = {"required": True, "min_frames": 2,
            "sample_fractions": [0, 1], "tolerance_fraction": .05, "require_display": False}
        self.contract["evidence_contract"]["required_items"][0]["kind"] = "frame_set"
        self.bundle["items"][0]["kind"] = "frame_set"
        for outcome in ("fail", "inconclusive", None):
            with self.subTest(outcome=outcome):
                self.assertRejected("visual.*pass", visual_validator=lambda *args, **kwargs: {"outcome": outcome})

    def test_human_visual_requirement_needs_human_receipt_in_addition_to_visual_ai(self):
        req = self.contract["evidence_contract"]["required_items"][0]
        req.update(kind="frame_set", method="human_observation")
        self.contract["evidence_contract"]["visual"] = {"required": True, "min_frames": 2,
            "sample_fractions": [0, 1], "tolerance_fraction": .05, "require_display": False}
        item = self.bundle["items"][0]
        item["kind"] = "frame_set"
        visual_ai = lambda *args, **kwargs: {"outcome": "pass"}
        self.assertRejected("review", visual_validator=visual_ai)
        item["metadata"]["review_ref"] = {"review_id": "human-review"}
        receipt = {"outcome": "pass", "method": "human_observation", "item_sha256": item["sha256"],
                   "run_id": self.run["run_id"], "candidate_id": self.run["candidate_id"], "requirement_id": req["id"],
                   "cell": {key: self.run[key] for key in ("layer", "consumer", "scenario")},
                   "contract_digest": self.api.contract_digest(self.contract)}
        self.validate(visual_validator=visual_ai, review_validator=lambda ref: receipt)

    def test_positive_review_is_bound_to_exact_requirement_cell_and_contract(self):
        self.contract["evidence_contract"]["required_items"][0]["method"] = "ai_review"
        self.bundle["items"][0]["metadata"]["review_ref"] = {"review_id": "review-1"}
        receipt = {"outcome": "pass", "method": "ai_review", "item_sha256": self.bundle["items"][0]["sha256"],
                   "run_id": self.run["run_id"], "candidate_id": self.run["candidate_id"], "requirement_id": "raw",
                   "cell": {key: self.run[key] for key in ("layer", "consumer", "scenario")},
                   "contract_digest": self.api.contract_digest(self.contract)}
        self.validate(review_validator=lambda ref: receipt)
        for key, value in (("requirement_id", "unreviewed"), ("cell", {"layer": "other"}),
                           ("contract_digest", "sha256:" + "b" * 64)):
            with self.subTest(field=key):
                changed = {**receipt, key: value}
                self.assertRejected("review.*binding", review_validator=lambda ref: changed)

    def test_an_independent_requirement_cannot_borrow_another_requirements_review(self):
        req = self.contract["evidence_contract"]["required_items"][0]
        req["method"] = "ai_review"
        self.contract["evidence_contract"]["required_items"].append({**req, "id": "independent-review"})
        raw = (self.root / "raw-1.json").read_bytes()
        other = self.add_item("raw-2", "independent-review", raw)
        for item in self.bundle["items"]:
            item["metadata"]["review_ref"] = {"review_id": "review-1"}
        receipt = {"outcome": "pass", "method": "ai_review", "item_sha256": other["sha256"],
                   "run_id": self.run["run_id"], "candidate_id": self.run["candidate_id"], "requirement_id": "raw",
                   "cell": {key: self.run[key] for key in ("layer", "consumer", "scenario")},
                   "contract_digest": self.api.contract_digest(self.contract)}
        self.assertRejected("review.*binding", review_validator=lambda ref: receipt)

    def test_large_numeric_inputs_are_structured_failures(self):
        with self.assertRaises(self.api.EvidenceBundleError):
            self.api._finite(10 ** 400, "metric")
        with self.assertRaises(self.api.EvidenceBundleError):
            self.api.Reader(self.root).json(json.dumps({"metric": 10 ** 400}).encode())

    def test_portable_paths_reject_ads_and_win32_aliases(self):
        for value in ("raw:stream", "C:stream", "raw. ", "raw.", "sub /raw.json", "sub./raw.json"):
            with self.subTest(path=value), self.assertRaises(self.api.EvidenceBundleError):
                self.api.Reader(self.root).path(value)

    def test_private_roots_and_children_are_forbidden(self):
        for name in (".git", ".claude", ".config", ".codex"):
            path = self.root / name
            path.mkdir()
            with self.subTest(name=name), self.assertRaises(self.api.EvidenceBundleError):
                self.api.Reader(path)
            with self.assertRaises(self.api.EvidenceBundleError):
                self.api.Reader(self.root).path(name + "/raw.json")

    def test_measure_rejects_non_mapping_run_metrics(self):
        with self.assertRaisesRegex(self.api.EvidenceBundleError, "metrics"):
            self.api._measure(self.api.Reader(self.root), {"measurements": []}, {}, None,
                              {"metrics": ["unexpected"]}, {})

    def test_absent_evidence_never_silently_becomes_legacy(self):
        self.assertRejected("reference", reference={})

    def test_missing_item_wrong_hash_size_and_count(self):
        for field, value in (("sha256", "sha256:" + "0" * 64), ("bytes", 1), ("complete", False)):
            original = copy.deepcopy(self.bundle)
            self.bundle["items"][0][field] = value
            self.assertRejected("hash|bytes|complete")
            self.bundle = original
        self.contract["evidence_contract"]["required_items"][0]["min_count"] = 2
        self.assertRejected("count")

    def test_only_requirements_in_this_run_cell_are_required(self):
        other = dict(self.contract["evidence_contract"]["required_items"][0], id="other", layer="physical")
        self.contract["required_layers"].append("physical")
        self.contract["evidence_contract"]["required_items"].append(other)
        self.validate()

    def test_explicit_item_scope_must_match_frozen_requirement_and_run(self):
        for field in ("layer", "consumer", "scenario"):
            with self.subTest(field=field):
                self.bundle["items"][0]["metadata"] = {field: "other-consumer"}
                self.assertRejected("scope")
        self.bundle["items"][0]["metadata"] = {"source_layer": "driver"}
        self.validate()

    def test_optional_frozen_expected_mode_rejects_self_consistent_wrong_sample(self):
        expected = {"width": 1920, "height": 1080, "fourcc": "PNG"}
        self.contract["evidence_contract"]["required_items"][0]["expected_mode"] = expected
        self.bundle["items"][0]["metadata"]["mode"] = {"width": 4, "height": 2, "fourcc": "PNG"}
        self.assertRejected("expected_mode")
        self.bundle["items"][0]["metadata"]["mode"] = expected
        self.validate()

    def test_unique_requirements_items_and_acyclic_sources(self):
        self.bundle["items"][0]["source_ids"] = ["raw-1"]
        self.assertRejected("source|cycle")
        self.bundle["items"][0]["source_ids"] = ["missing"]
        self.assertRejected("source")
        self.bundle["items"][0]["source_ids"] = []
        self.bundle["items"].append(copy.deepcopy(self.bundle["items"][0]))
        self.assertRejected("duplicate")

    def test_required_identity_rejects_placeholder_but_optional_unknown_is_allowed(self):
        for value in ("unknown", "not_applicable", "<device_id>", "TODO", "placeholder"):
            self.bundle["device"]["device_id"] = value
            self.assertRejected("identity")
        self.contract["evidence_contract"]["required_identity"] = []
        self.validate()

    def test_paths_outside_root_parent_traversal_and_private_paths_are_rejected(self):
        for value in ("../outside", "sub/../raw-1.json", ".ssh/key", "C:/Windows/system.ini"):
            self.bundle["items"][0]["path"] = value
            self.assertRejected("path|root|private")

    def test_symlink_parent_is_rejected(self):
        target = self.root / "actual"
        target.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink unavailable: " + str(exc))
        (target / "raw.json").write_bytes(b"complete original log\n")
        self.bundle["items"][0]["path"] = "linked/raw.json"
        self.assertRejected("symlink|reparse")

    def test_budget_is_fail_closed(self):
        self.assertRejected("budget", limits={"max_file_bytes": 5})
        self.assertRejected("budget", limits={"max_files": 1})

    def test_private_root_and_ordinary_file_replacement_are_rejected(self):
        private = self.root / ".ssh"
        private.mkdir()
        with self.assertRaisesRegex(self.api.EvidenceBundleError, "private|root"):
            self.api.Reader(private)
        reference = self.reference()
        original_open = os.open
        target = self.root / "raw-1.json"
        def replace_before_open(path, flags, *args, **kw):
            if Path(path) == target:
                replacement = self.root / "replacement.json"
                replacement.write_bytes(target.read_bytes())
                os.replace(replacement, target)
            return original_open(path, flags, *args, **kw)
        with mock.patch.object(self.api.os, "open", side_effect=replace_before_open):
            self.assertRejected("replaced", reference=reference)

    def test_duplicate_requirement_and_multi_node_source_cycle_are_rejected(self):
        requirement = copy.deepcopy(self.contract["evidence_contract"]["required_items"][0])
        self.contract["evidence_contract"]["required_items"].append(requirement)
        self.assertRejected("duplicate")
        self.contract["evidence_contract"]["required_items"].pop()
        self.add_item("raw-2", "raw", b"second raw file", sources=["raw-1"])
        self.bundle["items"][0]["source_ids"] = ["raw-2"]
        self.assertRejected("cycle")

    def test_ticket_stage_run_candidate_contract_and_baseline_binding(self):
        for field, value in (("ticket_id", "other"), ("stage_id", "other"), ("run_id", "other"),
                             ("candidate_id", "b" * 64), ("baseline_ref", "sha256:" + "c" * 64)):
            original = self.bundle[field]
            self.bundle[field] = value
            self.assertRejected("mismatch")
            self.bundle[field] = original
        reference = self.reference()
        self.contract["required"] = False
        self.assertRejected("contract.*mismatch", reference=reference)

    def test_device_boot_window_and_timestamp_order(self):
        self.run["device"] = "another-device"
        self.assertRejected("device.*mismatch")
        self.run["device"] = "device-1"
        self.run["boot_id"] = "another-boot"
        self.assertRejected("boot.*mismatch")
        del self.run["boot_id"]
        self.bundle["window"]["monotonic_end"] = 0
        self.assertRejected("window")

    def measurement(self, method="sample_rate", unit="Hz", value=2):
        self.contract["evidence_contract"]["required_items"].append({"id": "rate", "kind": "measurement", "layer": "host",
            "consumer": "consumer", "scenario": "steady", "min_count": 1, "complete": True, "method": "machine"})
        samples = {"schema_version": 1, "clock": "monotonic", "unit": "s",
                   "samples": [{"timestamp": 1, "sequence": 0}, {"timestamp": 1.5, "sequence": 1}, {"timestamp": 2, "sequence": 2}]}
        self.bundle["items"][0].update(self._replace_raw("raw-1", samples))
        self.bundle["measurements"] = [{"id": "rate-1", "item_id": "derived", "name": "rate", "method": method,
                                       "source_ids": ["raw-1"], "unit": unit, "value": value}]
        self.add_item("derived", "rate", json.dumps(self.bundle["measurements"][0]).encode(), "measurement", ["raw-1"])
        self.run["metrics"] = {"rate": value}
        self.contract["required_metrics"] = [{"name": "rate", "unit": unit, "operator": "gte", "value": 1,
                                              "layer": "host", "consumer": "consumer", "scenario": "steady"}]

    def _replace_raw(self, identifier, value):
        raw = json.dumps(value).encode()
        (self.root / (identifier + ".json")).write_bytes(raw)
        return {"sha256": digest(raw), "bytes": len(raw)}

    def test_sample_rate_recomputed_from_original_timestamps(self):
        self.measurement()
        self.assertEqual(self.validate()["measurements"]["rate"], 2)
        self.bundle["measurements"][0]["value"] = 99
        self.assertRejected("recomputed|measurement")

    def test_sequence_drop_rate_recomputed(self):
        self.measurement("sequence_drop_rate", "%", 0)
        self.contract["required_metrics"][0].update(operator="eq", value=0)
        self.assertEqual(self.validate()["measurements"]["rate"], 0)

    def test_raw_jsonl_sequence_gap_and_timestamp_bounds_are_recomputed(self):
        self.measurement("sequence_drop_rate", "%", 25)
        self.contract["required_metrics"][0].update(operator="eq", value=25)
        raw = b'{"schema_version":1,"clock":"monotonic","unit":"s"}\n{"timestamp":1,"sequence":0}\n{"timestamp":1.5,"sequence":1}\n{"timestamp":2,"sequence":3}\n'
        (self.root / "raw-1.json").write_bytes(raw)
        self.bundle["items"][0].update(sha256=digest(raw), bytes=len(raw), metadata={"format": "jsonl"})
        self.assertEqual(self.validate()["measurements"]["rate"], 25)
        raw = raw.replace(b'"timestamp":2', b'"timestamp":12')
        (self.root / "raw-1.json").write_bytes(raw)
        self.bundle["items"][0].update(sha256=digest(raw), bytes=len(raw))
        self.assertRejected("window")

    def test_raw_finite_clock_sequence_and_json_duplicate_guards(self):
        self.measurement()
        good = (self.root / "raw-1.json").read_bytes()
        for raw in (good.replace(b'"timestamp": 1', b'"timestamp": NaN', 1),
                    good.replace(b'"timestamp": 1', b'"timestamp": true', 1),
                    good.replace(b'"monotonic"', b'"wall"'),
                    good.replace(b'"schema_version": 1', b'"schema_version": 1,"schema_version":1')):
            (self.root / "raw-1.json").write_bytes(raw)
            self.bundle["items"][0].update(sha256=digest(raw), bytes=len(raw))
            self.assertRejected("finite|JSON|schema|clock")

    def test_measurement_source_header_binds_device_boot_and_run(self):
        self.measurement()
        samples = json.loads((self.root / "raw-1.json").read_bytes())
        for key in ("run_id", "device_id", "boot_id"):
            modified = dict(samples, **{key: "other"})
            self.bundle["items"][0].update(self._replace_raw("raw-1", modified))
            self.assertRejected("mismatch")

    def test_measurement_requires_raw_sources_finite_values_and_threshold(self):
        self.measurement()
        self.bundle["measurements"][0]["source_ids"] = []
        self.assertRejected("source")
        self.bundle["measurements"][0]["source_ids"] = ["raw-1"]
        self.run["metrics"]["rate"] = True
        self.assertRejected("finite|metric")
        self.run["metrics"]["rate"] = 2
        self.contract["required_metrics"][0]["value"] = 3
        self.assertRejected("threshold")

    def test_display_truncation_does_not_excuse_raw_truncation(self):
        self.bundle["items"][0]["metadata"] = {"display_truncated": True}
        self.validate()
        self.bundle["items"][0]["metadata"]["raw_truncated"] = True
        self.assertRejected("truncated")

    def test_visual_and_review_claims_cannot_self_attest(self):
        self.contract["evidence_contract"]["visual"] = {"required": True, "min_frames": 5,
            "sample_fractions": [0, .25, .5, .75, 1], "tolerance_fraction": .15, "require_display": False}
        self.bundle["visual"] = {"viewed": True, "pass": True}
        self.assertRejected("visual")
        self.contract["evidence_contract"]["visual"] = None
        self.contract["evidence_contract"]["required_items"][0]["method"] = "human_observation"
        self.bundle["items"][0]["metadata"] = {"pass": True}
        self.assertRejected("review")

    def test_assess_and_latest_queries_recheck_deleted_or_modified_files(self):
        evidence = module("verification_evidence")
        self.run["evidence_bundle"] = self.reference()
        meta = {"schema_version": 3, "ticket_id": "ticket-1", "verification_contract": self.contract,
                "verification_runs": [self.run]}
        ctx = evidence.context(self.root, meta)
        self.assertTrue(evidence.assess(meta, ctx)["ok"])
        (self.root / "raw-1.json").write_bytes(b"changed")
        self.assertFalse(evidence.assess(meta, ctx)["ok"])
        self.assertEqual(evidence.eligible_latest_runs(meta, ctx)["latest"], {})

    def test_old_contract_keeps_existing_semantics(self):
        evidence = module("verification_evidence")
        del self.contract["evidence_contract"]
        meta = {"verification_contract": self.contract, "verification_runs": [self.run]}
        self.assertTrue(evidence.assess(meta, evidence.context(self.root, meta))["ok"])

    def test_baseline_v2_derives_evidence_contract_without_changing_v1(self):
        profile = module("embedded_profile")
        baseline = json.loads((ROOT / "templates/embedded_baseline.json.template").read_text())
        profile.validate(baseline)
        self.assertNotIn("evidence_contract", profile.make_plan(Path("baseline.json"), baseline, "sha256:" + "a" * 64)["verification_contract"])
        baseline["schema_version"] = 2
        requirement = self.contract["evidence_contract"]["required_items"][0]
        scenario = baseline["verification"]["scenarios"][0]
        requirement.update(layer=scenario["layer"], consumer=scenario["consumer"], scenario=scenario["id"])
        baseline["verification"]["evidence_contract"] = self.contract["evidence_contract"]
        profile.validate(baseline)
        self.assertEqual(profile.make_plan(Path("baseline.json"), baseline, "sha256:" + "a" * 64)["verification_contract"]["evidence_contract"], self.contract["evidence_contract"])

    def test_linter_and_debt_recheck_current_raw_files(self):
        lint, debt = module("lint_workflow_contract"), module("verification_debt")
        self.run["evidence_bundle"] = self.reference()
        meta = {"schema_version": 3, "ticket_id": "ticket-1", "verification_contract": self.contract,
                "verification_runs": [self.run]}
        catalog = {"constants": {"verification_layers": ["host"]}}
        self.assertEqual(lint.validate_delivery_evidence(meta, catalog, self.root), [])
        self.assertEqual(debt.analyze_ticket(meta, self.root)["satisfied_units"], 1)
        (self.root / "raw-1.json").unlink()
        self.assertTrue(lint.validate_delivery_evidence(meta, catalog, self.root))
        self.assertEqual(debt.analyze_ticket(meta, self.root)["satisfied_units"], 0)

    def test_latest_actual_failure_without_bundle_invalidates_an_earlier_pass(self):
        evidence = module("verification_evidence")
        self.run["evidence_bundle"] = self.reference()
        negative = dict(self.run, run_id="failed-2", outcome="fail")
        negative.pop("evidence_bundle")
        meta = {"ticket_id": "ticket-1", "verification_contract": self.contract,
                "verification_runs": [self.run, negative]}
        result = evidence.assess(meta, evidence.context(self.root, meta))
        self.assertFalse(result["ok"])
        self.assertIn("latest_outcome_fail", result["reasons"])

    def test_negative_partial_bundle_keeps_raw_reference_without_requiring_pass_completeness(self):
        for outcome in ("fail", "inconclusive"):
            self.run["outcome"] = outcome
            self.bundle["device"]["boot_id"] = "unknown"
            self.bundle["items"][0].update(complete=False, metadata={"raw_truncated": True})
            self.contract["evidence_contract"]["required_items"][0]["min_count"] = 5
            try:
                result = self.validate()
            except self.api.EvidenceBundleError as exc:
                self.fail("partial negative evidence must be recordable: " + str(exc))
            self.assertEqual(result["bundle"]["items"][0]["complete"], False)
            self.bundle["items"] = []
            self.validate()
            self.add_item("raw-1", "raw", b"complete original log\n")

    def test_elapsed_budget_is_checked_after_local_io_returns(self):
        self.assertIn("max_elapsed_seconds", self.api.DEFAULT_LIMITS, "elapsed time budget is missing")
        reader = self.api.Reader(self.root, {"max_elapsed_seconds": 1})
        with mock.patch.object(self.api.time, "monotonic", return_value=reader.started + 2):
            with self.assertRaisesRegex(self.api.EvidenceBundleError, "budget"):
                reader.read("raw-1.json")

    def test_required_identity_rejects_replacement_template_tokens(self):
        for value in ("replace-with-real-device-id", "replace_me", "not applicable"):
            self.bundle["device"]["device_id"] = value
            self.assertRejected("identity")

    def test_review_adapter_failure_is_reported_as_ineligible_evidence(self):
        self.contract["evidence_contract"]["required_items"][0]["method"] = "ai_review"
        self.bundle["items"][0]["metadata"]["review_ref"] = {"review_id": "review-1"}
        def unavailable(reference):
            raise RuntimeError("adapter unavailable")
        try:
            self.assertRejected("review.*unavailable", review_validator=unavailable)
        except RuntimeError:
            self.fail("review adapter failure escaped instead of producing ineligible evidence")

    def test_newer_missing_bundle_pass_cannot_resurrect_previous_valid_pass(self):
        evidence = module("verification_evidence")
        self.run["evidence_bundle"] = self.reference()
        later = dict(self.run, run_id="later-pass")
        later.pop("evidence_bundle")
        meta = {"ticket_id": "ticket-1", "verification_contract": self.contract,
                "verification_runs": [self.run, later]}
        ctx = evidence.context(self.root, meta)
        self.assertFalse(evidence.assess(meta, ctx)["ok"])
        later["outcome"] = "fail"
        self.assertIn("latest_outcome_fail", evidence.assess(meta, ctx)["reasons"])

    def test_unit_state_contract_path_does_not_bypass_raw_validation(self):
        evidence = module("verification_evidence")
        meta = {"ticket_id": "ticket-1", "verification_contract": self.contract}
        self.assertIn("reference", evidence.unit_state(self.run, meta=meta, evidence_root=self.root))

    def test_newer_valid_pass_replaces_older_missing_bundle_pass(self):
        evidence = module("verification_evidence")
        earlier = dict(self.run, run_id="earlier-pass")
        self.run["evidence_bundle"] = self.reference()
        meta = {"ticket_id": "ticket-1", "verification_contract": self.contract,
                "verification_runs": [earlier, self.run]}
        self.assertTrue(evidence.assess(meta, evidence.context(self.root, meta))["ok"])

    def test_real_non_git_writer_rejects_missing_bundle_and_keeps_negative_and_legacy_writes(self):
        ctl = module("icode_control")
        parser = ctl.build_parser()
        # The directory has no Git repository; this test never creates commits.
        ticket = self.root / ".icode_output" / ".icode_output_1"
        with contextlib.redirect_stdout(io.StringIO()):
            args = parser.parse_args(["create", "--dir", str(ticket), "--ticket-id", "ticket-1", "--birth", "plan",
                "--requirement", "raw evidence test", "--metadata-json", json.dumps({"verification_contract": self.contract})])
            self.assertEqual(args.func(args), 0)
            base = ["record-verification", "--dir", str(ticket), "--kind", "device_test", "--layer", "host",
                "--consumer", "consumer", "--scenario", "steady", "--baseline", "source", "--evidence", "raw record"]
            before = [(ticket / name).read_bytes() for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME)]
            args = parser.parse_args(base + ["--outcome", "pass"])
            with self.assertRaisesRegex(ctl.ControlError, "reference"):
                args.func(args)
            self.assertEqual(before, [(ticket / name).read_bytes() for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME)])
            args = parser.parse_args(base + ["--outcome", "fail"])
            self.assertEqual(args.func(args), 0)
            metadata = ctl.load_metadata(ticket)
            self.assertEqual(metadata["verification_runs"][-1]["outcome"], "fail")
            self.assertEqual(ctl.verify_event_chain(ticket, metadata)[1], [])
            old_contract = copy.deepcopy(self.contract)
            del old_contract["evidence_contract"]
            args = parser.parse_args(["metadata-update", "--dir", str(ticket), "--set-json", json.dumps({"verification_contract": old_contract})])
            self.assertEqual(args.func(args), 0)
            args = parser.parse_args(base + ["--outcome", "pass"])
            self.assertEqual(args.func(args), 0)

    def test_writer_accepts_two_mutually_exclusive_bundle_inputs(self):
        ctl = module("icode_control")
        parser = ctl.build_parser()
        base = ["record-verification", "--dir", str(self.root), "--kind", "device_test", "--outcome", "pass", "--evidence", "raw"]
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                args = parser.parse_args(base + ["--evidence-bundle", "bundle.json"])
            except SystemExit:
                self.fail("record-verification is missing --evidence-bundle")
            self.assertEqual(args.evidence_bundle, "bundle.json")
            args = parser.parse_args(base + ["--evidence-bundle-json", '{"path":"bundle.json","sha256":"hash"}'])
            self.assertIsNotNone(args.evidence_bundle_json)
            with self.assertRaises(SystemExit):
                parser.parse_args(base + ["--evidence-bundle", "a", "--evidence-bundle-json", "{}"])


if __name__ == "__main__":
    unittest.main()
