"""资格合同采纳边界与幂等创建重试的控制面单元回归。

存量工单（旧版本工具创建，metadata 缺 mcp_gate_contract_version）续跑时，
控制面在下一次 metadata 写入采纳当前合同并记录采纳时刻；linter 以该时刻
区分历史 trace 行（合同 v1 解释）与续跑新行（当前合同）。幂等创建重试对
缺失的出生派生字段按当前出生值补齐后比较，升级后返回 already_applied。
"""

import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _probe_symlink_support():
    try:
        with tempfile.TemporaryDirectory() as base:
            (Path(base) / "probe").symlink_to(Path(base) / "target")
            return True
    except (OSError, NotImplementedError):
        return False


# 原生 Windows 无符号链接权限时跳过集成测试（单元测试不依赖符号链接，仍然运行）。
SYMLINK_SUPPORTED = _probe_symlink_support()
_REQUIRES_SYMLINK = pytest.mark.skipif(
    not SYMLINK_SUPPORTED, reason="环境无符号链接权限，真实控制面链路集成测试仅在支持符号链接的环境运行")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gate_contract_control", ROOT / "tools/icode_control.py")
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


def test_adopt_gate_contract_binds_current_version_once():
    meta = {}
    control.adopt_gate_contract(meta)
    assert meta["mcp_gate_contract_version"] == 2
    adopted = meta["mcp_gate_contract_adopted_at"]
    assert "T" in adopted and ("+00:00" in adopted or adopted.endswith("Z"))
    snapshot = dict(meta)
    control.adopt_gate_contract(meta)
    assert meta == snapshot  # 幂等：已采纳的工单不重写时刻


def test_adopt_gate_contract_preserves_existing_binding():
    meta = {"mcp_gate_contract_version": 2,
            "mcp_gate_contract_adopted_at": "2026-01-01T00:00:00+00:00"}
    snapshot = dict(meta)
    control.adopt_gate_contract(meta)
    assert meta == snapshot


def test_adoption_does_not_commit_when_event_chain_cannot_be_read(tmp_path):
    after = {"ticket_id": "t", "status": "init_in_progress"}
    with patch.object(control, "verify_event_chain", side_effect=OSError("temporary read failure")):
        with pytest.raises(control.ControlError) as raised:
            control.commit_metadata_and_event(
                tmp_path, {}, after, "metadata_updated", {"field": "value"})
    assert raised.value.extra["gate_id"] == "event_chain_invalid"
    assert "mcp_gate_contract_version" not in after

    after = {"ticket_id": "t", "status": "init_in_progress"}
    with patch.object(control, "verify_event_chain", return_value=([], ["broken hash chain"])):
        with pytest.raises(control.ControlError) as raised:
            control.commit_metadata_and_event(
                tmp_path, {}, after, "metadata_updated", {"field": "value"})
    assert raised.value.extra["gate_id"] == "event_chain_invalid"
    assert raised.value.extra["violations"] == ["broken hash chain"]
    assert "mcp_gate_contract_version" not in after


def test_comparable_birth_metadata_fills_missing_derived_fields():
    expected = dict(ticket_id="t", requirement="r", created_at="2026-11-17T00:00:00",
                    mcp_gate_contract_version=2)
    old_created = dict(ticket_id="t", requirement="r", created_at="2026-11-17T00:00:00")
    filled = control.comparable_birth_metadata(old_created, expected)
    assert filled == expected  # 升级后幂等重试 → already_applied


def test_comparable_birth_metadata_still_rejects_progress_drift():
    expected = dict(ticket_id="t", created_at="2026-11-17T00:00:00",
                    status="plan_done", mcp_gate_contract_version=2)
    progressed = dict(ticket_id="t", created_at="2026-11-17T00:00:00",
                      status="plan_done",
                      mcp_gate_contract_adopted_at="2026-11-17T04:00:00+00:00")
    assert control.comparable_birth_metadata(progressed, expected) != expected


def test_comparable_birth_metadata_never_masks_other_drift():
    expected = dict(ticket_id="t", requirement="r", mcp_gate_contract_version=2)
    drifted = dict(ticket_id="t", requirement="changed", mcp_gate_contract_version=2)
    assert control.comparable_birth_metadata(drifted, expected) != expected


# ---- 真实续跑链路集成（P1：采纳边界必须早于续跑产生的新 trace）----

import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def _parse_ts(value):
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)

BIRTH_CONTRACT_LINE = '        "mcp_gate_contract_version": _cheap_catalog["contract_version"],\n'
COMMIT_ADOPTION_HOOK = '''    if event_type != "ticket_created" and isinstance(after, dict):
        # 存量工单的每次控制面写入都是一次续跑：先绑定当前资格合同（幂等）。
        # 采纳时检查未终结 attempt，历史边界回退到其最早 start（覆盖升级后、
        # 采纳前写入的 trace）。
        if after.get("mcp_gate_contract_version") is None:
            try:
                pending_events, chain_problems = verify_event_chain(out_dir, after, semantic=False)
            except (OSError, ValueError, ControlError) as exc:
                raise ControlError("无法验证事件链，拒绝采纳资格合同",
                                   gate_id="event_chain_invalid") from exc
            if chain_problems:
                raise ControlError("事件链无效，拒绝采纳资格合同",
                                   gate_id="event_chain_invalid",
                                   violations=chain_problems)
            adopt_gate_contract(after, events=pending_events)
'''
STEP_ADOPTION_HOOK = '''        if meta.get("mcp_gate_contract_version") is None:
            before_meta = copy.deepcopy(meta)
            adopt_gate_contract(meta, events=events)
            commit_metadata_and_event(out_dir, before_meta, meta, "metadata_updated",
                {"gate_contract_adoption": {
                    "version": meta["mcp_gate_contract_version"],
                    "adopted_at": meta["mcp_gate_contract_adopted_at"],
                    "history_boundary": meta["mcp_gate_contract_history_boundary"]}})
'''


EXCLUDED_SKILL_ROOT_ENTRIES = {
    ".git", ".github", ".icode_output", ".pytest_cache", ".DS_Store",
    "__pycache__", "tests", "site", "demo", "docs", "evals", ".agents",
}


def _write_legacy_sim(tmp_path, *, legacy_review_contract=False,
                      legacy_plan_contract=False, review_drift=None):
    """在测试专属的临时 SKILL_ROOT 中构造旧版本工具副本（无出生合同字段、无采纳钩子）。

    仓库资源以符号链接挂载（只读复用），sim 文件真实存在于 <tmp>/skill_root/tools/，
    其 SKILL_ROOT = Path(__file__).resolve().parent.parent 指向该临时根——完全不写入
    源码目录；每个测试使用独立 tmp_path，天然并行安全，清理随 tmp_path 自动完成。
    legacy_review_contract：恢复 review 升级前的端口和路由。review_drift 可在旧合同上引入额外变化。
    """
    skill_root = tmp_path / "skill_root"
    tools_dir = skill_root / "tools"
    tools_dir.mkdir(parents=True)
    # mcp/workflow-gate 必须实体化（真实目录 + gates.json 真实文件）：
    # 符号链接目录内 unlink 会穿透到源码仓库删除真实文件。
    for entry in ROOT.iterdir():
        if entry.name in EXCLUDED_SKILL_ROOT_ENTRIES or entry.name in {"tools", "mcp"}:
            continue
        link = skill_root / entry.name
        if entry.is_dir():
            link.symlink_to(entry, target_is_directory=True)
        else:
            link.symlink_to(entry)
    mcp_dir = skill_root / "mcp"
    mcp_dir.mkdir()
    for entry in (ROOT / "mcp").iterdir():
        if entry.name == "workflow-gate":
            continue
        link = mcp_dir / entry.name
        if entry.is_dir():
            link.symlink_to(entry, target_is_directory=True)
        else:
            link.symlink_to(entry)
    wg_dir = mcp_dir / "workflow-gate"
    wg_dir.mkdir()
    for entry in (ROOT / "mcp/workflow-gate").iterdir():
        if entry.name == "gates.json":
            # gates.json 必须是真实文件副本（可能被补丁修改；绝不能穿透符号链接）。
            (wg_dir / "gates.json").write_text(
                (ROOT / "mcp/workflow-gate/gates.json").read_text(encoding="utf-8"),
                encoding="utf-8")
            continue
        link = wg_dir / entry.name
        if entry.is_dir():
            link.symlink_to(entry, target_is_directory=True)
        else:
            link.symlink_to(entry)
    for entry in (ROOT / "tools").iterdir():
        if entry.name in {"__pycache__", ".DS_Store"} or "legacy_sim" in entry.name:
            continue
        link = tools_dir / entry.name
        if entry.is_dir():
            link.symlink_to(entry, target_is_directory=True)
        else:
            link.symlink_to(entry)
    if legacy_review_contract:
        catalog_path = skill_root / "mcp/workflow-gate/gates.json"
        assert not catalog_path.is_symlink() and catalog_path.parent.is_symlink() is False
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        review_contract = catalog["execution_model"]["step_contracts"]["review"]
        review_contract["inputs"] = [
            port for port in review_contract["inputs"] if port["id"] != "plan_candidates"]
        review_contract["outputs"] = [
            port for port in review_contract["outputs"] if port["id"] != "review_dedup_evidence"]
        del review_contract["drift_routes"]["plan_candidates"]
        if review_drift:
            review_drift(review_contract)
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    if legacy_plan_contract:
        catalog_path = skill_root / "mcp/workflow-gate/gates.json"
        assert not catalog_path.is_symlink()
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        plan_contract = catalog["execution_model"]["step_contracts"]["plan"]
        plan_contract["outputs"] = [port for port in plan_contract["outputs"]
                                     if port["id"] != "plan_candidates"]
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    source = (ROOT / "tools/icode_control.py").read_text(encoding="utf-8")
    for marker in (BIRTH_CONTRACT_LINE, COMMIT_ADOPTION_HOOK, STEP_ADOPTION_HOOK):
        assert source.count(marker) == 1, marker[:60]
        source = source.replace(marker, "")
    sim = tools_dir / "icode_control_legacy_sim.py"
    sim.write_text(source, encoding="utf-8")
    return sim


def _new_tool():
    return [sys.executable, str(ROOT / "tools/icode_control.py")]


def _run(command, out_dir, ok=True):
    result = subprocess.run(command + ["--dir", str(out_dir)],
                            env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"),
                            capture_output=True, text=True)
    assert (result.returncode == 0) == ok, (command[1:3], result.stdout[-400:], result.stderr[-400:])
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_adopt_gate_contract_retracts_boundary_for_open_attempts():
    started = "2026-01-17T08:00:00+00:00"
    events = [{"event_type": "step_started", "at": started,
               "payload": {"attempt": "plan-a", "step": "plan"}}]
    meta = {}
    control.adopt_gate_contract(meta, events=events)
    assert meta["mcp_gate_contract_version"] == 2
    assert meta["mcp_gate_contract_history_boundary"] == started
    assert meta["mcp_gate_contract_adopted_at"] > started
    # 已终结 attempt 不回退边界。
    meta2 = {}
    events2 = events + [{"event_type": "step_finished", "at": "2026-11-17T09:00:00+00:00",
                         "payload": {"attempt": "plan-a"}}]
    control.adopt_gate_contract(meta2, events=events2)
    assert meta2["mcp_gate_contract_history_boundary"] == meta2["mcp_gate_contract_adopted_at"]
    # 无事件（纯 metadata 提交采纳）：边界 = 采纳时刻。
    meta3 = {}
    control.adopt_gate_contract(meta3)
    assert meta3["mcp_gate_contract_history_boundary"] == meta3["mcp_gate_contract_adopted_at"]


def test_plan_attempt_remains_compatible_when_optional_output_is_added(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    legacy = _write_legacy_sim(tmp_path, legacy_plan_contract=True)
    _run([sys.executable, str(legacy), "create", "--ticket-id", "plan-upgrade",
          "--requirement", "r", "--birth", "plan"], out)
    _run([sys.executable, str(legacy), "step", "--step", "plan", "--phase", "start",
          "--attempt", "plan-a", "--request", "plan-start"], out)
    _run([sys.executable, str(legacy), "step", "--step", "plan", "--phase", "check",
          "--attempt", "plan-a", "--boundary", "before_write"], out)
    (out / "01_plan.md").write_text("# plan\n", encoding="utf-8")
    _run([sys.executable, str(legacy), "artifact", "--step", "plan",
          "--attempt", "plan-a", "--path", "01_plan.md"], out)

    current = _new_tool()
    replay = _run(current + ["step", "--step", "plan", "--phase", "start",
                             "--attempt", "plan-a", "--request", "plan-start"], out)
    assert replay["already_applied"] is True
    assert replay["contract_drift_replayed"] is True
    boundary = _run(current + ["step", "--step", "plan", "--phase", "check",
                               "--attempt", "plan-a", "--boundary", "before_transition"], out)
    assert boundary["result"] == "pass"
    finished = _run(current + ["step", "--step", "plan", "--phase", "finish",
                               "--attempt", "plan-a", "--outcome", "success",
                               "--evidence", "01_plan.md"], out)
    assert finished["outcome"] == "success"


def test_birth_rejects_caller_supplied_adopted_at(tmp_path):
    # P1 回归：create --metadata-json 不得注入合同采纳时间（否则未来时刻把
    # 新工单全部 trace 行伪装成历史行按 v1 解释）。
    out = tmp_path / ".icode_output/.icode_output_1"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/icode_control.py"), "create",
         "--dir", str(out), "--ticket-id", "inject", "--requirement", "r",
         "--birth", "plan", "--request-id", "req-inject",
         "--metadata-json", '{"mcp_gate_contract_adopted_at": "2099-01-01T00:00:00+00:00"}'],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert "birth_protected_fields" in result.stdout + result.stderr


@_REQUIRES_SYMLINK
def test_step_start_adopts_contract_before_new_traces(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    sim = _write_legacy_sim(tmp_path)
    legacy = [sys.executable, str(sim), "create", "--ticket-id", "adopt-flow",
              "--requirement", "r", "--birth", "plan", "--request-id", "req-legacy"]
    _run(legacy, out)
    new = _new_tool()
    # 续跑第一处控制触点是 step start（append_event 路径，不经 metadata 提交）。
    _run(new + ["step", "--step", "plan", "--phase", "start",
                "--attempt", "plan-a"], out)
    metadata = json.loads((out / ".ico_metadata.json").read_text(encoding="utf-8"))
    state = metadata.get("value", metadata)
    assert state["mcp_gate_contract_version"] == 2
    assert "T" in state["mcp_gate_contract_adopted_at"]
    events, _problems = control.read_events(out)
    kinds = [e.get("event_type") for e in events]
    # 采纳的 metadata_updated 事件必须先于本次续跑的 step_started 事件。
    assert kinds.index("metadata_updated") < kinds.index("step_started")


@_REQUIRES_SYMLINK
def test_adopted_ticket_enforces_current_contract_on_new_traces(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    sim = _write_legacy_sim(tmp_path)
    legacy = [sys.executable, str(sim), "create", "--ticket-id", "adopt-lint",
              "--requirement", "r", "--birth", "plan", "--request-id", "req-legacy"]
    _run(legacy, out)
    (out / "01_plan.md").write_text("# plan\n", encoding="utf-8")
    new = _new_tool()
    _run(new + ["transition", "--to", "plan_done", "--skip-gates"], out)
    _run(new + ["transition", "--to", "review_in_progress", "--skip-gates"], out)
    _run(new + ["step", "--step", "review", "--phase", "start",
                "--attempt", "review-a"], out)

    historical = dict(schema_version=2, ticket_id="adopt-lint", step="review",
        gate_id="review.dedup", tool="extract", eligible=False,
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                      rg_available=True, cheap_available=False),
        decision="skipped_not_eligible", attempted=False, result="success",
        at="2020-01-01T00:00:00Z")
    fresh = dict(historical, at="2099-01-01T00:00:00+00:00")
    summary = dict(schema_version=2, ticket_id="adopt-lint", step="review",
        gate_id="review.result_summary", tool="summarize", eligible=True,
        evidence=dict(result_source="02_review.md", review_rounds=1),
        decision="called", attempted=True, result="success",
        at="2020-01-01T00:00:00Z")

    def lint_passes(rows):
        (out / ".mcp_gate_trace.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/lint_mcp_coverage.py"),
             str(out), "--step", "review", "--strict", "--json"],
            capture_output=True, text=True)
        report = json.loads(result.stdout)
        return result.returncode == 0, report

    # 采纳前的历史行（at 早于边界）：保留合同 v1 解释 → 合法 skip。
    passed, report = lint_passes([historical, summary])
    assert passed and report["skipped_not_eligible"] == 1, report
    # 采纳后的续跑新行（at 晚于边界、未标注版本）：强制当前合同 → 伪装 skip 被拒。
    passed, report = lint_passes([summary, historical, fresh])
    assert not passed and report["invalid_skip"] == 1, report
    assert report["gates"][0]["status"] == "invalid"


@_REQUIRES_SYMLINK
def test_open_attempt_boundary_blocks_traces_written_before_adoption(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    sim = _write_legacy_sim(tmp_path)
    legacy = [sys.executable, str(sim), "create", "--ticket-id", "open-attempt",
              "--requirement", "r", "--birth", "plan", "--request-id", "req-legacy"]
    _run(legacy, out)
    # 升级前启动 attempt（保持未终结跨越升级）。
    _run([sys.executable, str(sim), "step", "--step", "plan", "--phase", "start",
          "--attempt", "plan-a"], out)
    row_at = datetime.now(timezone.utc).isoformat()
    # 升级后、采纳前：直接写入未标版本的旧式 skip（阈值达标 + MCP 不可用）。
    fresh = dict(schema_version=2, ticket_id="open-attempt", step="review",
        gate_id="review.dedup", tool="extract", eligible=False,
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266,
                      threshold=50, rg_available=True, cheap_available=False),
        decision="skipped_not_eligible", attempted=False, result="success",
        at=row_at)
    # 首次新控制触点：step check（采纳合同，历史边界回退到 attempt start）。
    _run(_new_tool() + ["step", "--step", "plan", "--phase", "check",
          "--attempt", "plan-a", "--boundary", "before_write"], out)
    metadata = json.loads((out / ".ico_metadata.json").read_text(encoding="utf-8"))
    state = metadata.get("value", metadata)
    assert _parse_ts(state["mcp_gate_contract_history_boundary"]) <= _parse_ts(row_at)
    assert _parse_ts(state["mcp_gate_contract_adopted_at"]) > _parse_ts(row_at)

    historical = dict(fresh, at="2020-01-01T00:00:00Z")
    summary = dict(schema_version=2, ticket_id="open-attempt", step="review",
        gate_id="review.result_summary", tool="summarize", eligible=True,
        evidence=dict(result_source="02_review.md", review_rounds=1),
        decision="called", attempted=True, result="success",
        at="2020-01-01T00:00:00Z")

    def lint_passes(rows):
        (out / ".mcp_gate_trace.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/lint_mcp_coverage.py"),
             str(out), "--step", "review", "--strict", "--json"],
            capture_output=True, text=True)
        report = json.loads(result.stdout)
        return result.returncode == 0, report

    # attempt start 之前的历史行：保留合同 v1 解释 → 合法 skip。
    passed, report = lint_passes([historical, summary])
    assert passed and report["skipped_not_eligible"] == 1, report
    # 升级后、采纳前写入的记录：被历史边界覆盖 → 按当前合同拒绝。
    passed, report = lint_passes([summary, fresh])
    assert not passed and report["invalid_skip"] == 1, report


def test_gate_linters_surface_enumeration_debt(tmp_path):
    # P2 回归：控制面门禁结果必须透传校验报告中的枚举/去重债务计数。
    out = tmp_path / ".icode_output/.icode_output_1"
    _run(_new_tool() + ["create", "--ticket-id", "debt-surface",
                        "--requirement", "r", "--birth", "plan"], out)
    debt_row = dict(schema_version=2, ticket_id="debt-surface", step="review",
        gate_id="review.dedup", tool="extract", eligible=True,
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call", attempted=False, result="unavailable",
        at="2026-11-17T06:00:00Z",
        availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                          fallback="manual", evidence_ref="e"))
    summary = dict(schema_version=2, ticket_id="debt-surface", step="review",
        gate_id="review.result_summary", tool="summarize", eligible=True,
        evidence=dict(result_source="02_review.md", review_rounds=1),
        decision="called", attempted=True, result="success",
        at="2026-11-17T06:00:00Z")
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (debt_row, summary)) + "\n",
        encoding="utf-8")
    reports = control.run_gate_linters(out, to_status="review_done")
    mcp = next(r for r in reports if r["gate_id"] == "mcp_coverage")
    assert mcp["ok"], mcp
    assert mcp["enumeration_debt"] == 1
    # 工单级余额与被推进步骤的报告一致（债务 gate 即 review）。
    assert control.ticket_enumeration_debt(out, control.load_metadata(out)) == 1


def test_ticket_enumeration_debt_balance_tracks_rework(tmp_path):
    # P1 回归：跨步骤余额——review 产生债务后，后续只检查 merge/audit 的推进
    # 仍报告余额 1；对债务 gate 真实重做（最后一条不再携带债务状态）即清偿归零。
    out = tmp_path / ".icode_output/.icode_output_1"
    _run(_new_tool() + ["create", "--ticket-id", "debt-balance",
                        "--requirement", "r", "--birth", "plan"], out)
    debt_row = dict(schema_version=2, ticket_id="debt-balance", step="review",
        gate_id="review.dedup", tool="extract", eligible=True,
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call", attempted=False, result="unavailable",
        at="2026-11-17T06:00:00Z",
        availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                          fallback="manual", evidence_ref="e"))
    summary = dict(schema_version=2, ticket_id="debt-balance", step="review",
        gate_id="review.result_summary", tool="summarize", eligible=True,
        evidence=dict(result_source="02_review.md", review_rounds=1),
        decision="called", attempted=True, result="success",
        at="2026-11-17T06:00:00Z")
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (debt_row, summary)) + "\n",
        encoding="utf-8")
    # review 产生债务后进入后续推进：输出与 state_changed 事件都带工单级余额 1。
    result = _run(_new_tool() + ["transition", "--to", "plan_done", "--skip-gates"], out)
    assert result["enumeration_debt"] == 1
    assert result["enumeration_debt_unknown"] is False
    events, _problems = control.read_events(out)
    adoption_events = [e for e in events if e.get("event_type") == "state_changed"]
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt") == 1
    # P1 回归：unavailable 清偿记录的人工等价证据必须机器可验证——证据文件
    # 不存在时不得清偿（「函数已枚举」≠「去重债务已清偿」）。
    fake_clearing = dict(schema_version=2, ticket_id="debt-balance", step="review",
        gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="unavailable", at="2026-11-17T06:30:00Z",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                      rg_available=False, cheap_available=False),
        decision="unavailable_before_call",
        availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                          fallback="manual", evidence_ref="dedup/manual_report.json"))
    rows = [debt_row, summary, fake_clearing]
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    result = _run(_new_tool() + ["transition", "--to", "review_in_progress",
                                 "--skip-gates"], out)
    assert result["enumeration_debt"] == 1, result
    # 空对象即使落盘，也不是有效的人工审查证据。
    (out / "dedup").mkdir()
    (out / "dedup" / "manual_report.json").write_text("{}\n", encoding="utf-8")
    verified = dict(fake_clearing, at="2026-11-17T06:40:00Z")
    rows = [debt_row, summary, fake_clearing, verified]
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    result = _run(_new_tool() + ["transition", "--to", "review_done",
                                 "--skip-gates"], out)
    assert result["enumeration_debt"] == 1, result
    # P1 回归：流程要求调用前先写 pending——中断/未完成的重做不得清偿债务。
    # 此段重新产生 trace 并再次记录债务（trace 丢失后的未知不掩盖后续事实）。
    pending = dict(debt_row, eligible=True, decision="pending", attempted=False,
                   result="success",
                   evidence=dict(affected_repo_roots=["/fixture"], function_count=0,
                                 threshold=50, rg_available=False, cheap_available=False),
                   at="2026-11-17T06:30:00Z")
    rows = [debt_row, summary, pending]
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    result = _run(_new_tool() + ["transition", "--to", "review_in_progress",
                                 "--skip-gates"], out)
    assert result["enumeration_debt"] == 1, result
    events, _problems = control.read_events(out)
    adoption_events = [e for e in events if e.get("event_type") == "state_changed"]
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt") == 1
    # P2 回归：trace 追加不完整 JSON → 余额未知（null），不得报告为清偿。
    with (out / ".mcp_gate_trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version": 2, "gate_id": "review.dedup", "elig\n')
    result = _run(_new_tool() + ["transition", "--to", "review_done",
                                 "--skip-gates"], out)
    assert result["enumeration_debt"] is None and result["enumeration_debt_unknown"] is True, result
    events, _problems = control.read_events(out)
    adoption_events = [e for e in events if e.get("event_type") == "state_changed"]
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt") is None
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt_unknown") is True
    # 有效重做完成（真实 called）→ 余额清零。
    redo = dict(debt_row, eligible=True, decision="called", attempted=True,
                result="success",
                evidence=dict(affected_repo_roots=["/fixture"], function_count=266,
                              threshold=50, rg_available=True, cheap_available=True,
                              repo_review_scopes={"/fixture": ["."]}),
                at="2026-11-17T07:00:00Z")
    del redo["availability"]
    rows = [debt_row, summary, redo]
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    # 清偿后的后续推进仍报告 0：余额已被有效重做清偿。
    result = _run(_new_tool() + ["transition", "--to", "review_in_progress",
                                 "--skip-gates"], out)
    assert result["enumeration_debt"] == 0 and result["enumeration_debt_unknown"] is False
    events, _problems = control.read_events(out)
    adoption_events = [e for e in events if e.get("event_type") == "state_changed"]
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt") == 0


def test_trace_missing_with_recorded_debt_reports_unknown(tmp_path):
    # P2 回归：事件链曾记录未清偿债务而 trace 缺失/丢失 → 无法核实（未知），
    # 不得当作清偿；从未记录过债务的新工单缺失 trace 才是确认无债务。
    out = tmp_path / ".icode_output/.icode_output_1"
    _run(_new_tool() + ["create", "--ticket-id", "missing-trace",
                        "--requirement", "r", "--birth", "plan"], out)
    debt_row = dict(schema_version=2, ticket_id="missing-trace", step="review",
        gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="unavailable",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call", at="2026-11-17T06:00:00Z",
        availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                          fallback="manual", evidence_ref="e"))
    summary = dict(schema_version=2, ticket_id="missing-trace", step="review",
        gate_id="review.result_summary", tool="summarize", eligible=True,
        evidence=dict(result_source="02_review.md", review_rounds=1),
        decision="called", attempted=True, result="success",
        at="2026-11-17T06:00:00Z")
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (debt_row, summary)) + "\n",
        encoding="utf-8")
    result = _run(_new_tool() + ["transition", "--to", "plan_done", "--skip-gates"], out)
    assert result["enumeration_debt"] == 1
    # trace 丢失：曾记录未清偿债务 → 未知。
    (out / ".mcp_gate_trace.jsonl").unlink()
    result = _run(_new_tool() + ["transition", "--to", "review_in_progress",
                                 "--skip-gates"], out)
    assert result["enumeration_debt"] is None
    assert result["enumeration_debt_unknown"] is True
    events, _problems = control.read_events(out)
    adoption_events = [e for e in events if e.get("event_type") == "state_changed"]
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt") is None
    assert adoption_events[-1]["payload"].get("gate_enumeration_debt_unknown") is True
    # 对照：从未产生过 trace 的新工单缺失 trace = 确认无已知债务（0）。
    out2 = tmp_path / ".icode_output/.icode_output_2"
    _run([sys.executable, str(ROOT / "tools/icode_control.py"), "create",
          "--dir", str(out2), "--ticket-id", "fresh-no-trace",
          "--requirement", "r", "--birth", "plan"], out2)
    result = _run([sys.executable, str(ROOT / "tools/icode_control.py"),
                   "transition", "--dir", str(out2), "--to", "plan_done",
                   "--skip-gates"], out2)
    assert result["enumeration_debt"] == 0
    assert result["enumeration_debt_unknown"] is False


def test_foreign_trace_makes_ticket_debt_unknown(tmp_path):
    out = tmp_path / ".icode_output/.icode_output_1"
    _run(_new_tool() + ["create", "--ticket-id", "own-ticket",
                       "--requirement", "r", "--birth", "plan"], out)
    row = dict(schema_version=2, ticket_id="another-ticket", step="review",
               gate_id="review.dedup", tool="extract", eligible=True,
               attempted=False, result="unavailable", decision="unavailable_before_call",
               at="2026-11-17T06:00:00Z",
               evidence=dict(affected_repo_roots=["/other"], function_count=0,
                             threshold=50, rg_available=False, cheap_available=False,
                             enumeration_unverified=True))
    (out / ".mcp_gate_trace.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert control.ticket_enumeration_debt(out, control.load_metadata(out)) is None


def test_step_start_replay_across_contract_upgrade(tmp_path):
    """P2 回归：工具升级为步骤合同新增端口后，旧 attempt 的同请求重试是跨版本
    幂等重放（非 idempotency_conflict），且升级中的恢复（check-outputs）不再把
    合同摘要漂移判定为需要重入的漂移。"""
    out = tmp_path / ".icode_output/.icode_output_1"
    # sim 的临时合同缺少 plan_candidates 输入端口（旧版本形状）。
    sim = _write_legacy_sim(tmp_path, legacy_review_contract=True)
    legacy = [sys.executable, str(sim), "create", "--ticket-id", "contract-replay",
              "--requirement", "r", "--birth", "plan", "--request-id", "req-legacy"]
    _run(legacy, out)
    (out / "01_plan.md").write_text("# plan\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(
        json.dumps(dict(schema_version=1, ticket_id="contract-replay", attempt="plan-a",
                        files=["src/app.py"])), encoding="utf-8")
    _run([sys.executable, str(sim), "transition", "--dir", str(out),
          "--to", "plan_done", "--skip-gates"], out)
    _run([sys.executable, str(sim), "transition", "--dir", str(out),
          "--to", "review_in_progress", "--skip-gates"], out)
    _run([sys.executable, str(sim), "step", "--dir", str(out), "--step", "review",
          "--phase", "start", "--attempt", "review-a", "--request", "req-retry"], out)
    new = _new_tool()
    # 新工具以完全相同的请求重试：跨版本幂等重放（非冲突）。
    result = _run(new + ["step", "--step", "review", "--phase", "start",
                         "--attempt", "review-a", "--request", "req-retry"], out)
    assert result["already_applied"] is True, result
    assert result["contract_drift_replayed"] is True, result
    # 升级中的恢复：check-outputs 不再因合同摘要漂移拒绝旧 attempt 重入。
    result = subprocess.run(new + ["check-outputs", "--dir", str(out), "--step", "review"],
                            capture_output=True, text=True,
                            env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"))
    assert "contract_drift_reenter" not in (result.stdout + result.stderr), result.stdout[-400:]


def test_same_request_with_different_attempt_is_not_replayed(tmp_path):
    """P2 回归：同 request 但换了 attempt 是不同执行，兼容分支不得重放旧事件
    （否则调用方按新 attempt 找不到启动事件）。"""
    out = tmp_path / ".icode_output/.icode_output_1"
    sim = _write_legacy_sim(tmp_path, legacy_review_contract=True)
    legacy = [sys.executable, str(sim), "create", "--ticket-id", "attempt-identity",
              "--requirement", "r", "--birth", "plan", "--request-id", "req-legacy"]
    _run(legacy, out)
    (out / "01_plan.md").write_text("# plan\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(
        json.dumps(dict(schema_version=1, ticket_id="attempt-identity", attempt="plan-a",
                        files=["src/app.py"])), encoding="utf-8")
    _run([sys.executable, str(sim), "transition", "--dir", str(out),
          "--to", "plan_done", "--skip-gates"], out)
    _run([sys.executable, str(sim), "transition", "--dir", str(out),
          "--to", "review_in_progress", "--skip-gates"], out)
    _run([sys.executable, str(sim), "step", "--dir", str(out), "--step", "review",
          "--phase", "start", "--attempt", "attempt-a", "--request", "req-x"], out)
    # 同 request 换 attempt：必须是冲突，不能谎报重放新 attempt。
    result = subprocess.run(_new_tool() + ["step", "--step", "review", "--phase", "start",
                                          "--attempt", "attempt-b", "--request", "req-x",
                                          "--dir", str(out)],
                            capture_output=True, text=True,
                            env=dict(os.environ, ICODE_CONTROL_TEST_MODE="1"))
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    assert result.returncode != 0, payload
    assert payload.get("gate_id") == "idempotency_conflict", payload


def test_upgraded_contract_attempt_can_pass_boundary_checks(tmp_path):
    """P2 回归：旧合同启动的 attempt 在升级后重放启动成功，紧接着的边界检查
    也必须按升级兼容继续（不得因新增端口被判漂移并路由回 plan）。"""
    out = tmp_path / ".icode_output/.icode_output_1"
    sim = _write_legacy_sim(tmp_path, legacy_review_contract=True)
    legacy = [sys.executable, str(sim), "create", "--ticket-id", "boundary-upgrade",
              "--requirement", "r", "--birth", "plan", "--request-id", "req-legacy"]
    _run(legacy, out)
    (out / "01_plan.md").write_text("# plan\n", encoding="utf-8")
    (out / "plan_candidates.json").write_text(
        json.dumps(dict(schema_version=1, ticket_id="boundary-upgrade", attempt="plan-a",
                        files=["src/app.py"])), encoding="utf-8")
    _run([sys.executable, str(sim), "transition", "--dir", str(out),
          "--to", "plan_done", "--skip-gates"], out)
    _run([sys.executable, str(sim), "transition", "--dir", str(out),
          "--to", "review_in_progress", "--skip-gates"], out)
    _run([sys.executable, str(sim), "step", "--dir", str(out), "--step", "review",
          "--phase", "start", "--attempt", "review-a", "--request", "req-check"], out)
    new = _new_tool()
    # 同请求重放启动成功（跨版本兼容）。
    _run(new + ["step", "--step", "review", "--phase", "start",
                "--attempt", "review-a", "--request", "req-check"], out)
    # 紧接着的边界检查必须通过（升级兼容，不得判漂移）。
    result = _run(new + ["step", "--step", "review", "--phase", "check",
                         "--attempt", "review-a", "--boundary", "before_write"], out)
    assert result["result"] == "pass", result
    assert result["changed_inputs"] == [], result
    for boundary in ("after_wait", "before_transition"):
        _run(new + ["step", "--step", "review", "--phase", "check",
                    "--attempt", "review-a", "--boundary", boundary], out)
    finish = _run(new + ["step", "--step", "review", "--phase", "finish",
                         "--attempt", "review-a", "--outcome", "degraded",
                         "--evidence", "legacy review fixture"], out)
    assert finish["outcome"] == "degraded"


@pytest.mark.parametrize("drift", ["required_checks", "outputs", "drift_routes"])
def test_review_upgrade_blocks_unrecognized_contract(tmp_path, drift):
    def change_contract(contract):
        if drift == "required_checks":
            contract["required_checks"].remove("after_wait")
        elif drift == "outputs":
            contract["outputs"][0]["value"] = "old_review.md"
        else:
            contract["drift_routes"]["default"] = "plan"

    out = tmp_path / ".icode_output/.icode_output_1"
    sim = _write_legacy_sim(tmp_path, legacy_review_contract=True, review_drift=change_contract)
    legacy = [sys.executable, str(sim)]
    _run(legacy + ["create", "--ticket-id", "contract-drift", "--requirement", "r",
                   "--birth", "plan"], out)
    (out / "01_plan.md").write_text("# plan\n", encoding="utf-8")
    _run(legacy + ["transition", "--to", "plan_done", "--skip-gates"], out)
    _run(legacy + ["transition", "--to", "review_in_progress", "--skip-gates"], out)
    start = ["step", "--step", "review", "--phase", "start",
             "--attempt", "review-a", "--request", "req-review"]
    _run(legacy + start, out)

    for boundary in ("before_write", "after_wait", "before_transition"):
        _run(legacy + ["step", "--step", "review", "--phase", "check",
                       "--attempt", "review-a", "--boundary", boundary], out)
    finish = _run(_new_tool() + ["step", "--step", "review", "--phase", "finish",
                                 "--attempt", "review-a", "--outcome", "degraded",
                                 "--evidence", "legacy checks"], out, ok=False)
    assert finish["gate_id"] == "step_contract_drift"

    result = _run(_new_tool() + ["step", "--step", "review", "--phase", "check",
                                 "--attempt", "review-a", "--boundary", "before_write"], out, ok=False)
    assert result["result"] == "blocked"
    assert "$contract" in result["changed_inputs"]
    assert _run(_new_tool() + start, out, ok=False)["gate_id"] == "idempotency_conflict"
