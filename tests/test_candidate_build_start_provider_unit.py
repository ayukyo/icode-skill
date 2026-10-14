"""B3b build-start software contract; real ports, source bytes and directory."""
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

import test_candidate_dispatch_unit as dispatch
import test_candidate_history_unit as history

ctl, api = history.ctl, history.api


def invoke(function, *args, provider=None, required=False, **kwargs):
    # Let old code reach real behavior: missing kwargs is not the RED oracle.
    names = inspect.signature(function).parameters
    if "git_provider" in names:
        kwargs["git_provider"] = provider
    if "require_git_provider" in names:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class BuildStartProviderTests(unittest.TestCase):
    def fixture(self, *, sealed=True):
        helper = dispatch.CandidateDispatchTests(methodName="runTest")
        self.addCleanup(helper.doCleanups)
        f = helper.fixture()
        directory = f.code / "build"
        directory.mkdir()
        (directory / "keep.bin").write_bytes(b"existing build data\n")
        expected = f.snapshot()
        provider = helper.seal(f) if sealed else None
        return f, provider, directory, expected

    def seal(self, f):
        with patch.object(f, "snapshot", side_effect=lambda: ctl.candidate_snapshot(
                f.ticket, f.meta(), base=f.base)):
            return f.seal()

    def args(self, f, directory, candidate, request="b3b"):
        return ctl.build_parser().parse_args(["build-start", "--dir", str(f.ticket),
            "--build-dir", str(directory), "--candidate-id", candidate["candidate_id"],
            "--parameters-json", json.dumps({"configure_command": ["icode-no-execute-configure"],
                "build_command": ["icode-no-execute-build"]}), "--request-id", request])

    def run_build(self, f, provider, directory, candidate, request="b3b", *, required=True, main=False):
        args = self.args(f, directory, candidate, request)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                if main:
                    with patch.object(ctl, "build_parser") as parser:
                        parser.return_value.parse_args.return_value = args
                        code = invoke(ctl.main, provider=provider, required=required)
                else:
                    code = invoke(ctl.cmd_build_start, args, provider=provider, required=required)
        except ctl.ControlError as exc:
            self.fail("protected build-start must complete: " + str(exc))
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"], result)
        return result

    def frozen(self, f, before):
        self.assertEqual(before, f.frozen())
        self.assertFalse((f.ticket / ctl.TXN_NAME).exists())

    def receipt(self, f):
        return ctl.verification_evidence_module().build_starts(f.meta())[-1]

    def test_none_cli_observes_without_running_or_clearing(self):
        f, provider, directory, candidate = self.fixture(sealed=False)
        first = self.run_build(f, provider, directory, candidate, required=False, main=True)
        receipt, before = copy.deepcopy(self.receipt(f)), f.frozen()
        replay = self.run_build(f, None, directory, candidate, required=False, main=True)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(first["event_id"], replay["event_id"])
        self.assertEqual(self.receipt(f), receipt)
        self.assertFalse(receipt["directory"]["empty"])
        self.assertEqual((directory / "keep.bin").read_bytes(), b"existing build data\n")
        self.frozen(f, before)

    def test_provider_receipt_real_code_directory_and_exact_parameters(self):
        f, provider, directory, candidate = self.fixture()
        self.assertFalse((f.code / ".git").exists())
        self.assertNotEqual(f.code, f.control)
        with patch.object(ctl, "candidate_snapshot", wraps=ctl.candidate_snapshot) as capture, \
                patch.object(ctl, "verify_event_chain", wraps=ctl.verify_event_chain) as verify, \
                patch.object(ctl, "commit_metadata_and_event", wraps=ctl.commit_metadata_and_event) as publish:
            result = self.run_build(f, provider, directory, candidate)
        self.assertEqual(capture.call_count, 2)
        first, final = capture.call_args_list
        self.assertEqual(first.args, final.args)
        self.assertEqual(first.kwargs, final.kwargs)
        self.assertIs(first.kwargs["git_provider"], provider)
        self.assertIs(first.kwargs["require_git_provider"], True)
        self.assertIs(first.kwargs["events"], final.kwargs["events"])
        self.assertIs(publish.call_args.kwargs["git_provider"], provider)
        self.assertIs(publish.call_args.kwargs["require_git_provider"], True)
        self.assertTrue(callable(publish.call_args.kwargs["before_publish"]))
        for call in verify.call_args_list:
            if call.kwargs.get("semantic") is False:
                self.assertNotIn("git_provider", call.kwargs)
            else:
                self.assertIs(call.kwargs["git_provider"], provider)
                self.assertIs(call.kwargs["require_git_provider"], True)
        receipt = self.receipt(f)
        self.assertEqual(receipt["event_id"], result["event_id"])
        self.assertEqual(receipt["source_candidate"]["candidate_id"], candidate["candidate_id"])
        row = next(r for r in receipt["source_candidate"]["manifest"] if r["path"] == "source.py")
        self.assertEqual(row["sha256"], hashlib.sha256((f.code / "source.py").read_bytes()).hexdigest())
        info = directory.stat()
        self.assertEqual((receipt["directory"]["device"], receipt["directory"]["inode"]),
                         (info.st_dev, info.st_ino))
        self.assertEqual(receipt["build_dir"], str(directory.resolve()))
        self.assertFalse(receipt["directory"]["empty"])
        self.assertEqual((directory / "keep.bin").read_bytes(), b"existing build data\n")
        events, problems = invoke(ctl.verify_event_chain, f.ticket, f.meta(), provider=provider, required=True)
        self.assertFalse(problems, problems)
        self.assertEqual(events[-1]["payload"]["build_start_control"], 1)
        self.assertEqual(events[-1]["payload"]["build_start_receipt"], receipt)

    def test_main_exact_candidate_and_build_pairs_and_unknown_rejection(self):
        f, provider, directory, candidate = self.fixture()
        first = self.run_build(f, provider, directory, candidate, main=True)
        self.assertIn("event_id", first)
        before = f.frozen()
        args = ctl.build_parser().parse_args(["candidate", "--dir", str(f.ticket), "--phase", "inspect"])
        output = io.StringIO()
        with patch.object(ctl, "build_parser") as parser, contextlib.redirect_stdout(output):
            parser.return_value.parse_args.return_value = args
            code = invoke(ctl.main, provider=provider, required=True)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertFalse(json.loads(output.getvalue())["freshness_ok"])
        called = []
        def unknown(*args, **kwargs):
            called.append((args, kwargs))
        for command, handler in (("build-start", unknown), ("candidate", ctl.cmd_build_start),
                                 ("build-start", ctl.cmd_candidate), ("step", ctl.cmd_step)):
            with self.subTest(command=command, handler=handler.__name__):
                output = io.StringIO()
                with patch.object(ctl, "build_parser") as parser, contextlib.redirect_stdout(output):
                    parser.return_value.parse_args.return_value = SimpleNamespace(cmd=command, func=handler)
                    code = invoke(ctl.main, provider=provider, required=True)
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output.getvalue())["gate_id"], "candidate_business_propagation_pending")
        self.assertFalse(called)
        self.frozen(f, before)

    def test_admission_missing_foreign_root_class_and_falsey_required(self):
        f, provider, directory, candidate = self.fixture()
        foreign = history.load("b3b_foreign", "icode_candidate.py")
        class Other(foreign.CandidateGitProvider):
            workspace = f.code
            def assert_context(self):
                return None
            def query(self, request, *, limit):
                raise AssertionError("foreign query must not run")
        wrong_root = copy.copy(provider)
        wrong_root.workspace = f.control
        before = f.frozen()
        for supplied, required in ((None, True), (object(), True), (wrong_root, True),
                                  (Other(), True), (provider, 0), (provider, [])):
            with self.subTest(provider=type(supplied).__name__, required=required):
                with self.assertRaises(ctl.ControlError) as caught:
                    invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                           provider=supplied, required=required)
                self.assertEqual(caught.exception.report()["gate_id"], "candidate_provider_context")
                self.frozen(f, before)

    def test_pending_and_invalid_context_never_run_recovery(self):
        f, provider, directory, candidate = self.fixture()
        before = f.frozen()
        journal = f.ticket / ctl.TXN_NAME
        journal.write_bytes(b'{"diagnostic":"pending"}\n')
        for expired in (True, False):
            provider.expired = expired
            with self.subTest(expired=expired), \
                    patch.object(ctl, "recover_pending_transaction", side_effect=AssertionError("recovery ran")):
                with self.assertRaises(ctl.ControlError) as caught:
                    invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                           provider=provider, required=True)
                self.assertEqual(caught.exception.report()["gate_id"],
                                 "candidate_provider_context" if expired else "candidate_transaction_pending")
                self.assertEqual(journal.read_bytes(), b'{"diagnostic":"pending"}\n')
                self.assertEqual(before, f.frozen())

    def test_healthy_replay_rechecks_source_without_reobserving_receipt(self):
        f, _provider, directory, candidate = self.fixture(sealed=False)
        first = self.run_build(f, None, directory, candidate, required=False)
        provider = self.seal(f)
        receipt, before = copy.deepcopy(self.receipt(f)), f.frozen()
        (directory / "later.bin").write_bytes(b"later output")
        with patch.object(ctl, "candidate_snapshot", wraps=ctl.candidate_snapshot) as capture, \
                patch.object(ctl.verification_evidence_module(), "safe_directory",
                             side_effect=AssertionError("replay replaced directory observation")):
            replay = self.run_build(f, provider, directory, candidate)
        self.assertGreaterEqual(capture.call_count, 1)
        for call in capture.call_args_list:
            self.assertIs(call.kwargs["git_provider"], provider)
            self.assertIs(call.kwargs["require_git_provider"], True)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(first["event_id"], replay["event_id"])
        self.assertEqual(receipt, self.receipt(f))
        self.assertEqual((directory / "later.bin").read_bytes(), b"later output")
        self.frozen(f, before)

    def test_replay_rejects_real_source_drift_query_failure_and_expiry(self):
        for fault in ("source", "query", "expired", "post_query", "post_capture"):
            with self.subTest(fault=fault):
                if fault != "source":
                    f.doCleanups()
                f, _provider, directory, candidate = self.fixture(sealed=False)
                self.run_build(f, None, directory, candidate, required=False)
                provider = self.seal(f)
                before = f.frozen()
                original, original_capture = provider.query, ctl.candidate_snapshot
                def capture(*args, **kwargs):
                    result = original_capture(*args, **kwargs)
                    if fault == "post_capture":
                        provider.expired = True
                    return result
                def query(*args, **kwargs):
                    if fault == "query":
                        return api.CandidateReadResult(b"", 1, False, "fixture query failure")
                    result = original(*args, **kwargs)
                    if fault == "post_query":
                        provider.expired = True
                    return result
                if fault == "source":
                    (f.code / "source.py").write_bytes(b"value = 7\n")
                provider.expired = fault == "expired"
                with patch.object(provider, "query", side_effect=query), \
                        patch.object(ctl, "candidate_snapshot", side_effect=capture):
                    with self.assertRaises(ctl.ControlError):
                        invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                               provider=provider, required=True)
                self.frozen(f, before)

    def test_expiry_at_verify_capture_transaction_and_metadata_checkpoints(self):
        for checkpoint in ("verify", "capture", "txn", "metadata"):
            with self.subTest(checkpoint=checkpoint):
                if checkpoint != "verify":
                    f.doCleanups()
                f, provider, directory, candidate = self.fixture()
                before = f.frozen()
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
                        invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                               provider=provider, required=True)
                self.frozen(f, before)

    def test_live_source_drift_before_append_rolls_back_only_control(self):
        f, provider, directory, candidate = self.fixture()
        before, write_original, fired = f.frozen(), ctl.atomic_write_json, []
        def write(path, value):
            result = write_original(path, value)
            if Path(path).name == ctl.METADATA_NAME and not fired:
                fired.append(True)
                (f.code / "source.py").write_bytes(b"value = 99\n")
            return result
        with patch.object(ctl, "atomic_write_json", side_effect=write):
            with self.assertRaises(ctl.ControlError) as caught:
                invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                       provider=provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "build_start_receipt")
        self.assertEqual((f.code / "source.py").read_bytes(), b"value = 99\n")
        self.assertEqual((directory / "keep.bin").read_bytes(), b"existing build data\n")
        self.assertIsNone(provider.assert_context())
        self.frozen(f, before)

    def test_query_failure_and_post_query_expiry_do_not_publish(self):
        for fault in ("query", "post_query"):
            with self.subTest(fault=fault):
                if fault != "query":
                    f.doCleanups()
                f, provider, directory, candidate = self.fixture()
                before, original = f.frozen(), provider.query
                def query(*args, **kwargs):
                    if fault == "query":
                        return api.CandidateReadResult(b"", 1, False, "fixture query failure")
                    result = original(*args, **kwargs)
                    provider.expired = True
                    return result
                with patch.object(provider, "query", side_effect=query):
                    with self.assertRaises(ctl.ControlError):
                        invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                               provider=provider, required=True)
                self.frozen(f, before)

    def test_candidate_mismatch_directory_and_parameter_errors_do_not_write(self):
        f, provider, directory, candidate = self.fixture()
        before = f.frozen()
        for fault in ("candidate", "directory", "parameters"):
            args = self.args(f, directory, candidate)
            if fault == "candidate":
                args.candidate_id = "0" * 64
            elif fault == "directory":
                args.build_dir = str(f.code)
            else:
                args.parameters_json = "{}"
            with self.subTest(fault=fault), self.assertRaises(ctl.ControlError):
                invoke(ctl.cmd_build_start, args, provider=provider, required=True)
            self.frozen(f, before)

    def test_corrupt_chain_and_append_failures_keep_original_completion(self):
        f, provider, directory, candidate = self.fixture()
        before = f.frozen()
        with patch.object(ctl, "append_prepared_event", side_effect=OSError("before append")):
            with self.assertRaisesRegex(OSError, "before append"):
                invoke(ctl.cmd_build_start, self.args(f, directory, candidate),
                       provider=provider, required=True)
        self.frozen(f, before)
        original = ctl.append_prepared_event
        def committed_then_error(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("after append")
        with patch.object(ctl, "append_prepared_event", side_effect=committed_then_error):
            result = self.run_build(f, provider, directory, candidate, "committed")
        self.assertEqual(self.receipt(f)["event_id"], result["event_id"])
        self.assertEqual(len(ctl.verification_evidence_module().build_starts(f.meta())), 1)
        self.assertFalse((f.ticket / ctl.TXN_NAME).exists())
        self.assertNotEqual(before, f.frozen())
        path = f.ticket / ctl.EVENTS_NAME
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[-1]["schema_version"] = 2
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n")
        corrupt = f.frozen()
        with self.assertRaises(ctl.ControlError) as caught:
            invoke(ctl.cmd_build_start, self.args(f, directory, candidate, "corrupt"),
                   provider=provider, required=True)
        self.assertEqual(caught.exception.report()["gate_id"], "event_chain")
        violations = caught.exception.report()["violations"]
        self.assertTrue(any("event_hash" in item for item in violations), violations)
        self.assertTrue(any("event_schema" in item for item in violations), violations)
        self.frozen(f, corrupt)


if __name__ == "__main__":
    unittest.main()
