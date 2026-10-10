"""Candidate-bound evidence contracts, exercised through real local ports."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verification_control", ROOT / "tools/icode_control.py")
ctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ctl)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icode-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Evidence Test")
        (self.repo / ".gitignore").write_text(".icode_output/\nbuild/\n")
        (self.repo / "source.py").write_text("value = 1\n")
        (self.repo / "README.md").write_text("Initial documentation\n")
        (self.repo / "cookie_reader.py").write_text("value = 0\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.ticket = self.repo / ".icode_output/.icode_output_1"
        self.contract = {"required": True, "required_layers": ["physical"],
                         "required_consumers": ["camera"], "required_scenarios": ["steady_stream"]}
        self.call("create", "--dir", self.ticket, "--ticket-id", "evidence-1", "--birth", "plan",
                  "--requirement", "evidence", "--metadata-json", json.dumps({"verification_contract": self.contract}))

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def call(self, *args, ok=True):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/icode_control.py"), *map(str, args)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode == 0, ok, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}

    def meta(self):
        return ctl.load_metadata(self.ticket)

    def snapshot(self):
        return ctl.candidate_snapshot(self.ticket, self.meta())

    def frozen(self):
        return tuple((self.ticket / name).read_bytes() for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME))

    def record(self, outcome="pass", environment="physical", candidate=None, request=None, **options):
        args = ["record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", outcome,
                "--environment", environment, "--candidate-id", (candidate or self.snapshot())["candidate_id"],
                "--device", "VID=2bc5 PID=0660 SN=TEST1", "--layer", "physical", "--consumer", "camera",
                "--scenario", "steady_stream", "--baseline", "local fixture source", "--evidence", "fixture.log"]
        if request:
            args.extend(["--request-id", request])
        for key, value in options.items():
            args.extend(["--" + key.replace("_", "-"), json.dumps(value) if isinstance(value, dict) else str(value)])
        return self.call(*args)

    def applicability(self):
        self.assertTrue(callable(getattr(ctl, "verification_applicability", None)), "shared read-only applicability API missing")
        return ctl.verification_applicability(self.ticket)

    def lint(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/lint_workflow_contract.py"), str(self.ticket),
                                 "--step", "audit-verified", "--strict", "--json"], capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)

    def test_enabled_record_requires_explicit_candidate(self):
        before = self.frozen()
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                  "--layer", "physical", "--consumer", "camera", "--scenario", "steady_stream",
                  "--baseline", "fixture", "--evidence", "fixture.log", ok=False)
        self.assertEqual(before, self.frozen())

    def test_device_kind_cannot_close_enabled_build_cell_with_fresh_label(self):
        contract = {"required": True, "required_layers": ["build"], "required_consumers": ["target"],
                    "required_scenarios": ["empty-directory-build"]}
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps({"verification_contract": contract}))
        before = self.frozen()
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--layer", "build",
                  "--consumer", "target", "--scenario", "empty-directory-build", "--build-source", "fresh",
                  "--outcome", "pass", "--environment", "host", "--candidate-id", self.snapshot()["candidate_id"],
                  "--baseline", "source narrative", "--evidence", "narrative only", ok=False)
        self.assertEqual(before, self.frozen())
        self.assertFalse(self.applicability()["ok"])
        module = ctl.verification_evidence_module()
        snapshot = self.snapshot()
        forged = {"run_id": "forged-build-cell", "kind": "device_test", "layer": "build", "outcome": "pass",
                  "build_source": "fresh", "environment": "host", "candidate_id": snapshot["candidate_id"], "candidate": snapshot}
        metadata = dict(self.meta(), verification_runs=[forged])
        selection = module.eligible_latest_runs(metadata, ctl.verification_context(self.ticket))
        self.assertFalse(selection["latest"])
        self.assertEqual(selection["rejected"][0]["reason"], "build_cell_requires_actual_build_kind")
        events = ctl.read_events(self.ticket)[0] + [{"event_type": "verification_recorded", "payload": forged}]
        self.assertTrue(any("build_cell_requires_actual_build_kind" in item for item in module.event_issues(events, metadata, ctl.candidate_module())))

    def test_physical_fresh_claim_requires_real_source_and_unknown_failure_is_recordable(self):
        args = ["record-verification", "--dir", self.ticket, "--kind", "device_test", "--layer", "physical",
                "--consumer", "camera", "--scenario", "steady_stream", "--build-source", "fresh",
                "--environment", "physical", "--candidate-id", self.snapshot()["candidate_id"], "--device", "TEST1",
                "--baseline", "attempted fresh build", "--evidence", "attempt.log"]
        before = self.frozen()
        self.call(*args, "--outcome", "pass", ok=False)
        self.assertEqual(before, self.frozen())
        self.call(*args, "--outcome", "fail", "--request-id", "failed-fresh-attempt", ok=False)
        self.assertEqual(before, self.frozen())
        args[args.index("--build-source") + 1] = "unknown"
        self.call(*args, "--outcome", "fail", "--request-id", "failed-build-attempt")
        self.assertIn("latest_outcome_fail", self.applicability()["reasons"])

    def assert_reuse_cannot_cross_later_negative(self, outcome, origin_direct=None):
        old = self.snapshot()
        options = {"direct_test_json": origin_direct} if origin_direct else {}
        origin = self.record(request="origin-pass", **options)["run"]
        self.record(outcome, request="later-negative")
        self.assertIn("latest_outcome_" + outcome, self.applicability()["reasons"])
        (self.repo / "README.md").write_text("Documentation clarified\n")
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only docs changed", "scope_paths": ["source.py"],
                 "scope_hash": ctl.verification_evidence_module().scope_hash(old, ["source.py"]), "comparison_ref": "README.md"}
        before = self.frozen()
        args = ["record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                "--environment", "physical", "--candidate-id", self.snapshot()["candidate_id"],
                "--device", "VID=2bc5 PID=0660 SN=TEST1", "--layer", "physical", "--consumer", "camera",
                "--scenario", "steady_stream", "--baseline", "local fixture source", "--evidence", "fixture.log",
                "--reuse-json", json.dumps(reuse), "--request-id", "stale-pass-reuse"]
        if origin_direct:
            args.extend(["--direct-test-json", json.dumps(origin_direct)])
        self.call(*args, ok=False)
        self.assertEqual(before, self.frozen())
        self.assertFalse(self.applicability()["ok"])
        forged = dict(self.meta()["verification_runs"][0], candidate=self.snapshot(), candidate_id=self.snapshot()["candidate_id"],
                      run_id="forged-reuse", reuse=reuse)
        metadata = dict(self.meta(), verification_runs=[*self.meta()["verification_runs"], forged])
        events = ctl.read_events(self.ticket)[0] + [{"event_type": "verification_recorded", "payload": forged}]
        problems = ctl.verification_evidence_module().event_issues(events, metadata, ctl.candidate_module())
        self.assertTrue(any("reuse_origin_invalidated_by_later_" + outcome in item for item in problems))

    def test_reuse_cannot_bypass_later_same_candidate_failure(self):
        self.assert_reuse_cannot_cross_later_negative("fail")

    def test_reuse_cannot_bypass_later_same_candidate_inconclusive(self):
        self.assert_reuse_cannot_cross_later_negative("inconclusive")

    def test_ordinary_failure_after_direct_pass_blocks_current_and_reuse(self):
        self.assert_reuse_cannot_cross_later_negative("fail", {"target_built": True, "binary_executed": True,
                "binary_exit_code": 0, "test_runner_discovered": False, "ci_registered": False})

    def test_ordinary_inconclusive_after_direct_pass_blocks_current_and_reuse(self):
        self.assert_reuse_cannot_cross_later_negative("inconclusive", {"target_built": True, "binary_executed": True,
                "binary_exit_code": 0, "test_runner_discovered": False, "ci_registered": False})

    def assert_reuse_cannot_cross_related_candidate_negative(self, outcome, lineage):
        if lineage == "commit":
            (self.repo / "source.py").write_text("value = 2\n")
        old = self.snapshot()
        origin = self.record(request="lineage-origin")["run"]
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only docs changed", "scope_paths": ["source.py"],
                 "scope_hash": ctl.verification_evidence_module().scope_hash(old, ["source.py"]), "comparison_ref": "README.md"}
        if lineage == "commit":
            self.git("add", "source.py")
            self.git("commit", "-qm", "recorded final bytes")
            self.assertEqual(ctl.candidate_module().compare_candidates(old, self.snapshot())["reason"], "same_content_commit")
        else:
            (self.repo / "README.md").write_text("First documentation change\n")
            self.record(request="first-doc-reuse", reuse_json=reuse)
        self.record(outcome, request="lineage-negative")
        self.assertIn("latest_outcome_" + outcome, self.applicability()["reasons"])
        (self.repo / "README.md").write_text("Final documentation change\n")
        before = self.frozen()
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                  "--environment", "physical", "--candidate-id", self.snapshot()["candidate_id"],
                  "--device", "VID=2bc5 PID=0660 SN=TEST1", "--layer", "physical", "--consumer", "camera",
                  "--scenario", "steady_stream", "--baseline", "local fixture source", "--evidence", "fixture.log",
                  "--reuse-json", json.dumps(reuse), "--request-id", "invalidated-lineage-reuse", ok=False)
        self.assertEqual(before, self.frozen())
        self.assertFalse(self.applicability()["ok"])
        forged = dict(self.meta()["verification_runs"][0], candidate=self.snapshot(), candidate_id=self.snapshot()["candidate_id"],
                      run_id="forged-lineage-reuse", reuse=reuse)
        metadata = dict(self.meta(), verification_runs=[*self.meta()["verification_runs"], forged])
        events = ctl.read_events(self.ticket)[0] + [{"event_type": "verification_recorded", "payload": forged}]
        problems = ctl.verification_evidence_module().event_issues(events, metadata, ctl.candidate_module())
        self.assertTrue(any("reuse_origin_invalidated_by_later_" + outcome in item for item in problems))

    def test_linear_same_content_commit_failure_invalidates_dirty_origin(self):
        self.assert_reuse_cannot_cross_related_candidate_negative("fail", "commit")

    def test_linear_same_content_commit_inconclusive_invalidates_dirty_origin(self):
        self.assert_reuse_cannot_cross_related_candidate_negative("inconclusive", "commit")

    def test_doc_reuse_chain_failure_invalidates_original_origin(self):
        self.assert_reuse_cannot_cross_related_candidate_negative("fail", "docs")

    def test_doc_reuse_chain_inconclusive_invalidates_original_origin(self):
        self.assert_reuse_cannot_cross_related_candidate_negative("inconclusive", "docs")

    def test_new_real_pass_recovers_and_becomes_new_doc_reuse_origin(self):
        old = self.snapshot()
        origin = self.record(request="old-origin")["run"]
        (self.repo / "README.md").write_text("First documentation change\n")
        old_reuse = {"origin_run_id": origin["run_id"], "reason": "Only docs changed", "scope_paths": ["source.py"],
                     "scope_hash": ctl.verification_evidence_module().scope_hash(old, ["source.py"]), "comparison_ref": "README.md"}
        self.record(request="doc-transfer", reuse_json=old_reuse)
        self.record("fail", request="actual-negative")
        recovered = self.record(request="actual-recovery")["run"]
        new_source = self.snapshot()
        (self.repo / "README.md").write_text("Next documentation change\n")
        new_reuse = dict(old_reuse, origin_run_id=recovered["run_id"],
                         scope_hash=ctl.verification_evidence_module().scope_hash(new_source, ["source.py"]))
        before = self.frozen()
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                  "--environment", "physical", "--candidate-id", self.snapshot()["candidate_id"],
                  "--device", "VID=2bc5 PID=0660 SN=TEST1", "--layer", "physical", "--consumer", "camera",
                  "--scenario", "steady_stream", "--baseline", "local fixture source", "--evidence", "fixture.log",
                  "--reuse-json", json.dumps(old_reuse), "--request-id", "still-invalid-old-origin", ok=False)
        self.assertEqual(before, self.frozen())
        self.record(request="recovered-origin-reuse", reuse_json=new_reuse)
        self.assertTrue(self.applicability()["ok"])

    def test_reuse_ignores_unrelated_simulated_and_other_source_failures(self):
        old = self.snapshot()
        origin = self.record(request="origin-pass")["run"]
        self.record("fail", "simulated", request="simulated-fail")
        (self.repo / "source.py").write_text("value = 2\n")
        self.record("fail", request="different-source-fail")
        (self.repo / "source.py").write_text("value = 1\n")
        (self.repo / "README.md").write_text("Documentation clarified\n")
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only docs changed", "scope_paths": ["source.py"],
                 "scope_hash": ctl.verification_evidence_module().scope_hash(old, ["source.py"]), "comparison_ref": "README.md"}
        self.record(request="unaffected-origin-reuse", reuse_json=reuse)
        self.assertTrue(self.applicability()["ok"])

    def test_untracked_build_cell_preserves_legacy_kind_semantics(self):
        metadata = {"verification_contract": {"required": True, "required_layers": ["build"],
                    "required_consumers": ["target"], "required_scenarios": ["legacy-build"]},
                    "verification_runs": [{"kind": "device_test", "layer": "build", "consumer": "target",
                    "scenario": "legacy-build", "build_source": "fresh", "outcome": "pass", "baseline": "legacy", "evidence": "legacy.log"}]}
        module = ctl.verification_evidence_module()
        self.assertTrue(module.assess(metadata, module.context(None, metadata))["ok"])

    def test_candidate_drift_old_history_and_query_remain_read_only(self):
        old = self.snapshot()
        self.record(candidate=old, request="old-pass")
        self.assertTrue(self.applicability()["ok"])
        (self.repo / "source.py").write_text("value = 2\n")
        self.record(candidate=old, request="late-old-pass")
        before = self.frozen()
        self.assertFalse(self.applicability()["ok"])
        debt_spec = importlib.util.spec_from_file_location("debt", ROOT / "tools/verification_debt.py")
        debt = importlib.util.module_from_spec(debt_spec)
        debt_spec.loader.exec_module(debt)
        self.assertEqual(debt.analyze_ticket(self.meta(), self.ticket)["pending_units"], 1)
        self.assertEqual(before, self.frozen())

    def test_current_physical_failure_not_hidden_by_simulation(self):
        self.record("fail", request="physical-fail")
        self.record("pass", "simulated", request="simulation-pass")
        self.assertFalse(self.applicability()["ok"])
        self.assertIn("latest_outcome_fail", self.applicability()["reasons"])
        self.assertNotEqual(self.lint()[0], 0)

    def test_same_content_linear_commit_evidence_remains_applicable(self):
        (self.repo / "source.py").write_text("value = 2\n")
        self.record(request="dirty-source-pass")
        self.git("add", "source.py")
        self.git("commit", "-qm", "reviewed final bytes")
        self.assertTrue(self.applicability()["ok"])

    def test_real_env_new_risk_creates_requirements_pending_all_modes(self):
        for index, mode in enumerate(("full", "fast"), start=2):
            ticket = self.repo / f".icode_output/.icode_output_{index}"
            self.call("create", "--dir", ticket, "--ticket-id", f"risk-{index}", "--birth", "plan", "--requirement", "risk",
                      "--metadata-json", json.dumps({"mode": mode, "risk_profile": {"risk_flags": {"real_env_verification": True}, "override": True}}))
            contract = ctl.load_metadata(ticket).get("verification_contract")
            self.assertEqual(contract, {"required": True, "requirements_pending": True,
                                        "required_layers": [], "required_consumers": [], "required_scenarios": []})
            self.assertFalse(ctl.verification_applicability(ticket)["ok"])

    def test_metadata_new_risk_requires_contract_without_query_migration(self):
        self.call("metadata-update", "--dir", self.ticket, "--set-json", '{"verification_contract":null}', "--request-id", "remove-contract")
        self.call("metadata-update", "--dir", self.ticket, "--set-json", '{"risk_profile":{"triggers":["real_env_verification"]}}', "--request-id", "new-risk")
        self.assertIsInstance(self.meta().get("verification_contract"), dict, "new real-env risk must seed a pending contract")
        self.assertTrue(self.meta()["verification_contract"]["requirements_pending"])
        self.assertFalse(self.applicability()["ok"])

    def test_doc_only_reuse_and_supersedes_are_append_only(self):
        origin = self.record(request="origin")["run"]
        before_origin = copy.deepcopy(self.meta()["verification_runs"][0])
        old = origin["candidate"] if "candidate" in origin else self.meta()["verification_runs"][0]["candidate"]
        scope = [row for row in old["manifest"] if row["path"] == "source.py"]
        scope_hash = hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        (self.repo / "README.md").write_text("Documentation clarified\n")
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only documentation clarified",
                 "scope_paths": ["source.py"], "scope_hash": scope_hash, "comparison_ref": "README.md"}
        self.record(request="reused", reuse_json=reuse, supersedes_run_id=origin["run_id"])
        self.assertEqual(before_origin, self.meta()["verification_runs"][0])
        self.assertTrue(self.applicability()["ok"])
        self.record(request="reused", reuse_json=reuse, supersedes_run_id=origin["run_id"])
        self.assertEqual(len(self.meta()["verification_runs"]), 2)
        (self.repo / "source.py").write_text("value = 9\n")
        before = self.frozen()
        args = ["record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                "--candidate-id", self.snapshot()["candidate_id"], "--environment", "physical", "--layer", "physical",
                "--consumer", "camera", "--scenario", "steady_stream", "--device", "TEST1", "--baseline", "fixture", "--evidence", "fixture.log",
                "--reuse-json", json.dumps(reuse)]
        self.call(*args, ok=False)
        self.assertEqual(before, self.frozen())

    def start_build(self, request="build-start"):
        build = self.repo / "build"
        build.mkdir(exist_ok=True)
        return self.call("build-start", "--dir", self.ticket, "--build-dir", build,
                         "--candidate-id", self.snapshot()["candidate_id"], "--parameters-json",
                         '{"configure_command":["cmake","-S",".","-B","build"],"build_command":["cmake","--build","build"]}',
                         "--request-id", request)

    def budget_capture(self, limits, request="artifact-limits"):
        return self.call("candidate", "--dir", self.ticket, "--phase", "capture", "--limits-json", json.dumps(limits),
                         "--request-id", request)

    def binary_provenance(self, start, artifacts):
        return {"start_event_id": start["event_id"], "source_candidate_id": self.snapshot()["candidate_id"],
                "configure_command": ["cmake", "-S", ".", "-B", "build"], "build_command": ["cmake", "--build", "build"],
                "compiler": "fixture", "architecture": "host", "artifacts": artifacts, "evidence_refs": ["build.log"]}

    def record_artifacts(self, provenance, request="artifact-build", ok=True, extra=()):
        artifacts = provenance["artifacts"]
        identity = "sha256:" + artifacts[0]["sha256"] if len(artifacts) == 1 else "fixture artifact inventory"
        return self.call("record-verification", "--dir", self.ticket, "--kind", "build", "--outcome", "pass", "--environment", "host",
                         "--candidate-id", self.snapshot()["candidate_id"], "--build-source", "fresh", "--artifact-identity", identity,
                         "--baseline", "fixture source", "--evidence", "build.log", "--build-provenance-json", json.dumps(provenance),
                         "--request-id", request, *extra, ok=ok)

    def assert_free_candidate_note(self, payload, request):
        self.call("event", "--dir", self.ticket, "--type", "external_note", "--payload", json.dumps(payload), "--request-id", request)
        self.assertFalse(ctl.verify_event_chain(self.ticket, self.meta())[1])
        before = self.frozen()
        self.assertTrue(self.call("event", "--dir", self.ticket, "--type", "external_note", "--payload", json.dumps(payload),
                                 "--request-id", request)["already_applied"])
        self.assertEqual(before, self.frozen())
        self.call("metadata-update", "--dir", self.ticket, "--set-json", '{"extensions":{"note":"still writable"}}')

    def test_free_note_candidate_input_name_does_not_poison_control_chain(self):
        self.assert_free_candidate_note({"candidate_input": None, "candidate_control": "free", "build_start_control": "free"}, "free-input")

    def test_free_note_candidate_receipt_name_does_not_poison_control_chain(self):
        self.assert_free_candidate_note({"candidate_receipt": {"note": "free"}, "candidate_tracking": "free"}, "free-receipt")

    def test_controlled_candidate_fields_still_reject_forged_shapes(self):
        cases = (("step_started", {"candidate_input": None}, "candidate_input_event_shape"),
                 ("step_finished", {"candidate_receipt": None}, "candidate_receipt_event_shape"),
                 ("metadata_updated", {"candidate_control": None, "set": {}, "append": {}}, "candidate_control_event_shape"))
        before = self.frozen()
        for kind, payload, issue in cases:
            with self.subTest(kind=kind):
                self.call("event", "--dir", self.ticket, "--type", kind, "--payload", json.dumps(payload), ok=False)
                self.assertEqual(before, self.frozen())
                events = copy.deepcopy(ctl.read_events(self.ticket)[0])
                events.append({"event_type": kind, "event_id": "forged", "actor": "icode", "request_id": "forged", "payload": payload})
                self.assertIn(issue, ctl.candidate_event_issues(events, self.meta()))

    def test_reproducible_compiled_artifact_new_ctime_old_mtime_is_fresh(self):
        source = self.repo / "hello.c"
        source.write_text("int main(void) { return 0; }\n")
        build = self.repo / "build"
        build.mkdir()
        parameters = {"configure_command": ["cc", "--version"], "build_command": ["cc", "hello.c", "-o", "build/hello"]}
        candidate = self.snapshot()["candidate_id"]
        start = self.call("build-start", "--dir", self.ticket, "--build-dir", build, "--candidate-id", candidate,
                          "--parameters-json", json.dumps(parameters), "--request-id", "compile-start")
        self.assertEqual(subprocess.run(["cc", "--version"], capture_output=True).returncode, 0)
        compiled = subprocess.run(["cc", str(source), "-o", str(build / "hello")], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertEqual(subprocess.run([str(build / "hello")]).returncode, 0)
        os.utime(build / "hello", (1, 1))
        receipt = self.meta()["extensions"]["verification"]["build_starts"][-1]
        self.assertLess((build / "hello").stat().st_mtime_ns, receipt["observed_ns"])
        self.assertGreaterEqual((build / "hello").stat().st_ctime_ns, receipt["observed_ns"])
        sha = hashlib.sha256((build / "hello").read_bytes()).hexdigest()
        provenance = dict(self.binary_provenance(start, [{"path": "build/hello", "sha256": sha}]), **parameters, compiler="cc")
        self.record_artifacts(provenance, request="reproducible-artifact")

    def test_artifact_byte_budget_rejects_before_append_and_can_be_explicitly_raised(self):
        self.budget_capture({"max_bytes": 100})
        start = self.start_build()
        payload = b"x" * (256 * 1024)
        (self.repo / "build/app").write_bytes(payload)
        provenance = self.binary_provenance(start, [{"path": "build/app", "sha256": hashlib.sha256(payload).hexdigest()}])
        before = self.frozen()
        self.record_artifacts(provenance, ok=False)
        self.assertEqual(before, self.frozen())
        self.call("event", "--dir", self.ticket, "--type", "external_note", "--payload", '{"after_limit":"lock released"}')
        self.budget_capture({"max_bytes": 1024 * 1024}, request="raised-artifact-limits")
        self.record_artifacts(provenance, request="larger-artifact-budget")

    def test_artifact_byte_budget_is_cumulative_across_artifacts(self):
        self.budget_capture({"max_bytes": 100})
        start = self.start_build()
        artifacts = []
        for index in range(2):
            (self.repo / f"build/app{index}").write_bytes(b"x" * 60)
            artifacts.append({"path": f"build/app{index}", "sha256": hashlib.sha256(b"x" * 60).hexdigest()})
        before = self.frozen()
        self.record_artifacts(self.binary_provenance(start, artifacts), ok=False)
        self.assertEqual(before, self.frozen())

    def test_artifact_file_count_budget_is_cumulative_in_one_run(self):
        self.budget_capture({"max_files": 4})
        start = self.start_build()
        artifacts = []
        for index in range(5):
            (self.repo / f"build/app{index}").write_bytes(b"x")
            artifacts.append({"path": f"build/app{index}", "sha256": hashlib.sha256(b"x").hexdigest()})
        before = self.frozen()
        self.record_artifacts(self.binary_provenance(start, artifacts), ok=False)
        self.assertEqual(before, self.frozen())

    def test_artifact_byte_budget_is_shared_between_local_provenance_fields(self):
        self.budget_capture({"max_bytes": 100})
        start = self.start_build()
        (self.repo / "build/app").write_bytes(b"x" * 60)
        artifact = {"path": "build/app", "sha256": hashlib.sha256(b"x" * 60).hexdigest()}
        provenance = self.binary_provenance(start, [artifact])
        origin = self.record_artifacts(provenance, request="budget-origin")["run"]
        deployed = {"source_candidate_id": self.snapshot()["candidate_id"], "origin_build_run_id": origin["run_id"],
                    "artifacts": [artifact], "evidence_refs": ["deploy.log"]}
        before = self.frozen()
        self.record_artifacts(provenance, request="combined-provenance-budget", extra=("--deploy-provenance-json", json.dumps(deployed)), ok=False)
        self.assertEqual(before, self.frozen())

    def test_artifact_timeout_rejects_real_writer_and_releases_lock(self):
        self.budget_capture({"timeout_seconds": 1})
        start = self.start_build()
        (self.repo / "build/app").write_bytes(b"binary")
        provenance = self.binary_provenance(start, [{"path": "build/app", "sha256": hashlib.sha256(b"binary").hexdigest()}])
        arguments = ctl.build_parser().parse_args(["record-verification", "--dir", str(self.ticket), "--kind", "build", "--outcome", "pass",
                    "--environment", "host", "--candidate-id", self.snapshot()["candidate_id"], "--build-source", "fresh",
                    "--artifact-identity", "sha256:" + hashlib.sha256(b"binary").hexdigest(), "--baseline", "fixture", "--evidence", "build.log",
                    "--build-provenance-json", json.dumps(provenance), "--request-id", "timed-artifact"])
        module = ctl.verification_evidence_module()
        # Expire after the first actual stream read, rather than only at
        # preflight, to exercise the per-block timeout guard under the lock.
        ticks = iter([0, 0, 0, 2])
        clock = SimpleNamespace(monotonic=lambda: next(ticks, 2))
        before = self.frozen()
        with mock.patch.object(module, "time", clock, create=True):
            with self.assertRaises(ctl.ControlError) as failure:
                arguments.func(arguments)
        self.assertIn("artifact", failure.exception.message)
        self.assertEqual(before, self.frozen())
        self.call("event", "--dir", self.ticket, "--type", "external_note", "--payload", '{"after_timeout":"lock released"}')

    def test_fresh_old_ctime_and_changed_directory_identity_still_rejected(self):
        module = ctl.verification_evidence_module()
        artifact = self.repo / "build/app"
        artifact.parent.mkdir()
        artifact.write_bytes(b"binary")
        facts = [{"path": "build/app", "sha256": hashlib.sha256(b"binary").hexdigest()}]
        observed_ns = artifact.stat().st_ctime_ns + 1
        with self.assertRaisesRegex(module.EvidenceError, "predates"):
            module.artifact_facts(facts, self.repo, build_dir=artifact.parent, observed_ns=observed_ns)
        artifact.unlink()
        start = self.start_build()
        (self.repo / "build").rename(self.repo / "build.old")
        (self.repo / "build").mkdir()
        artifact.write_bytes(b"binary")
        provenance = self.binary_provenance(start, facts)
        before = self.frozen()
        result = self.record_artifacts(provenance, request="changed-build-directory", ok=False)
        self.assertIn("directory identity changed", result["error"])
        self.assertEqual(before, self.frozen())

    def build_record(self, provenance, source="fresh", ok=True, request="built"):
        return self.call("record-verification", "--dir", self.ticket, "--kind", "build", "--layer", "build", "--outcome", "pass",
                         "--environment", "host", "--candidate-id", self.snapshot()["candidate_id"], "--build-source", source,
                         "--artifact-identity", "sha256:" + hashlib.sha256(b"binary").hexdigest(), "--baseline", "fixture source",
                         "--evidence", "build.log", "--build-provenance-json", json.dumps(provenance), "--request-id", request, ok=ok)

    def test_fresh_build_receipt_real_sha_and_protected_extension(self):
        start = self.start_build()
        artifact = self.repo / "build/app"
        artifact.write_bytes(b"binary")
        provenance = {"start_event_id": start["event_id"], "source_candidate_id": self.snapshot()["candidate_id"],
                      "configure_command": ["cmake", "-S", ".", "-B", "build"], "build_command": ["cmake", "--build", "build"],
                      "compiler": "fixture-compiler", "architecture": "host", "artifacts": [{"path": "build/app", "sha256": hashlib.sha256(b"binary").hexdigest()}],
                      "evidence_refs": ["build.log"]}
        self.build_record(provenance)
        self.assertFalse(ctl.verify_event_chain(self.ticket, self.meta())[1])
        before = self.frozen()
        self.call("metadata-update", "--dir", self.ticket, "--set-json", '{"extensions":{"verification":{"build_starts":[]}}}', ok=False)
        self.assertEqual(before, self.frozen())
        bad = dict(provenance, start_event_id="made-up")
        self.build_record(bad, ok=False, request="bad-start")
        artifact.write_bytes(b"tampered")
        self.build_record(provenance, ok=False, request="bad-artifact")

    def test_fresh_rejects_nonempty_and_source_drift(self):
        build = self.repo / "build"
        build.mkdir()
        (build / "old").write_text("existing")
        start = self.start_build()
        (build / "app").write_bytes(b"binary")
        provenance = {"start_event_id": start["event_id"], "source_candidate_id": self.snapshot()["candidate_id"],
                      "configure_command": ["cmake", "-S", ".", "-B", "build"], "build_command": ["cmake", "--build", "build"],
                      "compiler": "fixture", "architecture": "host", "artifacts": [{"path": "build/app", "sha256": hashlib.sha256(b"binary").hexdigest()}], "evidence_refs": ["build.log"]}
        self.build_record(provenance, ok=False)
        self.build_record(provenance, source="existing", request="incremental")
        (self.repo / "source.py").write_text("value = 3\n")
        self.build_record(provenance, ok=False, request="drift")

    def test_direct_failure_not_overridden_by_runner_pass(self):
        direct = {"target_built": True, "binary_executed": True, "binary_exit_code": 2,
                  "test_runner_discovered": False, "ci_registered": False}
        self.record("fail", request="direct-fail", direct_test_json=direct)
        runner = {"target_built": True, "binary_executed": False, "binary_exit_code": None,
                  "test_runner_discovered": True, "ci_registered": True}
        self.record("pass", request="runner-pass", direct_test_json=runner)
        self.assertFalse(self.applicability()["ok"])

    def test_cookie_candidate_paths_are_metadata_and_free_evidence_is_checked(self):
        lint_spec = importlib.util.spec_from_file_location("workflow", ROOT / "tools/lint_workflow_contract.py")
        lint = importlib.util.module_from_spec(lint_spec)
        lint_spec.loader.exec_module(lint)
        self.assertFalse(lint.scan_sensitive(self.snapshot()))
        self.assertTrue(lint.scan_sensitive({"candidate": self.snapshot(), "evidence": "api_key=invalid-test-value"}))
        self.record(request="cookie-path")
        self.assertEqual(self.lint()[0], 0)
        metadata = self.meta()
        metadata["verification_runs"][0]["evidence"] = "api_key=invalid-test-value"
        (self.ticket / ctl.METADATA_NAME).write_text(json.dumps(metadata))
        self.assertNotEqual(self.lint()[0], 0)
        self.assertGreater(self.lint()[1]["sensitive_data"], 0)

    def test_explicit_external_and_ticket_side_build_directories(self):
        for index, directory in enumerate((Path(self.temp.name) / "external-build", self.ticket / "build/run1")):
            directory.mkdir(parents=True)
            start = self.call("build-start", "--dir", self.ticket, "--build-dir", directory,
                              "--candidate-id", self.snapshot()["candidate_id"], "--parameters-json",
                              '{"configure_command":["cmake","-S","."],"build_command":["cmake","--build","."]}',
                              "--request-id", f"external-{index}")
            artifact = directory / "app"
            artifact.write_bytes(b"binary")
            provenance = {"start_event_id": start["event_id"], "source_candidate_id": self.snapshot()["candidate_id"],
                          "configure_command": ["cmake", "-S", "."], "build_command": ["cmake", "--build", "."],
                          "compiler": "fixture", "architecture": "host", "artifacts": [{"path": str(artifact), "sha256": hashlib.sha256(b"binary").hexdigest()}],
                          "evidence_refs": ["build.log"]}
            self.build_record(provenance, request=f"external-built-{index}")

    def test_reuse_cannot_change_device_identity(self):
        origin = self.record(request="device-origin")["run"]
        snapshot = self.meta()["verification_runs"][0]["candidate"]
        module = ctl.verification_evidence_module()
        reuse = {"origin_run_id": origin["run_id"], "reason": "documentation only", "scope_paths": ["source.py"],
                 "scope_hash": module.scope_hash(snapshot, ["source.py"]), "comparison_ref": "README.md"}
        payload = copy.deepcopy(self.meta()["verification_runs"][0])
        payload.update(device="different-SN", reuse=reuse)
        with self.assertRaises(module.EvidenceError):
            module.validate_reuse(payload, self.meta()["verification_runs"], ctl.candidate_module())

    def test_candidate_capture_failure_never_reuses_historical_pass(self):
        self.record(request="prior-pass")
        # Git's untracked listing omits FIFOs; replace a tracked source path so
        # the candidate scanner must inspect and reject the special file.
        (self.repo / "source.py").unlink()
        import os
        os.mkfifo(self.repo / "source.py")
        before = self.frozen()
        self.assertFalse(self.applicability()["ok"])
        self.assertNotEqual(self.lint()[0], 0)
        self.assertEqual(before, self.frozen())

    def test_failed_origin_forged_scope_and_unknown_supersedes_rejected(self):
        origin = self.record("fail", request="failed-origin")["run"]
        module = ctl.verification_evidence_module()
        candidate = self.snapshot()
        payload = copy.deepcopy(self.meta()["verification_runs"][0])
        payload.update(outcome="pass", reuse={"origin_run_id": origin["run_id"], "reason": "docs", "scope_paths": ["source.py"],
                                           "scope_hash": module.scope_hash(candidate, ["source.py"]), "comparison_ref": "README.md"})
        with self.assertRaises(module.EvidenceError):
            module.validate_reuse(payload, self.meta()["verification_runs"], ctl.candidate_module())
        successful = self.record(request="successful-origin")["run"]
        payload["reuse"].update(origin_run_id=successful["run_id"], scope_hash="0" * 64)
        with self.assertRaises(module.EvidenceError):
            module.validate_reuse(payload, self.meta()["verification_runs"], ctl.candidate_module())
        with self.assertRaises(module.EvidenceError):
            module.validate_supersedes(dict(payload, supersedes_run_id="missing"), self.meta()["verification_runs"])

    def test_same_request_different_candidate_conflicts_and_old_pass_cannot_hide_failure(self):
        old = self.snapshot()
        self.record(request="candidate-key")
        (self.repo / "source.py").write_text("value = 4\n")
        self.record("fail", request="current-fail")
        self.record(candidate=old, request="late-history")
        before = self.frozen()
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass",
                  "--candidate-id", self.snapshot()["candidate_id"], "--environment", "physical", "--layer", "physical", "--consumer", "camera",
                  "--scenario", "steady_stream", "--device", "VID=2bc5 PID=0660 SN=TEST1", "--baseline", "local fixture source", "--evidence", "fixture.log",
                  "--request-id", "candidate-key", ok=False)
        self.assertIn("latest_outcome_fail", self.applicability()["reasons"])
        self.assertEqual(before, self.frozen())

    def test_fresh_old_candidate_cannot_hide_post_start_drift(self):
        old = self.snapshot()
        start = self.start_build()
        (self.repo / "build/app").write_bytes(b"binary")
        (self.repo / "source.py").write_text("value = 8\n")
        provenance = {"start_event_id": start["event_id"], "source_candidate_id": old["candidate_id"],
                      "configure_command": ["cmake", "-S", ".", "-B", "build"], "build_command": ["cmake", "--build", "build"],
                      "compiler": "fixture", "architecture": "host", "artifacts": [{"path": "build/app", "sha256": hashlib.sha256(b"binary").hexdigest()}],
                      "evidence_refs": ["build.log"]}
        self.call("record-verification", "--dir", self.ticket, "--kind", "build", "--outcome", "pass", "--environment", "host",
                  "--candidate-id", old["candidate_id"], "--build-source", "fresh", "--artifact-identity", "sha256:" + hashlib.sha256(b"binary").hexdigest(),
                  "--baseline", "fixture source", "--evidence", "build.log", "--build-provenance-json", json.dumps(provenance), ok=False)

    def test_risk_seed_metadata_update_is_idempotent(self):
        self.call("metadata-update", "--dir", self.ticket, "--set-json", '{"verification_contract":null}')
        args = ["metadata-update", "--dir", self.ticket, "--set-json", '{"risk_profile":{"flags":{"real_env_verification":true}}}', "--request-id", "seed-risk"]
        self.call(*args)
        before = self.frozen()
        self.assertTrue(self.call(*args)["already_applied"])
        self.assertEqual(before, self.frozen())

    def test_doc_reuse_retains_actual_build_and_runtime_origin(self):
        old = self.snapshot()
        start = self.start_build()
        (self.repo / "build/app").write_bytes(b"binary")
        sha = hashlib.sha256(b"binary").hexdigest()
        build = {"start_event_id": start["event_id"], "source_candidate_id": old["candidate_id"],
                 "configure_command": ["cmake", "-S", ".", "-B", "build"], "build_command": ["cmake", "--build", "build"],
                 "compiler": "fixture", "architecture": "host", "artifacts": [{"path": "build/app", "sha256": sha}], "evidence_refs": ["build.log"]}
        built = self.build_record(build)["run"]
        runtime = {"source_candidate_id": old["candidate_id"], "origin_build_run_id": built["run_id"],
                   "artifacts": build["artifacts"], "evidence_refs": ["runtime.log"]}
        origin = self.record(request="runtime-origin", build_source="fresh", artifact_identity="sha256:" + sha, runtime_provenance_json=runtime)["run"]
        (self.repo / "README.md").write_text("Docs improved without changing binary sources\n")
        reuse = {"origin_run_id": origin["run_id"], "reason": "Only docs changed", "scope_paths": ["source.py"],
                 "scope_hash": ctl.verification_evidence_module().scope_hash(old, ["source.py"]), "comparison_ref": "README.md"}
        self.record(request="runtime-reuse", build_source="fresh", artifact_identity="sha256:" + sha, runtime_provenance_json=runtime, reuse_json=reuse)
        self.assertEqual(self.meta()["verification_runs"][-1]["runtime_provenance"], runtime)
        self.assertTrue(self.applicability()["ok"])

    def test_old_untracked_real_env_declaration_is_not_query_migrated(self):
        module = ctl.verification_evidence_module()
        old = {"schema_version": 3, "risk_profile": {"triggers": ["real_env_verification"]}}
        self.assertTrue(module.assess(old, module.context(None, old))["ok"])
        self.assertNotIn("verification_contract", old)

    def test_completed_legacy_risk_only_query_lint_debt_preserve_bytes(self):
        old_dir = self.repo / ".icode_output/.icode_output_99"
        old_dir.mkdir()
        old = {"schema_version": 3, "ticket_id": "legacy-risk", "status": "completed",
               "delivery_verdict": "verified", "risk_profile": {"risk_flags": {"real_env_verification": True}}}
        (old_dir / ctl.METADATA_NAME).write_text(json.dumps(old))
        (old_dir / ctl.EVENTS_NAME).write_text("")
        before = tuple(path.read_bytes() for path in (old_dir / ctl.METADATA_NAME, old_dir / ctl.EVENTS_NAME))
        query = ctl.verification_applicability(old_dir)
        self.assertTrue(query["ok"])
        self.assertEqual(query["state"], "legacy_untracked")
        debt_spec = importlib.util.spec_from_file_location("legacy_debt", ROOT / "tools/verification_debt.py")
        debt = importlib.util.module_from_spec(debt_spec)
        debt_spec.loader.exec_module(debt)
        result = debt.analyze_ticket(ctl.load_metadata(old_dir), old_dir)
        self.assertEqual(result["tracking_status"], "legacy_untracked")
        self.assertFalse(result["ticket_blocked"])
        lint = subprocess.run([sys.executable, "-B", str(ROOT / "tools/lint_workflow_contract.py"), str(old_dir),
                               "--step", "audit-verified", "--strict", "--json"], capture_output=True, text=True)
        report = json.loads(lint.stdout)
        gate = next(item for item in report["gates"] if item["gate"] == "delivery_evidence")
        self.assertEqual(gate["status"], "pass")
        self.assertEqual(before, tuple(path.read_bytes() for path in (old_dir / ctl.METADATA_NAME, old_dir / ctl.EVENTS_NAME)))

    def test_build_start_rejects_empty_request_before_writing(self):
        (self.repo / "build").mkdir()
        before = self.frozen()
        self.call("build-start", "--dir", self.ticket, "--build-dir", self.repo / "build",
                  "--candidate-id", self.snapshot()["candidate_id"], "--parameters-json",
                  '{"configure_command":["cmake","-S","."],"build_command":["cmake","--build","."]}',
                  "--request-id", "", ok=False)
        self.assertEqual(before, self.frozen())

    def test_readonly_assessment_rejects_malformed_metric_contract(self):
        module = ctl.verification_evidence_module()
        metric = {"name": "rate", "unit": "fps", "operator": "gte", "value": 30,
                  "layer": "physical", "consumer": "camera", "scenario": "steady_stream"}
        for settings in ({"required_metrics": [None]}, {"required_metrics": [dict(metric, operator="invented")]},
                         {"profile": "invented"}, {"required_metrics": "invented"}):
            old = {"verification_contract": dict(self.contract, **settings),
                   "verification_runs": [{"outcome": "pass", "baseline": "fixture", "evidence": "fixture.log",
                                           "layer": "physical", "consumer": "camera", "scenario": "steady_stream",
                                           "metrics": {"rate": 30}}]}
            self.assertFalse(module.assess(old, module.context(None, old))["ok"])

    def test_debt_plan_candidate_parameters(self):
        debt_spec = importlib.util.spec_from_file_location("candidate_plan_debt", ROOT / "tools/verification_debt.py")
        debt = importlib.util.module_from_spec(debt_spec)
        debt_spec.loader.exec_module(debt)
        plan = debt.build_plan(self.ticket, None)
        command = plan["actions"][0]["record_command"]
        self.assertIn("--candidate-id", command)
        self.assertEqual(command[command.index("--candidate-id") + 1], self.snapshot()["candidate_id"])
        self.assertIn("--environment", command)

    def test_candidate_known_inspection_keys_keep_free_text_scanning(self):
        lint_spec = importlib.util.spec_from_file_location("candidate_scope_lint", ROOT / "tools/lint_workflow_contract.py")
        lint = importlib.util.module_from_spec(lint_spec)
        lint_spec.loader.exec_module(lint)
        snapshot = ctl.candidate_module().capture_candidate(self.repo, base=self.git("rev-parse", "HEAD"),
                   inspection_scope={"scopes": [], "related": [], "baselines": {"source.py": "api_key=invalid-test"}})
        self.assertTrue(ctl.candidate_module().snapshot_identity_ok(snapshot))
        self.assertTrue(lint.scan_sensitive(snapshot), "known inspection keys must not exempt free evidence text")

    def test_enabled_capture_failure_blocks_debt_linter_without_required_contract(self):
        import os
        debt_spec = importlib.util.spec_from_file_location("unrequired_debt", ROOT / "tools/verification_debt.py")
        debt = importlib.util.module_from_spec(debt_spec)
        debt_spec.loader.exec_module(debt)
        self.call("metadata-update", "--dir", self.ticket, "--set-json", '{"verification_contract":null}')
        (self.repo / "source.py").unlink()
        os.mkfifo(self.repo / "source.py")
        before = self.frozen()
        self.assertFalse(self.applicability()["ok"])
        with self.subTest(path="debt"):
            self.assertTrue(debt.analyze_ticket(self.meta(), self.ticket)["ticket_blocked"])
        with self.subTest(path="linter"):
            self.assertNotEqual(self.lint()[0], 0)
        self.assertEqual(before, self.frozen())

    def test_event_mirror_rejects_provenance_source_forgery_and_missing_snapshot(self):
        self.record(request="event-origin")
        events = ctl.read_events(self.ticket)[0]
        metadata = self.meta()
        bad_events, bad_meta = copy.deepcopy(events), copy.deepcopy(metadata)
        del bad_events[-1]["payload"]["candidate"]
        del bad_meta["verification_runs"][-1]["candidate"]
        self.assertTrue(ctl.verification_evidence_module().event_issues(bad_events, bad_meta, ctl.candidate_module()))

        bad_events, bad_meta = copy.deepcopy(events), copy.deepcopy(metadata)
        forged = {"source_candidate_id": "0" * 64, "origin_build_run_id": "made-up",
                  "artifacts": [{"path": "build/app", "sha256": "0" * 64}], "evidence_refs": ["unbacked"]}
        bad_events[-1]["payload"]["runtime_provenance"] = forged
        bad_meta["verification_runs"][-1]["runtime_provenance"] = forged
        self.assertTrue(ctl.verification_evidence_module().event_issues(bad_events, bad_meta, ctl.candidate_module()))

    def test_existing_build_cannot_claim_pass_without_structured_artifact_provenance(self):
        self.call("record-verification", "--dir", self.ticket, "--kind", "build", "--outcome", "pass", "--build-source", "existing",
                  "--environment", "host", "--candidate-id", self.snapshot()["candidate_id"], "--artifact-identity", "sha256:invented",
                  "--baseline", "fixture", "--evidence", "not-machine-checked", ok=False)

    def test_external_build_deploy_copy_remote_runtime_path_hash_binding(self):
        build_dir = Path(self.temp.name) / "outside-build"
        build_dir.mkdir()
        candidate = self.snapshot()["candidate_id"]
        start = self.call("build-start", "--dir", self.ticket, "--build-dir", build_dir, "--candidate-id", candidate,
                          "--parameters-json", '{"configure_command":["cmake","-S","."],"build_command":["cmake","--build","."]}', "--request-id", "outside-start")
        binary = build_dir / "app"
        binary.write_bytes(b"binary")
        sha = hashlib.sha256(b"binary").hexdigest()
        provenance = {"start_event_id": start["event_id"], "source_candidate_id": candidate,
                      "configure_command": ["cmake", "-S", "."], "build_command": ["cmake", "--build", "."],
                      "compiler": "fixture", "architecture": "host", "artifacts": [{"path": str(binary), "sha256": sha}], "evidence_refs": ["build.log"]}
        origin = self.build_record(provenance, request="outside-built")["run"]
        copied = Path(self.temp.name) / "deploy-copy/app"
        copied.parent.mkdir()
        copied.write_bytes(binary.read_bytes())
        deployed = {"source_candidate_id": candidate, "origin_build_run_id": origin["run_id"],
                    "artifacts": [{"path": str(copied), "sha256": sha}], "evidence_refs": ["deploy.log"]}
        self.call("record-verification", "--dir", self.ticket, "--kind", "deploy", "--outcome", "pass", "--candidate-id", candidate,
                  "--environment", "physical", "--artifact-identity", "sha256:" + sha, "--baseline", "fixture", "--evidence", "deploy.log",
                  "--deploy-provenance-json", json.dumps(deployed), "--request-id", "copied")
        runtime = dict(deployed, artifacts=[{"path": "/opt/device/bin/app", "sha256": sha}], evidence_refs=["remote-version.log"])
        self.record(request="remote-runtime", artifact_identity="sha256:" + sha, runtime_provenance_json=runtime)
        self.assertTrue(self.applicability()["ok"])
        runtime["artifacts"][0]["sha256"] = "0" * 64
        self.call("record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", "pass", "--candidate-id", candidate,
                  "--environment", "physical", "--artifact-identity", "sha256:" + "0" * 64, "--baseline", "fixture", "--evidence", "bad-runtime.log",
                  "--runtime-provenance-json", json.dumps(runtime), ok=False)


if __name__ == "__main__":
    unittest.main()
