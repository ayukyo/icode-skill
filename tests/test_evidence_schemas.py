"""Pure schema contracts: no devices, subprocesses, or Git fixture mutations."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_schema_control", ROOT / "tools/icode_control.py")
ctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ctl)
HASH = "sha256:" + "a" * 64
CANDIDATE = "b" * 64


def evidence_contract():
    return {
        "schema_version": 1,
        "required_items": [{
            "id": "runtime_log", "kind": "log", "layer": "physical",
            "consumer": "stream_consumer", "scenario": "steady_run",
            "min_count": 1, "complete": True, "method": "machine",
        }],
        "required_identity": ["device_id", "boot_id"],
        "visual": None,
    }


def visual_contract():
    return {
        "required": True, "min_frames": 5,
        "sample_fractions": [0, 0.25, 0.5, 0.75, 1],
        "tolerance_fraction": 0.15, "require_display": False,
    }


def evidence_bundle():
    return {
        "schema_version": 1, "ticket_id": "ticket-1", "stage_id": None,
        "run_id": "run-1", "candidate_id": CANDIDATE,
        "contract_sha256": HASH, "baseline_ref": None,
        "device": {"device_id": "device-1", "boot_id": "boot-1"},
        "window": {"host_start": "2026-10-15T00:00:00Z",
                   "host_end": "2026-10-15T00:00:10Z",
                   "monotonic_start": 1.0, "monotonic_end": 11.0},
        "items": [{"id": "log-1", "requirement_id": "runtime_log", "kind": "log",
                   "path": "raw/runtime.log", "sha256": HASH, "bytes": 12,
                   "complete": True, "source_ids": [], "metadata": {"encoding": "utf-8"}}],
        "measurements": [], "visual": None,
    }


def stage_program():
    return {
        "schema_version": 1, "program_id": "program-1", "project_root": ".",
        "authorized_actions": ["observe"],
        "stages": [{"id": "observe", "title": "Observe runtime", "required_actions": ["observe"]}],
    }


def ticket_metadata():
    return {
        "schema_version": 3, "requirement": "evidence validation",
        "created_at": "2026-10-15T00:00:00Z", "status": "init_in_progress",
        "completed_steps": [], "ticket_id": "ticket-1",
    }


class EvidenceSchemaTests(unittest.TestCase):
    def schema(self, name):
        path = ROOT / "schemas" / (name + ".schema.json")
        self.assertTrue(path.is_file(), "missing evidence contract schema: " + path.name)
        return json.loads(path.read_text(encoding="utf-8"))

    def errors(self, name, value):
        return ctl.check_schema(value, self.schema(name))

    def assertValid(self, name, value):
        self.assertEqual(self.errors(name, value), [])

    def assertInvalid(self, name, value):
        self.assertTrue(self.errors(name, value), repr(value))

    def baseline(self, version=1):
        value = json.loads((ROOT / "templates/embedded_baseline.template.json").read_text(encoding="utf-8"))
        value["schema_version"] = version
        return value

    def test_contract_accepts_every_evidence_kind_and_method(self):
        for kind in ("log", "measurement", "artifact_identity", "frame_set", "visual_review"):
            for method in ("machine", "ai_review", "human_observation"):
                with self.subTest(kind=kind, method=method):
                    value = evidence_contract()
                    value["required_items"][0].update(kind=kind, method=method)
                    self.assertValid("evidence-contract", value)

    def test_visual_requirement_can_freeze_expected_mode_without_guessing_scenario(self):
        value = evidence_contract()
        requirement = value["required_items"][0]
        requirement.update(kind="frame_set", expected_mode={"width": 4, "height": 2, "fourcc": "PNG"})
        self.assertValid("evidence-contract", value)
        for field, invalid in (("width", True), ("height", 0), ("fourcc", ""), ("extra", 1)):
            broken = copy.deepcopy(value)
            broken["required_items"][0]["expected_mode"][field] = invalid
            self.assertInvalid("evidence-contract", broken)

    def test_contract_requires_explicit_fields(self):
        for field in evidence_contract():
            with self.subTest(field=field):
                value = evidence_contract()
                del value[field]
                self.assertInvalid("evidence-contract", value)

    def test_contract_requires_complete_requirement_identity(self):
        for field in evidence_contract()["required_items"][0]:
            with self.subTest(field=field):
                value = evidence_contract()
                del value["required_items"][0][field]
                self.assertInvalid("evidence-contract", value)

    def test_contract_rejects_unknown_fields_and_invalid_enums(self):
        for field, invalid in (("kind", "screenshot"), ("layer", "runtime"),
                               ("method", "assumed"), ("extra", True)):
            with self.subTest(field=field):
                value = evidence_contract()
                value["required_items"][0][field] = invalid
                self.assertInvalid("evidence-contract", value)
        value = evidence_contract()
        value["extra"] = True
        self.assertInvalid("evidence-contract", value)

    def test_contract_rejects_empty_identity_and_nonpositive_count(self):
        for field, invalid in (("id", ""), ("consumer", ""), ("scenario", ""),
                               ("min_count", 0), ("min_count", True), ("min_count", 1.5),
                               ("complete", "yes")):
            with self.subTest(field=field, invalid=invalid):
                value = evidence_contract()
                value["required_items"][0][field] = invalid
                self.assertInvalid("evidence-contract", value)
        value = evidence_contract()
        value["required_identity"] = [""]
        self.assertInvalid("evidence-contract", value)

    def test_contract_accepts_visual_and_documents_defaults(self):
        value = evidence_contract()
        value["visual"] = visual_contract()
        self.assertValid("evidence-contract", value)
        fields = self.schema("evidence-contract")["properties"]["visual"]["properties"]
        for field in ("min_frames", "sample_fractions", "tolerance_fraction", "require_display"):
            with self.subTest(field=field):
                self.assertEqual(fields[field]["default"], visual_contract()[field])

    def test_visual_requires_at_least_two_frames(self):
        value = evidence_contract()
        value["visual"] = visual_contract()
        value["visual"]["min_frames"] = 2
        self.assertValid("evidence-contract", value)
        for invalid in (0, 1, True, 2.5):
            with self.subTest(invalid=invalid):
                value["visual"]["min_frames"] = invalid
                self.assertInvalid("evidence-contract", value)

    def test_visual_rejects_invalid_fraction_numbers(self):
        for invalid in (-0.01, 1.01, True, float("nan"), float("inf"), -float("inf")):
            for field in ("sample_fractions", "tolerance_fraction"):
                with self.subTest(field=field, invalid=invalid):
                    value = evidence_contract()
                    value["visual"] = visual_contract()
                    value["visual"][field] = [0, invalid, 1] if field == "sample_fractions" else invalid
                    self.assertInvalid("evidence-contract", value)

    def test_visual_requires_explicit_fields(self):
        for field in visual_contract():
            with self.subTest(field=field):
                value = evidence_contract()
                value["visual"] = visual_contract()
                del value["visual"][field]
                self.assertInvalid("evidence-contract", value)

    def test_visual_rejects_false_required_or_unknown_fields(self):
        for field, invalid in (("required", False), ("required", 1),
                               ("require_display", "yes"), ("extra", True),
                               ("sample_fractions", [0])):
            with self.subTest(field=field):
                value = evidence_contract()
                value["visual"] = visual_contract()
                value["visual"][field] = invalid
                self.assertInvalid("evidence-contract", value)

    def test_bundle_accepts_nullable_stage_and_baseline(self):
        self.assertValid("verification-evidence", evidence_bundle())
        value = evidence_bundle()
        value.update(stage_id="observe", baseline_ref=HASH, visual={"review": "pending"})
        value["measurements"] = [{"name": "observed_count", "value": 12, "source_ids": ["log-1"]}]
        self.assertValid("verification-evidence", value)

    def test_bundle_requires_all_top_level_fields(self):
        for field in evidence_bundle():
            with self.subTest(field=field):
                value = evidence_bundle()
                del value[field]
                self.assertInvalid("verification-evidence", value)

    def test_bundle_requires_every_raw_item_field(self):
        for field in evidence_bundle()["items"][0]:
            with self.subTest(field=field):
                value = evidence_bundle()
                del value["items"][0][field]
                self.assertInvalid("verification-evidence", value)

    def test_bundle_digest_uses_prefixed_lowercase_sha256(self):
        for invalid in ("a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63):
            for field in ("contract_sha256", "baseline_ref"):
                with self.subTest(field=field, invalid=invalid):
                    value = evidence_bundle()
                    value[field] = invalid
                    self.assertInvalid("verification-evidence", value)
            value = evidence_bundle()
            value["items"][0]["sha256"] = invalid
            self.assertInvalid("verification-evidence", value)

    def test_bundle_candidate_keeps_existing_bare_hash_format(self):
        for invalid in (HASH, "B" * 64, None, "b" * 63):
            with self.subTest(invalid=invalid):
                value = evidence_bundle()
                value["candidate_id"] = invalid
                self.assertInvalid("verification-evidence", value)

    def test_hashes_reject_trailing_newlines(self):
        for field in ("contract_sha256", "baseline_ref"):
            with self.subTest(field=field):
                value = evidence_bundle()
                value[field] = HASH + "\n"
                self.assertInvalid("verification-evidence", value)
        value = evidence_bundle()
        value["candidate_id"] = CANDIDATE + "\n"
        self.assertInvalid("verification-evidence", value)
        value = evidence_bundle()
        value["items"][0]["sha256"] = HASH + "\n"
        self.assertInvalid("verification-evidence", value)
        value = ticket_metadata()
        value["verification_runs"] = [{"run_id": "run-1", "at": "now", "kind": "listen", "outcome": "pass",
                                       "evidence_bundle": {"path": "evidence.json", "sha256": HASH + "\n"}}]
        self.assertInvalid("ticket-metadata", value)

    def test_bundle_requires_nonempty_device_identity(self):
        for field in ("device_id", "boot_id"):
            for invalid in (None, ""):
                with self.subTest(field=field, invalid=invalid):
                    value = evidence_bundle()
                    value["device"][field] = invalid
                    self.assertInvalid("verification-evidence", value)
            value = evidence_bundle()
            del value["device"][field]
            self.assertInvalid("verification-evidence", value)

    def test_bundle_window_requires_host_and_monotonic_bounds(self):
        for field in evidence_bundle()["window"]:
            with self.subTest(field=field):
                value = evidence_bundle()
                del value["window"][field]
                self.assertInvalid("verification-evidence", value)

    def test_bundle_monotonic_bounds_reject_nonfinite_bool_and_negative(self):
        for field in ("monotonic_start", "monotonic_end"):
            for invalid in (True, float("nan"), float("inf"), -float("inf"), -1):
                with self.subTest(field=field, invalid=invalid):
                    value = evidence_bundle()
                    value["window"][field] = invalid
                    self.assertInvalid("verification-evidence", value)

    def test_bundle_rejects_invalid_raw_item_values(self):
        for field, invalid in (("kind", "summary"), ("path", ""), ("bytes", -1),
                               ("bytes", True), ("complete", "true"), ("source_ids", [""]),
                               ("metadata", []), ("extra", True)):
            with self.subTest(field=field, invalid=invalid):
                value = evidence_bundle()
                value["items"][0][field] = invalid
                self.assertInvalid("verification-evidence", value)

    def test_bundle_extensions_are_objects_with_explicit_open_metadata(self):
        schema = self.schema("verification-evidence")
        self.assertIs(schema["properties"]["items"]["items"]["properties"]["metadata"]["additionalProperties"], True)
        for field in ("measurements", "visual"):
            with self.subTest(field=field):
                value = evidence_bundle()
                value[field] = ["summary"] if field == "measurements" else []
                self.assertInvalid("verification-evidence", value)

    def test_bundle_rejects_unregistered_top_level_fields(self):
        value = evidence_bundle()
        value["execute"] = "untrusted data"
        self.assertInvalid("verification-evidence", value)

    def test_metadata_preserves_legacy_verification_shapes(self):
        value = ticket_metadata()
        value["verification_contract"] = {"required": False, "required_layers": [],
                                          "required_consumers": [], "required_scenarios": []}
        value["verification_runs"] = [{"run_id": "old", "at": "then", "kind": "listen", "outcome": "pass"}]
        self.assertValid("ticket-metadata", value)

    def test_metadata_accepts_contract_and_bundle_reference(self):
        value = ticket_metadata()
        value["verification_contract"] = {"required": True, "required_layers": ["physical"],
                                          "required_consumers": ["stream_consumer"],
                                          "required_scenarios": ["steady_run"],
                                          "evidence_contract": evidence_contract()}
        value["verification_runs"] = [{"run_id": "run-1", "at": "now", "kind": "listen", "outcome": "pass",
                                       "evidence_bundle": {"path": "evidence/run-1.json", "sha256": HASH}}]
        self.assertValid("ticket-metadata", value)

    def test_metadata_checks_bundle_reference_shape(self):
        for reference in ({"path": "evidence.json"}, {"sha256": HASH},
                          {"path": "", "sha256": HASH}, {"path": "evidence.json", "sha256": CANDIDATE},
                          {"path": "evidence.json", "sha256": HASH, "extra": True}, None):
            with self.subTest(reference=reference):
                value = ticket_metadata()
                value["verification_runs"] = [{"run_id": "run-1", "at": "now", "kind": "listen",
                                               "outcome": "pass", "evidence_bundle": reference}]
                self.assertInvalid("ticket-metadata", value)

    def test_metadata_stage_extensions_remain_open_objects(self):
        value = ticket_metadata()
        value["extensions"] = {"stage_program": {"future_receipts": []},
                               "stage_binding": {"future_identity": "binding"}}
        self.assertValid("ticket-metadata", value)
        for namespace in value["extensions"]:
            with self.subTest(namespace=namespace):
                invalid = copy.deepcopy(value)
                invalid["extensions"][namespace] = []
                self.assertInvalid("ticket-metadata", invalid)

    def test_embedded_v1_template_is_unchanged_and_rejects_contract(self):
        value = self.baseline()
        self.assertValid("embedded-baseline", value)
        value["verification"]["evidence_contract"] = evidence_contract()
        self.assertInvalid("embedded-baseline", value)

    def test_embedded_v2_requires_evidence_contract(self):
        value = self.baseline(2)
        self.assertInvalid("embedded-baseline", value)
        value["verification"]["evidence_contract"] = evidence_contract()
        self.assertValid("embedded-baseline", value)

    def test_embedded_v2_rejects_invalid_contract_and_unknown_version(self):
        value = self.baseline(2)
        value["verification"]["evidence_contract"] = evidence_contract()
        value["verification"]["evidence_contract"]["required_items"][0]["method"] = "assumed"
        self.assertInvalid("embedded-baseline", value)
        value["verification"]["evidence_contract"] = evidence_contract()
        value["schema_version"] = 3
        self.assertInvalid("embedded-baseline", value)

    def test_inlined_contracts_match_standalone_source(self):
        source = self.schema("evidence-contract")
        body = {key: value for key, value in source.items() if key not in ("$schema", "$id", "title", "description")}
        metadata = self.schema("ticket-metadata")["properties"]["verification_contract"]
        self.assertEqual(metadata["properties"]["evidence_contract"], body)
        baseline = self.schema("embedded-baseline")["then"]["properties"]["verification"]
        self.assertEqual(baseline["properties"]["evidence_contract"], body)

    def test_stage_plan_accepts_ordered_generic_stages(self):
        value = stage_program()
        value["stages"].append({"id": "review", "title": "Review evidence", "required_actions": []})
        self.assertValid("stage-program", value)

    def test_stage_plan_requires_all_fields_and_nonempty_stages(self):
        for field in stage_program():
            with self.subTest(field=field):
                value = stage_program()
                del value[field]
                self.assertInvalid("stage-program", value)
        value = stage_program()
        value["stages"] = []
        self.assertInvalid("stage-program", value)

    def test_stage_plan_rejects_invalid_stage_and_unknown_fields(self):
        for field, invalid in (("id", ""), ("title", ""), ("required_actions", [""]), ("extra", True)):
            with self.subTest(field=field):
                value = stage_program()
                value["stages"][0][field] = invalid
                self.assertInvalid("stage-program", value)
        value = stage_program()
        value["unknown"] = True
        self.assertInvalid("stage-program", value)

    def test_stage_plan_rejects_wrong_types_and_empty_action_names(self):
        for field, invalid in (("schema_version", True), ("schema_version", 2),
                               ("program_id", ""), ("project_root", ""),
                               ("authorized_actions", [""]), ("authorized_actions", "observe")):
            with self.subTest(field=field, invalid=invalid):
                value = stage_program()
                value[field] = invalid
                self.assertInvalid("stage-program", value)


if __name__ == "__main__":
    unittest.main()
