"""Stage contracts use temporary ordinary tickets and no Git commits/devices."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("stage_test_" + name, ROOT / "tools" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StageAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.ctl = load("icode_control")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.coord = self.project / ".icode_output" / ".icode_output_1"
        self.first = self.project / ".icode_output" / ".icode_output_2"
        self.second = self.project / ".icode_output" / ".icode_output_3"
        for path in (self.coord, self.first, self.second):
            self.cli("create", "--dir", str(path), "--ticket-id", "ticket" + path.name,
                     "--requirement", "Stage fixture", "--birth", "plan")
        self.plan = {"schema_version": 1, "program_id": "program", "project_root": str(self.project),
                     "authorized_actions": ["code", "verify", "build", "serial_exec"], "stages": [
                         {"id": "one", "title": "First", "required_actions": ["verify", "serial_exec"]},
                         {"id": "two", "title": "Second", "required_actions": ["code", "verify", "build"]}]}

    def cli(self, *argv):
        args = self.ctl.build_parser().parse_args(argv)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            result = args.func(args)
        self.assertEqual(result, 0, stream.getvalue())
        return json.loads(stream.getvalue())

    def enable(self):
        self.ctl.register_stage_program(self.coord, self.plan, request_id="register")
        self.ctl.bind_stage(self.coord, "one", self.first, request_id="bind-one")
        self.ctl.bind_stage(self.coord, "two", self.second, request_id="bind-two")

    def provider(self):
        """Closed plumbing facts for real source bytes; never calls Git."""
        api = self.ctl.candidate_module()
        project = self.project
        source = project / "source.py"
        source.write_bytes(b"value = 1\n")
        objects = project / "objects"
        objects.mkdir(exist_ok=True)
        info, git_info = project.stat(), objects.stat()
        raw = source.read_bytes()
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        facts = {"identity": {"common_git_dir": str(objects), "checkout_realpath": str(project), "object_format": "sha1",
                              "common_git_device": git_info.st_dev, "common_git_inode": git_info.st_ino,
                              "checkout_device": info.st_dev, "checkout_inode": info.st_ino},
                 "base": "1" * 40, "head": "1" * 40, "head_tree": "2" * 40}
        class Provider(api.CandidateGitProvider):
            workspace = project
            def assert_context(self):
                return None
            def query(self, request, *, limit):
                op = request.operation.value
                if op == "facts": data = json.dumps(facts).encode()
                elif op == "tree": data = ("100644 blob " + oid + "\tsource.py\0").encode()
                elif op == "index": data = ("100644 " + oid + " 0\tsource.py\0").encode()
                elif op == "others": data = b""
                elif op == "blob": data = str(len(raw)).encode() if request.kind == "size" else raw
                else: raise AssertionError("unexpected provider query: " + op)
                return api.CandidateReadResult(data, 0, False)
        return Provider()

    def ready(self, *, method="machine", visual=False, expected_mode=None):
        contract = {"required": True, "required_layers": ["host"], "required_consumers": ["tool"],
                    "required_scenarios": ["steady"], "evidence_contract": {"schema_version": 1,
                        "required_items": [{"id": "raw", "kind": "log", "layer": "host", "consumer": "tool",
                                            "scenario": "steady", "min_count": 1, "complete": True, "method": method}],
                        "required_identity": [], "visual": None}}
        if expected_mode is not None:
            contract["evidence_contract"]["required_items"][0]["expected_mode"] = expected_mode
        if visual:
            contract["evidence_contract"]["visual"] = {"required": True, "min_frames": 5,
                "sample_fractions": [0, .25, .5, .75, 1], "tolerance_fraction": .05, "require_display": False}
            contract["evidence_contract"]["required_items"].append({"id": "display", "kind": "frame_set", "layer": "host",
                "consumer": "tool", "scenario": "steady", "min_count": 5, "complete": True, "method": "ai_review"})
        self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": contract}))
        self.enable()
        provider = self.provider()
        access = {"git_provider": provider, "require_git_provider": True}
        args = self.ctl.build_parser().parse_args(["candidate", "--dir", str(self.first), "--phase", "capture",
            "--base", "1" * 40, "--request-id", "capture"])
        with contextlib.redirect_stdout(io.StringIO()): self.ctl.cmd_candidate(args, **access)
        candidate = self.ctl.candidate_snapshot(self.first, self.ctl.load_metadata(self.first), **access)
        raw = b"complete observed original data\n"
        (self.first / "raw.log").write_bytes(raw)
        item = {"id": "raw-1", "requirement_id": "raw", "kind": "log", "path": "raw.log",
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "complete": True, "source_ids": [], "metadata": {}}
        run = {"run_id": "run-one", "candidate_id": candidate["candidate_id"], "layer": "host", "consumer": "tool", "scenario": "steady"}
        bundle_api = self.ctl.verification_evidence_module().bundle_module()
        bundle = {"schema_version": 1, "ticket_id": "ticket" + self.first.name, "stage_id": "one", "run_id": run["run_id"],
                  "candidate_id": candidate["candidate_id"], "contract_sha256": bundle_api.contract_digest(contract), "baseline_ref": None,
                  "device": {"device_id": "unknown", "boot_id": "unknown"},
                  "window": {"host_start": "2026-10-15T00:00:00Z", "host_end": "2026-10-15T00:00:10Z",
                             "monotonic_start": 0, "monotonic_end": 10}, "items": [item], "measurements": [], "visual": None}
        return access, contract, item, run, bundle

    def record(self, access, run, bundle):
        (self.first / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        args = self.ctl.build_parser().parse_args(["record-verification", "--dir", str(self.first), "--kind", "device_test",
            "--outcome", "pass", "--environment", "host", "--candidate-id", run["candidate_id"], "--layer", "host",
            "--consumer", "tool", "--scenario", "steady", "--baseline", "fixture source", "--evidence", "raw.log",
            "--evidence-bundle", "bundle.json", "--request-id", "record-one"])
        with contextlib.redirect_stdout(io.StringIO()): self.ctl.cmd_record_verification(args, **access)

    def accept(self, access):
        review = self.ctl.review_stage(self.first, request_id="review-pass", **access)
        self.assertEqual(review["outcome"], "pass", review)
        view = self.ctl.stage_projection(self.first, **access)
        self.assertTrue(view["can_confirm"], view)
        request = {**view["challenge"], "decision": "confirm", "request_id": "confirm-one"}
        trusted = lambda request: {"source": "ui", "source_id": "test-ui:confirm-one", "intent": "确认本阶段验收"}
        return self.ctl.confirm_stage(self.first, request, trusted_confirmation=trusted, **access), request, trusted

    def enable_next_candidate(self, access):
        args = self.ctl.build_parser().parse_args(["candidate", "--dir", str(self.second), "--phase", "capture",
            "--base", "1" * 40, "--request-id", "second-candidate"])
        with contextlib.redirect_stdout(io.StringIO()): self.ctl.cmd_candidate(args, **access)

    def test_g02_real_bundle_review_confirmation_unlocks_only_planned_actions(self):
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        accepted, request, trusted = self.accept(access)
        self.assertEqual(accepted["projection"]["state"], "accepted")
        self.enable_next_candidate(access)
        self.ctl.require_stage_action(self.second, "code", **access)
        with self.assertRaises(self.ctl.ControlError): self.ctl.require_stage_action(self.second, "serial_exec", **access)
        replay = self.ctl.confirm_stage(self.first, request, trusted_confirmation=trusted, **access)
        self.assertTrue(replay["already_applied"])

    def assert_drift_rejected(self, drift):
        access, contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        review = self.ctl.review_stage(self.first, request_id="review-pass", **access)
        view = self.ctl.stage_projection(self.first, **access)
        self.assertTrue(view["can_confirm"], view)
        if drift == "raw": (self.first / "raw.log").write_bytes(b"altered")
        elif drift == "source": (self.project / "source.py").write_bytes(b"value = 2\n")
        elif drift == "contract":
            contract["evidence_contract"]["required_items"][0]["min_count"] = 2
            self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": contract}))
        else: Path(review["report_path"]).write_text("altered report", encoding="utf-8")
        request = {**view["challenge"], "decision": "confirm", "request_id": "stale"}
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.confirm_stage(self.first, request, trusted_confirmation=lambda request: {
                "source": "ui", "source_id": "stale", "intent": "确认本阶段验收"}, **access)

    def test_g05_raw_drift(self): self.assert_drift_rejected("raw")
    def test_g05_source_drift(self): self.assert_drift_rejected("source")
    def test_g05_report_drift(self): self.assert_drift_rejected("report")
    def test_g05_contract_drift(self): self.assert_drift_rejected("contract")

    def test_g06_later_source_change_preserves_history_but_raw_damage_blocks(self):
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.accept(access)
        self.enable_next_candidate(access)
        (self.project / "source.py").write_bytes(b"value = 2\n")
        self.ctl.require_stage_action(self.second, "code", **access)
        (self.first / "raw.log").write_bytes(b"damaged original")
        with self.assertRaises(self.ctl.ControlError): self.ctl.require_stage_action(self.second, "code", **access)
        view = self.ctl.stage_projection(self.first, **access)
        self.assertTrue(view["can_revoke"], view)
        request = {**view["acceptance_snapshot"], "decision": "revoke", "request_id": "revoke", "reason": "original evidence damaged"}
        revoked = self.ctl.confirm_stage(self.first, request, trusted_confirmation=lambda request: {
            "source": "ui", "source_id": "revoke", "intent": "撤销本阶段验收"}, **access)
        self.assertEqual(revoked["decision"], "revoke")

    def test_generic_ai_review_writer_executes_adapter_and_protects_receipt(self):
        access, _contract, item, run, bundle = self.ready(method="ai_review")
        calls = []
        def reviewer(request):
            calls.append(request)
            self.assertEqual(request["content"], (self.first / "raw.log").read_bytes())
            return {key: request[key] for key in ("method", "item_sha256", "run_id", "candidate_id")} | {
                "outcome": "pass", "conclusion": "Observed required log content", "limitations": [],
                "source_id": "trusted-test-call", "provider": "fixture", "model": "mock-reviewer"}
        result = self.ctl.record_evidence_review(self.first, item=item, run=run, requirement_id="raw", method="ai_review",
            trusted_reviewer=reviewer, request_id="actual-review", **access)
        self.assertEqual(len(calls), 1)
        replay = self.ctl.record_evidence_review(self.first, item=item, run=run, requirement_id="raw", method="ai_review",
            trusted_reviewer=reviewer, request_id="actual-review", **access)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(len(calls), 1)
        bundle["items"][0]["metadata"]["review_ref"] = {"review_id": result["review_id"]}
        self.record(access, run, bundle)
        self.accept(access)

    def actual_generic_review(self, access, item, run, *, outcome, request_id):
        def reviewer(request):
            return {key: request[key] for key in ("method", "item_sha256", "run_id", "candidate_id")} | {
                "outcome": outcome, "conclusion": "Actual callback result " + outcome, "limitations": [],
                "source_id": "call:" + request_id, "provider": "fixture", "model": "mock"}
        return self.ctl.record_evidence_review(self.first, item=item, run=run, requirement_id="raw", method="ai_review",
            trusted_reviewer=reviewer, request_id=request_id, **access)

    def test_actual_review_failure_cannot_hide_behind_previous_pass_run(self):
        access, _contract, item, run, bundle = self.ready(method="ai_review")
        passed = self.actual_generic_review(access, item, run, outcome="pass", request_id="first-pass")
        bundle["items"][0]["metadata"]["review_ref"] = {"review_id": passed["review_id"]}
        self.record(access, run, bundle)
        self.actual_generic_review(access, item, run, outcome="fail", request_id="actual-failure")
        report = self.ctl.review_stage(self.first, request_id="known-fail", **access)
        self.assertEqual(report["outcome"], "fail", report)
        self.assertIsNone(report["projection"]["challenge"])

    def test_actual_failure_survives_stronger_contract_until_new_qualified_calls(self):
        access, contract, item, run, bundle = self.ready(method="ai_review")
        failed = self.actual_generic_review(access, item, run, outcome="fail", request_id="old-contract-failure")
        contract["evidence_contract"]["required_items"][0]["min_count"] = 2
        self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": contract}),
                 "--request-id", "stronger-contract")
        report = self.ctl.review_stage(self.first, request_id="still-failed", **access)
        self.assertEqual(report["outcome"], "fail", report)
        self.assertIn("actual_review_fail:" + failed["review_id"], report["reasons"])
        self.assertFalse(report["projection"]["can_confirm"])
        bundle["contract_sha256"] = self.ctl.verification_evidence_module().bundle_module().contract_digest(contract)
        second = copy.deepcopy(item)
        raw = b"second distinct original\n"
        (self.first / "raw2.log").write_bytes(raw)
        second.update(id="raw-2", path="raw2.log", sha256="sha256:" + hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        for index, row in enumerate((item, second)):
            receipt = self.actual_generic_review(access, row, run, outcome="pass", request_id="new-contract-pass-" + str(index))
            row["metadata"]["review_ref"] = {"review_id": receipt["review_id"]}
        bundle["items"] = [item, second]
        self.record(access, run, bundle)
        self.accept(access)

    def test_actual_review_after_acceptance_refutes_history_and_blocks_successor(self):
        access, _contract, item, run, bundle = self.ready(method="ai_review")
        passed = self.actual_generic_review(access, item, run, outcome="pass", request_id="first-pass")
        bundle["items"][0]["metadata"]["review_ref"] = {"review_id": passed["review_id"]}
        self.record(access, run, bundle)
        self.accept(access)
        self.actual_generic_review(access, item, run, outcome="fail", request_id="actual-refutation")
        with self.assertRaises(self.ctl.ControlError): self.ctl.require_stage_action(self.second, "candidate", **access)
        view = self.ctl.stage_projection(self.first, **access)
        self.assertTrue(view["blocked"], view)
        self.assertTrue(view["can_revoke"], view)

    def test_g04_read_acknowledgement_and_wrong_target_never_confirm(self):
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.ctl.review_stage(self.first, request_id="review", **access)
        view = self.ctl.stage_projection(self.first, **access)
        request = {**view["challenge"], "decision": "confirm", "request_id": "ack"}
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.confirm_stage(self.first, request, trusted_confirmation=lambda request: {
                "source": "ui", "source_id": "ack", "intent": "已读"}, **access)
        request["stage_id"] = "two"
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.confirm_stage(self.first, request, trusted_confirmation=lambda request: {
                "source": "ui", "source_id": "ack", "intent": "确认本阶段验收"}, **access)

    def test_human_observation_is_separate_actual_adapter_receipt(self):
        access, _contract, item, run, bundle = self.ready(method="human_observation")
        self.record_expected_failure(access, run, bundle)
        def observer(request):
            return {key: request[key] for key in ("method", "item_sha256", "run_id", "candidate_id")} | {
                "source": "host_observation", "source_id": "observed-fixture-action", "outcome": "pass",
                "conclusion": "Operator observation bound to original evidence", "limitations": ["Offline test callback"]}
        receipt = self.ctl.record_evidence_review(self.first, item=item, run=run, requirement_id="raw", method="human_observation",
            trusted_reviewer=observer, request_id="observe", **access)
        bundle["items"][0]["metadata"]["review_ref"] = {"review_id": receipt["review_id"]}
        self.record(access, run, bundle)
        self.accept(access)

    def record_expected_failure(self, access, run, bundle):
        with self.assertRaises(self.ctl.ControlError): self.record(access, run, bundle)

    def test_provider_source_drift_is_rejected_before_review_publication(self):
        access, _contract, item, run, _bundle = self.ready(method="ai_review")
        def reviewer(request):
            (self.project / "source.py").write_bytes(b"value = 2\n")
            return {key: request[key] for key in ("method", "item_sha256", "run_id", "candidate_id")} | {
                "outcome": "pass", "conclusion": "Observed", "limitations": [], "source_id": "call",
                "provider": "fixture", "model": "mock"}
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.record_evidence_review(self.first, item=item, run=run, requirement_id="raw", method="ai_review",
                trusted_reviewer=reviewer, request_id="drift", **access)
        self.assertIsNone(self.ctl.stage_module().protected_values(self.ctl.load_metadata(self.first))["evidence_reviews"])

    def actual_visual_review(self, access, run, frames, route, *, outcome, request_id):
        batches = []
        def adapter(request):
            batches.append(request)
            return {"status": "success", "frame_results": [{"frame_id": row["frame_id"], "frame_set_id": row["frame_set_id"],
                "input_sha256": row["input_sha256"], "source_sha256": row["source_sha256"], "channel": request["channel"],
                "provider": "fixture", "model": "mock-visual", "prompt_version": request["prompt_version"],
                "conclusion": "Actual synthetic visual observation: " + outcome, "outcome": outcome,
                "limitations": ["Synthetic test only"]} for row in request["frames"]]}
        receipt = self.ctl.record_visual_review(self.first, frames=frames, route=route, run=run,
            prompt_version="display-v1", adapters={"native": adapter}, request_id=request_id, **access)
        self.assertEqual([len(batch["frames"]) for batch in batches], [4, 1])
        self.assertEqual(receipt["outcome"], outcome, receipt)
        return receipt

    def visual_bundle(self, *, method="ai_review", kind="frame_set"):
        access, contract, _item, run, bundle = self.ready(visual=True)
        contract["evidence_contract"]["required_items"][-1].update(method=method, kind=kind)
        self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": contract}))
        bundle["contract_sha256"] = self.ctl.verification_evidence_module().bundle_module().contract_digest(contract)
        router = load("media_router")
        route = router.route_media("native", "supported", "available", "general", "normal",
            router._safe_profile({"provider": "fixture", "model": "fixture", "declared_capabilities": ["visual_reasoning"],
                                  "max_images_per_message": 4}), 4)
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 2, 8, 2, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 4) * 2)) + chunk(b"IEND", b""))
        sha = "sha256:" + hashlib.sha256(raw).hexdigest()
        frames, group_frames = [], []
        mode = {"width": 4, "height": 2, "fourcc": "PNG"}
        for i, when in enumerate((0, 2.5, 5, 7.5, 10)):
            path = self.first / ("frame-" + str(i) + ".png")
            path.write_bytes(raw)
            frames.append({"frame_id": "frame-" + str(i), "frame_set_id": "set", "source_path": str(path),
                           "preview_path": str(path), "source_sha256": sha, "input_sha256": sha})
            bundle["items"].append({"id": "frame-" + str(i), "requirement_id": "display", "kind": kind,
                "path": path.name, "sha256": sha, "bytes": len(raw), "complete": True, "source_ids": [],
                "metadata": {"mode": mode, "layer": "host", "consumer": "tool", "scenario": "steady", "layout": mode,
                             "sequence": i, "monotonic": when, "timestamp": when, "clock_domain": "host_monotonic"}})
            group_frames.append({"id": "frame-" + str(i), "source_item_id": "frame-" + str(i), "preview_item_id": "frame-" + str(i),
                "sequence": i, "monotonic": when, "timestamp": when, "clock_domain": "host_monotonic", "review_refs": []})
        receipt = self.actual_visual_review(access, run, frames, route, outcome="pass", request_id="visual")
        for row in group_frames: row["review_refs"] = [{"review_id": receipt["review_id"]}]
        bundle["visual"] = {"schema_version": 1, "frame_sets": [{"id": "set", "requirement_id": "display", "mode": mode,
            "layer": "host", "consumer": "tool", "scenario": "steady", "display": False, "window": bundle["window"], "frames": group_frames}]}
        return access, run, bundle, frames, route

    def test_visual_writer_executes_two_actual_adapter_batches_then_bundle_qualifies(self):
        access, run, bundle, _frames, _route = self.visual_bundle()
        self.record(access, run, bundle)
        accepted, _request, _trusted = self.accept(access)
        self.assertEqual(accepted["projection"]["report"]["text"].count("![代表性帧"), 5)

    def assert_actual_visual_failure_blocks(self, method):
        access, run, bundle, frames, route = self.visual_bundle(method=method)
        if method == "human_observation":
            # Actual AI display review and explicit human observation are both
            # required: neither one may stand in for the other.
            self.record_expected_failure(access, run, bundle)
            self.assertFalse(self.ctl.load_metadata(self.first).get("verification_runs"))
            def observer(request):
                return {key: request[key] for key in ("method", "item_sha256", "run_id", "candidate_id")} | {
                    "source": "host_observation", "source_id": "observed:" + request["item"]["path"], "outcome": "pass",
                    "conclusion": "Explicit synthetic operator observation", "limitations": ["Offline test callback"]}
            for item in bundle["items"]:
                if item["requirement_id"] == "display":
                    receipt = self.ctl.record_evidence_review(self.first, item=item, run=run, requirement_id="display",
                        method="human_observation", trusted_reviewer=observer, request_id="observe:" + item["id"], **access)
                    item["metadata"]["review_ref"] = {"review_id": receipt["review_id"]}
        self.record(access, run, bundle)
        before = self.ctl.review_stage(self.first, request_id="visual-pass-before-refutation", **access)
        self.assertEqual(before["outcome"], "pass", before)
        self.assertTrue(before["projection"]["can_confirm"])
        failed = self.actual_visual_review(access, run, frames, route, outcome="fail", request_id="actual-visual-refutation")
        after = self.ctl.review_stage(self.first, request_id="visual-failed-after-refutation", **access)
        self.assertEqual(after["outcome"], "fail", after)
        self.assertIn("actual_review_fail:" + failed["review_id"], after["reasons"])
        self.assertFalse(after["projection"]["can_confirm"])
        self.assertIsNone(after["projection"]["challenge"])

    def test_actual_visual_failure_blocks_machine_frame_requirement(self):
        self.assert_actual_visual_failure_blocks("machine")

    def test_actual_visual_failure_blocks_ai_frame_requirement(self):
        self.assert_actual_visual_failure_blocks("ai_review")

    def test_actual_visual_failure_blocks_human_frame_requirement(self):
        self.assert_actual_visual_failure_blocks("human_observation")

    def test_actual_visual_failure_preserved_for_visual_review_requirement(self):
        # A qualified bundle is not necessary to preserve an actual failure.
        access, run, _bundle, frames, route = self.visual_bundle(method="machine", kind="visual_review")
        failed = self.actual_visual_review(access, run, frames, route, outcome="fail", request_id="actual-review-kind-failure")
        report = self.ctl.review_stage(self.first, request_id="review-kind-failed", **access)
        self.assertEqual(report["outcome"], "fail", report)
        self.assertIn("actual_review_fail:" + failed["review_id"], report["reasons"])
        self.assertFalse(report["projection"]["can_confirm"])

    def test_g09_every_real_execution_entry_blocks_future_stage(self):
        self.enable()
        common = ["--dir", str(self.second)]
        commands = [("step", *common, "--phase", "start", "--step", step) for step in ("code", "patch", "fast", "verify", "deploy")]
        commands += [("transition", *common, "--to", "code_in_progress"),
            ("operation", *common, "--phase", "start", "--name", "future", "--opclass", "external_side_effect", "--request", "future"),
            ("candidate", *common, "--phase", "capture", "--request-id", "future"),
            ("bind-execution-root", *common, "--ticket-id", "ticket" + self.second.name, "--execution-root", str(self.project), "--request-id", "future"),
            ("build-start", *common, "--candidate-id", "a" * 64, "--build-dir", str(self.project / "build"), "--parameters-json", "{}", "--request-id", "future"),
            ("record-verification", *common, "--kind", "device_test", "--outcome", "pass", "--evidence", "future"),
            ("action-policy", *common, "--action", "code")]
        before = tuple((self.second / name).read_bytes() for name in (self.ctl.METADATA_NAME, self.ctl.EVENTS_NAME))
        for command in commands:
            with self.subTest(command=command[0:4]), self.assertRaisesRegex(self.ctl.ControlError, "stage|动作"):
                self.cli(*command)
        self.assertEqual(before, tuple((self.second / name).read_bytes() for name in (self.ctl.METADATA_NAME, self.ctl.EVENTS_NAME)))

    def test_g09_tool_agent_spawn_cannot_implement_future_stage(self):
        self.enable()
        with self.assertRaises(self.ctl.ControlError):
            self.cli("record-agent-spawn", "--dir", str(self.second), "--task-scope", "Implement future code",
                "--expected-artifact", "source.py", "--evidence-boundary", "project files", "--join-condition", "written",
                "--backend", "fixture", "--model", "mock", "--capability", "text", "--capability", "tools", "--request-id", "future-agent")

    def test_g09_future_requirement_draft_is_allowed_but_execution_contract_is_not(self):
        self.enable()
        self.cli("metadata-update", "--dir", str(self.second), "--set-json", json.dumps({"requirement_summary": "Draft only"}))
        with self.assertRaises(self.ctl.ControlError):
            self.cli("metadata-update", "--dir", str(self.second), "--set-json", json.dumps({"code_files": ["future.py"]}))

    def test_g09_before_append_rechecks_predecessor_raw_not_only_coordinator(self):
        self.plan["stages"][1]["required_actions"].append("serial_exec")
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.accept(access)
        prepare = self.ctl.prepare_event
        def corrupt_after_prepare(*args, **kwargs):
            event = prepare(*args, **kwargs)
            if event["event_type"] == "operation_started": (self.first / "raw.log").write_bytes(b"damaged just before append")
            return event
        argv = ["operation", "--dir", str(self.second), "--phase", "start", "--name", "late-check",
                "--opclass", "external_side_effect", "--input", json.dumps({"action": "serial_exec"}), "--request", "late-check"]
        with mock.patch.object(self.ctl, "prepare_event", side_effect=corrupt_after_prepare), self.assertRaises(self.ctl.ControlError):
            self.cli(*argv)
        self.assertFalse(self.ctl.execution_trace(self.ctl.read_events(self.second)[0])["open_operations"])

    def test_g09_verify_build_variant_requires_explicit_planned_build(self):
        self.enable()
        with self.assertRaises(self.ctl.ControlError):
            self.cli("action-policy", "--dir", str(self.first), "--action", "verify", "--verify-action", "build")
        # A requirement/verification plan remains a draft in the future stage.
        result = self.cli("action-policy", "--dir", str(self.second), "--action", "verify", "--verify-action", "plan")
        self.assertTrue(result["action_allowed"])

    def test_g10_failed_run_cannot_be_changed_into_human_observation(self):
        access, contract, _item, run, bundle = self.ready()
        args = self.ctl.build_parser().parse_args(["record-verification", "--dir", str(self.first), "--kind", "device_test",
            "--outcome", "fail", "--environment", "host", "--candidate-id", run["candidate_id"], "--layer", "host",
            "--consumer", "tool", "--scenario", "steady", "--baseline", "fixture", "--evidence", "failed.log", "--request-id", "actual-fail"])
        with contextlib.redirect_stdout(io.StringIO()): self.ctl.cmd_record_verification(args, **access)
        for change in ("human_observation", "delete"):
            changed = copy.deepcopy(contract)
            if change == "delete": changed["evidence_contract"]["required_items"] = []
            else: changed["evidence_contract"]["required_items"][0]["method"] = change
            with self.subTest(change=change), self.assertRaises(self.ctl.ControlError):
                self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": changed}))
        review = self.ctl.review_stage(self.first, request_id="fail-review", **access)
        self.assertEqual(review["outcome"], "fail")
        self.assertIsNone(review["projection"]["challenge"])

    def test_g01_next_stage_blocked_before_first_acceptance(self):
        self.enable()
        self.ctl.require_stage_action(self.first, "verify")
        with self.assertRaisesRegex(self.ctl.ControlError, "stage"):
            self.ctl.require_stage_action(self.second, "code")

    def test_g03_cli_actor_and_json_cannot_confirm(self):
        self.enable()
        with self.assertRaisesRegex(self.ctl.ControlError, "trusted"):
            self.ctl.confirm_stage(self.first, {"actor": "human"})

    def test_g09_namespaces_are_protected_at_birth_and_update(self):
        self.enable()
        for namespace in ("stage_program", "stage_binding"):
            with self.subTest(namespace=namespace), self.assertRaises(self.ctl.ControlError):
                self.cli("metadata-update", "--dir", str(self.first), "--set-json",
                         json.dumps({"extensions": {namespace: {"fake": True}}}))
        for namespace in ("serial_claims", "visual_reviews", "evidence_reviews"):
            with self.subTest(namespace=namespace), self.assertRaises(self.ctl.ControlError):
                self.cli("metadata-update", "--dir", str(self.first), "--set-json",
                         json.dumps({"extensions": {"verification": {namespace: [{"fake": True}]}}}))

    def test_g09_missing_locator_never_downgrades_bound_ticket(self):
        self.enable()
        self.ctl.stage_module().locator_path(self.project).unlink()
        projection = self.ctl.stage_projection(self.first)
        self.assertTrue(projection["enabled"])
        self.assertTrue(projection["blocked"])

    def test_g09_missing_entire_locator_directory_never_downgrades_new_birth(self):
        unbound = self.project / ".icode_output/.icode_output_4"
        self.cli("create", "--dir", str(unbound), "--ticket-id", "previously-ordinary", "--requirement", "draft", "--birth", "plan")
        self.enable()
        directory = self.ctl.stage_module().locator_path(self.project).parent
        directory.rename(self.project / "saved-stage-locator")
        with self.assertRaises(self.ctl.ControlError):
            self.cli("create", "--dir", str(self.project / ".icode_output/.icode_output_5"), "--ticket-id", "new",
                     "--requirement", "must not downgrade", "--birth", "plan")
        view = self.ctl.stage_projection(unbound)
        self.assertTrue(view["enabled"])
        self.assertTrue(view["blocked"])
        self.ctl.register_stage_program(self.coord, self.plan, request_id="register")
        self.assertTrue(self.ctl.stage_module().locator_path(self.project).is_file())

    def test_g09_history_discovery_requires_authentic_ledger_and_bounded_work(self):
        stage = self.ctl.stage_module()
        self.assertIsNone(stage.discover_stage_history(self.project))
        for key in ("max_entries", "max_files", "max_events", "max_file_bytes", "max_total_bytes"):
            with self.subTest(budget=key), self.assertRaisesRegex(self.ctl.ControlError, "budget"):
                stage.discover_stage_history(self.project, limits={key: 1})
        with mock.patch.object(stage.time, "monotonic", side_effect=[0, 6]), self.assertRaisesRegex(self.ctl.ControlError, "time budget"):
            stage.discover_stage_history(self.project)
        path = self.coord / self.ctl.METADATA_NAME
        original = path.read_bytes()
        metadata = json.loads(original)
        metadata.setdefault("extensions", {})["stage_program"] = {"forged": True}
        path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(self.ctl.ControlError, "mirrors disagree"):
            stage.discover_stage_history(self.project)
        path.write_bytes(original)
        ledger = self.coord / self.ctl.EVENTS_NAME
        ledger.write_bytes(ledger.read_bytes().replace(b'"ticket_created"', b'"ticket_forged"', 1))
        with self.assertRaisesRegex(self.ctl.ControlError, "damaged"):
            stage.discover_stage_history(self.project)

    def test_g09_history_discovery_rejects_dangling_debug_directory(self):
        link = self.project / ".icode_output/.debug"
        try:
            link.symlink_to(self.project / "missing-debug-target", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host cannot create directory symlinks")
        with self.assertRaisesRegex(self.ctl.ControlError, "symlink|ordinary"):
            self.ctl.stage_module().discover_stage_history(self.project)

    def test_g10_expected_mode_is_immutable_after_a_failed_run(self):
        expected = {"width": 1920, "height": 1080, "fourcc": "PNG"}
        access, contract, _item, run, _bundle = self.ready(expected_mode=expected)
        args = self.ctl.build_parser().parse_args(["record-verification", "--dir", str(self.first), "--kind", "device_test",
            "--outcome", "fail", "--environment", "host", "--candidate-id", run["candidate_id"], "--layer", "host",
            "--consumer", "tool", "--scenario", "steady", "--baseline", "fixture", "--evidence", "failed.log", "--request-id", "failed-mode"])
        with contextlib.redirect_stdout(io.StringIO()): self.ctl.cmd_record_verification(args, **access)
        for field in ("width", "height", "fourcc", "delete"):
            changed = copy.deepcopy(contract)
            if field == "delete": changed["evidence_contract"]["required_items"][0].pop("expected_mode")
            else: changed["evidence_contract"]["required_items"][0]["expected_mode"][field] = {"width": 4, "height": 2, "fourcc": "JPEG"}[field]
            with self.subTest(field=field), self.assertRaises(self.ctl.ControlError):
                self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": changed}))
        stricter = copy.deepcopy(contract)
        stricter["evidence_contract"]["required_items"][0]["min_count"] = 2
        self.cli("metadata-update", "--dir", str(self.first), "--set-json", json.dumps({"verification_contract": stricter}))
        self.assertEqual(self.ctl.load_metadata(self.first)["verification_contract"]["evidence_contract"]["required_items"][0]["expected_mode"], expected)

    def test_g10_empty_contract_produces_inconclusive_report(self):
        self.enable()
        result = self.ctl.review_stage(self.first, request_id="review-empty")
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertTrue(Path(result["report_path"]).is_file())
        self.assertIsNone(self.ctl.stage_projection(self.first)["challenge"])

    def test_nonstage_project_is_compatible(self):
        self.assertFalse(self.ctl.stage_projection(self.first)["enabled"])
        self.ctl.require_stage_action(self.first, "serial_exec")

    def test_g09_stage_project_create_requires_explicit_stage(self):
        self.enable()
        with self.assertRaises(self.ctl.ControlError):
            self.cli("create", "--dir", str(self.project / ".icode_output" / ".icode_output_4"),
                     "--ticket-id", "new", "--requirement", "no implicit bypass", "--birth", "plan")

    def test_g09_stage_plan_rejects_action_expansion(self):
        bad = copy.deepcopy(self.plan)
        bad["stages"][0]["required_actions"].append("publish")
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.register_stage_program(self.coord, bad, request_id="bad")

    def test_g08_registration_locator_failure_stays_enabled_and_recovers(self):
        locator = self.ctl.stage_module().locator_path(self.project)
        real = self.ctl.atomic_write_json
        def fail_locator(path, value):
            if Path(path) == locator: raise OSError("injected locator publication failure")
            return real(path, value)
        with mock.patch.object(self.ctl, "atomic_write_json", side_effect=fail_locator), self.assertRaises(OSError):
            self.ctl.register_stage_program(self.coord, self.plan, request_id="register")
        self.assertTrue(locator.parent.is_dir())
        self.assertFalse(locator.exists())
        with self.assertRaises(self.ctl.ControlError):
            self.cli("create", "--dir", str(self.project / ".icode_output/.icode_output_4"), "--ticket-id", "new",
                     "--requirement", "must not downgrade", "--birth", "plan")
        self.ctl.register_stage_program(self.coord, self.plan, request_id="register")
        self.assertTrue(locator.is_file())
        self.assertFalse(self.ctl.stage_projection(self.coord)["blocked"])

    def test_g07_new_stage_birth_binds_and_exact_replay_is_idempotent(self):
        self.ctl.register_stage_program(self.coord, self.plan, request_id="register")
        path = self.project / ".icode_output/.icode_output_4"
        argv = ["create", "--dir", str(path), "--ticket-id", "new-stage", "--requirement", "Draft future stage",
                "--birth", "plan", "--stage-id", "two", "--request-id", "new-stage-birth"]
        created = self.cli(*argv)
        replay = self.cli(*argv)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(replay["event_id"], created["event_id"])
        self.assertEqual(self.ctl.load_metadata(path)["extensions"]["stage_binding"]["stage_id"], "two")
        with self.assertRaises(self.ctl.ControlError):
            changed = list(argv)
            changed[changed.index("Draft future stage")] = "Changed request"
            self.cli(*changed)

    def test_g07_concurrent_same_confirmation_is_one_decision(self):
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.ctl.review_stage(self.first, request_id="review", **access)
        request = {**self.ctl.stage_projection(self.first, **access)["challenge"], "decision": "confirm", "request_id": "confirm"}
        trusted = lambda request: {"source": "ui", "source_id": "confirm", "intent": "确认本阶段验收"}
        def confirm(_):
            return self.ctl.confirm_stage(self.first, request, trusted_confirmation=trusted, **access)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, range(2)))
        self.assertEqual(sorted(result["already_applied"] for result in results), [False, True])
        self.assertEqual(len(self.ctl.load_metadata(self.coord)["extensions"]["stage_program"]["decisions"]), 1)

    def test_g07_concurrent_coordinators_cannot_replace_project_locator(self):
        def register(directory):
            try:
                self.ctl.register_stage_program(directory, self.plan, request_id="register:" + directory.name)
                return "registered", directory
            except self.ctl.ControlError:
                return "blocked", directory
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(register, (self.coord, self.first)))
        self.assertEqual(sorted(row[0] for row in results), ["blocked", "registered"])
        winner = next(row[1] for row in results if row[0] == "registered")
        locator = self.ctl.load_json(self.ctl.stage_module().locator_path(self.project), "locator")
        self.assertEqual(locator["coordinator_root"], str(winner))

    def test_g08_confirmation_append_failure_rolls_back_and_retry_is_exact(self):
        access, _contract, _item, run, bundle = self.ready()
        self.record(access, run, bundle)
        self.ctl.review_stage(self.first, request_id="review", **access)
        request = {**self.ctl.stage_projection(self.first, **access)["challenge"], "decision": "confirm", "request_id": "confirm"}
        trusted = lambda request: {"source": "ui", "source_id": "confirm", "intent": "确认本阶段验收"}
        with mock.patch.object(self.ctl, "append_prepared_event", side_effect=OSError("injected append failure")), self.assertRaises(OSError):
            self.ctl.confirm_stage(self.first, request, trusted_confirmation=trusted, **access)
        self.assertEqual(self.ctl.stage_projection(self.first, **access)["state"], "awaiting_confirmation")
        self.assertFalse((self.coord / self.ctl.TXN_NAME).exists())
        accepted = self.ctl.confirm_stage(self.first, request, trusted_confirmation=trusted, **access)
        self.assertEqual(accepted["projection"]["state"], "accepted")

    def test_serial_claim_is_once_only_even_when_remote_unknown(self):
        binding = json.dumps({"action": "serial_exec", "operation_class": "external_side_effect",
                              "script_sha256": "a" * 64, "config_sha256": "b" * 64}, sort_keys=True)
        started = self.cli("operation", "--dir", str(self.first), "--phase", "start", "--name", "serial",
                           "--opclass", "external_side_effect", "--input", binding, "--request", "serial")
        event = self.ctl.find_operation_start(self.ctl.read_events(self.first)[0], started["attempt"])
        proof = {"dir": str(self.first), "attempt": started["attempt"], "event_id": event["event_id"],
                 "ticket_id": "ticket" + self.first.name, "input_digest": self.ctl.canonical_digest(binding),
                 "operation_class": "external_side_effect"}
        info = {"session_id": "session", "output_dir": str(self.first / "serial"), "operation_input": binding}
        self.ctl.claim_serial_operation(proof, info)
        with self.assertRaises(self.ctl.ControlError):
            self.ctl.claim_serial_operation(proof, info)
        self.assertEqual(self.ctl.verify_event_chain(self.first, self.ctl.load_metadata(self.first))[1], [])

    def test_production_serial_authorizer_and_claim_execute_once_then_reject_replay(self):
        import test_serial_session as fixture
        session = fixture.session
        config = {"name": "console", "type": "serial", "port": "mock-stage-control", "baud": 115200,
            "console_type": "posix_shell", "ready_pattern": r"unit\$ $",
            "identity": {"script": "printf 'unit-probe\\n'", "pattern": r"(?m)^unit-id$"},
            "budgets": {"listen_seconds": .02, "exec_seconds": 2, "read_timeout": .002,
                        "write_timeout": .01, "ready_timeout": .5, "identity_timeout": .5, "max_raw_bytes": 32768}}
        script = self.first / "request.sh"
        script.write_bytes(b"printf 'user-script\\n'\n")
        normalized = session.load_connection(config, "console")
        binding = session.operation_input(normalized, script.read_bytes())
        start = self.cli("operation", "--dir", str(self.first), "--phase", "start", "--name", "serial-real-chain",
            "--opclass", "external_side_effect", "--input", binding, "--request", "serial-real-chain")
        factory = fixture.Factory()
        ref = {"dir": str(self.first), "attempt": start["attempt"]}
        with mock.patch.object(session, "_control", return_value=self.ctl):
            result = session.run_exec(config, "console", self.first / "serial-first", script, ref, serial_factory=factory)
            self.assertEqual(result["outcome"], "pass", result)
            replay = fixture.Factory()
            with self.assertRaises(session.AuthorizationError):
                session.run_exec(config, "console", self.first / "serial-replay", script, ref, serial_factory=replay)
            self.assertFalse(replay.instances)
        self.assertEqual(len(factory.instances[0].scripts), 2)


if __name__ == "__main__":
    unittest.main()
