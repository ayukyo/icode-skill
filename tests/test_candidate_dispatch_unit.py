"""B3a candidate-only software dispatch; real ports and bounded transaction checks."""
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
import test_candidate_freshness as freshness

ctl, api = history.ctl, history.api


def invoke(function, *args, provider=None, required=False, **kwargs):
    names = inspect.signature(function).parameters
    if "git_provider" in names:
        kwargs["git_provider"] = provider
    if "require_git_provider" in names:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class CandidateDispatchTests(unittest.TestCase):
    def fixture(self, *, legacy=False):
        fixture = history.HistoryTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        command = fixture.command
        def bootstrap(*args, **kwargs):
            if args[0] == "candidate":
                return {}
            return command(*args, **kwargs)
        with patch.object(fixture, "command", side_effect=bootstrap) if legacy else contextlib.nullcontext():
            fixture.setUp()
        return fixture

    def seal(self, fixture):
        # Explicit real Git baseline only for exporting the disposable fixture.
        with patch.object(fixture, "snapshot", side_effect=lambda: ctl.candidate_snapshot(
                fixture.ticket, fixture.meta(), base=fixture.base)):
            return fixture.seal()

    def args(self, fixture, request="b3a"):
        return ctl.build_parser().parse_args(["candidate", "--dir", str(fixture.ticket),
            "--phase", "capture", "--request-id", request])

    def capture(self, fixture, provider, request="b3a", required=True):
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = invoke(ctl.cmd_candidate, self.args(fixture, request),
                              provider=provider, required=required)
        except ctl.ControlError as exc:
            self.fail("candidate-only protected capture must complete: " + str(exc))
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"], result)
        return result

    def main(self, fixture, phase="inspect", *, provider=None, required=False, extra=()):
        output = io.StringIO()
        argv = ["icode_control.py", "candidate", "--dir", str(fixture.ticket), "--phase", phase, *extra]
        with patch("sys.argv", argv), contextlib.redirect_stdout(output):
            code = invoke(ctl.main, provider=provider, required=required)
        return code, json.loads(output.getvalue())

    def assert_frozen(self, fixture, before):
        self.assertEqual(before, fixture.frozen())
        self.assertFalse((fixture.ticket / ctl.TXN_NAME).exists())

    def test_none_real_cli_capture_replay_and_inspect_preserved(self):
        f = self.fixture()
        first = self.capture(f, None, required=False)
        before = f.frozen()
        replay = self.capture(f, None, required=False)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(first["event_id"], replay["event_id"])
        code, result = self.main(f)
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["freshness_ok"])
        self.assert_frozen(f, before)

    def test_legacy_enable_uses_provider_head_and_real_code(self):
        f = self.fixture(legacy=True)
        self.assertNotIn("candidate_tracking", f.meta())
        provider = self.seal(f)
        with patch.object(ctl, "candidate_snapshot", wraps=ctl.candidate_snapshot) as capture, \
                patch.object(ctl, "verify_event_chain", wraps=ctl.verify_event_chain) as verify, \
                patch.object(ctl, "commit_metadata_and_event", wraps=ctl.commit_metadata_and_event) as publish:
            result = self.capture(f, provider)
        self.assertEqual(capture.call_count, 2)
        for call in capture.call_args_list:
            self.assertIs(call.kwargs.get("git_provider"), provider)
            self.assertIs(call.kwargs.get("require_git_provider"), True)
        first, final = capture.call_args_list
        for name in ("base", "excluded_side_effects", "limits", "events"):
            self.assertEqual(first.kwargs[name], final.kwargs[name])
        self.assertIs(publish.call_args.kwargs.get("git_provider"), provider)
        self.assertIs(publish.call_args.kwargs.get("require_git_provider"), True)
        self.assertTrue(callable(publish.call_args.kwargs.get("before_publish")))
        for call in verify.call_args_list:
            if call.kwargs.get("semantic") is False:
                # Existing pure envelope construction, after a full before verify.
                self.assertNotIn("git_provider", call.kwargs)
            else:
                self.assertIs(call.kwargs.get("git_provider"), provider)
                self.assertIs(call.kwargs.get("require_git_provider"), True)
        current = f.meta()["candidate_tracking"]["current"]
        self.assertEqual(current["base"], f.base)
        self.assertEqual(current["candidate_id"], result["candidate_id"])
        self.assertEqual(current["identity"]["checkout_realpath"], str(f.code))
        row = next(row for row in current["manifest"] if row["path"] == "source.py")
        self.assertEqual(row["sha256"], hashlib.sha256((f.code / "source.py").read_bytes()).hexdigest())
        events, problems = invoke(ctl.verify_event_chain, f.ticket, f.meta(), provider=provider, required=True)
        self.assertFalse(problems, problems)
        self.assertEqual(events[-1]["payload"]["candidate_control"], 1)
        self.assertEqual(events[-1]["payload"]["set"]["candidate_tracking"], f.meta()["candidate_tracking"])
        self.assertIsNone(provider.assert_context())

    def test_main_candidate_capture_and_idempotent_replay(self):
        f = self.fixture()
        provider = self.seal(f)
        extra = ("--request-id", "dispatch-capture")
        code, first = self.main(f, "capture", provider=provider, required=True, extra=extra)
        self.assertEqual(code, 0, first)
        self.assertTrue(first["ok"], first)
        self.assertFalse(first["already_applied"])
        before = f.frozen()
        code, replay = self.main(f, "capture", provider=provider, required=True, extra=extra)
        self.assertEqual(code, 0, replay)
        self.assertTrue(replay["already_applied"], replay)
        self.assertEqual(first["event_id"], replay["event_id"])
        self.assert_frozen(f, before)

    def test_inspect_query_exit_is_not_freshness_acceptance(self):
        f = self.fixture()
        provider, before = self.seal(f), f.frozen()
        for required_steps, expected_code in (((), 0), (("--require-step", "code"), 1)):
            with self.subTest(require_steps=required_steps):
                code, result = self.main(f, provider=provider, required=True, extra=required_steps)
                self.assertEqual(code, expected_code, result)
                self.assertIn("freshness_ok", result)
                self.assertFalse(result["freshness_ok"], result)
                self.assertEqual(result["ok"], not required_steps)
                self.assertEqual(result["state"], "review_required")
                self.assert_frozen(f, before)

    def test_real_review_receipt_freshness_then_source_drift(self):
        f = self.fixture()
        f.repo, f.call = f.code, f.command
        freshness.CandidateTests.finish_review(f, "code", attempt="b3a-code")
        provider, before = self.seal(f), f.frozen()
        with patch.object(api, "delivery_freshness", wraps=api.delivery_freshness) as freshness_call:
            result = invoke(ctl.delivery_freshness, f.ticket, required_steps=("code",),
                            provider=provider, required=True)
        self.assertIs(freshness_call.call_args.kwargs.get("git_provider"), provider)
        self.assertIs(freshness_call.call_args.kwargs.get("require_git_provider"), True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["applicable_receipts"]), 1)
        self.assertEqual(result["applicable_receipts"][0]["step"], "code")
        (f.code / "source.py").write_bytes(b"value = 3\n")
        stale = invoke(ctl.delivery_freshness, f.ticket, required_steps=("code",),
                       provider=provider, required=True)
        self.assertFalse(stale["ok"], stale)
        self.assert_frozen(f, before)

    def test_admission_missing_wrong_root_foreign_and_falsey(self):
        f = self.fixture()
        provider, before = self.seal(f), f.frozen()
        foreign = freshness.ctl.candidate_module()
        class Other(foreign.CandidateGitProvider):
            workspace = f.code
            def assert_context(self):
                return None
            def query(self, request, *, limit):
                raise AssertionError("foreign provider must never query")
        wrong_root = copy.copy(provider)
        wrong_root.workspace = f.control
        for supplied, required in ((None, True), (object(), True), (wrong_root, True),
                                   (Other(), True), (provider, 0)):
            with self.subTest(provider=type(supplied).__name__, required=required):
                result = invoke(ctl.delivery_freshness, f.ticket, required_steps=(),
                                provider=supplied, required=required)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["state"], "blocked", result)
                with self.assertRaises(ctl.ControlError):
                    invoke(ctl.cmd_candidate, self.args(f), provider=supplied, required=required)
                for extra, expected in (((), 0), (("--require-step", "code"), 1)):
                    code, query = self.main(f, provider=supplied, required=required, extra=extra)
                    self.assertEqual(code, expected, query)
                    self.assertIn("freshness_ok", query)
                    self.assertFalse(query["freshness_ok"], query)
                    self.assertEqual(query["state"], "blocked", query)
                self.assert_frozen(f, before)
        provider._raw_guard.stop()
        legacy = self.fixture(legacy=True)
        other, frozen = self.seal(legacy), legacy.frozen()
        result = invoke(ctl.delivery_freshness, legacy.ticket, required_steps=(),
                        provider=other, required=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["state"], "blocked", result)
        self.assert_frozen(legacy, frozen)

    def test_unconnected_handler_unknown_handler_and_phase_reject(self):
        f = self.fixture()
        provider, before = self.seal(f), f.frozen()
        parsed = ctl.build_parser().parse_args(["build-start", "--dir", str(f.ticket),
            "--build-dir", str(f.code / "build"), "--candidate-id", "0" * 64,
            "--parameters-json", "{}", "--request-id", "blocked"])
        output = io.StringIO()
        with patch.object(ctl, "build_parser") as parser, contextlib.redirect_stdout(output):
            parser.return_value.parse_args.return_value = parsed
            code = invoke(ctl.main, provider=provider, required=True)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["gate_id"], "candidate_business_propagation_pending")
        called = []
        parsed = SimpleNamespace(cmd="candidate", func=lambda args: called.append(args))
        output = io.StringIO()
        with patch.object(ctl, "build_parser") as parser, contextlib.redirect_stdout(output):
            parser.return_value.parse_args.return_value = parsed
            code = invoke(ctl.main, provider=provider, required=True)
        self.assertEqual(code, 1)
        self.assertFalse(called)
        args = self.args(f)
        args.phase = "unknown"
        with self.assertRaises(ctl.ControlError) as caught:
            invoke(ctl.cmd_candidate, args, provider=provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "candidate_business_propagation_pending")
        self.assert_frozen(f, before)

    def test_pending_recovery_and_replay_context_do_not_publish(self):
        f = self.fixture()
        provider = self.seal(f)
        self.capture(f, provider, "prior")
        before = f.frozen()
        provider.expired = True
        with self.assertRaises(ctl.ControlError):
            invoke(ctl.cmd_candidate, self.args(f, "prior"), provider=provider, required=True)
        self.assert_frozen(f, before)
        provider.expired = False
        path = f.ticket / ctl.TXN_NAME
        # An intentionally invalid pending journal must not be interpreted/recovered.
        path.write_bytes(b'{"diagnostic":"pending"}\n')
        pending = path.read_bytes()
        with patch.object(ctl, "recover_pending_transaction", side_effect=AssertionError("provider recovery unsupported")):
            with self.assertRaises(ctl.ControlError) as caught:
                invoke(ctl.cmd_candidate, self.args(f), provider=provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "candidate_transaction_pending")
        self.assertEqual(pending, path.read_bytes())
        self.assertEqual(before, f.frozen())

    def test_context_expiry_at_verify_capture_and_publication_checkpoints(self):
        for checkpoint in ("verify", "capture", "txn", "metadata"):
            with self.subTest(checkpoint=checkpoint):
                if checkpoint != "verify":
                    f.doCleanups()
                f = self.fixture()
                provider, before = self.seal(f), f.frozen()
                original_verify, original_capture, original_write = ctl.verify_event_chain, ctl.candidate_snapshot, ctl.atomic_write_json
                def verify(*args, **kwargs):
                    result = original_verify(*args, **kwargs)
                    if checkpoint == "verify":
                        provider.expired = True
                    return result
                def capture(*args, **kwargs):
                    result = original_capture(*args, **kwargs)
                    if checkpoint == "capture":
                        provider.expired = True
                    return result
                fired = []
                def write(path, value):
                    result = original_write(path, value)
                    target = ctl.TXN_NAME if checkpoint == "txn" else ctl.METADATA_NAME
                    if checkpoint in {"txn", "metadata"} and Path(path).name == target and not fired:
                        fired.append(True)
                        provider.expired = True
                    return result
                with patch.object(ctl, "verify_event_chain", side_effect=verify), \
                        patch.object(ctl, "candidate_snapshot", side_effect=capture), \
                        patch.object(ctl, "atomic_write_json", side_effect=write):
                    with self.assertRaises(ctl.ControlError):
                        invoke(ctl.cmd_candidate, self.args(f), provider=provider, required=True)
                self.assert_frozen(f, before)

    def test_late_live_bytes_drift_rolls_back_before_append(self):
        f = self.fixture()
        provider, before = self.seal(f), f.frozen()
        original_write, fired = ctl.atomic_write_json, []
        def write(path, value):
            result = original_write(path, value)
            if Path(path).name == ctl.METADATA_NAME and not fired:
                fired.append(True)
                (f.code / "source.py").write_bytes(b"value = 99\n")
            return result
        with patch.object(ctl, "atomic_write_json", side_effect=write):
            with self.assertRaises(ctl.ControlError) as caught:
                invoke(ctl.cmd_candidate, self.args(f), provider=provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "candidate_capture")
        self.assertIsNone(provider.assert_context())
        self.assertEqual((f.code / "source.py").read_bytes(), b"value = 99\n")
        self.assert_frozen(f, before)

    def test_query_failure_and_post_query_expiry_never_fresh(self):
        f = self.fixture()
        provider, before = self.seal(f), f.frozen()
        with patch.object(provider, "query", return_value=api.CandidateReadResult(b"", 1, False, "fixture query failed")):
            result = invoke(ctl.delivery_freshness, f.ticket, required_steps=(),
                            provider=provider, required=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["state"], "blocked")
        original = api.delivery_freshness
        def expire(*args, **kwargs):
            result = original(*args, **kwargs)
            provider.expired = True
            return result
        with patch.object(api, "delivery_freshness", side_effect=expire):
            result = invoke(ctl.delivery_freshness, f.ticket, required_steps=(),
                            provider=provider, required=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["state"], "blocked")
        self.assert_frozen(f, before)

    def test_corrupt_chain_and_append_failures_keep_real_commit_rules(self):
        f = self.fixture()
        provider, before = self.seal(f), f.frozen()
        with patch.object(ctl, "append_prepared_event", side_effect=OSError("before append")):
            with self.assertRaisesRegex(OSError, "before append"):
                invoke(ctl.cmd_candidate, self.args(f), provider=provider, required=True)
        self.assert_frozen(f, before)
        original = ctl.append_prepared_event
        def committed_then_error(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("after append")
        with patch.object(ctl, "append_prepared_event", side_effect=committed_then_error):
            result = self.capture(f, provider, "committed")
        self.assertTrue(result["ok"])
        self.assertNotEqual(before, f.frozen())
        self.assertFalse((f.ticket / ctl.TXN_NAME).exists())
        events_path = f.ticket / ctl.EVENTS_NAME
        rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        rows[-1]["schema_version"] = 2
        events_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n")
        corrupt = f.frozen()
        with self.assertRaises(ctl.ControlError) as caught:
            invoke(ctl.cmd_candidate, self.args(f, "corrupt"), provider=provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "event_chain")
        violations = caught.exception.report()["violations"]
        self.assertTrue(any("event_hash" in item for item in violations), violations)
        self.assertTrue(any("event_schema" in item for item in violations), violations)
        self.assert_frozen(f, corrupt)


if __name__ == "__main__":
    unittest.main()
