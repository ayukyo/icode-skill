"""B3f real CODE inspection round; software-only, never host/native authority."""
import contextlib
import copy
import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_candidate_history_unit as history

ctl, api = history.ctl, history.api
OLD = b"int timestamp_old(void) { return 1; }\n"


def invoke(function, args, provider=None, required=False, factory=None):
    names = inspect.signature(function).parameters
    kwargs = {}
    for key, value in (("git_provider", provider), ("require_git_provider", required),
                       ("inspection_provider_factory", factory)):
        if key in names:
            kwargs[key] = value
    return function(**kwargs) if function is ctl.main else function(args, **kwargs)


class CodeInspectionProviderTests(unittest.TestCase):
    def setUp(self):
        self.f = history.HistoryTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        (self.f.code / "old.c").write_bytes(OLD)
        self.f.git("add", "old.c")
        self.f.git("commit", "-qm", "historical source")
        self.f.base = self.f.git("rev-parse", "HEAD").decode().strip()
        self.f.command("candidate", "--dir", self.f.ticket, "--phase", "capture",
                       "--base", self.f.base, "--request-id", "b3f:base")
        (self.f.code / "old.c").unlink()
        (self.f.code / "source.py").write_bytes(b"timestamp_ns = 2\n")
        (self.f.ticket / "03_plan_final.md").write_bytes(b"real plan\n")
        self.responses = {}
        for ref in ("HEAD", self.f.base):
            self.responses[("resolve_commit", ref, "", "", (), "")] = (self.f.base + "\n").encode()
        common = ("--no-ext-diff", "--no-textconv", "--no-renames")
        self.responses[("diff_names", "", self.f.base, "", (".",), "")] = self.f.git(
            "diff", *common, "--name-only", "-z", self.f.base, "--", ":(literal).",
            ":(glob,exclude)**/.icode_output/**")
        for kind, options in (("others", ("--others", "--exclude-standard")),
                              ("association", ("--cached", "--others", "--exclude-standard"))):
            self.responses[("list_files", "", "", "", (".",), kind)] = self.f.git(
                "ls-files", *options, "-z", "--", ":(literal).", ":(glob,exclude)**/.icode_output/**")
        self.responses[("diff_names", "", self.f.base, "", (), "")] = self.f.git(
            "diff", *common, "--name-only", "-z", self.f.base, "--",
            ":(glob,exclude)**/.icode_output/**")
        self.responses[("list_files", "", "", "", (), "others")] = self.f.git(
            "ls-files", "--others", "--exclude-standard", "-z", "--",
            ":(glob,exclude)**/.icode_output/**")
        for path in ("source.py", "old.c"):
            self.responses[("trigger_diff", "", self.f.base, path, (), "")] = self.f.git(
                "diff", *common, "--unified=0", self.f.base, "--", ":(literal)" + path)
            self.responses[("historical_blob", "", self.f.base, path, (), "")] = self.f.git(
                "show", self.f.base + ":" + path)
        self.query_fault = None
        self.factory_fault = None
        self.factory_calls = []
        self.provider = None

    def frozen(self):
        return tuple(sorted((label + "/" + p.relative_to(root).as_posix(), p.read_bytes())
                     for label, root in (("code", self.f.code), ("control", self.f.control))
                     for p in root.rglob("*") if p.is_file()))

    def factory(self, module, workspace):
        self.factory_calls.append(module)
        if self.factory_fault == "raise":
            raise OSError("factory unavailable")
        if self.factory_fault == "expire":
            self.provider.expired = True
        if self.factory_fault == "late":
            self.clock[0] += api.DEFAULT_LIMITS["timeout_seconds"] + 1
        base = module.InspectionGitProvider
        if self.factory_fault == "foreign":
            module = ctl.inspection_module()
            base = module.InspectionGitProvider
        fixture = self
        class Transport(base):
            def __init__(self):
                self.workspace = fixture.f.control if fixture.factory_fault == "root" else workspace
            def query(self, request, *, limit):
                key = (request.operation.value, request.ref, request.oid,
                       request.path, request.scopes, request.kind)
                data = fixture.responses[key]
                result = module.GitQueryResult(data, 0, False)
                if fixture.query_fault == "foreign":
                    result = ctl.inspection_module().GitQueryResult(data, 0, False)
                elif fixture.query_fault == "error":
                    result = module.GitQueryResult(b"", 1, False, "unavailable")
                elif fixture.query_fault == "truncate":
                    result = module.GitQueryResult(data, 0, True)
                elif fixture.query_fault == "expire":
                    fixture.provider.expired = True
                elif fixture.query_fault == "late":
                    fixture.clock[0] += api.DEFAULT_LIMITS["timeout_seconds"] + 1
                return result
        return object() if self.factory_fault == "object" else Transport()

    def seal(self):
        self.provider = self.f.seal()
        guard = patch.object(ctl.subprocess, "run", side_effect=AssertionError("B3f raw Git"))
        guard.start()
        self.addCleanup(guard.stop)
        self.assertFalse((self.f.code / ".git").exists())
        return self.provider

    def call(self, command, *extra, protected=False, main=False, provider=None,
             required=None, factory=None):
        args = ctl.build_parser().parse_args([command, "--dir", str(self.f.ticket), *map(str, extra)])
        provider = self.provider if protected and provider is None else provider
        required = protected if required is None else required
        factory = self.factory if protected and factory is None else factory
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = invoke(ctl.main if main else args.func, args,
                              provider, required, factory) if not main else self.run_main(
                                  command, extra, provider, required, factory)
        except ctl.ControlError as exc:
            return exc.exit_code, exc.report()
        return code, json.loads(output.getvalue())

    def run_main(self, command, extra, provider, required, factory):
        with patch("sys.argv", ["icode_control.py", command, "--dir", str(self.f.ticket), *map(str, extra)]):
            return invoke(ctl.main, None, provider, required, factory)

    def ok(self, command, *extra, **kwargs):
        code, result = self.call(command, *extra, **kwargs)
        self.assertEqual(code, 0, result)
        self.assertIs(result["ok"], True, result)
        return result

    def start(self, **kwargs):
        return self.ok("step", "--step", "code", "--phase", "start",
                       "--attempt", "code-1", "--request", "start", **kwargs)

    def check(self, boundary="before_write", **kwargs):
        return self.ok("step", "--step", "code", "--phase", "check",
                       "--attempt", "code-1", "--boundary", boundary, **kwargs)

    def inspection(self, phase, *extra, **kwargs):
        return self.ok("inspection", "--step", "code", "--phase", phase,
                       "--attempt", "code-1", *extra, **kwargs)

    def report(self):
        return json.loads((self.f.ticket / "code_worklist.json").read_text(encoding="utf-8"))

    def prepare(self, **kwargs):
        self.inspection("prepare", "--request", "prepare", "--scope", ".", **kwargs)

    def complete_reads(self, **kwargs):
        for unit in self.report()["units"]:
            for row in unit["files"]:
                self.inspection("read", "--read-phase", "code_review", "--path", row["path"],
                                "--request", "read:" + row["path"], **kwargs)

    def assess(self, **kwargs):
        report = self.report()
        old = next(row for unit in report["units"] for row in unit["files"] if row["path"] == "old.c")
        evidence = dict(kind="source", path="old.c", start_line=1, end_line=1,
                        source_sha256=old["sha256"], excerpt=OLD.decode())
        self.assertTrue(report["checks"])
        for check in report["checks"]:
            value = {"cells": {item: {"status": "handled", "reason": "located software evidence",
                     "evidence": [evidence]} for item in check["required_items"]}}
            self.inspection("assess", "--check-id", check["check_id"], "--read-phase", "code_review",
                            "--assessment-json", json.dumps(value), "--request", "assess:" + check["check_id"], **kwargs)

    def outputs_ready(self, **kwargs):
        (self.f.ticket / "04_code_review_fix.md").write_bytes(b"actual review\n")
        for path, scope in (("04_code_review_fix.md", "ticket"), ("source.py", "workspace")):
            self.ok("artifact", "--step", "code", "--attempt", "code-1",
                    "--path", path, "--scope", scope, "--request", "artifact:" + path, **kwargs)
        self.check("before_transition", **kwargs)

    def finish(self, **kwargs):
        self.outputs_ready(**kwargs)
        return self.ok("step", "--step", "code", "--phase", "finish", "--attempt", "code-1",
                       "--outcome", "success", "--evidence", "review.log", "--request", "finish", **kwargs)

    def prepared(self):
        self.start()
        self.check()
        self.prepare()
        return self.seal()

    def test_none_real_round_keeps_old_receipt_shapes(self):
        self.start(); self.check(); self.prepare(); self.complete_reads(); self.assess()
        self.inspection("check")
        finish = self.finish()
        self.assertTrue(finish["ok"])
        receipt = self.f.meta()["review_receipts"][-1]
        self.assertEqual(receipt["step"], "code")
        self.assertEqual(set(receipt), {"version","step","attempt","event_id","candidate_id","candidate","evidence"})

    def test_main_full_round_real_current_and_deleted_bytes_then_freshness(self):
        self.seal()
        options = dict(protected=True, main=True)
        self.start(**options); self.check(**options); self.prepare(**options)
        report = self.report()
        rows = {r["path"]: r for u in report["units"] for r in u["files"]}
        self.assertEqual(rows["old.c"]["kind"], "deleted")
        self.assertEqual(rows["source.py"]["sha256"], ctl.file_sha256(self.f.code / "source.py"))
        self.complete_reads(**options); self.assess(**options); self.inspection("check", **options)
        finished = self.finish(**options)
        before = self.frozen()
        replay = self.ok("step","--step","code","--phase","finish","--attempt","code-1",
                        "--outcome","success","--evidence","review.log","--request","finish",**options)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(replay["event_id"],finished["event_id"])
        self.assertEqual(before,self.frozen())
        self.ok("check-outputs", "--step", "code", **options)
        result = self.ok("candidate", "--phase", "inspect", "--require-step", "code",
                         provider=self.provider, required=True, main=True)
        self.assertTrue(result["freshness_ok"])
        self.assertEqual(before, self.frozen())
        self.assertTrue(self.factory_calls)

    def test_missing_pair_and_wrong_candidate_fail_before_any_write(self):
        provider = self.seal()
        other = history.load("b3f_foreign_candidate", "icode_control.py").candidate_module()
        class Foreign(other.CandidateGitProvider):
            workspace = self.f.code
            def assert_context(self): return None
            def query(self, request, *, limit): raise AssertionError("foreign candidate query")
        for supplied, factory, required in ((None, self.factory, False), (None, self.factory, True),
                (provider, None, True), (object(), self.factory, True),
                (Foreign(), self.factory, True), (provider, self.factory, 0)):
            for main in (False, True):
                with self.subTest(provider=type(supplied).__name__, required=required, main=main):
                    before = self.frozen()
                    code, result = self.call("step", "--step", "code", "--phase", "start",
                        "--attempt", "bad", provider=supplied, required=required, factory=factory, main=main)
                    self.assertEqual(code, 1, result)
                    gate = "candidate_business_propagation_pending" if main and factory is None else "candidate_provider_context"
                    self.assertEqual(result.get("gate_id"), gate, result)
                    self.assertEqual(before, self.frozen())

    def test_foreign_factory_provider_wrong_root_and_bad_object_reject(self):
        self.seal()
        for fault in ("foreign", "root", "object", "raise"):
            self.factory_fault = fault
            before = self.frozen()
            code, result = self.call("step", "--step", "code", "--phase", "start",
                                    "--attempt", "bad", protected=True)
            self.assertEqual(code, 1, result)
            self.assertEqual(result.get("gate_id"), "candidate_provider_context", result)
            self.assertEqual(before, self.frozen())

    def test_factory_expiry_and_elapsed_time_are_not_admission(self):
        self.seal()
        for fault in ("expire", "late"):
            self.factory_fault = fault
            self.provider.expired = False
            self.clock = [0.0]
            before = self.frozen()
            with patch.object(api.time, "monotonic", side_effect=lambda: self.clock[0]):
                code, result = self.call("step", "--step", "code", "--phase", "start",
                                        "--attempt", "bad", protected=True)
            self.assertEqual(code, 1, result)
            self.assertEqual(result.get("gate_id"), "candidate_provider_context", result)
            self.assertEqual(before, self.frozen())

    def test_query_faults_cannot_become_partial_prepare_success(self):
        self.start(); self.check(); self.seal()
        for fault in ("foreign", "error", "truncate", "expire", "late"):
            self.query_fault = fault
            self.provider.expired = False
            self.clock = [0.0]
            before = self.frozen()
            with patch.object(api.time, "monotonic", side_effect=lambda: self.clock[0]):
                code, result = self.call("inspection", "--step", "code", "--phase", "prepare",
                                        "--attempt", "code-1", "--scope", ".", protected=True)
            self.assertEqual(code, 1, result)
            self.assertEqual(result.get("gate_id"), "candidate_provider_context", result)
            self.assertEqual(before, self.frozen())

    def test_real_read_and_assessment_are_mandatory(self):
        self.prepared()
        before = self.frozen()
        code, result = self.call("inspection", "--step", "code", "--phase", "check",
                                "--attempt", "code-1", protected=True)
        self.assertEqual(code, 1, result)
        self.assertEqual(before, self.frozen())
        self.complete_reads(protected=True)
        code, result = self.call("inspection", "--step", "code", "--phase", "check",
                                "--attempt", "code-1", protected=True)
        self.assertEqual(code, 1, result)
        self.assess(protected=True)
        self.inspection("check", protected=True)

    def test_prepare_replay_keeps_real_read_progress_and_original_receipt(self):
        self.prepared()
        self.complete_reads(protected=True)
        before = self.frozen()
        replay = self.inspection("prepare", "--request", "prepare", "--scope", ".", protected=True)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(before, self.frozen())
        self.provider.expired = True
        code, result = self.call("inspection", "--step", "code", "--phase", "prepare",
                "--attempt", "code-1", "--request", "prepare", "--scope", ".", protected=True)
        self.assertEqual(code, 1, result)
        self.assertEqual(before, self.frozen())

    def test_source_drift_is_rejected_by_check_and_output_validator(self):
        self.prepared(); self.complete_reads(protected=True); self.assess(protected=True)
        (self.f.code / "source.py").write_bytes(b"timestamp_ns = 99\n")
        before = self.frozen()
        for command, extra in (("inspection", ("--phase","check","--attempt","code-1")),
                               ("check-outputs", ())):
            code, result = self.call(command, "--step", "code", *extra, protected=True)
            self.assertEqual(code, 1, result)
            self.assertFalse(result["ok"])
            self.assertEqual(before, self.frozen())

    def test_noncode_and_forged_dispatch_are_still_pending(self):
        self.seal()
        called = []
        for command, handler in (("step", lambda *a, **k: called.append(True)),
                                 ("step", ctl.cmd_step), ("inspection", ctl.cmd_inspection),
                                 ("check-outputs", ctl.cmd_check_outputs), ("artifact", ctl.cmd_artifact),
                                 ("transition", ctl.cmd_transition), ("validate", ctl.cmd_validate)):
            output = io.StringIO()
            with patch.object(ctl,"build_parser") as parser, contextlib.redirect_stdout(output):
                parser.return_value.parse_args.return_value = SimpleNamespace(
                    cmd=command, func=handler, step="code" if handler.__name__ == "<lambda>" else "audit",
                    phase="check", dir=str(self.f.ticket))
                code = invoke(ctl.main, None, self.provider, True, self.factory)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["gate_id"],"candidate_business_propagation_pending")

        self.assertEqual(called, [])
        for command, handler in (("step",ctl.cmd_step),("inspection",ctl.cmd_inspection)):
            args=ctl.build_parser().parse_args([command,"--dir",str(self.f.ticket),"--step","code",
                "--phase","start" if command=="step" else "prepare","--attempt","code-1"])
            args.phase="unknown"
            before=self.frozen()
            with self.assertRaises(ctl.ControlError) as caught:
                invoke(handler,args,self.provider,True,self.factory)
            self.assertEqual(caught.exception.report()["gate_id"],"candidate_business_propagation_pending")
            output=io.StringIO()
            with patch.object(ctl,"build_parser") as parser,contextlib.redirect_stdout(output):
                parser.return_value.parse_args.return_value=args
                code=invoke(ctl.main,None,self.provider,True,self.factory)
            self.assertEqual(code,1)
            self.assertEqual(json.loads(output.getvalue())["gate_id"],"candidate_business_propagation_pending")
            self.assertEqual(before,self.frozen())

    def test_pending_journal_is_not_recovered_by_new_writer(self):
        self.seal()
        (self.f.ticket / ctl.TXN_NAME).write_bytes(b'{"pending":true}\n')
        before = self.frozen()
        with patch.object(ctl,"recover_pending_transaction",side_effect=AssertionError("recovery forbidden")):
            code,result = self.call("step","--step","code","--phase","start",
                                    "--attempt","bad",protected=True)
        self.assertEqual(code,1,result)
        self.assertEqual(result.get("gate_id"),"candidate_transaction_pending",result)
        self.assertEqual(before,self.frozen())

    def test_post_file_write_failure_reports_error_and_preserves_actual_unreceipted_file(self):
        self.start(); self.check(); self.seal()
        original = ctl.atomic_write_json
        before_events = (self.f.ticket / ctl.EVENTS_NAME).read_bytes()
        def write(path, value):
            result = original(path, value)
            if Path(path).name == "code_worklist.json":
                self.provider.expired = True
            return result
        with patch.object(ctl,"atomic_write_json",side_effect=write):
            code,result = self.call("inspection","--step","code","--phase","prepare",
                                    "--attempt","code-1","--scope",".",protected=True)
        self.assertEqual(code,1,result)
        self.assertTrue((self.f.ticket/"code_worklist.json").is_file())
        self.assertEqual((self.f.ticket/ctl.EVENTS_NAME).read_bytes(),before_events)

    def test_post_event_failure_preserves_written_event_not_fake_rollback(self):
        self.seal()
        original = ctl.append_prepared_event
        before = (self.f.ticket / ctl.EVENTS_NAME).read_bytes()
        def append(*args,**kwargs):
            result = original(*args,**kwargs)
            self.provider.expired = True
            return result
        with patch.object(ctl,"append_prepared_event",side_effect=append):
            code,result = self.call("step","--step","code","--phase","start",
                                    "--attempt","code-1","--request","start",protected=True)
        self.assertEqual(code,1,result)
        self.assertNotEqual((self.f.ticket/ctl.EVENTS_NAME).read_bytes(),before)
        self.assertEqual(ctl.read_events(self.f.ticket)[0][-1]["event_type"],"step_started")

    def test_each_code_entrypoint_rejects_stale_context_before_side_effects(self):
        self.prepared()
        self.provider.expired = True
        operations = [
            ("step", ("--phase","check","--attempt","code-1","--boundary","before_write")),
            ("step", ("--phase","finish","--attempt","code-1","--outcome","blocked","--evidence","blocked")),
            ("inspection", ("--phase","prepare","--attempt","code-1")),
            ("inspection", ("--phase","read","--attempt","code-1","--read-phase","code_review","--path","source.py")),
            ("inspection", ("--phase","assess","--attempt","code-1","--read-phase","code_review","--check-id","unknown","--assessment-json","{}")),
            ("inspection", ("--phase","check","--attempt","code-1")),
            ("artifact", ("--attempt","code-1","--path","source.py","--scope","workspace")),
            ("check-outputs", ()),
        ]
        for command, extra in operations:
            with self.subTest(command=command, extra=extra):
                before = self.frozen()
                code, result = self.call(command,"--step","code",*extra,protected=True)
                self.assertEqual(code,1,result)
                self.assertEqual(result.get("gate_id"),"candidate_provider_context",result)
                self.assertEqual(before,self.frozen())

    def test_whole_window_includes_serialization_after_real_event_publication(self):
        self.seal()
        clock=[0.0]
        original=ctl.json.dumps
        def dumps(value,*args,**kwargs):
            result=original(value,*args,**kwargs)
            if isinstance(value,dict) and value.get("phase")=="start" and value.get("ok") is True:
                clock[0]=api.DEFAULT_LIMITS["timeout_seconds"]+1
            return result
        before=(self.f.ticket/ctl.EVENTS_NAME).read_bytes()
        with patch.object(api.time,"monotonic",side_effect=lambda:clock[0]), \
                patch.object(ctl.json,"dumps",side_effect=dumps):
            code,result=self.call("step","--step","code","--phase","start",
                                  "--attempt","code-1",protected=True)
        self.assertEqual(code,1,result)
        self.assertIn("deadline",result.get("error",""))
        self.assertNotEqual((self.f.ticket/ctl.EVENTS_NAME).read_bytes(),before)

    def test_finish_prepublication_live_drift_uses_original_metadata_rollback(self):
        self.prepared(); self.complete_reads(protected=True); self.assess(protected=True)
        self.outputs_ready(protected=True)
        before=self.f.frozen()
        original=ctl.atomic_write_json
        fired=[]
        def write(path,value):
            result=original(path,value)
            if Path(path).name==ctl.METADATA_NAME and not fired:
                fired.append(True)
                (self.f.code/"source.py").write_bytes(b"timestamp_ns = 999\\n")
            return result
        with patch.object(ctl,"atomic_write_json",side_effect=write):
            code,result=self.call("step","--step","code","--phase","finish","--attempt","code-1",
                "--outcome","success","--evidence","review.log","--request","finish",protected=True)
        self.assertEqual(code,1,result)
        self.assertEqual(result.get("gate_id"),"candidate_capture",result)
        self.assertTrue(fired)
        self.assertEqual(self.f.frozen(),before)
        self.assertEqual((self.f.code/"source.py").read_bytes(),b"timestamp_ns = 999\\n")

    def test_finish_postappend_expiry_keeps_real_success_receipt_but_rejects_result(self):
        self.prepared(); self.complete_reads(protected=True); self.assess(protected=True)
        self.outputs_ready(protected=True)
        original=ctl.append_prepared_event
        before=self.f.frozen()
        def append(out,event):
            result=original(out,event)
            if event["event_type"]=="step_finished":
                self.provider.expired=True
            return result
        with patch.object(ctl,"append_prepared_event",side_effect=append):
            code,result=self.call("step","--step","code","--phase","finish","--attempt","code-1",
                "--outcome","success","--evidence","review.log","--request","finish",protected=True)
        self.assertEqual(code,1,result)
        self.assertEqual(result.get("gate_id"),"candidate_provider_context",result)
        self.assertNotEqual(self.f.frozen(),before)
        event=ctl.read_events(self.f.ticket)[0][-1]
        self.assertEqual(event["event_type"],"step_finished")
        self.assertEqual(event["payload"]["outcome"],"success")
        self.assertEqual(self.f.meta()["review_receipts"][-1]["event_id"],event["event_id"])

    def test_main_code_buffer_boundary_still_checks_context_before_result_acceptance(self):
        self.prepared(); self.complete_reads(protected=True); self.assess(protected=True)
        self.outputs_ready(protected=True)
        self.ok("check-outputs","--step","code",protected=True)
        output=io.StringIO()
        fixture=self
        class ExpiringBuffer(io.StringIO):
            def getvalue(buffer):
                value=super().getvalue()
                fixture.provider.expired=True
                return value
        before=self.frozen()
        with patch.object(ctl.io,"StringIO",ExpiringBuffer),contextlib.redirect_stdout(output):
            code=self.run_main("check-outputs",("--step","code"),self.provider,True,self.factory)
        result=json.loads(output.getvalue())
        self.assertEqual(code,1,result)
        self.assertEqual(result.get("gate_id"),"candidate_provider_context",result)
        self.assertEqual(before,self.frozen())

    def test_catalog_cannot_silently_activate_final_linter_for_protected_code(self):
        self.prepared(); self.complete_reads(protected=True); self.assess(protected=True)
        self.outputs_ready(protected=True)
        model=copy.deepcopy(ctl.load_execution_model())
        model["finish_gate_steps"]=["code"]
        before=self.frozen()
        with patch.object(ctl,"load_execution_model",return_value=model), \
                patch.object(ctl,"run_gate_linters",side_effect=AssertionError("final linter pending")):
            code,result=self.call("step","--step","code","--phase","finish","--attempt","code-1",
                "--outcome","success","--evidence","review.log","--request","finish",protected=True)
        self.assertEqual(code,1,result)
        self.assertEqual(result.get("gate_id"),"candidate_business_propagation_pending",result)
        self.assertEqual(before,self.frozen())

    def test_ci_strict_round_gate_and_old_port_count_are_both_kept(self):
        text=(history.ROOT/".github/workflows/candidate-provider-software.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("pattern='test_candidate_code_inspection_provider_unit.py'); assert s.countTestCases()==23"),1)
        self.assertEqual(text.count("pattern='test_candidate_port_provider_unit.py'); assert s.countTestCases()==21"),1)
        self.assertIn("      - 'tools/inspection_worklist.py'", text)
        self.assertEqual(text.count("      - '.github/workflows/inspection-software.yml'"),1)
        inspection=(history.ROOT/".github/workflows/inspection-software.yml").read_text(encoding="utf-8")
        self.assertEqual(inspection.count("      - 'tests/test_candidate_code_inspection_provider_unit.py'"),1)


    def test_finish_last_original_guard_deadline_expiry_rejects_before_append(self):
        self.prepared(); self.complete_reads(protected=True); self.assess(protected=True)
        self.outputs_ready(protected=True)
        before=self.f.frozen()
        clock=[0.0]
        published=[]
        fired=[]
        original_commit=ctl.commit_metadata_and_event
        original_guard=ctl._candidate_context_guard
        def commit(*args,**kwargs):
            callback=kwargs.get("before_publish")
            if callback is not None:
                def before_publish():
                    callback()
                    published.append(True)
                kwargs["before_publish"]=before_publish
            return original_commit(*args,**kwargs)
        def guard(*args,**kwargs):
            result=original_guard(*args,**kwargs)
            if published and not fired:
                fired.append(True)
                clock[0]=api.DEFAULT_LIMITS["timeout_seconds"]+1
            return result
        with patch.object(api.time,"monotonic",side_effect=lambda:clock[0]), \
                patch.object(ctl,"commit_metadata_and_event",side_effect=commit), \
                patch.object(ctl,"_candidate_context_guard",side_effect=guard):
            code,result=self.call("step","--step","code","--phase","finish","--attempt","code-1",
                "--outcome","success","--evidence","review.log","--request","finish",protected=True)
        self.assertEqual(code,1,result)
        self.assertEqual(result.get("gate_id"),"candidate_provider_context",result)
        self.assertIn("deadline",result.get("error",""))
        self.assertEqual(fired,[True])
        self.assertEqual(self.f.frozen(),before)
        self.assertFalse((self.f.ticket/ctl.TXN_NAME).exists())
        self.assertFalse(any(event["event_type"]=="step_finished"
                             for event in ctl.read_events(self.f.ticket)[0]))

    def test_transaction_hook_default_none_and_original_callback_order(self):
        meta=self.f.meta()
        ignored=[]
        with ctl.DirLock(self.f.ticket):
            event=ctl.commit_metadata_and_event(self.f.ticket,meta,copy.deepcopy(meta),
                "external_note",{"note":"legacy none"},request_id="legacy-none",
                before_publish=lambda:ignored.append(True))
        self.assertEqual(ignored,[])
        self.assertEqual(self.f.meta(),meta)
        self.assertEqual(ctl.read_events(self.f.ticket)[0][-1],event)
        self.assertFalse((self.f.ticket/ctl.TXN_NAME).exists())
        self.seal()
        order=[]
        original_guard=ctl._candidate_context_guard
        original_append=ctl.append_prepared_event
        def guard(*args,**kwargs):
            result=original_guard(*args,**kwargs)
            if "publish" in order:
                order.append("guard")
            return result
        def append(*args,**kwargs):
            order.append("append")
            return original_append(*args,**kwargs)
        kwargs=dict(git_provider=self.provider,require_git_provider=True,
                    before_publish=lambda:order.append("publish"))
        if "before_append" in inspect.signature(ctl.commit_metadata_and_event).parameters:
            kwargs["before_append"]=lambda:order.append("hook")
        meta=self.f.meta()
        with ctl.DirLock(self.f.ticket),patch.object(ctl,"_candidate_context_guard",side_effect=guard), \
                patch.object(ctl,"append_prepared_event",side_effect=append):
            event=ctl.commit_metadata_and_event(self.f.ticket,meta,copy.deepcopy(meta),
                "external_note",{"note":"ordered provider"},request_id="ordered-provider",**kwargs)
        self.assertEqual(order,["publish","guard","hook","append"])
        self.assertEqual(self.f.meta(),meta)
        self.assertEqual(ctl.read_events(self.f.ticket)[0][-1],event)
        self.assertFalse((self.f.ticket/ctl.TXN_NAME).exists())

    def test_transaction_noncallable_hook_is_rejected_before_any_write(self):
        self.seal()
        for protected in (False,True):
            with self.subTest(protected=protected):
                before=self.f.frozen()
                kwargs={}
                if protected:
                    kwargs.update(git_provider=self.provider,require_git_provider=True,
                                  before_publish=lambda:None)
                if "before_append" in inspect.signature(ctl.commit_metadata_and_event).parameters:
                    kwargs["before_append"]=object()
                caught=None
                with ctl.DirLock(self.f.ticket):
                    try:
                        ctl.commit_metadata_and_event(self.f.ticket,self.f.meta(),self.f.meta(),
                            "external_note",{"note":"must not publish"},**kwargs)
                    except Exception as exc:
                        caught=exc
                self.assertIsInstance(caught,ctl.ControlError)
                self.assertEqual(caught.report()["gate_id"],"candidate_provider_context")
                self.assertEqual(self.f.frozen(),before)
                self.assertFalse((self.f.ticket/ctl.TXN_NAME).exists())


if __name__ == "__main__":
    unittest.main()
