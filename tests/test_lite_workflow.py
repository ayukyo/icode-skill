"""lite 原生流程的实际控制面合同，使用临时 Git 仓库。"""
import json
import unittest
import os
import sys
import subprocess
import time
import shutil
import builtins
import hashlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, Mock

import test_candidate_freshness as fixtures

ctl = fixtures.ctl


@contextmanager
def replace_review_after_binary_read(path, replacement):
    """模拟另一个写入者在读取句柄关闭后替换审核文件。"""
    original_open, original_path_open = builtins.open, Path.open
    replaced = []

    @contextmanager
    def observed_open(opener, file, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        with opener(file, *args, **kwargs) as stream:
            yield stream
        if mode == "rb" and Path(file).resolve() == path.resolve() and not replaced:
            replaced.append(True)
            with original_open(path, "wb") as target:
                target.write(replacement)

    def selected_open(opener, file, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if mode == "rb" and Path(file).resolve() == path.resolve():
            return observed_open(opener, file, *args, **kwargs)
        return opener(file, *args, **kwargs)

    def file_open(file, *args, **kwargs):
        return selected_open(original_open, file, *args, **kwargs)

    def path_open(file, *args, **kwargs):
        return selected_open(original_path_open, file, *args, **kwargs)

    with patch.object(builtins, "open", file_open), patch.object(Path, "open", path_open):
        yield replaced


class LiteWorkflowTests(unittest.TestCase):
    setUp = fixtures.CandidateTests.setUp
    git = fixtures.CandidateTests.git
    call = fixtures.CandidateTests.call
    bytes = fixtures.CandidateTests.bytes

    def contracts(self, external=False):
        cell = {"layer": "unit", "consumer": "value", "scenario": "update", "criterion_ids": ["AC-1"]}
        cells = [cell]
        if external:
            cells.append({"layer": "consumption", "consumer": "value", "scenario": "device",
                          "criterion_ids": ["AC-1"], "external": True, "pending_reason": "设备尚未接入"})
        return {"scope_contract": {"requirements": [{"id": "R-1", "text": "修复值更新行为",
                 "source_ref": "metadata.requirement"}], "current_facts": ["source.py 当前 value=1"],
                 "in_scope": ["值更新"], "invariants": ["其它值保持"], "plan": {"approach": "修改值更新点",
                 "change_points": ["source.py"], "risk_checks": ["重复更新"], "rollback": "撤销本任务差异"}},
                "semantic_decisions": [],
                "acceptance_contract": {"criteria": [{"id": "AC-1", "requirement_ids": ["R-1"],
                                                        "expected": "更新值可被消费者读取"}]},
                "verification_contract": {"required": True, "required_layers": ["unit", "consumption"] if external else ["unit"],
                 "required_consumers": ["value"], "required_scenarios": ["update", "device"] if external else ["update"],
                 "required_cells": cells}}

    def transition(self, target, verdict=None, ok=True):
        args = ["transition", "--dir", self.ticket, "--to", target]
        if verdict:
            args += ["--delivery-verdict", verdict]
        return self.call(*args, ok=ok)

    def start(self, step):
        return self.call("step", "--dir", self.ticket, "--step", step, "--phase", "start")["attempt"]

    def finish(self, step, attempt, outcome="success", ok=True):
        model = ctl.load_execution_model(ctl.load_metadata(self.ticket))
        for boundary in model["step_contracts"][step]["required_checks"]:
            self.call("step", "--dir", self.ticket, "--step", step, "--phase", "check",
                      "--attempt", attempt, "--boundary", boundary)
        return self.call("step", "--dir", self.ticket, "--step", step, "--phase", "finish",
                         "--attempt", attempt, "--outcome", outcome, "--evidence", "真实夹具检查", ok=ok)

    def prepare(self, external=False):
        self.create_lite()
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps(self.contracts(external)))
        a = self.start("lite_prepare")
        self.finish("lite_prepare", a)
        self.transition("lite_prepare_done")
        self.transition("lite_code_in_progress")

    def verify(self, outcome="pass", scenario="update", layer="unit"):
        report = self.ticket / ("report-" + outcome + ".xml")
        report.write_text('<testsuite tests="1"><testcase name="update">' +
                          ('<failure message="broken"/>' if outcome == "fail" else '') +
                          '</testcase></testsuite>', encoding="utf-8")
        return self.call("record-verification", "--dir", self.ticket, "--kind", "device_test",
                         "--candidate-id", ctl.candidate_snapshot(self.ticket, ctl.load_metadata(self.ticket), events=ctl.read_events(self.ticket)[0])["candidate_id"],
                         "--outcome", outcome, "--environment", "host", "--layer", layer,
                         "--consumer", "value", "--scenario", scenario, "--baseline", self.base,
                         "--evidence", str(report), "--test-report", str(report))

    def code(self, external=False):
        self.prepare(external)
        a = self.start("lite_code")
        (self.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.verify()
        self.finish("lite_code", a)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        return self.start("lite_review")

    def fake_host_review(self, attempt, verdict="pass", findings=None, ok=None, during_wait=None):
        # 模拟真实 CLI 协议，不以手填事件或自由 PASS 文件代替控制面调用。
        binary = self.repo / ".icode_output/bin"
        binary.mkdir(parents=True, exist_ok=True)
        script = binary / "codex"
        report = {"verdict": verdict, "findings": findings or [], "criteria": [
                  {"id": "AC-1", "status": "pass", "evidence": "source.py 及测试报告"}],
                  "requirement_assessment": "原始目标与方案一致", "implementation_assessment": "源码与证据已检查"}
        started, release = self.ticket / "host-started", self.ticket / "host-release"
        waiting = (f'pathlib.Path({str(started)!r}).write_text("ready")\n'
                   f'while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n') if during_wait else ''
        script.write_text('#!' + sys.executable + '\nimport sys,json,pathlib,time\n'
                          'prompt = sys.stdin.read()\n'
                          'assert "verification_contract" in prompt and "impact_contract" in prompt\n'
                          + waiting +
                          'pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text('
                          + repr(json.dumps(report)) + ')\n'
                          'import uuid\nprint(json.dumps({"type":"thread.started","thread_id":str(uuid.uuid4())}))\n',
                          encoding="utf-8")
        script.chmod(0o755)
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(binary) + os.pathsep + old
            if during_wait:
                command = [sys.executable, str(fixtures.ROOT / "tools/icode_control.py"), "lite-review",
                           "--dir", str(self.ticket), "--attempt", attempt, "--backend", "codex",
                           "--request-id", "fixture-review-" + attempt]
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not started.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(started.exists(), "CLI fixture 未进入等待")
                    during_wait()
                    release.write_text("return")
                    stdout, stderr = process.communicate(timeout=20)
                    self.assertNotEqual(process.returncode, 0, (stdout, stderr))
                    return json.loads(stdout)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            return self.call("lite-review", "--dir", self.ticket, "--attempt", attempt,
                             "--backend", "codex", "--request-id", "fixture-review-" + attempt,
                             ok=verdict == "pass" if ok is None else ok)
        finally:
            os.environ["PATH"] = old
    def create_lite(self):
        return self.call("create", "--dir", self.ticket, "--ticket-id", "candidate-1",
                         "--birth", "lite", "--requirement", "修复值更新行为",
                         "--metadata-json", json.dumps({"mode": "lite", "workflow_version": 1,
                                                       "code_files": ["source.py"]}))

    def test_native_lite_birth_and_identity(self):
        result = self.create_lite()
        self.assertEqual(result["status"], "lite_prepare_in_progress")
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["completed_steps"], [])
        self.assertEqual(ctl.workflow_contract(meta)["step_order"],
                         ["lite_prepare", "lite_code", "lite_review"])

    def test_lite_identity_and_requirement_are_immutable(self):
        self.create_lite()
        for field, value in (("mode", "full"), ("workflow_version", 2), ("requirement", "改写")):
            before = self.bytes()
            self.call("metadata-update", "--dir", self.ticket,
                      "--set-json", json.dumps({field: value}), ok=False)
            self.assertEqual(self.bytes(), before)

    def test_lite_cannot_enter_old_code_or_finish_without_prepare(self):
        self.create_lite()
        self.call("step", "--dir", self.ticket, "--step", "code", "--phase", "start", ok=False)
        self.call("transition", "--dir", self.ticket, "--to", "lite_code_in_progress", ok=False)
        self.call("transition", "--dir", self.ticket, "--to", "completed",
                  "--delivery-verdict", "verification_pending", ok=False)

    def test_broken_lite_module_load_is_controlled_and_not_cached(self):
        name = "icode_lite_workflow"
        previous = sys.modules.pop(name, None)
        self.addCleanup(sys.modules.pop, name, None)
        if previous is not None:
            self.addCleanup(sys.modules.__setitem__, name, previous)
        module_path = self.repo / "tools" / "lite_workflow.py"
        module_path.parent.mkdir(parents=True)
        module_path.write_text("def broken(:\n", encoding="utf-8")
        with patch.object(ctl, "SKILL_ROOT", self.repo):
            with self.assertRaises(ctl.ControlError) as raised:
                ctl.lite_module()
        self.assertEqual(raised.exception.extra["gate_id"], "workflow_profile")
        self.assertNotIn(name, sys.modules)

    def test_unknown_lite_version_rejected(self):
        self.call("create", "--dir", self.ticket, "--ticket-id", "candidate-1",
                  "--birth", "lite", "--requirement", "r", "--metadata-json",
                  '{"mode":"lite","workflow_version":2}', ok=False)

    def test_three_native_steps_complete_and_freshness(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        self.assertEqual(ctl.load_metadata(self.ticket)["completed_steps"],
                         ["lite_prepare", "lite_code", "lite_review"])
        self.assertTrue(ctl.delivery_freshness(self.ticket)["ok"])
        self.assertFalse((self.ticket / "01_plan.md").exists())

    def test_fake_pass_and_degraded_do_not_complete(self):
        a = self.code()
        (self.ticket / "fake-pass.json").write_text('{"verdict":"pass"}')
        self.finish("lite_review", a, ok=False)
        self.finish("lite_review", a, outcome="degraded", ok=False)
        self.transition("completed", "verification_pending", ok=False)

    def test_review_then_untracked_config_change_invalidates(self):
        a = self.code()
        self.fake_host_review(a)
        (self.repo / "runtime.json").write_text('{"value":3}')
        self.finish("lite_review", a, ok=False)
        self.transition("completed", "verified", ok=False)

    def test_latest_failure_not_old_pass(self):
        a = self.code()
        self.fake_host_review(a)
        self.verify("fail")
        self.finish("lite_review", a, ok=False)

    def test_external_pending_and_same_candidate_revalidation(self):
        a = self.code(external=True)
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verification_pending")
        events = ctl.read_events(self.ticket)[0]
        review_count = sum(e["event_type"] == "lite_review_invoked" for e in events)
        self.verify(scenario="device", layer="consumption")
        self.transition("completed", "verified")
        self.assertEqual(sum(e["event_type"] == "lite_review_invoked"
                             for e in ctl.read_events(self.ticket)[0]), review_count)

    def test_unresolved_decision_blocks_g1(self):
        self.create_lite()
        contract = self.contracts()
        contract["semantic_decisions"] = [{"status": "resolved", "selected": "two"}]
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps(contract))
        a = self.start("lite_prepare")
        self.finish("lite_prepare", a, ok=False)

    def test_validate_snapshot_and_derived_report(self):
        self.create_lite()
        self.call("validate", "--dir", self.ticket)
        self.call("snapshot", "--dir", self.ticket)
        snapshot = json.loads((self.ticket / "ticket_snapshot.json").read_text())
        self.assertIn("lite_prepare_done", snapshot["next_legal_transitions"])
        self.call("lite-report", "--dir", self.ticket)
        self.assertIn("需求与方案", (self.ticket / "lite.md").read_text())

    def test_new_failure_atomically_revokes_completed_projection(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        self.verify("fail")
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["status"], "lite_code_in_progress")
        self.assertEqual(meta["completed_steps"], ["lite_prepare"])
        self.assertNotEqual(meta["delivery_verdict"], "verified")
        self.assertFalse(ctl.delivery_freshness(self.ticket)["ok"])
        self.call("validate", "--dir", self.ticket)

    def test_external_pending_cannot_hide_local_required_metric(self):
        original = self.contracts
        def metrics(external=False):
            value = original(external)
            value["verification_contract"]["required_metrics"] = [{"name": "latency", "unit": "ms",
                "operator": "lte", "value": 10, "layer": "unit", "consumer": "value", "scenario": "update"}]
            return value
        self.contracts = metrics
        self.prepare(external=True)
        a = self.start("lite_code")
        (self.repo / "source.py").write_text("value = 2\n")
        self.verify()
        self.finish("lite_code", a, ok=False)

    def test_finish_request_replays_after_transition(self):
        self.create_lite()
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps(self.contracts()))
        a = self.start("lite_prepare")
        for boundary in ["before_write", "before_transition"]:
            self.call("step", "--dir", self.ticket, "--step", "lite_prepare", "--phase", "check",
                      "--attempt", a, "--boundary", boundary)
        command = ["step", "--dir", self.ticket, "--step", "lite_prepare", "--phase", "finish",
                   "--attempt", a, "--outcome", "success", "--evidence", "checked", "--request", "finish-once"]
        self.call(*command)
        self.transition("lite_prepare_done")
        before = self.bytes()
        self.assertTrue(self.call(*command)["already_applied"])
        self.assertEqual(self.bytes(), before)

    def test_blocking_finding_cannot_disappear_in_new_pass(self):
        a = self.code()
        finding = {"id": "F-1", "blocking": True, "status": "open",
                   "description": "值更新缺少边界核对", "evidence": "source.py:1"}
        self.fake_host_review(a, "changes_required", [finding])
        self.finish("lite_review", a, outcome="blocked")
        self.transition("lite_code_in_progress")
        code = self.start("lite_code")
        self.finish("lite_code", code)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        review = self.start("lite_review")
        self.fake_host_review(review, ok=False)
        self.finish("lite_review", review, ok=False)
        self.transition("completed", "verified", ok=False)

    def test_late_resolved_report_cannot_close_previous_finding(self):
        a = self.code()
        finding = {"id": "F-1", "blocking": True, "status": "open",
                   "description": "需要核对值更新边界", "evidence": "source.py:1"}
        self.fake_host_review(a, "changes_required", [finding])
        self.finish("lite_review", a, outcome="blocked")
        self.transition("lite_code_in_progress")
        code = self.start("lite_code")
        self.finish("lite_code", code)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        waiting = self.start("lite_review")
        next_attempt = []
        def rollback_while_waiting():
            self.finish("lite_review", waiting, outcome="blocked")
            self.transition("lite_code_in_progress")
            code = self.start("lite_code")
            self.finish("lite_code", code)
            self.transition("lite_code_done")
            self.transition("lite_review_in_progress")
            next_attempt.append(self.start("lite_review"))
        self.fake_host_review(waiting, findings=[dict(finding, status="resolved")],
                              during_wait=rollback_while_waiting)
        self.fake_host_review(next_attempt[0], ok=False)
        self.transition("completed", "verified", ok=False)

    def test_stage_program_lite_birth_and_action_fail_closed(self):
        locator = self.repo / ".icode_output/.stage_program"
        locator.mkdir(parents=True)
        result = self.call("create", "--dir", self.ticket, "--ticket-id", "candidate-1",
                           "--birth", "lite", "--requirement", "r", ok=False)
        self.assertEqual(result["gate_id"], "lite_stage_unsupported")
        locator.rmdir()
        self.create_lite()
        stage = Mock()
        stage.require_action.return_value = {"enabled": True, "available_actions": ["verify"]}
        with patch.object(ctl, "stage_module", return_value=stage):
            with self.assertRaises(ctl.ControlError) as caught:
                ctl.require_stage_action(self.ticket, "review")
        self.assertEqual(caught.exception.extra["gate_id"], "lite_stage_unsupported")

    def test_delivery_rechecks_missing_or_changed_review_report(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        fact = next(e["payload"] for e in ctl.read_events(self.ticket)[0]
                    if e["event_type"] == "lite_review_returned")
        path = self.ticket / fact["report_path"]
        original = path.read_bytes()
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    path.unlink()
                else:
                    path.write_text('{}')
                before = self.bytes()
                self.assertFalse(ctl.delivery_freshness(self.ticket)["ok"])
                self.call("candidate", "--dir", self.ticket, "--phase", "inspect",
                          "--require-step", "lite_review", ok=False)
                proc = subprocess.run([sys.executable, "-B", str(fixtures.ROOT / "tools/icode_delivery.py"),
                                       "--dir", str(self.ticket)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertFalse(json.loads(proc.stdout)["ok"])
                proc = subprocess.run([sys.executable, "-B", str(fixtures.ROOT / "scripts/submission_guard.py"),
                                       "submit-check", "--metadata", str(self.ticket / ctl.METADATA_NAME)],
                                      capture_output=True, text=True)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertEqual(self.bytes(), before)
                path.write_bytes(original)
        self.assertTrue(ctl.delivery_freshness(self.ticket)["ok"])

    def test_delivery_rechecks_contract_even_without_source_change(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        before = self.bytes()
        # 保持事件与源码完整，仅将当前语义摘要视为不同，隔离G2读侧的责任。
        with patch.object(ctl.lite_module(), "contract_digest", return_value="0" * 64):
            self.assertFalse(ctl.delivery_freshness(self.ticket)["ok"])
        self.assertEqual(self.bytes(), before)

    def test_report_rejects_tampered_or_missing_review(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        self.call("lite-report", "--dir", self.ticket)
        rendered = (self.ticket / "lite.md").read_bytes()
        fact = next(e["payload"] for e in ctl.read_events(self.ticket)[0]
                    if e["event_type"] == "lite_review_returned")
        path = self.ticket / fact["report_path"]
        original = path.read_bytes()
        tampered = json.loads(original)
        tampered["implementation_assessment"] = "未经签收的替换正文"
        for content in (json.dumps(tampered).encode(), None):
            try:
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                before = self.bytes()
                result = self.call("lite-report", "--dir", self.ticket, ok=False)
                self.assertEqual(result["gate_id"], "lite_review_report")
                self.assertEqual(self.bytes(), before)
                self.assertEqual((self.ticket / "lite.md").read_bytes(), rendered)
            finally:
                path.write_bytes(original)
        self.call("lite-report", "--dir", self.ticket)

    def test_report_rejects_drifted_contract_source_and_return(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        self.call("lite-report", "--dir", self.ticket)
        rendered = (self.ticket / "lite.md").read_bytes()
        before = self.bytes()
        with patch.object(ctl.lite_module(), "contract_digest", return_value="0" * 64):
            with self.assertRaises(ctl.ControlError) as caught:
                ctl.cmd_lite_report(Mock(dir=str(self.ticket)))
            self.assertEqual(caught.exception.extra["gate_id"], "lite_review_stale")
        self.assertEqual(self.bytes(), before)
        drift = self.repo / "runtime.json"
        drift.write_text('{"value":3}')
        result = self.call("lite-report", "--dir", self.ticket, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_stale")
        self.assertEqual(self.bytes(), before)
        drift.unlink()
        self.transition("lite_prepare_in_progress")
        before = self.bytes()
        result = self.call("lite-report", "--dir", self.ticket, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_stale")
        self.assertEqual(self.bytes(), before)
        self.assertEqual((self.ticket / "lite.md").read_bytes(), rendered)

    def test_report_rejects_previous_success_during_failed_retry(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        self.call("lite-report", "--dir", self.ticket)
        rendered = (self.ticket / "lite.md").read_bytes()
        self.transition("lite_code_in_progress")
        a = self.start("lite_code")
        self.finish("lite_code", a)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        a = self.start("lite_review")
        before = self.bytes()
        self.call("lite-report", "--dir", self.ticket, ok=False)
        self.assertEqual(self.bytes(), before)
        binary = self.repo / ".icode_output/bin"
        (binary / "codex").write_text('#!' + sys.executable +
            '\nimport sys\nsys.stdin.read()\nsys.exit(7)\n')
        with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ.get("PATH", "")}):
            self.call("lite-review", "--dir", self.ticket, "--attempt", a,
                      "--backend", "codex", "--request-id", "retry-failed", ok=False)
        before = self.bytes()
        self.call("lite-report", "--dir", self.ticket, ok=False)
        self.assertEqual(self.bytes(), before)
        self.assertEqual((self.ticket / "lite.md").read_bytes(), rendered)

    def test_report_rejects_pass_with_blocking_findings(self):
        a = self.code()
        self.fake_host_review(a, findings=[{"id": "F-1", "blocking": True, "status": "open",
            "description": "仍缺边界检查", "evidence": "source.py:1"}], ok=False)
        before = self.bytes()
        result = self.call("lite-report", "--dir", self.ticket, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_report")
        self.assertEqual(self.bytes(), before)
        self.assertFalse((self.ticket / "lite.md").exists())

    def test_report_preserves_valid_rejected_review(self):
        a = self.code()
        self.fake_host_review(a, verdict="changes_required")
        before = self.bytes()
        self.call("lite-report", "--dir", self.ticket)
        text = (self.ticket / "lite.md").read_text()
        self.assertIn("审核结论：changes_required", text)
        self.assertNotIn("交付：verified", text)
        self.assertEqual(self.bytes(), before)
        self.finish("lite_review", a, ok=False)

    def test_report_preserves_valid_blocked_review(self):
        a = self.code()
        self.fake_host_review(a, verdict="blocked")
        self.call("lite-report", "--dir", self.ticket)
        self.assertIn("审核结论：blocked", (self.ticket / "lite.md").read_text())
        self.finish("lite_review", a, ok=False)

    def test_rejected_review_identity_is_checked_in_report_mode(self):
        a = self.code()
        self.fake_host_review(a, verdict="changes_required")
        meta = ctl.load_metadata(self.ticket)
        fact = next(e["payload"] for e in ctl.read_events(self.ticket)[0]
                    if e["event_type"] == "lite_review_returned")
        report = json.loads((self.ticket / fact["report_path"]).read_text())
        for field, value in (("schema_version", 2), ("attempt", "another-attempt"),
                             ("candidate_id", "0" * 64), ("contract_digest", "0" * 64)):
            altered = dict(report, **{field: value})
            self.assertIn("review_" + field, ctl.lite_module().review_issues(
                altered, meta, report["candidate_id"], a, allow_rejected=True))

    def test_report_snapshot_survives_replacement_after_g2(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        fact = next(e["payload"] for e in ctl.read_events(self.ticket)[0]
                    if e["event_type"] == "lite_review_returned")
        path = self.ticket / fact["report_path"]
        altered = json.loads(path.read_bytes())
        altered["implementation_assessment"] = "G2后未经签收的替换正文"
        original = ctl.lite_review_fact
        candidate = original(self.ticket, ctl.load_metadata(self.ticket), ctl.read_events(self.ticket)[0])
        self.assertEqual(candidate["candidate_id"], fact["candidate_id"])
        def replace_after_g2(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_text(json.dumps(altered))
            return result
        before = self.bytes()
        with patch.object(ctl, "lite_review_fact", side_effect=replace_after_g2) as checked:
            self.assertEqual(ctl.cmd_lite_report(Mock(dir=str(self.ticket))), 0)
        self.assertEqual(checked.call_count, 1)
        text = (self.ticket / "lite.md").read_text()
        self.assertIn("源码与证据已检查", text)
        self.assertNotIn(altered["implementation_assessment"], text)
        self.assertEqual(self.bytes(), before)

    def test_report_snapshot_hash_and_parse_share_bytes(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        fact = next(e["payload"] for e in ctl.read_events(self.ticket)[0]
                    if e["event_type"] == "lite_review_returned")
        path = self.ticket / fact["report_path"]
        altered = json.loads(path.read_bytes())
        altered["implementation_assessment"] = "哈希读取后未经签收的替换正文"
        before = self.bytes()
        with replace_review_after_binary_read(path, json.dumps(altered).encode()) as replaced:
            self.assertEqual(ctl.cmd_lite_report(Mock(dir=str(self.ticket))), 0)
        self.assertEqual(replaced, [True])
        text = (self.ticket / "lite.md").read_text()
        self.assertIn("源码与证据已检查", text)
        self.assertNotIn(altered["implementation_assessment"], text)
        self.assertEqual(self.bytes(), before)

    def test_historical_findings_snapshot_preserves_signed_status(self):
        a = self.code()
        self.fake_host_review(a, verdict="changes_required", findings=[{
            "id": "F-1", "blocking": True, "status": "open",
            "description": "缺少边界检查", "evidence": "source.py:1"}])
        events = ctl.read_events(self.ticket)[0]
        fact = next(e["payload"] for e in events if e["event_type"] == "lite_review_returned")
        path = self.ticket / fact["report_path"]
        altered = json.loads(path.read_bytes())
        altered["findings"][0]["status"] = "resolved"
        before = self.bytes()
        with replace_review_after_binary_read(path, json.dumps(altered).encode()) as replaced:
            findings = ctl.lite_open_findings(self.ticket, events)
        self.assertEqual(replaced, [True])
        self.assertIn("F-1", findings)
        self.assertEqual(findings["F-1"]["status"], "open")
        self.assertEqual(self.bytes(), before)

    def test_review_snapshot_errors_are_controlled(self):
        self.create_lite()
        path = self.ticket / "reader.json"
        for raw in (b"\xff", b"{", b'{"value":NaN}'):
            path.write_bytes(raw)
            fact = {"report_path": path.name, "report_sha256": hashlib.sha256(raw).hexdigest()}
            with self.assertRaises(ctl.ControlError) as caught:
                ctl._load_lite_review_report(self.ticket, fact)
            self.assertEqual(caught.exception.extra["gate_id"], "lite_review_report")
        with patch.object(Path, "read_bytes", side_effect=FileNotFoundError(path)):
            with self.assertRaises(ctl.ControlError) as caught:
                ctl._load_lite_review_report(self.ticket, fact)
            self.assertEqual(caught.exception.extra["gate_id"], "lite_review_report")

    def test_delivery_rechecks_protected_lite_without_activating_provider(self):
        self.create_lite()
        before = self.bytes()
        with patch.object(ctl, "_candidate_context_guard",
                          side_effect=AssertionError("unsupported lite must not activate provider")), \
                patch.object(ctl.subprocess, "run", side_effect=AssertionError("raw Git fallback")):
            result = ctl.delivery_freshness(self.ticket, git_provider=object(), require_git_provider=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["lite_provider_unsupported"])
        self.assertEqual(self.bytes(), before)

    def test_delta_requires_prepare_after_completion_and_exact_replay(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        command = ["metadata-update", "--dir", self.ticket, "--append-json",
                   json.dumps({"requirement_deltas": [{"source_ref": "user followup", "text": "支持值3"}]}),
                   "--request-id", "delta-once"]
        before = self.bytes()
        self.call(*command, ok=False)
        self.assertEqual(self.bytes(), before)
        self.transition("lite_prepare_in_progress")
        self.assertEqual(ctl.load_metadata(self.ticket)["completed_steps"], [])
        self.assertNotEqual(ctl.load_metadata(self.ticket)["delivery_verdict"], "verified")
        self.call(*command)
        a = self.start("lite_prepare")
        self.finish("lite_prepare", a)
        self.transition("lite_prepare_done")
        before = self.bytes()
        self.assertTrue(self.call(*command)["already_applied"])
        self.assertEqual(self.bytes(), before)
        conflicting = list(command)
        conflicting[4] = json.dumps({"requirement_deltas": [{"source_ref": "other", "text": "不同内容"}]})
        self.call(*conflicting, ok=False)
        self.assertEqual(self.bytes(), before)

    def test_delta_requires_prepare_during_implementation(self):
        self.prepare()
        before = self.bytes()
        self.call("metadata-update", "--dir", self.ticket, "--append-json",
                  '{"requirement_deltas":[{"source_ref":"user","text":"value3"}]}', ok=False)
        self.assertEqual(self.bytes(), before)

    def test_identical_commit_preserves_review_evidence_and_archive(self):
        a = self.code(external=True)
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verification_pending")
        fact = next(e["payload"] for e in ctl.read_events(self.ticket)[0]
                    if e["event_type"] == "lite_review_returned")
        original = (self.ticket / fact["report_path"]).read_bytes()
        self.git("add", "source.py")
        self.git("commit", "-qm", "reviewed content")
        self.call("validate", "--dir", self.ticket)
        self.verify(scenario="device", layer="consumption")
        self.transition("completed", "verified")
        self.assertTrue(ctl.delivery_freshness(self.ticket)["ok"])
        self.assertEqual((self.ticket / fact["report_path"]).read_bytes(), original)
        self.assertEqual(sum(e["event_type"] == "lite_review_invoked"
                             for e in ctl.read_events(self.ticket)[0]), 1)
        self.call("lite-report", "--dir", self.ticket)
        archive = self.repo / ".icode_output/archive"
        shutil.copytree(self.ticket, archive)
        self.call("archive-manifest", "--dir", self.ticket, "--archive-dir", archive, "--write")
        (self.repo / "source.py").write_text("value = 3\n")
        self.assertFalse(ctl.delivery_freshness(self.ticket)["ok"])

    def test_identical_commit_does_not_allow_amended_history(self):
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified")
        self.git("add", "source.py")
        self.git("commit", "--amend", "-qm", "replaced history")
        self.assertFalse(ctl.delivery_freshness(self.ticket)["ok"])
        self.call("validate", "--dir", self.ticket, ok=False)

    def test_identical_commit_between_start_and_call_requires_new_attempt(self):
        a = self.code()
        self.git("add", "source.py")
        self.git("commit", "-qm", "reviewed input moved before invocation")
        self.fake_host_review(a, ok=False)
        self.assertFalse(any(e["event_type"] == "lite_review_invoked"
                             for e in ctl.read_events(self.ticket)[0]))

    def test_identical_commit_cannot_hide_latest_ineligible_verification(self):
        a = self.code()
        self.fake_host_review(a)
        self.git("add", "source.py")
        self.git("commit", "-qm", "same content")
        (self.repo / "source.py").write_text("value = 3\n")
        self.verify()
        (self.repo / "source.py").write_text("value = 2\n")
        self.finish("lite_review", a, ok=False)


if __name__ == "__main__":
    unittest.main()
