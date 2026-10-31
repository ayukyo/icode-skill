"""B3e action-policy software boundary; no native host or writer authority."""
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

ctl, api = history.ctl, history.api


def invoke(function, *args, provider=None, required=False, factory=None):
    # Old source executes normally: unsupported kwargs never manufacture ERROR.
    names = inspect.signature(function).parameters
    access = {}
    if "git_provider" in names:
        access["git_provider"] = provider
    if "require_git_provider" in names:
        access["require_git_provider"] = required
    if factory is not None:
        access["inspection_provider_factory"] = factory
    return function(*args, **access)


class ActionPolicyProviderTests(unittest.TestCase):
    def setUp(self):
        self.f = history.HistoryTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def args(self, *extra):
        return ctl.build_parser().parse_args(
            ["action-policy", "--dir", str(self.f.ticket), *extra])

    def frozen(self):
        return tuple(sorted((label + "/" + p.relative_to(root).as_posix(), p.read_bytes())
                            for label, root in (("control", self.f.control), ("code", self.f.code))
                            for p in root.rglob("*") if p.is_file()))

    def seal(self):
        provider = self.f.seal()
        guard = patch.object(ctl.subprocess, "run",
                             side_effect=AssertionError("B3e raw Git fallback"))
        guard.start()
        self.addCleanup(guard.stop)
        self.assertFalse((self.f.code / ".git").exists())
        return provider

    def call(self, *extra, provider=None, required=False, main=False, factory=None):
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                if main:
                    argv = ["icode_control.py", "action-policy", "--dir",
                            str(self.f.ticket), *extra]
                    with patch("sys.argv", argv):
                        code = invoke(ctl.main, provider=provider, required=required, factory=factory)
                else:
                    code = invoke(ctl.cmd_action_policy, self.args(*extra),
                                  provider=provider, required=required, factory=factory)
        except ctl.ControlError as exc:
            return exc.exit_code, exc.report(), output.getvalue()
        raw = output.getvalue()
        return code, json.loads(raw), raw

    def rejected(self, *extra, provider=None, required=True, main=False,
                 gate="candidate_provider_context", factory=None):
        before = self.frozen()
        code, result, raw = self.call(*extra, provider=provider, required=required, main=main, factory=factory)
        self.assertEqual(code, 1, result)
        self.assertEqual(result.get("gate_id"), gate, result)
        self.assertIs(result["ok"], False)
        self.assertNotIn('"action_allowed": true', raw)
        self.assertEqual(self.frozen(), before)
        return result

    def code_ready(self):
        self.advance("plan_finalized")
        (self.f.ticket / "03_plan_final.md").write_text("native fixture plan\n", encoding="utf-8")
        return lambda *args: self.fail("projection must not execute inspection transport")

    def assert_protected_projection(self, actual, local):
        # Capability support intentionally differs; every other state/receipt
        # fact must retain its exact local interpretation.
        actual, expected = copy.deepcopy(actual), copy.deepcopy(local)
        preflight = actual.pop("capability_preflight")
        expected.pop("capability_preflight")
        self.assertEqual(preflight["profile"], "protected_provider")
        self.assertFalse(preflight["runtime_verified"])
        expected["allowed_actions"] = [name for name in expected["allowed_actions"] if name == "status"]
        if expected["allowed_actions"] != local["allowed_actions"] and expected["blocked_reason"] is None:
            expected["blocked_reason"] = "workflow_capability"
        expected["recommended_action"] = None
        self.assertEqual(actual, expected)

    def advance(self, target):
        route = ["plan_done", "review_in_progress", "review_done", "plan_finalized",
                 "code_in_progress", "code_done", "deepcheck_in_progress",
                 "deepcheck_done", "completed"]
        with patch.dict("os.environ", {"ICODE_CONTROL_TEST_MODE": "1"}):
            for state in route:
                extra = ["--delivery-verdict", "verification_pending"] if state == "completed" else []
                self.f.command("transition", "--dir", self.f.ticket, "--to", state,
                               "--skip-gates", "--request-id", "b3e:" + state, *extra)
                if state == target:
                    break

    def test_none_direct_and_main_preserve_exact_result_and_errors(self):
        before = self.frozen()
        for extra in ((), ("--action", "status"), ("--action", "plan"),
                      ("--action", "verify", "--verify-action", "intent"),
                      ("--action", "review"), ("--expected-revision", "0" * 64)):
            with self.subTest(extra=extra):
                direct = self.call(*extra)
                main = self.call(*extra, main=True)
                self.assertEqual(direct[:2], main[:2])
                self.assertEqual(direct[1].get("gate_id"),
                                 "action_policy" if "review" in extra else
                                 "revision_mismatch" if "--expected-revision" in extra else None)
        self.assertEqual(before, self.frozen())

    def test_direct_verify_reads_real_git_free_code_and_canonical_head(self):
        factory = self.code_ready()
        provider = self.seal()
        before = self.frozen()
        captured = []
        original = ctl.capture_step_inputs
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append((result, kwargs))
            return result
        with patch.object(ctl, "capture_step_inputs", side_effect=capture), \
                patch.object(ctl, "verify_event_chain", wraps=ctl.verify_event_chain) as verify:
            code, result, _ = self.call("--action", "code", provider=provider, required=True, factory=factory)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["capability_preflight"]["task_supported"])
        self.assertEqual(len(captured), 1)
        (snapshot, missing), access = captured[0]
        self.assertEqual(missing, [])
        self.assertIs(access.get("git_provider"), provider)
        self.assertIs(access.get("require_git_provider"), True)
        facts = {fact["id"]: fact for fact in snapshot["inputs"]}
        self.assertEqual(facts["final_plan"]["sha256"],
                         hashlib.sha256((self.f.ticket / "03_plan_final.md").read_bytes()).hexdigest())
        self.assertEqual(facts["workspace_head"]["roots"],
                         [{"root": self.f.code.as_posix(), "head": self.f.base}])
        for call in verify.call_args_list:
            self.assertIs(call.kwargs.get("git_provider"), provider)
            self.assertIs(call.kwargs.get("require_git_provider"), True)
        self.assertIn("facts", provider.calls)
        self.assertEqual(before, self.frozen())

    def test_main_no_action_and_non_step_action_return_unchanged_readonly_result(self):
        expected = [self.call(*extra)[1] for extra in ((), ("--action", "status"))]
        provider = self.seal()
        (self.f.ticket / ctl.TXN_NAME).write_bytes(b'{"pending":"untouched"}\n')
        before = self.frozen()
        for extra, wanted in zip(((), ("--action", "status")), expected):
            for required in (True, False):
                with self.subTest(extra=extra, required=required):
                    code, result, _ = self.call(*extra, provider=provider, required=required, main=True)
                    self.assertEqual(code, 0, result)
                    self.assert_protected_projection(result, wanted)
        self.assertEqual(before, self.frozen())

    def test_required_provider_missing_guards_every_action_shape(self):
        for extra in ((), ("--action", "status"), ("--action", "verify")):
            for main in (False, True):
                with self.subTest(extra=extra, main=main):
                    self.rejected(*extra, main=main)

    def test_wrong_foreign_root_and_nonbool_provider_are_not_admitted(self):
        provider = self.seal()
        other_api = history.load("b3e_foreign_control", "icode_control.py").candidate_module()
        class Foreign(other_api.CandidateGitProvider):
            workspace = self.f.code
            def assert_context(self): return None
            def query(self, request, *, limit): raise AssertionError("foreign query")
        wrong_root = copy.copy(provider)
        wrong_root.workspace = self.f.control
        for supplied, required in ((object(), True), (Foreign(), True),
                                   (wrong_root, True), (provider, 0), (provider, [])):
            for main in (False, True):
                with self.subTest(kind=type(supplied).__name__, required=required, main=main):
                    self.rejected(provider=supplied, required=required, main=main)

    def test_stale_before_admission_including_without_action(self):
        provider = self.seal()
        provider.expired = True
        for extra in ((), ("--action", "status"), ("--action", "verify")):
            for main in (False, True):
                with self.subTest(extra=extra, main=main):
                    self.rejected(*extra, provider=provider, main=main)

    def test_stale_after_real_event_verification_is_not_success(self):
        provider = self.seal()
        original = ctl.verify_event_chain
        def verify(*args, **kwargs):
            result = original(*args, **kwargs)
            provider.expired = True
            return result
        with patch.object(ctl, "verify_event_chain", side_effect=verify):
            self.rejected(provider=provider)

    def test_stale_after_actual_capture_is_not_success(self):
        factory = self.code_ready()
        provider = self.seal()
        original = ctl.capture_step_inputs
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            provider.expired = True
            return result
        with patch.object(ctl, "capture_step_inputs", side_effect=capture):
            self.rejected("--action", "code", provider=provider, factory=factory)

    def test_serialization_expiry_rejects_before_direct_success_output(self):
        provider = self.seal()
        original = ctl.json.dumps
        def dumps(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            if isinstance(value, dict) and "allowed_actions" in value:
                provider.expired = True
            return result
        with patch.object(ctl.json, "dumps", side_effect=dumps):
            self.rejected(provider=provider)

    def test_deadline_covers_whole_handler_not_only_individual_port_reads(self):
        factory = self.code_ready()
        provider = self.seal()
        clock = [0.0]
        original, read = ctl.json.dumps, ctl.capture_step_inputs
        def capture(*args, **kwargs):
            result = read(*args, **kwargs)
            clock[0] += api.DEFAULT_LIMITS["timeout_seconds"] * 0.6
            return result
        def dumps(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            if isinstance(value, dict) and "allowed_actions" in value:
                clock[0] += api.DEFAULT_LIMITS["timeout_seconds"] * 0.6
            return result
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(ctl, "capture_step_inputs", side_effect=capture), \
                patch.object(ctl.json, "dumps", side_effect=dumps):
            self.assertIn("deadline", self.rejected("--action", "code", provider=provider, factory=factory)["error"])

    def test_deadline_covers_final_context_guard_itself(self):
        provider = self.seal()
        original, serialize = ctl._candidate_context_guard, ctl.json.dumps
        for main, target in ((False, 1), (True, 2)):
            clock, calls, serialized = [0.0], [], [False]
            def dumps(value, *args, **kwargs):
                result = serialize(value, *args, **kwargs)
                if isinstance(value, dict) and "allowed_actions" in value:
                    serialized[0] = True
                return result
            def guard(*args, **kwargs):
                result = original(*args, **kwargs)
                if serialized[0]:
                    calls.append(1)
                    if len(calls) == target:
                        clock[0] = api.DEFAULT_LIMITS["timeout_seconds"] + 1
                return result
            with self.subTest(main=main), \
                    patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                    patch.object(ctl.json, "dumps", side_effect=dumps), \
                    patch.object(ctl, "_candidate_context_guard", side_effect=guard):
                self.assertIn("deadline", self.rejected(provider=provider, main=main)["error"])
                self.assertEqual(len(calls), target)

    def test_main_rechecks_after_buffer_read_before_outer_success_output(self):
        provider = self.seal()
        class ExpiringBuffer(io.StringIO):
            def getvalue(buffer):
                value = super().getvalue()
                provider.expired = True
                return value
        # Outer capture is allocated before replacing the inner main buffer.
        output = io.StringIO()
        before = self.frozen()
        with patch.object(ctl.io, "StringIO", ExpiringBuffer), \
                patch("sys.argv", ["icode_control.py", "action-policy", "--dir", str(self.f.ticket)]), \
                contextlib.redirect_stdout(output):
            code = invoke(ctl.main, provider=provider, required=True)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1, result)
        self.assertEqual(result.get("gate_id"), "candidate_provider_context", result)
        self.assertNotIn("allowed_actions", result)
        self.assertEqual(before, self.frozen())

    def test_main_deadline_spans_handler_and_buffer_handoff(self):
        provider = self.seal()
        clock = [0.0]
        class LateBuffer(io.StringIO):
            def getvalue(buffer):
                value = super().getvalue()
                clock[0] = api.DEFAULT_LIMITS["timeout_seconds"] + 1
                return value
        output = io.StringIO()
        before = self.frozen()
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(ctl.io, "StringIO", LateBuffer), \
                patch("sys.argv", ["icode_control.py", "action-policy", "--dir", str(self.f.ticket)]), \
                contextlib.redirect_stdout(output):
            code = invoke(ctl.main, provider=provider, required=True)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1, result)
        self.assertIn("deadline", result.get("error", ""))
        self.assertNotIn("allowed_actions", result)
        self.assertEqual(before, self.frozen())

    def test_exact_dispatch_does_not_open_fake_handlers_or_pending_commands(self):
        provider = self.seal()
        called = []
        fake = lambda *args, **kwargs: called.append(True)
        for cmd, handler in (("action-policy", fake), ("step", ctl.cmd_step),
                             ("check-outputs", ctl.cmd_check_outputs),
                             ("artifact", ctl.cmd_artifact), ("inspection", ctl.cmd_inspection),
                             ("transition", ctl.cmd_transition), ("validate", ctl.cmd_validate),
                             ("snapshot", ctl.cmd_snapshot), ("candidate", ctl.cmd_action_policy)):
            output = io.StringIO()
            before = self.frozen()
            with self.subTest(cmd=cmd), patch.object(ctl, "build_parser") as parser, \
                    contextlib.redirect_stdout(output):
                parser.return_value.parse_args.return_value = SimpleNamespace(
                    cmd=cmd, func=handler, dir=str(self.f.ticket))
                code = invoke(ctl.main, provider=provider, required=True)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["gate_id"],
                             "candidate_business_propagation_pending")
            self.assertEqual(before, self.frozen())
        self.assertEqual(called, [])

    def test_revision_status_and_unknown_action_keep_existing_failures(self):
        expected = [self.call(*extra)[:2] for extra in (
            ("--expected-revision", "0" * 64), ("--action", "review"), ("--action", "absent"))]
        provider = self.seal()
        for extra, wanted in zip((
                ("--expected-revision", "0" * 64), ("--action", "review"),
                ("--action", "absent")), expected):
            with self.subTest(extra=extra):
                if "review" in extra:
                    self.rejected(*extra, provider=provider, gate="workflow_capability")
                else:
                    self.assertEqual(self.call(*extra, provider=provider, required=True)[:2], wanted)

    def test_real_open_execution_limits_actions_and_keeps_receipts_unchanged(self):
        self.f.command("step", "--dir", self.f.ticket, "--step", "plan", "--phase", "start",
                       "--attempt", "b3e-open", "--request", "b3e:start")
        expected = self.call()[1]
        self.assertEqual(expected["blocked_reason"], "open_execution")
        self.assertEqual(expected["allowed_actions"], ["status"])
        provider = self.seal()
        before = self.frozen()
        self.assert_protected_projection(self.call(provider=provider, required=True)[1], expected)
        self.rejected("--action", "plan", provider=provider, gate="workflow_capability")
        self.assertEqual(before, self.frozen())

    def test_real_closed_ticket_remains_readonly_and_action_limited(self):
        self.advance("completed")
        self.f.command("close-phase", "--dir", self.f.ticket, "--phase", "close_planned",
                       "--request-id", "b3e:close")
        expected = self.call()[1]
        self.assertEqual(expected["blocked_reason"], "closed_ticket")
        self.assertEqual(expected["allowed_actions"], ["status"])
        provider = self.seal()
        before = self.frozen()
        self.assert_protected_projection(self.call(provider=provider, required=True, main=True)[1], expected)
        self.assertEqual(before, self.frozen())

    def test_verify_variant_intent_default_and_restrictions_are_preserved(self):
        extras = [("--action", "verify"), ("--action", "verify", "--verify-action", "default"),
                  ("--action", "verify", "--verify-action", "build"),
                  ("--action", "verify", "--verify-action", "deploy"),
                  ("--action", "status", "--verify-action", "build")]
        expected = [self.call(*extra)[:2] for extra in extras]
        self.assertEqual(expected[0][1]["verify_action"], "intent")
        self.assertEqual(expected[1][1]["verify_action"], "intent")
        self.assertEqual(expected[2][1]["side_effect"], "managed_write")
        self.assertEqual(expected[3][1]["gate_id"], "action_policy")
        provider = self.seal()
        for extra, wanted in zip(extras, expected):
            with self.subTest(extra=extra):
                if "verify" in extra:
                    self.rejected(*extra, provider=provider, gate="workflow_capability")
                else:
                    self.assertEqual(self.call(*extra, provider=provider, required=True)[:2], wanted)

    def test_corrupt_event_and_metadata_are_still_rejected_without_writes(self):
        provider = self.seal()
        path = self.f.ticket / ctl.EVENTS_NAME
        original_events = path.read_bytes()
        rows = path.read_text(encoding="utf-8").splitlines()
        event = json.loads(rows[-1])
        event["schema_version"] = 2
        rows[-1] = json.dumps(event)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        self.rejected(provider=provider, gate="event_chain")
        path.write_bytes(original_events)
        path = self.f.ticket / ctl.METADATA_NAME
        original = path.read_bytes()
        meta = json.loads(original)
        meta["requirement"] = 42
        path.write_text(json.dumps(meta), encoding="utf-8", newline="\n")
        before = self.frozen()
        code, result, _ = self.call(provider=provider, required=True)
        self.assertEqual(code, 1, result)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("gate_id"), "metadata_schema", result)
        self.assertEqual(before, self.frozen())

    def test_real_required_step_input_missing_preserves_old_port_gate(self):
        self.advance("plan_finalized")
        expected = self.call("--action", "code")[:2]
        self.assertEqual(expected[1].get("gate_id"), "step_ports")
        provider = self.seal()
        factory = lambda *args: self.fail("missing inputs must not run inspection")
        self.assertEqual(self.call("--action", "code", provider=provider, required=True, factory=factory)[:2], expected)

    def test_query_error_bad_facts_and_late_fact_result_cannot_admit_action(self):
        factory = self.code_ready()
        provider = self.seal()
        original = provider.query
        def late(*args, **kwargs):
            result = original(*args, **kwargs)
            provider.expired = True
            return result
        for response in (api.CandidateReadResult(b"", 1, False, "unavailable"),
                         api.CandidateReadResult(b"{}", 0, False), None):
            provider.expired = False
            before = self.frozen()
            with self.subTest(response=response), patch.object(provider, "query",
                    side_effect=late if response is None else lambda *a, **k: response):
                code, result, raw = self.call("--action", "code", provider=provider, required=True, factory=factory)
                self.assertEqual(code, 1, result)
                self.assertFalse(result["ok"])
                self.assertNotIn('"action_allowed": true', raw)
                self.assertEqual(before, self.frozen())

    def test_ci_has_strict_new_gate_direct_dependency_paths_and_legacy_consumer(self):
        text = (history.ROOT / ".github/workflows/candidate-provider-software.yml").read_text(encoding="utf-8")
        strict = "s=unittest.defaultTestLoader.discover('tests', pattern='test_candidate_action_policy_provider_unit.py'); assert s.countTestCases()==22, s.countTestCases(); r=unittest.TextTestRunner(verbosity=2).run(s); raise SystemExit(not r.wasSuccessful() or bool(r.skipped))"
        self.assertEqual(text.count(strict), 1)
        for path in ("tests/test_candidate*", "tests/test_ui_action_policy.py",
                     "mcp/workflow-gate/gates.json", "schemas/ticket-metadata.schema.json",
                     "schemas/ticket-event.schema.json"):
            self.assertIn("      - '" + path + "'\n", text)
        self.assertIn("python -X utf8 -m pytest -p no:anyio tests/test_ui_action_policy.py::test_action_policy_is_control_plane_truth_and_revision_guard", text)
        self.assertIn("tests/test_review_followup.py::test_early_verify_only_build_and_plan", text)


if __name__ == "__main__":
    unittest.main()
