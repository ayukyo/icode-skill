"""lite 原生流程的实际控制面合同，使用临时 Git 仓库。"""
import json
import unittest
import sys
import subprocess
import shutil
import tempfile
import builtins
import hashlib
import os
import uuid
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
    _base_setUp = fixtures.CandidateTests.setUp
    git = fixtures.CandidateTests.git
    bytes = fixtures.CandidateTests.bytes

    def call(self, *args, ok=True):
        """在隔离子进程里调用控制面，信任根由夹具包装器进程内注入。

        生产控制面完全不存在环境变量/命令行形式的信任根入口；夹具改用一个小
        包装器脚本，在导入模块后直接给 _HOST_CONFIG_ROOT_OVERRIDE 赋值，再调用
        main()。该包装器只存在于测试临时目录，调用方无法让生产 CLI 使用它。
        """
        proc = subprocess.run([sys.executable, "-B", str(self._harness_script()),
                               *map(str, args)], capture_output=True, text=True, timeout=20,
                              encoding="utf-8", env=dict(os.environ))
        self.assertEqual(proc.returncode == 0, ok, (proc.stdout, proc.stderr))
        return json.loads(proc.stdout)

    def _harness_script(self):
        cached = getattr(self, "_harness_path", None)
        if cached is not None and cached.exists():
            return cached
        script = self.host_home / "run_control.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            f"ROOT = Path({str(fixtures.ROOT)!r})\n"
            "spec = importlib.util.spec_from_file_location(\n"
            "    'candidate_control', ROOT / 'tools/icode_control.py')\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            f"mod._HOST_CONFIG_ROOT_OVERRIDE = Path({str(self.host_root)!r})\n"
            "sys.argv = ['icode_control.py', *sys.argv[1:]]\n"
            "sys.exit(mod.main())\n", encoding="utf-8")
        self._harness_path = script
        return script

    def setUp(self):
        self._base_setUp()
        self.challenge = {}
        # 宿主侧配置放在仓库外的临时目录，避免污染候选快照。
        # 生产信任根来自系统账户数据库（不受 HOME 影响），不接受任何环境变量；
        # 夹具改为进程内注入 _HOST_CONFIG_ROOT_OVERRIDE（见 call/_harness_script）。
        self.host_home = self.repo.parent / "hostcfg"
        self.host_root = self.host_home / ".icode"
        self.host_root.mkdir(parents=True, exist_ok=True)
        self.host_root.chmod(0o700)
        self.host_config = self.write_host_config()

    def drop_host_config(self):
        self.host_config.unlink(missing_ok=True)

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

    def write_host_adapter(self):
        """夹具宿主适配器：查询“宿主”侧的启动/会话状态，再回传确认。

        真实适配器应查询实际宿主（如 DSH 会话注册表）；夹具用测试写入的
        host-launch/host-sessions 记录代表宿主状态，未登记即拒绝确认。
        """
        if getattr(self, "_adapter", None):
            return self._adapter
        path = self.host_root / "fixture-host-adapter.py"
        path.write_text(
            "#!" + sys.executable + "\n"
            "import json,sys,pathlib\n"
            f"root=pathlib.Path({str(self.ticket)!r})\n"
            "req=json.load(sys.stdin)\n"
            "launch=root/'host-launch'/f\"{req['spawn_id']}.json\"\n"
            "sess=root/'host-sessions'/f\"{req['host_session_id']}.json\"\n"
            "if not launch.is_file() or not sess.is_file(): sys.exit(3)\n"
            "l=json.loads(launch.read_text()); s=json.loads(sess.read_text())\n"
            "if l.get('spawn_id')!=req['spawn_id'] or l.get('challenge')!=req['challenge']: sys.exit(4)\n"
            "if s.get('spawn_id')!=req['spawn_id'] or s.get('challenge')!=req['challenge']: sys.exit(5)\n"
            "print(json.dumps({'launched':True,'spawn_id':req['spawn_id'],\n"
            " 'host_session_id':req['host_session_id'],'challenge':req['challenge'],\n"
            " 'report_sha256':req['report_sha256'],\n"
            " 'stored_report_sha256':req['stored_report_sha256']}))\n", encoding="utf-8")
        path.chmod(0o755)
        self._adapter = path
        return path

    def register_launch(self, spawn):
        launch_dir = self.ticket / "host-launch"
        launch_dir.mkdir(exist_ok=True)
        (launch_dir / (spawn + ".json")).write_text(json.dumps(
            {"spawn_id": spawn, "challenge": self.challenge[spawn]}), encoding="utf-8")

    def register_session(self, spawn, session):
        sess_dir = self.ticket / "host-sessions"
        sess_dir.mkdir(exist_ok=True)
        (sess_dir / (session + ".json")).write_text(json.dumps(
            {"spawn_id": spawn, "challenge": self.challenge[spawn]}), encoding="utf-8")

    def write_host_config(self, *, pin=None, adapter=None):
        """宿主侧可信配置：固定命令与摘要（模拟宿主安装器写入，权限 0600）。"""
        adapter = adapter or self.write_host_adapter()
        config = self.host_root / "host_adapter.json"
        config.parent.mkdir(exist_ok=True)
        digest = pin if pin is not None else hashlib.sha256(adapter.read_bytes()).hexdigest()
        config.write_text(json.dumps({"command": str(adapter), "sha256": digest}), encoding="utf-8")
        config.chmod(0o600)
        return config

    def prepare_host_review(self, attempt, ok=True):
        out = self.call("lite-review", "--dir", self.ticket, "--attempt", attempt,
                        "--request-id", "fixture-review-" + attempt, ok=ok)
        if ok and "review_input" in out:
            self.challenge[out["spawn"]["spawn_id"]] = out["review_input"]["host_receipt_challenge"]
            self.register_launch(out["spawn"]["spawn_id"])
        return out

    def host_result(self, spawn, *, result="joined", verdict="pass", findings=None,
                    identity=None, adopted=None, challenge=None, ok=True,
                    with_receipt=True, with_session=True):
        adopt = adopted if adopted is not None else ("yes" if result == "joined" else "no")
        args = ["record-agent-result", "--dir", self.ticket, "--spawn-id", spawn,
                "--result", result, "--adopted", adopt,
                "--adoption-reason", "核对宿主返回",
                "--evidence-ref", "宿主工具调用夹具", "--summary", "结构化审核结果",
                "--request-id", str(uuid.uuid4())]
        if result == "joined":
            raw = self.ticket / "fixture-review-result.json"
            raw.write_text(json.dumps({"verdict": verdict, "findings": findings or [], "criteria": [
                {"id": "AC-1", "status": "pass", "evidence": "source.py 及测试报告"}],
                "requirement_assessment": "原始目标与方案一致", "implementation_assessment": "源码与证据已检查"}),
                encoding="utf-8")
            session = identity or str(uuid.uuid4())
            if with_session:
                self.register_session(spawn, session)
            args += ["--host-session-id", session, "--lite-review-report", raw]
            if with_receipt:
                receipt = self.ticket / "fixture-host-receipt.json"
                receipt.write_text(json.dumps({
                    "spawn_id": spawn, "host_session_id": session,
                    "challenge": challenge if challenge is not None else self.challenge[spawn],
                    "returned_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
                args += ["--host-receipt", receipt]
        return self.call(*args, ok=ok)

    def fake_host_review(self, attempt, verdict="pass", findings=None):
        # 模拟宿主的工具返回；登记、校验和签收均通过实际控制面。
        spawn = self.prepare_host_review(attempt)["spawn"]["spawn_id"]
        return self.host_result(spawn, verdict=verdict, findings=findings)

    def review_records(self):
        return ctl.lite_review_records(ctl.read_events(self.ticket)[0])

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

    def test_degraded_review_completes_only_as_verification_pending(self):
        # P1：无适配器时不能停在 Review。降级工单应能走完并交付，但只能
        # verification_pending，不能声称 verified。
        self.drop_host_config()
        a = self.code()
        self.fake_host_review(a)
        self.finish("lite_review", a)
        self.transition("completed", "verified", ok=False)
        self.transition("completed", "verification_pending")
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["delivery_verdict"], "verification_pending")
        self.assertEqual(meta["status"], "completed")
        self.assertEqual(meta["extensions"]["lite"]["host_adapter"]["status"], "unavailable")

    def test_degraded_review_is_marked_unattested(self):
        # 降级审核只作留痕：不得写入表示"已获可信确认"的 host_attestation，
        # 且审核报告仍留档可追溯。
        self.drop_host_config()
        a = self.code()
        self.fake_host_review(a)
        returns = ctl.lite_review_records(ctl.read_events(self.ticket)[0])[1]
        self.assertIsNone(returns[-1].get("host_attestation"))
        self.assertEqual(returns[-1]["host_attestation_degraded"]["status"], "unavailable")
        self.assertEqual(returns[-1]["outcome"], "degraded")
        self.assertTrue(returns[-1].get("report_path"))
        self.finish("lite_review", a, ok=True)

    def test_degraded_review_cannot_close_blocking_findings(self):
        # 降级审核只留痕：即使报告声称 resolved，也不得关闭历史阻断问题。
        a = self.code()
        finding = {"id": "F-1", "blocking": True, "status": "open",
                   "description": "未处理值更新边界", "evidence": "source.py:1"}
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        self.host_result(spawn, verdict="changes_required", findings=[finding], adopted="no")
        self.finish("lite_review", a, outcome="blocked")

        # 去掉适配器后重新进入 Code：冻结身份降级为 unavailable。
        self.drop_host_config()
        self.transition("lite_code_in_progress")
        code = self.start("lite_code")
        self.finish("lite_code", code)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        review = self.start("lite_review")
        self.fake_host_review(review, findings=[dict(finding, status="resolved")])
        # 降级报告不能关闭 F-1，因此既不能过审，也不能以 pending 交付。
        self.finish("lite_review", review, ok=False)
        self.transition("completed", "verification_pending", ok=False)

    def test_fake_pass_and_degraded_do_not_complete(self):
        a = self.code()
        (self.ticket / "fake-pass.json").write_text('{"verdict":"pass"}')
        self.finish("lite_review", a, ok=False)
        self.finish("lite_review", a, outcome="degraded", ok=False)
        self.transition("completed", "verification_pending", ok=False)

    def test_review_prepares_host_subagent_with_bounded_input(self):
        for i in range(96):
            (self.repo / (f"fixture_{i:03d}_" + "a" * 100)).write_text("candidate content")
        a = self.code()
        prepared = self.call("lite-review", "--dir", self.ticket, "--attempt", a,
                             "--request-id", "prepare-subagent")
        self.assertEqual(prepared["spawn"]["backend"], "host-subagent")
        self.assertEqual(prepared["review_input"]["workspace"], str(self.repo))
        self.assertIn("候选", prepared["review_input"]["task"])
        self.assertGreater(len(json.dumps(ctl.load_metadata(self.ticket)["verification_runs"])), 20000)
        self.assertLess(len(json.dumps(prepared)), 4096)
        self.assertNotIn("index_manifest", json.dumps(prepared))
        self.assertNotIn("verification_runs", prepared["review_input"])
        self.finish("lite_review", a, ok=False)
        repeated = self.call("lite-review", "--dir", self.ticket, "--attempt", a,
                             "--request-id", "prepare-subagent")
        self.assertTrue(repeated["already_applied"])
        self.assertEqual(repeated["spawn"]["spawn_id"], prepared["spawn"]["spawn_id"])
        # 重放补回同一 challenge 的只读输入，避免中断后丢失审核输入。
        self.assertEqual(repeated["review_input"]["host_receipt_challenge"],
                         prepared["review_input"]["host_receipt_challenge"])
        self.assertLess(len(json.dumps(repeated)), 4096)
        self.call("lite-review", "--dir", self.ticket, "--attempt", a,
                  "--request-id", "duplicate-subagent", ok=False)

    def test_review_rejects_join_without_return_and_reused_host_context(self):
        a = self.code()
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        result = self.call("record-agent-result", "--dir", self.ticket, "--spawn-id", spawn,
                           "--result", "joined", "--adopted", "yes", "--adoption-reason", "夹具",
                           "--evidence-ref", "普通返回", "--summary", "pass",
                           "--request-id", "no-review-report", ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host")
        self.host_result(spawn, verdict="blocked", identity="first-thread")
        self.finish("lite_review", a, outcome="blocked")
        a = self.start("lite_review")
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        result = self.host_result(spawn, identity="first-thread", ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host")
        self.finish("lite_review", a, ok=False)

    def test_review_requires_bound_host_receipt(self):
        # P1：缺少绑定本次登记的宿主回执时，joined 结果被拒绝。
        a = self.code()
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        result = self.host_result(spawn, with_receipt=False, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host_receipt")
        # 伪造/错配 challenge 的回执同样被拒。
        result = self.host_result(spawn, challenge="0" * 64, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host_receipt")
        # challenge 与 spawn_id 不一致被拒。
        wrong_spawn_receipt = self.ticket / "wrong.json"
        wrong_spawn_receipt.write_text(json.dumps({
            "spawn_id": "other-spawn", "host_session_id": "s1",
            "challenge": self.challenge[spawn]}), encoding="utf-8")
        raw = self.ticket / "fixture-review-result.json"
        raw.write_text(json.dumps({"verdict": "pass", "findings": [], "criteria": [
            {"id": "AC-1", "status": "pass", "evidence": "x"}],
            "requirement_assessment": "ok", "implementation_assessment": "ok"}), encoding="utf-8")
        result = self.call("record-agent-result", "--dir", self.ticket, "--spawn-id", spawn,
                           "--result", "joined", "--adopted", "yes", "--adoption-reason", "r",
                           "--evidence-ref", "e", "--summary", "s", "--request-id", str(uuid.uuid4()),
                           "--host-session-id", "s1", "--lite-review-report", raw,
                           "--host-receipt", wrong_spawn_receipt, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host_receipt")
        self.finish("lite_review", a, ok=False)

    def test_unadopted_report_cannot_satisfy_review_gate(self):
        # P2：adopted=no 的 joined 报告保留追溯，但不能推动交付。
        a = self.code()
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        self.host_result(spawn, verdict="pass", adopted="no")
        events = ctl.read_events(self.ticket)[0]
        invocations, returns = ctl.lite_review_records(events)
        # 记录仍在（可追溯），但不是可采信成功回执。
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0]["adopted"], "no")
        self.assertEqual(returns[0]["outcome"], "failure")
        meta = ctl.load_metadata(self.ticket)
        with self.assertRaises(Exception):
            ctl.lite_review_fact(self.ticket, meta, events, attempt=a)
        self.finish("lite_review", a, ok=False)

    def test_missing_host_adapter_degrades_but_still_blocks_signing(self):
        # 与其他模式一致：适配器缺失不再阻断规划/实施，而是记降级事实，
        # 交付结论降为 verification_pending；但签收仍须可信适配器。
        self.drop_host_config()
        self.create_lite()
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps(self.contracts()))
        a = self.start("lite_prepare")
        self.finish("lite_prepare", a)          # 不再阻断
        self.transition("lite_prepare_done")
        self.transition("lite_code_in_progress")

        meta = ctl.load_metadata(self.ticket)
        pinned = meta["extensions"]["lite"]["host_adapter"]
        self.assertEqual(pinned["status"], "unavailable")
        self.assertEqual(meta["delivery_verdict"], "verification_pending")
        # 签收仍 fail-closed：降级只影响进展，不影响"审核结果可采信"。
        with self.assertRaises(ctl.ControlError) as caught:
            ctl._pinned_adapter_sha256(meta)
        self.assertEqual(caught.exception.extra["gate_id"], "lite_host_adapter_unavailable")

    def test_host_adapter_pin_mismatch_degrades_then_blocks_signing(self):
        bad = self.host_root / "swapped-adapter.py"
        bad.write_text("#!" + sys.executable + "\nimport sys\nsys.exit(0)\n", encoding="utf-8")
        bad.chmod(0o755)
        # 配置指向另一个程序但摘要仍是原适配器 → 该配置不可信，按不可用降级。
        self.write_host_config(adapter=bad, pin=hashlib.sha256(
            (self.host_root / "fixture-host-adapter.py").read_bytes()).hexdigest())
        self.create_lite()
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps(self.contracts()))
        a = self.start("lite_prepare")
        self.finish("lite_prepare", a)
        self.transition("lite_prepare_done")
        self.transition("lite_code_in_progress")

        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["extensions"]["lite"]["host_adapter"]["status"], "unavailable")
        with self.assertRaises(ctl.ControlError):
            ctl._pinned_adapter_sha256(meta)

    def test_caller_env_cannot_redirect_host_config(self):
        # P1：调用方设置 ICODE_HOST_CONFIG 指向自建配置时，控制面必须忽略它，
        # 仍读宿主信任根；否则"回显即可"的自建适配器会通过来源确认。
        evil_root = self.repo.parent / "caller_owned"
        evil_root.mkdir()
        evil_adapter = evil_root / "echo-adapter.py"
        evil_adapter.write_text(
            "#!" + sys.executable + "\nimport json,sys\n"
            "req=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,**{k:req[k] for k in "
            "('spawn_id','host_session_id','challenge','report_sha256',"
            "'stored_report_sha256')}}))\n", encoding="utf-8")
        evil_adapter.chmod(0o700)
        evil_config = evil_root / "host_adapter.json"
        evil_config.write_text(json.dumps({
            "command": str(evil_adapter),
            "sha256": hashlib.sha256(evil_adapter.read_bytes()).hexdigest()}), encoding="utf-8")
        evil_config.chmod(0o600)

        # 宿主信任根本身未安装配置：调用方的环境变量不能把门禁"救活"。
        self.drop_host_config()
        self._env_guard = patch.dict(os.environ, {"ICODE_HOST_CONFIG": str(evil_config)})
        self._env_guard.start()
        self.addCleanup(self._env_guard.stop)
        self.create_lite()
        self.call("metadata-update", "--dir", self.ticket, "--set-json", json.dumps(self.contracts()))
        a = self.start("lite_prepare")
        self.finish("lite_prepare", a)
        self.transition("lite_prepare_done")
        self.transition("lite_code_in_progress")

        # 环境变量不能把降级状态"救活"：仍记为 unavailable，签收仍被拒。
        meta = ctl.load_metadata(self.ticket)
        self.assertEqual(meta["extensions"]["lite"]["host_adapter"]["status"], "unavailable")
        with self.assertRaises(ctl.ControlError) as caught:
            ctl._pinned_adapter_sha256(meta)
        self.assertEqual(caught.exception.extra["gate_id"], "lite_host_adapter_unavailable")

    def test_caller_adapter_outside_host_root_is_not_used(self):
        # P1：即使环境变量指向摘要自洽的私有配置，签收也不能用它做来源确认。
        evil_root = self.repo.parent / "caller_owned2"
        evil_root.mkdir()
        evil_adapter = evil_root / "echo-adapter.py"
        evil_adapter.write_text(
            "#!" + sys.executable + "\nimport json,sys\n"
            "req=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,**{k:req[k] for k in "
            "('spawn_id','host_session_id','challenge','report_sha256',"
            "'stored_report_sha256')}}))\n", encoding="utf-8")
        evil_adapter.chmod(0o700)
        evil_config = evil_root / "host_adapter.json"
        evil_config.write_text(json.dumps({
            "command": str(evil_adapter),
            "sha256": hashlib.sha256(evil_adapter.read_bytes()).hexdigest()}), encoding="utf-8")
        evil_config.chmod(0o600)

        a = self.code()
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        # 宿主未登记该会话；若控制了信任根，回显适配器会误判 launched=true。
        self._env_guard = patch.dict(os.environ, {"ICODE_HOST_CONFIG": str(evil_config)})
        self._env_guard.start()
        self.addCleanup(self._env_guard.stop)
        result = self.host_result(spawn, with_session=False, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host_unattested")

    def test_home_env_cannot_redirect_trust_root(self):
        # P1：HOME 可被调用方设置；信任根必须来自系统账户数据库，不能用 HOME。
        evil_home = self.repo.parent / "attacker-home"
        (evil_home / ".icode").mkdir(parents=True)
        (evil_home / ".icode").chmod(0o700)
        evil_adapter = evil_home / ".icode" / "echo.py"
        evil_adapter.write_text(
            "#!" + sys.executable + "\nimport json,sys\n"
            "req=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,**{k:req[k] for k in "
            "('spawn_id','host_session_id','challenge','report_sha256',"
            "'stored_report_sha256')}}))\n", encoding="utf-8")
        evil_adapter.chmod(0o700)
        (evil_home / ".icode" / "host_adapter.json").write_text(json.dumps({
            "command": str(evil_adapter),
            "sha256": hashlib.sha256(evil_adapter.read_bytes()).hexdigest()}), encoding="utf-8")
        (evil_home / ".icode" / "host_adapter.json").chmod(0o600)

        # 生产路径（未设进程内钩子）下，仅改 HOME 不能换掉信任根。
        real_home = ctl._host_home()
        with patch.object(ctl, "_HOST_CONFIG_ROOT_OVERRIDE", None), \
                patch.dict(os.environ, {"HOME": str(evil_home)}):
            root = ctl._host_config_root()
            self.assertNotIn(str(evil_home), str(root))
            self.assertEqual(str(root), str(real_home / ".icode"))

    def test_test_mode_env_cannot_redirect_trust_root(self):
        # P1：ICODE_CONTROL_TEST_MODE/ICODE_TEST_HOST_ROOT 不能成为信任根入口。
        evil_root = self.repo.parent / "env-evil"
        (evil_root).mkdir(parents=True)
        evil_adapter = evil_root / "echo.py"
        evil_adapter.write_text(
            "#!" + sys.executable + "\nimport json,sys\n"
            "req=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,**{k:req[k] for k in "
            "('spawn_id','host_session_id','challenge','report_sha256',"
            "'stored_report_sha256')}}))\n", encoding="utf-8")
        evil_adapter.chmod(0o700)
        (evil_root / "host_adapter.json").write_text(json.dumps({
            "command": str(evil_adapter),
            "sha256": hashlib.sha256(evil_adapter.read_bytes()).hexdigest()}), encoding="utf-8")
        (evil_root / "host_adapter.json").chmod(0o600)

        # 未启用进程内钩子时，仅靠环境变量无法改变信任根。
        with patch.object(ctl, "_HOST_CONFIG_ROOT_OVERRIDE", None), \
                patch.dict(os.environ, {"ICODE_CONTROL_TEST_MODE": "1",
                                        "ICODE_TEST_HOST_ROOT": str(evil_root)}):
            root = ctl._host_config_root()
            self.assertNotIn(str(evil_root), str(root))

    def test_staged_adapter_gets_extension_and_is_cleaned(self):
        # P1/P2：暂存文件必须带扩展名（Windows CreateProcess 要求），
        # 并且清理时不能因为只读位而遗留文件。
        python_payload = b"#!" + sys.executable.encode() + b"\nprint('{}')\n"
        staged = ctl._stage_adapter_payload(python_payload, Path("/x/adapter"))
        workspace = staged.parent
        try:
            self.assertTrue(staged.exists())
            self.assertEqual(staged.suffix, ".py")
        finally:
            ctl._remove_staged_adapter(staged)
        self.assertFalse(workspace.exists(), "暂存工作目录未被清理")

    def test_staged_adapter_can_import_sibling_resources(self):
        # P2：暂存副本改写了 __file__/目录，适配器仍须能解析同目录资源。
        origin = Path(tempfile.mkdtemp(prefix="icode-adapter-origin-"))
        self.addCleanup(shutil.rmtree, origin, True)
        (origin / "helper.py").write_text("VALUE = 'sibling-ok'\n", encoding="utf-8")
        real = origin / "adapter.py"
        real.write_text(
            "#!" + sys.executable + "\n"
            "import json,sys,helper\nreq=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,'sibling':helper.VALUE}))\n",
            encoding="utf-8")
        real.chmod(0o700)

        staged = ctl._stage_adapter_payload(real.read_bytes(), real)
        try:
            proc = ctl._run_staged_adapter(staged, {"spawn_id": "s-1"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["sibling"], "sibling-ok")
        finally:
            ctl._remove_staged_adapter(staged)

    def test_others_writable_check_flags_group_and_world(self):
        # P1：权限判定必须真正拦截组/其他人可写，而不是恒真或恒假。
        base = Path(tempfile.mkdtemp(prefix="icode-acl-"))
        self.addCleanup(shutil.rmtree, base, True)
        if os.name != "nt":
            for mode, expected in ((0o700, False), (0o770, True), (0o707, True), (0o755, False)):
                with self.subTest(mode=oct(mode)):
                    target = base / ("d" + oct(mode)[-3:])
                    target.mkdir()
                    target.chmod(mode)
                    self.assertEqual(ctl._path_is_others_writable(target), expected)
            # 符号链接必须按其目标判定：0755 的链接指向 0777 目录仍是他人可写。
            open_dir = base / "open"; open_dir.mkdir(); open_dir.chmod(0o777)
            tight_dir = base / "tight"; tight_dir.mkdir(); tight_dir.chmod(0o700)
            link = base / "link-to-open"
            link.symlink_to(open_dir)
            self.assertTrue(ctl._path_is_others_writable(link))
            link2 = base / "link-to-tight"
            link2.symlink_to(tight_dir)
            self.assertFalse(ctl._path_is_others_writable(link2))
        else:
            self.skipTest("Windows ACL 断言需在 Windows 实机验证")


    def test_adapter_is_not_executed_when_pin_differs(self):
        # P1：适配器在实施期间被换掉时，未受信任的程序不得被执行。
        a = self.code()
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        marker = self.repo.parent / "executed.marker"
        swapped = self.host_root / "swapped-adapter.py"
        swapped.write_text(
            "#!" + sys.executable + "\n"
            f"open({str(marker)!r},'w').write('ran')\n"
            "import json,sys\n"
            "req=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,**{k:req[k] for k in "
            "('spawn_id','host_session_id','challenge','report_sha256',"
            "'stored_report_sha256')}}))\n", encoding="utf-8")
        swapped.chmod(0o700)
        self.write_host_config(adapter=swapped)

        result = self.host_result(spawn, with_session=False, ok=False)
        self.assertEqual(result["gate_id"], "lite_host_adapter_unavailable")
        # 关键断言：拒绝发生在执行之前，副作用没有发生。
        self.assertFalse(marker.exists(), "未受信任的适配器被执行了")

    def test_frozen_adapter_identity_cannot_be_rewritten(self):
        # P2：进入 Code 后不能用 metadata-update 改写冻结的适配器身份。
        self.prepare()
        before = self.bytes()
        forged = {"sha256": "f" * 64, "path": "/tmp/forged", "pinned_at": "2026-01-01T00:00:00"}
        result = self.call("metadata-update", "--dir", self.ticket, "--set-json",
                           json.dumps({"extensions": {"lite": {"host_adapter": forged}}}),
                           ok=False)
        self.assertEqual(result["gate_id"], "metadata_update_protected")
        self.assertEqual(self.bytes(), before)

    def test_review_requires_trusted_host_attestation(self):
        # P1：配置可信但宿主未登记该会话时，适配器拒绝确认，不能进入交付。
        a = self.code()
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        result = self.host_result(spawn, with_session=False, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_host_unattested")

        # 未取得可信确认前，工单无法推进到交付。
        self.finish("lite_review", a, ok=False)
        self.transition("completed", "verified", ok=False)

        # 宿主登记会话后由适配器确认，才可签收并交付。
        self.host_result(spawn)
        self.finish("lite_review", a)
        self.transition("completed", "verified")

    def test_adapter_swapped_after_prepare_blocks_attestation(self):
        # P1：Prepare 已冻结适配器身份；实施期间换成"只回显"的程序必须被拒。
        a = self.code()
        pinned = (ctl.load_metadata(self.ticket)["extensions"]["lite"]["host_adapter"])["sha256"]
        self.assertTrue(pinned)
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]

        # 宿主信任根被换成另一个摘要自洽、但只会回显的适配器。
        echo = self.host_root / "echo-adapter.py"
        echo.write_text(
            "#!" + sys.executable + "\nimport json,sys\n"
            "req=json.load(sys.stdin)\n"
            "print(json.dumps({'launched':True,**{k:req[k] for k in "
            "('spawn_id','host_session_id','challenge','report_sha256',"
            "'stored_report_sha256')}}))\n", encoding="utf-8")
        echo.chmod(0o700)
        self.write_host_config(adapter=echo)
        # 连宿主会话登记也不需要了：回显适配器会无条件"确认"。
        result = self.host_result(spawn, with_session=False, ok=False)
        self.assertEqual(result["gate_id"], "lite_host_adapter_unavailable")

    def test_unadopted_blocking_finding_must_be_readdressed(self):
        # P2：未采纳报告里的阻断问题仍须复审，不能因报告未被采纳就消失。
        a = self.code()
        finding = {"id": "F-1", "blocking": True, "status": "open",
                   "description": "未处理值更新的边界", "evidence": "source.py:1"}
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        self.host_result(spawn, verdict="changes_required", findings=[finding], adopted="no")
        self.finish("lite_review", a, outcome="blocked")
        self.transition("lite_code_in_progress")
        code = self.start("lite_code")
        self.finish("lite_code", code)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        review = self.start("lite_review")
        # 第二轮既未修复也未解释 F-1 → 仍不能通过。
        self.fake_host_review(review)
        self.finish("lite_review", review, ok=False)
        self.transition("completed", "verified", ok=False)
        # 显式以同一 ID + resolved + 证据关闭后才可交付。
        self.finish("lite_review", review, outcome="blocked")
        self.transition("lite_code_in_progress")
        code = self.start("lite_code")
        self.finish("lite_code", code)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        final = self.start("lite_review")
        self.fake_host_review(final, findings=[dict(finding, status="resolved")])
        self.finish("lite_review", final)
        self.transition("completed", "verified")

    def test_unadopted_resolved_cannot_close_existing_finding(self):
        # P2：只有可采信报告能关闭历史阻断；未采纳报告的 resolved 不算关闭。
        a = self.code()
        finding = {"id": "F-1", "blocking": True, "status": "open",
                   "description": "缺少边界核对", "evidence": "source.py:1"}
        self.fake_host_review(a, "changes_required", [finding])
        self.finish("lite_review", a, outcome="blocked")

        def redo_review():
            self.transition("lite_code_in_progress")
            code = self.start("lite_code")
            self.finish("lite_code", code)
            self.transition("lite_code_done")
            self.transition("lite_review_in_progress")
            return self.start("lite_review")

        second = redo_review()
        spawn = self.prepare_host_review(second)["spawn"]["spawn_id"]
        self.host_result(spawn, verdict="pass", adopted="no",
                         findings=[dict(finding, status="resolved")])
        self.finish("lite_review", second, outcome="blocked")

        third = redo_review()
        self.fake_host_review(third)  # 未提及 F-1
        result = self.finish("lite_review", third, ok=False)
        self.assertEqual(result["gate_id"], "lite_review_report")
        self.assertIn("unresolved_previous_finding:F-1", result.get("violations") or [])

    def test_legacy_failed_receipt_with_null_findings_is_skipped(self):
        # P2：旧版先写报告后记 schema 失败；这类回执不应纳入复审或被解析崩溃。
        report = self.host_root / "legacy-null.json"
        report.write_text(json.dumps({"verdict": "pass", "findings": None}), encoding="utf-8")
        digest = hashlib.sha256(report.read_bytes()).hexdigest()
        events = [
            {"event_type": "lite_review_invoked", "payload": {"invocation_id": "x", "attempt": "1"}},
            {"event_type": "lite_review_returned", "payload": {
                "invocation_id": "x", "attempt": "1", "outcome": "failure",
                "report_path": report.name, "report_sha256": digest}},
        ]
        self.assertEqual(ctl.lite_open_findings(self.host_root, events), {})
        # 即使旧版把失败回执记成 success，也不能因 null findings 崩溃。
        events[1]["payload"]["outcome"] = "success"
        self.assertEqual(ctl.lite_open_findings(self.host_root, events), {})

    def test_legacy_cli_review_fact_and_archive_remain_readable(self):
        a = self.code()
        self.fake_host_review(a)
        events = ctl.read_events(self.ticket)[0]
        invocations, returns = ctl.lite_review_records(events)
        # 保留旧版事件形状，验证所有 G2 读侧使用同一套校验。
        legacy = [e for e in events if e["event_type"] not in {"agent_spawned", "agent_result"}]
        legacy.extend(({"event_type": "lite_review_invoked", "payload": invocations[0]},
                       {"event_type": "lite_review_returned", "payload": returns[0]}))
        meta = ctl.load_metadata(self.ticket)
        report = ctl.lite_review_fact(self.ticket, meta, legacy, attempt=a, return_report=True)
        self.assertEqual(report["verdict"], "pass")
        self.assertIn(returns[0]["report_path"], ctl.required_archive_files(meta, legacy))

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
        review_count = len(ctl.lite_review_records(events)[0])
        self.verify(scenario="device", layer="consumption")
        self.transition("completed", "verified")
        self.assertEqual(len(ctl.lite_review_records(ctl.read_events(self.ticket)[0])[0]), review_count)

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
        self.fake_host_review(review)
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
        spawn = self.prepare_host_review(waiting)["spawn"]["spawn_id"]
        self.finish("lite_review", waiting, outcome="blocked")
        self.transition("lite_code_in_progress", ok=False)
        self.host_result(spawn, findings=[dict(finding, status="resolved")], ok=False)
        self.host_result(spawn, result="failed")
        self.transition("lite_code_in_progress")
        code = self.start("lite_code")
        self.finish("lite_code", code)
        self.transition("lite_code_done")
        self.transition("lite_review_in_progress")
        self.fake_host_review(self.start("lite_review"))
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
        fact = self.review_records()[1][0]
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
        fact = self.review_records()[1][0]
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
        spawn = self.prepare_host_review(a)["spawn"]["spawn_id"]
        self.host_result(spawn, result="failed")
        before = self.bytes()
        self.call("lite-report", "--dir", self.ticket, ok=False)
        self.assertEqual(self.bytes(), before)
        self.assertEqual((self.ticket / "lite.md").read_bytes(), rendered)

    def test_report_rejects_pass_with_blocking_findings(self):
        a = self.code()
        self.fake_host_review(a, findings=[{"id": "F-1", "blocking": True, "status": "open",
            "description": "仍缺边界检查", "evidence": "source.py:1"}])
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
        fact = self.review_records()[1][0]
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
        fact = self.review_records()[1][0]
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
        fact = self.review_records()[1][0]
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
        fact = ctl.lite_review_records(events)[1][0]
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
        fact = self.review_records()[1][0]
        original = (self.ticket / fact["report_path"]).read_bytes()
        self.git("add", "source.py")
        self.git("commit", "-qm", "reviewed content")
        self.call("validate", "--dir", self.ticket)
        self.verify(scenario="device", layer="consumption")
        self.transition("completed", "verified")
        self.assertTrue(ctl.delivery_freshness(self.ticket)["ok"])
        self.assertEqual((self.ticket / fact["report_path"]).read_bytes(), original)
        self.assertEqual(len(ctl.lite_review_records(ctl.read_events(self.ticket)[0])[0]), 1)
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
        self.prepare_host_review(a, ok=False)
        self.assertFalse(self.review_records()[0])

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
