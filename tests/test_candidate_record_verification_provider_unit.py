"""B3c record-verification: real ports and bytes, software authority only."""
import contextlib
import copy
import hashlib
import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_candidate_history_unit as history

ctl, api, ev = history.ctl, history.api, history.ev


def invoke(function, *args, provider=None, required=False, **kwargs):
    # Old implementations must reach behavior, not fail on missing kwargs.
    names = inspect.signature(function).parameters
    if "git_provider" in names:
        kwargs["git_provider"] = provider
    if "require_git_provider" in names:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class RecordVerificationProviderTests(unittest.TestCase):
    def fixture(self, *, enabled=True):
        f = history.HistoryTests(methodName="runTest")
        self.addCleanup(f.doCleanups)
        command = f.command
        def bootstrap(*args, **kwargs):
            return {} if args[0] == "candidate" else command(*args, **kwargs)
        with contextlib.nullcontext() if enabled else patch.object(f, "command", side_effect=bootstrap):
            f.setUp()
        return f

    def seal(self, f):
        with patch.object(f, "snapshot", side_effect=lambda: ctl.candidate_snapshot(
                f.ticket, f.meta(), base=f.base)):
            return f.seal()

    def args(self, f, candidate, request="b3c", **options):
        argv = ["record-verification", "--dir", str(f.ticket), "--kind", "device_test",
                "--outcome", "pass", "--environment", "physical",
                "--candidate-id", candidate["candidate_id"], "--device", "SN=B3c-fixture",
                "--layer", "physical", "--consumer", "camera", "--scenario", "steady",
                "--baseline", "fixture source", "--evidence", "fixture.log",
                "--request-id", request]
        args = ctl.build_parser().parse_args(argv)
        for key, value in options.items():
            setattr(args, key, json.dumps(value) if key.endswith("_json") else value)
        return args

    def call(self, args, provider=None, *, required=False, main=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            if main:
                with patch.object(ctl, "build_parser") as parser:
                    parser.return_value.parse_args.return_value = args
                    code = invoke(ctl.main, provider=provider, required=required)
            else:
                code = invoke(ctl.cmd_record_verification, args, provider=provider, required=required)
        return code, json.loads(output.getvalue())

    def success(self, args, provider=None, *, required=False, main=False):
        try:
            code, result = self.call(args, provider, required=required, main=main)
        except ctl.ControlError as exc:
            self.fail("protected verification must complete: " + str(exc))
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"], result)
        return result

    def frozen(self, f, before):
        self.assertEqual(f.frozen(), before)
        self.assertFalse((f.ticket / ctl.TXN_NAME).exists())

    def build_args(self, f, request="b3c-build"):
        directory = f.code / "build"
        directory.mkdir()
        candidate = f.snapshot()
        start = f.command("build-start", "--dir", f.ticket, "--build-dir", directory,
            "--candidate-id", candidate["candidate_id"], "--parameters-json",
            json.dumps({"configure_command": ["cmake", "-S", ".", "-B", "build"],
                        "build_command": ["cmake", "--build", "build"]}),
            "--request-id", request + ":start")
        artifact = directory / "app"
        artifact.write_bytes(b"binary")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        provenance = {"start_event_id": start["event_id"], "source_candidate_id": candidate["candidate_id"],
            "configure_command": ["cmake", "-S", ".", "-B", "build"],
            "build_command": ["cmake", "--build", "build"], "compiler": "fixture",
            "architecture": "host", "artifacts": [{"path": "build/app", "sha256": digest}],
            "evidence_refs": ["build.log"]}
        args = self.args(f, candidate, request, kind="build", layer="build", device=None,
            environment="host", build_source="fresh", artifact_identity="sha256:" + digest,
            build_provenance_json=provenance)
        return args, artifact

    def test_none_cli_real_record_and_immutable_report_replay(self):
        f = self.fixture()
        directory = f.code / "build"
        directory.mkdir()
        report = directory / "junit.xml"
        report.write_bytes(b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="ok"/></testsuite>')
        args = self.args(f, f.snapshot(), test_report="build/junit.xml")
        first = self.success(args, main=True)
        before = f.frozen()
        report.unlink()
        again = self.success(args, main=True)
        self.assertTrue(again["already_applied"])
        self.assertEqual(first["event_id"], again["event_id"])
        self.frozen(f, before)

    def test_provider_real_code_callbacks_and_exact_canonical_kwargs(self):
        f = self.fixture()
        candidate = f.snapshot()
        args, provider = self.args(f, candidate), self.seal(f)
        self.assertFalse((f.code / ".git").exists())
        with patch.object(ctl, "candidate_snapshot", wraps=ctl.candidate_snapshot) as capture, \
                patch.object(ctl, "verify_event_chain", wraps=ctl.verify_event_chain) as verify, \
                patch.object(ev, "validate_reuse", wraps=ev.validate_reuse) as reuse, \
                patch.object(ev, "validate_provenance", wraps=ev.validate_provenance) as provenance, \
                patch.object(ctl, "commit_metadata_and_event", wraps=ctl.commit_metadata_and_event) as publish:
            result = self.success(args, provider, required=True)
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(capture.call_args_list[0], capture.call_args_list[1])
        self.assertIs(capture.call_args.kwargs["git_provider"], provider)
        self.assertIs(capture.call_args.kwargs["require_git_provider"], True)
        self.assertIs(publish.call_args.kwargs["git_provider"], provider)
        self.assertTrue(callable(publish.call_args.kwargs["before_publish"]))
        for spy in (reuse, provenance):
            self.assertTrue(spy.call_count)
            for call in spy.call_args_list:
                self.assertIs(call.kwargs["git_provider"], provider)
                self.assertIs(call.kwargs["require_git_provider"], True)
                self.assertIs(call.args[2] if spy is reuse else call.args[4], api)
        for call in verify.call_args_list:
            if call.kwargs.get("semantic") is False:
                self.assertNotIn("git_provider", call.kwargs)
            else:
                self.assertIs(call.kwargs["git_provider"], provider)
                self.assertIs(call.kwargs["require_git_provider"], True)
        run = f.meta()["verification_runs"][-1]
        self.assertEqual(run["run_id"], result["run"]["run_id"])
        row = next(x for x in run["candidate"]["manifest"] if x["path"] == "source.py")
        self.assertEqual(row["sha256"], hashlib.sha256((f.code / "source.py").read_bytes()).hexdigest())
        events, issues = invoke(ctl.verify_event_chain, f.ticket, f.meta(), provider=provider, required=True)
        self.assertFalse(issues, issues)
        self.assertEqual(events[-1]["payload"]["run_id"], run["run_id"])

    def test_main_exact_three_pairs_and_unconnected_step_reject(self):
        f = self.fixture()
        args, provider = self.args(f, f.snapshot()), self.seal(f)
        self.success(args, provider, required=True, main=True)
        for command, function in (("step", ctl.cmd_step), ("candidate", ctl.cmd_record_verification),
                                  ("record-verification", ctl.cmd_candidate), ("record-verification", lambda args, **kw: self.fail("unknown handler executed"))):
            before = f.frozen()
            forged = SimpleNamespace(cmd=command, func=function)
            code, report = self.call(forged, provider, required=True, main=True)
            self.assertEqual(code, 1, report)
            self.assertEqual(report["gate_id"], "candidate_business_propagation_pending")
            self.frozen(f, before)
        for command, function in (("candidate", ctl.cmd_candidate), ("build-start", ctl.cmd_build_start)):
            real = ctl.build_parser().parse_args(
                ["candidate", "--dir", str(f.ticket), "--phase", "inspect"] if command == "candidate" else
                ["build-start", "--dir", str(f.ticket), "--build-dir", str(f.code / "later"),
                 "--candidate-id", f.meta()["candidate_tracking"]["current"]["candidate_id"],
                 "--parameters-json", "{}", "--request-id", "still-build"])
            with patch.object(ctl, "build_parser") as parser, patch.object(ctl, function.__name__, return_value=0) as allowed:
                real.func = allowed
                parser.return_value.parse_args.return_value = real
                invoke(ctl.main, provider=provider, required=True)
                self.assertIs(allowed.call_args.kwargs["git_provider"], provider)

    def test_missing_wrong_root_foreign_class_and_falsey_reject(self):
        f = self.fixture()
        args, provider = self.args(f, f.snapshot()), self.seal(f)
        foreign_api = history.load("b3c_foreign", "icode_control.py").candidate_module()
        class Foreign(foreign_api.CandidateGitProvider):
            workspace = f.code
            def assert_context(self): return None
            def query(self, request, *, limit): raise AssertionError("foreign query")
        for supplied, required in ((None, True), (object(), True), (Foreign(), True), (provider, 0), (provider, [])):
            before = f.frozen()
            with self.subTest(provider_type=type(supplied).__name__, required=required):
                with self.assertRaises(ctl.ControlError) as caught:
                    self.call(args, supplied, required=required)
                self.assertEqual(caught.exception.report()["gate_id"], "candidate_provider_context")
                self.frozen(f, before)
        provider.workspace = f.control
        with self.assertRaises(ctl.ControlError) as caught:
            self.call(args, provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "candidate_provider_context")
        provider.workspace = f.code

    def test_real_disabled_contract_rejects_missing_and_invalid_candidate_without_live(self):
        for candidate_id in (None, "0" * 64):
            f = self.fixture(enabled=False)
            candidate = ctl.candidate_snapshot(f.ticket, f.meta(), base=f.base)
            args = self.args(f, candidate)
            args.candidate_id = candidate_id
            self.assertNotIn("candidate_tracking", f.meta())
            provider, before = self.seal(f), f.frozen()
            with self.subTest(candidate_id=candidate_id):
                with self.assertRaises(ctl.ControlError) as caught:
                    self.call(args, provider, required=True)
                self.assertEqual(caught.exception.report()["gate_id"], "candidate_provider_context")
                self.assertIn("candidate tracking required", str(caught.exception))
                self.frozen(f, before)
                self.assertNotIn("candidate_tracking", f.meta())
            f.doCleanups()

    def test_pending_never_recovers_even_with_invalid_context(self):
        f = self.fixture()
        args, provider = self.args(f, f.snapshot()), self.seal(f)
        pending = f.ticket / ctl.TXN_NAME
        pending.write_bytes(b"pending must survive\n")
        before = f.frozen()
        with patch.object(ctl, "recover_pending_transaction", side_effect=AssertionError("recovery called")):
            for expired, gate in ((True, "candidate_provider_context"), (False, "candidate_transaction_pending")):
                provider.expired = expired
                with self.subTest(expired=expired):
                    with self.assertRaises(ctl.ControlError) as caught:
                        self.call(args, provider, required=True)
                    self.assertEqual(caught.exception.report()["gate_id"], gate)
        self.assertEqual(pending.read_bytes(), b"pending must survive\n")
        self.assertEqual(before, f.frozen())

    def test_fresh_build_keeps_real_provenance_and_second_capture(self):
        f = self.fixture()
        args, artifact = self.build_args(f)
        provider = self.seal(f)
        with patch.object(ctl, "candidate_snapshot", wraps=ctl.candidate_snapshot) as capture:
            self.success(args, provider, required=True)
        self.assertEqual(capture.call_count, 3)
        self.assertEqual(capture.call_args_list[0], capture.call_args_list[1])
        self.assertEqual(capture.call_args_list[0], capture.call_args_list[2])
        run = f.meta()["verification_runs"][-1]
        self.assertEqual(run["build_provenance"]["artifacts"][0]["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(run["build_provenance"]["source_candidate_id"], args.candidate_id)

    def test_second_compare_proof_unavailable_is_not_ordinary_mismatch(self):
        f = self.fixture()
        args, artifact = self.build_args(f)
        provider, before = self.seal(f), f.frozen()
        original = api.compare_candidates
        for reason in ("unknown_future_reason", "head_history_changed", "source_or_contract_drift"):
            hits = []
            def compare(*pos, **kw):
                frame = inspect.currentframe().f_back
                while frame is not None:
                    if frame.f_code.co_name in ("validate_provenance", "validate_reuse", "event_issues"):
                        break
                    if frame.f_code.co_name == "cmd_record_verification":
                        hits.append(reason)
                        return {"equivalent": False, "reason": reason}
                    frame = frame.f_back
                return original(*pos, **kw)
            with self.subTest(reason=reason), patch.object(api, "compare_candidates", side_effect=compare):
                with self.assertRaises(ctl.ControlError) as caught:
                    self.call(args, provider, required=True)
                expected = "source candidate changed" if reason == "source_or_contract_drift" else "candidate_proof_unavailable"
                self.assertIn(expected, str(caught.exception))
                self.assertEqual(caught.exception.report()["gate_id"], "verification_evidence")
                self.assertEqual(hits, [reason])
                self.assertIsNone(provider.assert_context())
                self.frozen(f, before)

    def test_historical_reused_candidate_is_not_publication_live_binding(self):
        f = self.fixture()
        old, current, build, second, origin, later = f.linear_history()
        args = self.args(f, old, "old-origin-new-record", device=origin["device"],
            build_source="fresh", artifact_identity=build["artifact_identity"],
            reuse_json=later["reuse"], runtime_provenance_json=later["runtime_provenance"])
        provider = self.seal(f)
        self.assertNotEqual(old["candidate_id"], current["candidate_id"])
        self.success(args, provider, required=True)
        run = f.meta()["verification_runs"][-1]
        self.assertEqual(run["candidate"]["candidate_id"], old["candidate_id"])
        self.assertEqual(run["reuse"]["origin_run_id"], origin["run_id"])
        self.assertTrue(invoke(ctl.verification_applicability, f.ticket, provider=provider, required=True)["ok"])

    def test_provider_report_replay_does_not_reread_or_duplicate(self):
        f = self.fixture()
        directory = f.code / "build"
        directory.mkdir()
        report = directory / "junit.xml"
        report.write_bytes(b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="ok"/></testsuite>')
        args = self.args(f, f.snapshot(), test_report="build/junit.xml")
        first = self.success(args)
        report.unlink()
        provider, before = self.seal(f), f.frozen()
        original = ev.validate_provenance
        def historical(*pos, **kw):
            self.assertIs(kw.get("check_files"), False, "historical artifacts reread")
            return original(*pos, **kw)
        with patch.object(ev.test_result_module(), "summarize_report", side_effect=AssertionError("historical report reread")), \
                patch.object(ev, "validate_provenance", side_effect=historical):
            again = self.success(args, provider, required=True)
        self.assertTrue(again["already_applied"])
        self.assertEqual(again["event_id"], first["event_id"])
        self.assertEqual(again["run_id"], first["run"]["run_id"])
        self.frozen(f, before)

    def test_replay_query_failure_and_late_expiry_cannot_return_success(self):
        f = self.fixture()
        args = self.args(f, f.snapshot())
        self.success(args)
        provider, before = self.seal(f), f.frozen()
        original = provider.query
        for failure in ("query", "post_query", "capture_return", "replay_return"):
            calls = []
            def query(request, *, limit):
                result = original(request, limit=limit)
                if failure == "query":
                    return api.CandidateReadResult(b"", 1, False)
                if failure == "post_query":
                    provider.expired = True
                return result
            original_capture = ctl.candidate_snapshot
            def capture(*pos, **kw):
                result = original_capture(*pos, **kw)
                calls.append(1)
                if failure == "capture_return" or (failure == "replay_return" and len(calls) == 2):
                    provider.expired = True
                return result
            with self.subTest(failure=failure), patch.object(provider, "query", side_effect=query), \
                    patch.object(ctl, "candidate_snapshot", side_effect=capture):
                with self.assertRaises(ctl.ControlError):
                    self.call(args, provider, required=True)
            provider.expired = False
            self.frozen(f, before)

    def test_expiry_at_verify_transaction_and_metadata_checkpoints(self):
        for checkpoint in ("verify", "txn", "metadata"):
            f = self.fixture()
            args, provider = self.args(f, f.snapshot()), self.seal(f)
            before, original_write, original_verify = f.frozen(), ctl.atomic_write_json, ctl.verify_event_chain
            def write(path, data):
                result = original_write(path, data)
                if (checkpoint == "txn" and Path(path).name == ctl.TXN_NAME) or \
                        (checkpoint == "metadata" and Path(path).name == ctl.METADATA_NAME):
                    provider.expired = True
                return result
            def verify(*pos, **kw):
                result = original_verify(*pos, **kw)
                if checkpoint == "verify" and kw.get("semantic") is not False:
                    provider.expired = True
                return result
            with self.subTest(checkpoint=checkpoint), patch.object(ctl, "atomic_write_json", side_effect=write), \
                    patch.object(ctl, "verify_event_chain", side_effect=verify):
                with self.assertRaises(ctl.ControlError):
                    self.call(args, provider, required=True)
            provider.expired = False
            self.frozen(f, before)
            f.doCleanups()

    def test_live_code_drift_before_append_rolls_back_only_control(self):
        f = self.fixture()
        args, provider = self.args(f, f.snapshot()), self.seal(f)
        before, original = f.frozen(), ctl.atomic_write_json
        changed = b"value = 72\n"
        def write(path, data):
            result = original(path, data)
            if Path(path).name == ctl.METADATA_NAME:
                (f.code / "source.py").write_bytes(changed)
            return result
        with patch.object(ctl, "atomic_write_json", side_effect=write):
            with self.assertRaises(ctl.ControlError) as caught:
                self.call(args, provider, required=True)
        self.assertIn("source candidate changed", str(caught.exception))
        self.frozen(f, before)
        self.assertEqual((f.code / "source.py").read_bytes(), changed)

    def test_append_failures_keep_original_rollback_and_completion(self):
        f = self.fixture()
        args, provider = self.args(f, f.snapshot()), self.seal(f)
        before, original = f.frozen(), ctl.append_prepared_event
        with patch.object(ctl, "append_prepared_event", side_effect=OSError("before append")):
            with self.assertRaises(OSError):
                self.call(args, provider, required=True)
        self.frozen(f, before)
        def append_then_raise(*pos, **kw):
            original(*pos, **kw)
            raise OSError("after append")
        with patch.object(ctl, "append_prepared_event", side_effect=append_then_raise):
            first = self.success(args, provider, required=True)
        after = f.frozen()
        again = self.success(args, provider, required=True)
        self.assertEqual(first["event_id"], again["event_id"])
        self.assertEqual(len(f.meta()["verification_runs"]), 1)
        self.frozen(f, after)

    def test_bad_chain_and_original_parameter_close_repair_controls(self):
        f = self.fixture()
        args, provider = self.args(f, f.snapshot()), self.seal(f)
        original = ctl.read_events
        for corruption in ("hash", "schema"):
            before = f.frozen()
            def read(*pos, **kw):
                events, detail = original(*pos, **kw)
                events = copy.deepcopy(events)
                if corruption == "hash":
                    events[0]["event_hash"] = "0" * 64
                else:
                    events[0]["schema_version"] = 99
                return events, detail
            with self.subTest(corruption=corruption), patch.object(ctl, "read_events", side_effect=read):
                with self.assertRaises(ctl.ControlError) as caught:
                    self.call(args, provider, required=True)
                self.assertEqual(caught.exception.report()["gate_id"], "event_chain")
                self.frozen(f, before)
        for updates, gate in (({"evidence": ""}, "verification_evidence"),
                              ({"metrics_json": '{"fps":true}'}, "verification_metrics"),
                              ({"close_repair": True}, "close_repair_state"),
                              ({"candidate_id": "0" * 64}, "verification_evidence")):
            before = f.frozen()
            altered = copy.copy(args)
            for key, value in updates.items():
                setattr(altered, key, value)
            with self.subTest(updates=updates):
                with self.assertRaises(ctl.ControlError) as caught:
                    self.call(altered, provider, required=True)
                self.assertEqual(caught.exception.report()["gate_id"], gate)
                self.frozen(f, before)


if __name__ == "__main__":
    unittest.main()
