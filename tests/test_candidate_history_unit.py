"""B2 historical software evidence; anonymous real ports, no native host credit."""
import copy
import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ctl = load("b2_control", "icode_control.py")
api = ctl.candidate_module()
ev = ctl.verification_evidence_module()
debt = load("b2_debt", "verification_debt.py")


def invoke(function, *args, provider=None, required=False, **kwargs):
    names = inspect.signature(function).parameters
    if "git_provider" in names:
        kwargs["git_provider"] = provider
    if "require_git_provider" in names:
        kwargs["require_git_provider"] = required
    return function(*args, **kwargs)


class Provider(api.CandidateGitProvider):
    def __init__(self, workspace, facts, trees, index, blobs, linear):
        self.workspace, self.facts, self.trees = workspace, facts, trees
        self.index, self.blobs, self.linear = index, blobs, linear
        self.failure = None
        self.linear_response = None
        self.expired = False
        self.calls = []

    def assert_context(self):
        if self.expired:
            raise api.CandidateError("expired B2 fixture")

    def query(self, request, *, limit):
        operation = request.operation.value
        self.calls.append(operation)
        if operation == "linear" and self.failure:
            if self.failure == "timeout":
                raise api.CandidateError("fixture proof timeout")
            return api.CandidateReadResult(b"", 1 if self.failure == "query" else 0,
                                           self.failure == "truncated", error=None)
        if operation == "facts":
            data = json.dumps(self.facts).encode("utf-8")
        elif operation == "tree":
            data = self.trees[request.oid]
        elif operation == "index":
            data = self.index
        elif operation == "others":
            data = b""
        elif operation == "blob":
            blob = self.blobs[request.oid]
            data = str(len(blob)).encode("ascii") if request.kind == "size" else blob
        elif operation == "linear":
            data = (self.linear[(request.old_oid, request.oid)] if self.linear_response is None
                    else self.linear_response)
        else:
            raise api.CandidateError("unknown fixture query")
        return api.CandidateReadResult(data, 0, False)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="icode-b2-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.code, self.control = self.root / "code", self.root / "control"
        self.code.mkdir()
        self.control.mkdir()
        self.git("init", "-q", "--separate-git-dir=" + str(self.root / "objects"))
        for key, value in (("user.name", "B2 Fixture"), ("user.email", "fixture@example.invalid"),
                           ("core.autocrlf", "false")):
            self.git("config", key, value)
        for name, data in ((".gitignore", b".icode_output/\nbuild/\n"),
                           ("source.py", b"value = 1\n"), ("README.md", b"Initial docs\n")):
            (self.code / name).write_bytes(data)
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        self.ticket = self.control / ".icode_output/.icode_output_1"
        contract = {"required": True, "required_layers": ["physical"],
                    "required_consumers": ["camera"], "required_scenarios": ["steady"]}
        self.command("create", "--dir", self.ticket, "--ticket-id", "b2", "--birth", "plan",
                     "--requirement", "B2 fixture", "--metadata-json",
                     json.dumps({"code_files": ["source.py"], "verification_contract": contract}),
                     "--request-id", "birth")
        self.command("bind-execution-root", "--dir", self.ticket, "--ticket-id", "b2",
                     "--execution-root", self.code, "--request-id", "bind")
        self.command("candidate", "--dir", self.ticket, "--phase", "capture",
                     "--base", self.base, "--request-id", "enable")

    def git(self, *args):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1")
        result = subprocess.run(["git", "-C", str(self.code), "-c", "core.hooksPath=" + os.devnull,
                                 "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false", "-c", "gc.auto=0", *args],
                                capture_output=True, timeout=15, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def command(self, *args, ok=True):
        parsed = ctl.build_parser().parse_args(list(map(str, args)))
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = parsed.func(parsed)
        except ctl.ControlError as exc:
            self.assertFalse(ok, exc.report())
            return exc.report()
        self.assertEqual(code == 0, ok, output.getvalue())
        return json.loads(output.getvalue())

    def meta(self):
        return ctl.load_metadata(self.ticket)

    def snapshot(self):
        return ctl.candidate_snapshot(self.ticket, self.meta())

    def frozen(self):
        return tuple((self.ticket / name).read_bytes() for name in (ctl.METADATA_NAME, ctl.EVENTS_NAME))

    def record(self, request, outcome="pass", environment="physical", **options):
        args = ["record-verification", "--dir", self.ticket, "--kind", "device_test", "--outcome", outcome,
                "--environment", environment, "--candidate-id", self.snapshot()["candidate_id"],
                "--device", "SN=B2-fixture", "--layer", "physical", "--consumer", "camera", "--scenario", "steady",
                "--baseline", "fixture source", "--evidence", "fixture.log", "--request-id", request]
        for key, value in options.items():
            args.extend(["--" + key.replace("_", "-"), json.dumps(value) if isinstance(value, dict) else str(value)])
        self.command(*args)
        return self.meta()["verification_runs"][-1]

    def build(self, request, start, candidate):
        digest = hashlib.sha256(b"binary").hexdigest()
        provenance = {"start_event_id": start["event_id"], "source_candidate_id": candidate["candidate_id"],
                      "configure_command": ["cmake", "-S", ".", "-B", "build"], "build_command": ["cmake", "--build", "build"],
                      "compiler": "fixture", "architecture": "host",
                      "artifacts": [{"path": "build/app", "sha256": digest}], "evidence_refs": ["build.log"]}
        self.command("record-verification", "--dir", self.ticket, "--kind", "build", "--layer", "build",
                     "--outcome", "pass", "--environment", "host", "--candidate-id", candidate["candidate_id"],
                     "--build-source", "fresh", "--artifact-identity", "sha256:" + digest,
                     "--baseline", "fixture source", "--evidence", "build.log",
                     "--build-provenance-json", json.dumps(provenance), "--request-id", request)
        return self.meta()["verification_runs"][-1]

    def linear_history(self):
        old = self.snapshot()
        directory = self.code / "build"
        directory.mkdir()
        start = self.command("build-start", "--dir", self.ticket, "--build-dir", directory,
                             "--candidate-id", old["candidate_id"], "--parameters-json",
                             json.dumps({"configure_command": ["cmake", "-S", ".", "-B", "build"],
                                         "build_command": ["cmake", "--build", "build"]}), "--request-id", "start")
        (directory / "app").write_bytes(b"binary")
        first_build = self.build("old-build", start, old)
        runtime = {"source_candidate_id": old["candidate_id"], "origin_build_run_id": first_build["run_id"],
                   "artifacts": first_build["build_provenance"]["artifacts"], "evidence_refs": ["remote.log"]}
        origin = self.record("old-runtime", build_source="fresh", artifact_identity=first_build["artifact_identity"],
                             runtime_provenance_json=runtime)
        self.git("commit", "--allow-empty", "-qm", "same content")
        current = self.snapshot()
        second_build = self.build("new-build", start, current)
        reuse = {"origin_run_id": origin["run_id"], "reason": "unchanged verified source",
                 "scope_paths": ["source.py"], "scope_hash": ev.scope_hash(old, ["source.py"]), "comparison_ref": "fixture"}
        later = self.record("new-runtime", build_source="fresh", artifact_identity=first_build["artifact_identity"],
                            runtime_provenance_json=runtime, reuse_json=reuse)
        return old, current, first_build, second_build, origin, later

    def seal(self):
        snapshot = self.snapshot()
        heads = self.git("rev-list", "--all").decode().splitlines()
        trees, blobs, linear = {}, {}, {}
        for head in heads:
            trees[head] = self.git("ls-tree", "-rz", head)
            for row in trees[head].split(b"\0"):
                if row:
                    oid = row.split(b"\t", 1)[0].split()[2].decode("ascii")
                    blobs[oid] = self.git("cat-file", "blob", oid)
            for old in heads:
                if head != old:
                    linear[(old, head)] = self.git("rev-list", "--parents", "--ancestry-path", old + ".." + head)
        facts = {"identity": snapshot["identity"], "base": self.base,
                 "head": snapshot["head"], "head_tree": snapshot["head_tree"]}
        provider = Provider(self.code, facts, trees, self.git("ls-files", "--stage", "-z"), blobs, linear)
        marker = self.code / ".git"
        self.assertTrue(marker.is_file())
        marker.unlink()  # Exact disposable fixture marker; shared object directory/inodes stay intact.
        raw_guard = patch.object(api, "_git", side_effect=AssertionError("B2 raw Git fallback"))
        raw_guard.start()
        self.addCleanup(raw_guard.stop)
        provider._raw_guard = raw_guard
        self.assertFalse(marker.exists())
        return provider

    @contextlib.contextmanager
    def temporary_seal(self):
        marker = self.code / ".git"
        original = marker.read_bytes()
        provider = self.seal()
        try:
            yield provider
        finally:
            provider._raw_guard.stop()
            marker.write_bytes(original)  # Restore only this disposable fixture's exact Git pointer bytes.

    def context(self, provider):
        ctx = invoke(ctl.verification_context, self.ticket, self.meta(), provider=provider, required=True)
        self.assertIsNotNone(ctx["current"], ctx)
        return ctx

    def test_real_history_replay_applicability_and_both_debt_entries(self):
        self.linear_history()
        self.assertTrue(ctl.verification_applicability(self.ticket)["ok"])
        provider, before = self.seal(), self.frozen()
        events, problems = invoke(ctl.verify_event_chain, self.ticket, self.meta(), provider=provider, required=True)
        self.assertEqual(problems, [])
        ctx = self.context(provider)
        self.assertFalse(ctx["reasons"], ctx)
        self.assertTrue(invoke(ev.assess, self.meta(), ctx, provider=provider, required=True)["ok"])
        trio = dict(capture=ctl.candidate_snapshot, verify=ctl.verify_event_chain, candidate_api=api)
        latest = invoke(debt.latest_matching_runs, self.meta(), self.ticket, provider=provider, required=True, **trio)
        self.assertEqual(latest[("physical", "camera", "steady")]["outcome"], "pass")
        for supplied in (None, ctx):
            report = invoke(debt.analyze_ticket, self.meta(), self.ticket, context=supplied,
                            provider=provider, required=True, **trio)
            self.assertEqual(report["tracking_status"], "satisfied", report)
        with patch.object(ev, "artifact_facts", side_effect=AssertionError("historical artifact read")):
            self.assertFalse(invoke(ev.event_issues, events, self.meta(), api, provider=provider, required=True))
        self.assertIn("linear", provider.calls)
        self.assertEqual(before, self.frozen())

    def test_query_failure_healthy_context_never_doc_or_origin_fallback(self):
        old, current, first, second, origin, later = self.linear_history()
        provider, before = self.seal(), self.frozen()
        ctx = self.context(provider)
        events = ctl.read_events(self.ticket)[0]
        deployed = copy.deepcopy(later)
        deployed["runtime_provenance"]["source_candidate_id"] = current["candidate_id"]
        self.assertTrue(api.compare_candidates(old, current, git_provider=provider,
                                              require_git_provider=True)["equivalent"])
        self.assertTrue(invoke(ev.reuse_source_related, old, current, later["reuse"], api,
                               provider=provider, required=True))
        self.assertIsNone(invoke(ev.validate_provenance, later, self.meta(), events, self.code, api,
                                 check_files=False, provider=provider, required=True))
        for mode in ("query", "truncated", "timeout"):
            provider.failure = mode
            self.assertIsNone(provider.assert_context())
            self.assertEqual(api.compare_candidates(old, current, git_provider=provider,
                                                     require_git_provider=True)["reason"], "commit_equivalence_unproven")
            operations = (
                lambda: invoke(ev.reuse_source_related, old, current, later["reuse"], api, provider=provider, required=True),
                lambda: invoke(ev.validate_provenance, later, self.meta(), events, self.code, api,
                               check_files=False, provider=provider, required=True),
                lambda: invoke(ev.validate_provenance, first, self.meta(), events, self.code, api,
                               check_files=False, live_candidate=current, provider=provider, required=True),
                lambda: invoke(ev.validate_provenance, second, self.meta(), events, self.code, api,
                               check_files=False, provider=provider, required=True),
                lambda: invoke(ev.validate_provenance, deployed, self.meta(), events, self.code, api,
                               check_files=False, provider=provider, required=True),
            )
            for number, operation in enumerate(operations, 9):
                with self.subTest(mode=mode, comparison=number), self.assertRaises(ev.EvidenceError):
                    operation()
            selection = invoke(ev.eligible_latest_runs, self.meta(), ctx, provider=provider, required=True)
            self.assertFalse(selection["latest"])
            self.assertTrue(selection["reasons"])
            self.assertTrue(invoke(ev.event_issues, events, self.meta(), api, provider=provider, required=True))
        provider.failure = None
        responses = (("wrong_parent", current["head"] + " " + "0" * 40 + "\n"),
                     ("wrong_terminal", "0" * 40 + " " + old["head"] + "\n"))
        for mode, response in responses:
            provider.linear_response = response.encode("ascii")
            self.assertIsNone(provider.assert_context())
            proof = api.compare_candidates(old, current, git_provider=provider, require_git_provider=True)
            self.assertEqual(proof, {"equivalent": False, "reason": "head_history_changed"})
            for comparison, operation in (("D09", lambda: invoke(ev.reuse_source_related, old, current,
                    later["reuse"], api, provider=provider, required=True)),
                    ("D10", lambda: invoke(ev.validate_provenance, later, self.meta(), events, self.code, api,
                    check_files=False, provider=provider, required=True))):
                with self.subTest(typed_response=mode, comparison=comparison), \
                        self.assertRaisesRegex(ev.EvidenceError, "candidate_proof_unavailable"):
                    operation()
            self.assertIsNone(provider.assert_context())
            self.assertEqual(before, self.frozen())
        self.assertEqual(before, self.frozen())

    def test_context_binding_missing_wrong_and_falsey_reject(self):
        self.record("pass")
        provider, before = self.seal(), self.frozen()
        ctx = self.context(provider)
        omitted = invoke(ev.eligible_latest_runs, self.meta(), ctx)
        self.assertFalse(omitted["latest"], omitted)
        self.assertTrue(omitted["reasons"])
        for supplied, required in ((None, True), (object(), True), (provider, 0)):
            result = invoke(ev.eligible_latest_runs, self.meta(), ctx, provider=supplied, required=required)
            self.assertFalse(result["latest"], result)
            self.assertTrue(result["reasons"])
        for altered in ({}, {"enabled": False}, dict(ctx, enabled=False), dict(ctx, git_provider=None),
                        {k: v for k, v in ctx.items() if k != "require_git_provider"}):
            self.assertFalse(invoke(ev.assess, self.meta(), altered, provider=provider, required=True)["ok"])
            report = invoke(debt.analyze_ticket, self.meta(), self.ticket, context=altered, provider=provider, required=True)
            self.assertTrue(report["ticket_blocked"], report)
            self.assertFalse(invoke(debt.latest_matching_runs, self.meta(), self.ticket, context=altered,
                                    provider=provider, required=True))
        # Context shape is checked before any consumer indexes required fields.
        manual = dict(ctx, events=[], reasons=[])
        consumers = (
            ("assess", lambda value: invoke(ev.assess, self.meta(), value, provider=provider, required=True)),
            ("latest", lambda value: invoke(debt.latest_matching_runs, self.meta(), self.ticket,
                context=value, provider=provider, required=True)),
            ("debt", lambda value: invoke(debt.analyze_ticket, self.meta(), self.ticket,
                context=value, provider=provider, required=True)),
        )
        for name, consume in consumers:
            with self.subTest(context_shape="manual_empty_lists", consumer=name):
                result = consume(manual)
                if name == "assess":
                    self.assertTrue(result["ok"], result)
                elif name == "latest":
                    self.assertTrue(result, result)
                else:
                    self.assertFalse(result["ticket_blocked"], result)
        altered_contexts = [("missing_" + key, {k: v for k, v in ctx.items() if k != key})
                            for key in ("reasons", "events", "current", "candidate_api")]
        altered_contexts += [("reasons_" + str(index), dict(ctx, reasons=value))
                             for index, value in enumerate((None, "", {}, [None]))]
        altered_contexts += [("events_" + str(index), dict(ctx, events=value))
                             for index, value in enumerate((None, "", {}))]
        for shape, altered in altered_contexts:
            for name, consume in consumers:
                with self.subTest(context_shape=shape, consumer=name):
                    try:
                        result = consume(altered)
                    except KeyError as exc:
                        self.fail("malformed context leaked KeyError: " + str(exc))
                    finally:
                        self.assertIsNone(provider.assert_context())
                        self.assertEqual(before, self.frozen())
                    if name == "assess":
                        self.assertFalse(result["ok"], result)
                        self.assertEqual(result["state"], "blocked", result)
                        self.assertIn("candidate_provider_context", result["reasons"])
                    elif name == "latest":
                        self.assertFalse(result, result)
                    else:
                        self.assertTrue(result["ticket_blocked"], result)
                        self.assertEqual(result["blocking_reason"], "candidate_provider_context", result)
                    self.assertIsNone(provider.assert_context())
                    self.assertEqual(before, self.frozen())
        self.assertEqual(before, self.frozen())

    def test_expiry_in_identical_fastpath_blocks_current_and_debt(self):
        self.record("pass")
        provider, before = self.seal(), self.frozen()
        ctx = self.context(provider)
        provider.expired = True
        self.assertFalse(invoke(ev.assess, self.meta(), ctx, provider=provider, required=True)["ok"])
        report = invoke(debt.analyze_ticket, self.meta(), self.ticket, context=ctx, provider=provider, required=True)
        self.assertTrue(report["ticket_blocked"], report)
        self.assertFalse(invoke(debt.latest_matching_runs, self.meta(), self.ticket, context=ctx,
                                provider=provider, required=True))
        self.assertEqual(before, self.frozen())

    def test_documentation_reuse_keeps_later_fail_inconclusive_and_recovery(self):
        for outcome in ("fail", "inconclusive"):
            origin = self.record("origin-" + outcome)
            snapshot = self.snapshot()
            self.record("negative-" + outcome, outcome)
            reuse = {"origin_run_id": origin["run_id"], "reason": "docs only", "scope_paths": ["source.py"],
                     "scope_hash": ev.scope_hash(snapshot, ["source.py"]), "comparison_ref": "README.md"}
            (self.code / "README.md").write_bytes(("docs " + outcome + "\n").encode("utf-8"))
            self.command("candidate", "--dir", self.ticket, "--phase", "capture", "--request-id", "docs-" + outcome)
            bad = dict(origin, run_id="proposed-" + outcome, reuse=reuse, candidate=self.snapshot(),
                       candidate_id=self.snapshot()["candidate_id"])
            history, before = self.meta()["verification_runs"], self.frozen()
            reason = "reuse_origin_invalidated_by_later_" + outcome
            with self.assertRaisesRegex(ev.EvidenceError, reason):
                ev.validate_reuse(bad, history, api)
            self.assertEqual(before, self.frozen())
            with self.temporary_seal() as provider:
                self.assertIsNone(provider.assert_context())
                with self.subTest(provider_outcome=outcome), self.assertRaisesRegex(ev.EvidenceError, reason):
                    invoke(ev.validate_reuse, bad, history, api, provider=provider, required=True)
                self.assertIsNone(provider.assert_context())
                self.assertEqual(before, self.frozen())
            self.assertEqual(before, self.frozen())
            recovered = self.record("recovery-" + outcome)
            later = self.snapshot()
            (self.code / "README.md").write_bytes(("recovered " + outcome + "\n").encode("utf-8"))
            reuse.update(origin_run_id=recovered["run_id"], scope_hash=ev.scope_hash(later, ["source.py"]))
            self.record("reuse-" + outcome, reuse_json=reuse)
        provider, before = self.seal(), self.frozen()
        self.assertTrue(invoke(ctl.verification_applicability, self.ticket, provider=provider, required=True)["ok"])
        self.assertEqual(before, self.frozen())

    def test_physical_direct_negative_not_replaced_by_ordinary_or_simulated_pass(self):
        direct = {"target_built": True, "binary_executed": True, "test_runner_discovered": False,
                  "ci_registered": False, "binary_exit_code": 1}
        self.record("direct-fail", "fail", direct_test_json=direct)
        self.record("ordinary-pass")
        self.record("simulation-pass", environment="simulated")
        provider, before = self.seal(), self.frozen()
        result = invoke(ctl.verification_applicability, self.ticket, provider=provider, required=True)
        self.assertFalse(result["ok"], result)
        self.assertIn("latest_outcome_fail", result["reasons"])
        self.assertEqual(before, self.frozen())

    def test_corrupt_chain_hash_schema_stays_visible_alongside_proof_error(self):
        self.linear_history()
        provider = self.seal()
        events = ctl.read_events(self.ticket)[0]
        events[-1]["schema_version"] = 2
        (self.ticket / ctl.EVENTS_NAME).write_text("".join(json.dumps(e) + "\n" for e in events),
                                                encoding="utf-8", newline="\n")
        before = self.frozen()
        provider.failure = "query"
        _events, problems = invoke(ctl.verify_event_chain, self.ticket, self.meta(), provider=provider, required=True)
        for text in ("event_hash", "event_schema", "candidate_proof_unavailable"):
            self.assertTrue(any(text in issue for issue in problems), (text, problems))
        self.assertEqual(before, self.frozen())

    def test_report_backed_negative_not_replaced_by_runner_only_pass(self):
        directory = self.code / "build"
        directory.mkdir()
        report = directory / "result.xml"
        report.write_bytes(b'<testsuite tests="1" failures="1"><testcase name="real"><failure/></testcase></testsuite>')
        self.record("report-fail", "fail", test_report=str(report))
        self.record("ordinary-after-report")
        provider, before = self.seal(), self.frozen()
        result = invoke(ctl.verification_applicability, self.ticket, provider=provider, required=True)
        self.assertFalse(result["ok"], result)
        self.assertIn("latest_outcome_fail", result["reasons"])
        self.assertEqual(before, self.frozen())

    def test_foreign_canonical_and_wrong_root_contexts_reject(self):
        self.record("pass")
        provider, before = self.seal(), self.frozen()
        ctx = self.context(provider)
        foreign = load("b2_foreign_control", "icode_control.py").candidate_module()
        for altered in (dict(ctx, candidate_api=foreign),
                        dict(ctx, current=dict(ctx["current"], candidate_id="0" * 64))):
            result = invoke(ev.assess, self.meta(), altered, provider=provider, required=True)
            self.assertFalse(result["ok"], result)
        provider.workspace = self.control
        result = invoke(ev.assess, self.meta(), ctx, provider=provider, required=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(before, self.frozen())

    def test_unknown_and_budget_failure_reason_cannot_authorize_fallback(self):
        old, current, first, second, origin, later = self.linear_history()
        provider, before = self.seal(), self.frozen()
        for reason in ("commit_proof_budget_exceeded", "unknown_future_reason"):
            # These adapter-only reason branches cannot be produced reliably
            # by a small real history; real query failures are covered above.
            with patch.object(api, "compare_candidates", return_value={"equivalent": False, "reason": reason}):
                with self.subTest(reason=reason), self.assertRaises(ev.EvidenceError):
                    invoke(ev.reuse_source_related, old, current, later["reuse"], api, provider=provider, required=True)
        ctx = self.context(provider)
        self.assertEqual(before, self.frozen())
        for reason in ([], {}):
            # Adapter-only malformed return mock; current C1a itself returns string reasons.
            with patch.object(api, "compare_candidates", return_value={"equivalent": False, "reason": reason}):
                for consumer in ("D09", "eligibility"):
                    with self.subTest(reason_type=type(reason).__name__, consumer=consumer):
                        try:
                            if consumer == "D09":
                                with self.assertRaisesRegex(ev.EvidenceError, "candidate_proof_unavailable"):
                                    invoke(ev.reuse_source_related, old, current, later["reuse"], api,
                                           provider=provider, required=True)
                            else:
                                selection = invoke(ev.eligible_latest_runs, self.meta(), ctx,
                                                   provider=provider, required=True)
                                self.assertFalse(selection["latest"], selection)
                                self.assertTrue(selection["reasons"], selection)
                                self.assertIn("candidate_proof_unavailable", selection.get("detail", ""))
                        except TypeError as exc:
                            self.fail("malformed adapter result leaked TypeError: " + str(exc))
                        finally:
                            self.assertIsNone(provider.assert_context())
                            self.assertEqual(before, self.frozen())
        self.assertEqual(before, self.frozen())

    def test_later_simulated_or_unrelated_source_failure_does_not_invalidate_origin(self):
        origin = self.record("origin")
        old = self.snapshot()
        self.record("simulation", "fail", environment="simulated")
        (self.code / "source.py").write_bytes(b"value = 9\n")
        self.record("unrelated-source", "fail")
        (self.code / "source.py").write_bytes(b"value = 1\n")
        (self.code / "README.md").write_bytes(b"documentation clarified\n")
        reuse = {"origin_run_id": origin["run_id"], "reason": "docs only", "scope_paths": ["source.py"],
                 "scope_hash": ev.scope_hash(old, ["source.py"]), "comparison_ref": "README.md"}
        self.record("reuse-origin", reuse_json=reuse)
        provider, before = self.seal(), self.frozen()
        result = invoke(ctl.verification_applicability, self.ticket, provider=provider, required=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(before, self.frozen())

    def test_none_preserves_real_ports_and_read_only_results(self):
        self.linear_history()
        before, meta = self.frozen(), self.meta()
        first = ctl.verification_context(self.ticket, meta)
        second = invoke(ctl.verification_context, self.ticket, meta)
        self.assertEqual(first, second)
        self.assertTrue(ctl.verification_applicability(self.ticket, meta)["ok"])
        self.assertEqual(debt.latest_matching_runs(meta, self.ticket),
                         invoke(debt.latest_matching_runs, meta, self.ticket))
        self.assertEqual(debt.analyze_ticket(meta, self.ticket), invoke(debt.analyze_ticket, meta, self.ticket))
        self.assertEqual(before, self.frozen())


if __name__ == "__main__":
    unittest.main()
