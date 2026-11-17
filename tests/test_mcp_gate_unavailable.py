"""Every existing MCP coverage gate accepts evidenced pre-call unavailability.

eligible 是业务义务资格（阈值/范围/轮次/模式），与工具可用性分离：
达到业务阈值而 cheap-research 不可用时仍 eligible=true，由
unavailable_before_call / degraded_after_attempt / cache_hit 履行；
仅业务不达标（低于阈值）才允许 skipped_not_eligible。

资格语义按合同版本解释（gates.json contract_version）：无 contract_version 的
存量行按合同 v1 旧语义解释（历史记录不追溯改判）；contract_version=2 行 /
mcp_gate_contract_version=2 工单按新语义解释，且声称低于阈值必须基于可信枚举
（rg_available=true 或 enumeration_verified=true）。
"""

import hashlib
import importlib.util
import json
import os
import time
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("coverage_unavailable", ROOT / "tools/lint_mcp_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)
CATALOG = json.loads((ROOT / "mcp/cheap-research/gates.json").read_text(encoding="utf-8"))

# Real catalog conditions, including registered/provider readiness versus this
# session's tool exposure. Tool discovery and provider configuration differ.
EVIDENCE = {
    "log.comments_extract": dict(tb_comments_count=8, threshold=8, cheap_available=True),
    "log.long_log_summary": dict(candidate_text_bytes=8192, threshold=8192, cheap_available=True),
    "review.dedup": dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50,
                         rg_available=True, cheap_available=True),
    "review.result_summary": dict(result_source="02_review.md", review_rounds=1),
    "merge.cross_round_summary": dict(review_rounds=2, threshold=2),
    "deepcheck.fixed_scan": dict(mode="full", phase="fixed", function_points_count=1),
    "deepcheck.dedup": dict(mode="full", phase="dedup", function_count=50, threshold=50),
    "audit.repo_facts": dict(affected_repo_roots=["/fixture"]),
    "audit.plan_diff": dict(plan_source="03_plan_final.md", code_sources=["source.py"]),
    "patch.context_summary": dict(cross_session=True, candidate_text_bytes=8192, threshold=8192),
    "patch.listen_log_summary": dict(listen_mode=True, incremental_bytes=8192, threshold=8192),
    "verify.listen_log_summary": dict(listen_mode=True, incremental_bytes=8192, threshold=8192),
}


def report(tmp_path, gate, *, missing_evidence=False):
    evidence = dict(EVIDENCE[gate["id"]])
    row = dict(schema_version=2, ticket_id="coverage-test", step=gate["step"],
        gate_id=gate["id"], tool=gate["tool"], eligible=True, evidence=evidence,
        decision="unavailable_before_call",
        attempted=False, result="unavailable",
        at="2026-11-13T08:00:00Z")
    if not missing_evidence:
        row["availability"] = dict(tool_visible=False, reason="not_exposed",
            discovery_ref="session:tools", fallback="main_agent", evidence_ref="fallback-report.md")
    metadata = dict(ticket_id="coverage-test", mcp_gate_schema_version=2, mode="full",
                    mcp_gate_contract_version=2)
    return coverage.build_report(tmp_path, gate["step"], False, metadata, [row],
                                 {**CATALOG, "gates": [gate]}, [])


@pytest.mark.parametrize("gate", CATALOG["gates"], ids=lambda gate: gate["id"])
def test_unavailable_gate_keeps_evidenced_fallback(tmp_path, gate):
    result = report(tmp_path, gate)
    assert coverage.report_is_pass(result, False, True), result
    assert result["unavailable_before_call"] == 1
    assert result["called"] == 0


@pytest.mark.parametrize("gate", CATALOG["gates"], ids=lambda gate: gate["id"])
def test_unavailable_gate_cannot_omit_evidence(tmp_path, gate):
    assert not coverage.report_is_pass(report(tmp_path, gate, missing_evidence=True), False, True)


# 业务资格与工具可用性分离的回归（无 MCP 工单的核心场景）：
# 每个阈值 gate 给出「达阈值 / 低于阈值」两份 cheap_available=False 事实。
NO_MCP_EVIDENCE = {
    "log.comments_extract": (
        dict(tb_comments_count=8, threshold=8, cheap_available=False),
        dict(tb_comments_count=7, threshold=8, cheap_available=False)),
    "log.long_log_summary": (
        dict(candidate_text_bytes=8192, threshold=8192, cheap_available=False),
        dict(candidate_text_bytes=8191, threshold=8192, cheap_available=False)),
    "review.dedup": (
        dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50,
             rg_available=True, cheap_available=False),
        dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
             rg_available=True, cheap_available=False)),
}


def no_mcp_report(tmp_path, gate, evidence, *, eligible, decision, row_extra=None,
                  metadata=None):
    row = dict(schema_version=2, ticket_id="coverage-test", step=gate["step"],
        gate_id=gate["id"], tool=gate["tool"], eligible=eligible, evidence=evidence,
        decision=decision, attempted=decision == "degraded_after_attempt",
        result={"unavailable_before_call": "unavailable",
                "degraded_after_attempt": "error",
                "skipped_not_eligible": "not_applicable",
                "cache_hit": "success",
                "called": "success"}[decision],
        at="2026-11-16T14:00:00Z")
    if decision == "unavailable_before_call":
        row["availability"] = dict(tool_visible=False, reason="not_exposed",
            discovery_ref="session:tools", fallback="manual_equivalent_review",
            evidence_ref="dedup/manual_report.json")
    if decision == "degraded_after_attempt":
        row["error_class"] = "ProviderConfigUnreadable"
    if decision == "cache_hit":
        identity = hashlib.sha256(
            json.dumps([gate["tool"], evidence], sort_keys=True).encode()).hexdigest()
        row["cache_key"] = identity
        row["input_digest"] = identity
    if row_extra:
        row.update(row_extra)
    metadata = metadata or dict(ticket_id="coverage-test", mcp_gate_schema_version=2,
                                mode="full", mcp_gate_contract_version=2)
    return coverage.build_report(tmp_path, gate["step"], False, metadata, [row],
                                 {**CATALOG, "gates": [gate]}, [])


@pytest.mark.parametrize("gid", sorted(NO_MCP_EVIDENCE))
def test_threshold_reached_without_mcp_stays_eligible(tmp_path, gid):
    gate = next(g for g in CATALOG["gates"] if g["id"] == gid)
    at_threshold, _ = NO_MCP_EVIDENCE[gid]
    result = no_mcp_report(tmp_path, gate, dict(at_threshold), eligible=True,
                           decision="unavailable_before_call")
    assert coverage.report_is_pass(result, False, True), result
    assert result["unavailable_before_call"] == 1
    assert result["skipped_not_eligible"] == 0
    assert result["eligible"] == 1 and result["coverage"] == 1.0


@pytest.mark.parametrize("gid", sorted(NO_MCP_EVIDENCE))
def test_threshold_reached_without_mcp_cannot_skip_as_not_eligible(tmp_path, gid):
    gate = next(g for g in CATALOG["gates"] if g["id"] == gid)
    at_threshold, _ = NO_MCP_EVIDENCE[gid]
    result = no_mcp_report(tmp_path, gate, dict(at_threshold), eligible=False,
                           decision="skipped_not_eligible")
    assert not coverage.report_is_pass(result, False, True)
    assert result["skipped_not_eligible"] == 0
    assert result["invalid_skip"] == 1
    assert any("eligible 与结构化事实不一致（应为 True）" in issue
               for issue in result["gates"][0]["issues"])


@pytest.mark.parametrize("gid", sorted(NO_MCP_EVIDENCE))
def test_below_threshold_without_mcp_is_legitimate_skip(tmp_path, gid):
    gate = next(g for g in CATALOG["gates"] if g["id"] == gid)
    _, below_threshold = NO_MCP_EVIDENCE[gid]
    result = no_mcp_report(tmp_path, gate, dict(below_threshold), eligible=False,
                           decision="skipped_not_eligible")
    assert coverage.report_is_pass(result, False, True), result
    assert result["skipped_not_eligible"] == 1
    assert result["eligible"] == 0


def test_review_dedup_degraded_after_attempt_without_provider_config(tmp_path):
    # 配置不可读不能否定会话已暴露的工具：实际尝试后失败仍按 degraded 履行。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    at_threshold, _ = NO_MCP_EVIDENCE["review.dedup"]
    result = no_mcp_report(tmp_path, gate, dict(at_threshold), eligible=True,
                           decision="degraded_after_attempt")
    assert coverage.report_is_pass(result, False, True), result
    assert result["degraded_after_attempt"] == 1


def test_review_dedup_cache_hit_without_provider(tmp_path):
    # 有效缓存履行不依赖 provider 配置。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    at_threshold, _ = NO_MCP_EVIDENCE["review.dedup"]
    result = no_mcp_report(tmp_path, gate, dict(at_threshold), eligible=True,
                           decision="cache_hit")
    assert coverage.report_is_pass(result, False, True), result
    assert result["cache_hit"] == 1


def test_visible_tool_denied_by_policy_can_degrade_honestly(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "audit.plan_diff")
    evidence = dict(EVIDENCE[gate["id"]])
    row = dict(schema_version=2, ticket_id="coverage-test", step=gate["step"],
        gate_id=gate["id"], tool=gate["tool"], eligible=True, evidence=evidence,
        decision="unavailable_before_call", attempted=False, result="unavailable",
        at="2026-11-13T08:00:00Z", availability=dict(tool_visible=True,
            reason="policy_denied", policy_ref="session:policy", discovery_ref="session:tools",
            fallback="manual comparison", evidence_ref="06_audit.md"))
    result = coverage.build_report(tmp_path, gate["step"], False,
        dict(ticket_id="coverage-test", mcp_gate_schema_version=2, mode="full"), [row],
        {**CATALOG, "gates": [gate]}, [])
    assert coverage.report_is_pass(result, False, True), result


@pytest.mark.parametrize("change", [
    {"policy_ref": ""}, {"policy_ref": None}, {"reason": "not_exposed"},
    {"tool_visible": False},
])
def test_policy_denied_needs_consistent_visibility_and_policy_evidence(change):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "audit.plan_diff")
    availability = dict(tool_visible=True, reason="policy_denied", policy_ref="session:policy",
                        discovery_ref="session:tools", fallback="manual comparison", evidence_ref="06_audit.md")
    availability.update(change)
    row = dict(schema_version=2, ticket_id="coverage-test", step=gate["step"], gate_id=gate["id"],
        tool=gate["tool"], eligible=True, evidence=EVIDENCE[gate["id"]], decision="unavailable_before_call",
        attempted=False, result="unavailable", at="2026-11-13T08:00:00Z", availability=availability)
    assert coverage.validate_row(row, gate, 1)


@pytest.mark.parametrize("reason", [[], {}])
def test_unavailable_rejects_non_string_reason_without_exception(reason):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "audit.plan_diff")
    row = dict(schema_version=2, ticket_id="coverage-test", step=gate["step"], gate_id=gate["id"],
        tool=gate["tool"], eligible=True, evidence=EVIDENCE[gate["id"]], decision="unavailable_before_call",
        attempted=False, result="unavailable", at="2026-11-13T08:00:00Z",
        availability=dict(tool_visible=False, reason=reason, discovery_ref="session:tools",
                          fallback="manual", evidence_ref="audit.md"))
    assert any("availability.reason 非法" in issue for issue in coverage.validate_row(row, gate, 1))


# ---- 资格合同版本回归（P1-1：历史记录保留原始解释；续跑按新合同真实重做）----

LEGACY_METADATA = dict(ticket_id="coverage-test", mcp_gate_schema_version=2, mode="full")

# 存量工单的真实形状（icode-skill-main-2/3）：达阈值 + cheap_available=false + skipped_not_eligible
HISTORICAL_DEDUP = dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                        rg_available=True, cheap_available=False)


def test_unlabeled_historical_rows_keep_contract_v1_semantics(tmp_path):
    # 无 contract_version 的存量行按合同 v1 旧语义解释，不追溯改判历史记录。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(HISTORICAL_DEDUP), eligible=False,
                           decision="skipped_not_eligible", metadata=dict(LEGACY_METADATA))
    assert coverage.report_is_pass(result, False, True), result
    assert result["skipped_not_eligible"] == 1


def test_contract_v2_metadata_reinterprets_unlabeled_rows(tmp_path):
    # 续跑把工单合同升到 v2 时，未重做的历史 skip 行必须被判非法（须真实重做并追加新行）。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(HISTORICAL_DEDUP), eligible=False,
                           decision="skipped_not_eligible",
                           metadata={**LEGACY_METADATA, "mcp_gate_contract_version": 2})
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 1


def test_row_labeled_contract_v2_rejects_historical_skip(tmp_path):
    # 行级声明 contract_version=2 即按新合同解释，历史 skip 形状不再合法。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(HISTORICAL_DEDUP), eligible=False,
                           decision="skipped_not_eligible",
                           row_extra={"contract_version": 2},
                           metadata=dict(LEGACY_METADATA))
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 1


def test_contract_v2_ticket_rejects_downgraded_rows(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50,
                    rg_available=True, cheap_available=False)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=True,
                           decision="unavailable_before_call",
                           row_extra={"contract_version": 1})
    assert not coverage.report_is_pass(result, False, True)
    assert any("禁止将 trace 行降级" in issue for issue in result["gates"][0]["issues"])


def test_invalid_contract_version_is_schema_error(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(HISTORICAL_DEDUP), eligible=False,
                           decision="skipped_not_eligible",
                           row_extra={"contract_version": "2"})
    assert not coverage.report_is_pass(result, False, True)
    assert result["schema_errors"] >= 1
    assert any("contract_version 非受支持合同版本" in issue
               for issue in result["gates"][0]["issues"])


def test_invalid_metadata_contract_version_is_schema_error(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "audit.repo_facts")
    result = no_mcp_report(tmp_path, gate, dict(EVIDENCE["audit.repo_facts"]), eligible=True,
                           decision="unavailable_before_call",
                           metadata={**LEGACY_METADATA, "mcp_gate_contract_version": "2"})
    assert not coverage.report_is_pass(result, False, True)
    assert any("mcp_gate_contract_version 非受支持合同版本" in terr
               for terr in result["trace_errors"])


# ---- review.dedup 枚举可信性回归（P1-2：无法枚举不得视为业务不适用）----

def test_threshold_reached_with_unavailable_rg_stays_eligible(tmp_path):
    # 审查构造的绕过形态：rg_available=false + function_count 达阈值却记 skipped_not_eligible。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50,
                    rg_available=False, cheap_available=False)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=False,
                           decision="skipped_not_eligible")
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 1
    assert result["skipped_not_eligible"] == 0


def test_unenumerated_below_threshold_cannot_skip_as_not_eligible(tmp_path):
    # rg 不可用且无替代枚举核验：function_count 不可信，不得证明业务不适用（掩盖去重债务）。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
                    rg_available=False, cheap_available=False)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=False,
                           decision="skipped_not_eligible")
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 0 and result["schema_errors"] >= 1
    assert any("未经受支持枚举核验" in issue for issue in result["gates"][0]["issues"])


def test_verified_alternative_enumeration_allows_below_threshold_skip(tmp_path):
    # 受支持的替代枚举已核验（enumeration_verified=true）：低于阈值是合法业务 skip。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
                    rg_available=False, cheap_available=False, enumeration_verified=True)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=False,
                           decision="skipped_not_eligible")
    assert coverage.report_is_pass(result, False, True), result
    assert result["skipped_not_eligible"] == 1


def test_enumeration_verified_wrong_type_is_schema_error(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
                    rg_available=False, cheap_available=False, enumeration_verified="yes")
    result = no_mcp_report(tmp_path, gate, evidence, eligible=False,
                           decision="skipped_not_eligible")
    assert not coverage.report_is_pass(result, False, True)
    assert any("enumeration_verified 类型/值不符合" in issue
               for issue in result["gates"][0]["issues"])


# ---- 枚举未知（enumeration_unverified）债务状态回归（P1：诚实保留债务必须有合法记录）----

UNENUMERATED_DEBT = dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                         rg_available=False, cheap_available=False, enumeration_unverified=True)


def test_unenumerated_debt_record_with_unavailable_passes(tmp_path):
    # ripgrep 与受支持的替代枚举都不可用：显式声明枚举未知后，eligible=true +
    # unavailable_before_call 是文档要求的合法债务记录。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(UNENUMERATED_DEBT), eligible=True,
                           decision="unavailable_before_call")
    assert coverage.report_is_pass(result, False, True), result
    assert result["unavailable_before_call"] == 1


def test_unenumerated_debt_record_with_degraded_passes(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(UNENUMERATED_DEBT), eligible=True,
                           decision="degraded_after_attempt")
    assert coverage.report_is_pass(result, False, True), result
    assert result["degraded_after_attempt"] == 1


def test_unknown_state_cannot_skip_as_not_eligible(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(UNENUMERATED_DEBT), eligible=False,
                           decision="skipped_not_eligible")
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 1


def test_debt_record_without_state_declaration_fails(tmp_path):
    # 未显式声明枚举未知时，eligible=true 的债务记录同样不通过（状态必须机器可识别）。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
                    rg_available=False, cheap_available=False)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=True,
                           decision="unavailable_before_call")
    assert not coverage.report_is_pass(result, False, True)
    assert any("enumeration_unverified" in issue for issue in result["gates"][0]["issues"])


def test_enumeration_unverified_wrong_type_is_schema_error(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(UNENUMERATED_DEBT, enumeration_unverified="yes")
    result = no_mcp_report(tmp_path, gate, evidence, eligible=True,
                           decision="unavailable_before_call")
    assert not coverage.report_is_pass(result, False, True)
    assert any("enumeration_unverified 类型/值不符合" in issue
               for issue in result["gates"][0]["issues"])


def test_contract_v1_ignores_enumeration_unverified(tmp_path):
    # 合同 v1 历史解释不受新状态字段影响（存量工单不追溯改判）。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=365, threshold=50,
                    rg_available=False, cheap_available=False, enumeration_unverified=True)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=False,
                           decision="skipped_not_eligible", metadata=dict(LEGACY_METADATA))
    assert coverage.report_is_pass(result, False, True), result


# ---- 采纳边界回归（P2：续跑新记录强制当前合同，历史行保留原解释）----

ADOPTED_METADATA = dict(ticket_id="coverage-test", mcp_gate_schema_version=2, mode="full",
                        mcp_gate_contract_version=2,
                        mcp_gate_contract_adopted_at="2026-11-17T00:00:00+00:00")


def test_row_contract_adoption_boundary():
    meta = dict(mcp_gate_contract_version=2, mcp_gate_contract_adopted_at="2026-11-17T00:00:00+00:00")
    assert coverage.row_contract(dict(at="2026-11-16T23:59:59Z"), meta) == 1
    assert coverage.row_contract(dict(at="2026-11-17T00:00:01Z"), meta) == 2
    # 行级显式声明优先于采纳边界。
    assert coverage.row_contract(dict(at="2026-11-18T00:00:00Z", contract_version=1), meta) == 1
    assert coverage.row_contract(dict(at="2026-11-16T00:00:00Z", contract_version=2), meta) == 2
    # naive 采纳时刻按 UTC 解释。
    naive = dict(mcp_gate_contract_version=2, mcp_gate_contract_adopted_at="2026-11-17T00:00:00")
    assert coverage.row_contract(dict(at="2026-11-17T00:00:01"), naive) == 2
    # 出生即绑定（无采纳时刻）：全部行按 metadata 合同。
    assert coverage.row_contract(dict(at="2026-01-01T00:00:00Z"),
                                 dict(mcp_gate_contract_version=2)) == 2
    # 无法解析时间的行保守取当前合同。
    assert coverage.row_contract(dict(at="not-a-time"), meta) == 2


def test_adoption_boundary_preserves_historical_skip(tmp_path):
    # 采纳前的历史 skip 行（工单 2/3 形状）仍按 v1 解释合法。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(HISTORICAL_DEDUP), eligible=False,
                           decision="skipped_not_eligible",
                           row_extra={"at": "2026-11-16T10:00:00Z"},
                           metadata=dict(ADOPTED_METADATA))
    assert coverage.report_is_pass(result, False, True), result
    assert result["skipped_not_eligible"] == 1


def test_post_adoption_rows_use_current_contract(tmp_path):
    # 采纳后的续跑新行（即使未标注版本号）强制当前合同：达阈值 + MCP 不可用不得 skip。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(HISTORICAL_DEDUP), eligible=False,
                           decision="skipped_not_eligible",
                           row_extra={"at": "2026-11-17T08:00:00Z"},
                           metadata=dict(ADOPTED_METADATA))
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 1


def test_debt_state_rejects_cache_hit_fulfillment(tmp_path):
    # P2 回归：enumeration_unverified=true 债务状态不得记 cache_hit 计入 fulfilled。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(UNENUMERATED_DEBT), eligible=True,
                           decision="cache_hit")
    assert not coverage.report_is_pass(result, False, True)
    assert result["invalid_skip"] == 0 and result["schema_errors"] >= 1
    assert result["fulfilled"] == 0
    assert any("债务状态只能以" in issue for issue in result["gates"][0]["issues"])


def test_debt_state_rejects_called_fulfillment(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(UNENUMERATED_DEBT), eligible=True,
                           decision="called", row_extra={"attempted": True})
    assert not coverage.report_is_pass(result, False, True)
    assert result["fulfilled"] == 0
    assert any("债务状态只能以" in issue for issue in result["gates"][0]["issues"])


def test_report_exposes_enumeration_debt_counter(tmp_path):
    # 报告显式暴露未解决的枚举/去重债务（不改变通过判定）。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    result = no_mcp_report(tmp_path, gate, dict(UNENUMERATED_DEBT), eligible=True,
                           decision="unavailable_before_call")
    assert coverage.report_is_pass(result, False, True)
    assert result["enumeration_debt"] == 1
    assert result["gates"][0]["enumeration_debt"] is True
    verified = dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
                    rg_available=False, cheap_available=False, enumeration_verified=True)
    plain = no_mcp_report(tmp_path, gate, verified, eligible=False,
                          decision="skipped_not_eligible")
    assert plain["enumeration_debt"] == 0
    assert "enumeration_debt" not in plain["gates"][0]


# ---- P1 回归：债务状态与尝试值是否达到阈值相互独立 ----

def test_debt_state_at_threshold_rejects_cache_hit(tmp_path):
    # 尝试值达到阈值也不能把枚举未知的缓存结果记为成功。
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    for count in (50, 266):
        evidence = dict(affected_repo_roots=["/fixture"], function_count=count,
                        threshold=50, rg_available=False, cheap_available=False,
                        enumeration_unverified=True)
        result = no_mcp_report(tmp_path, gate, evidence, eligible=True,
                               decision="cache_hit")
        assert not coverage.report_is_pass(result, False, True), (count, result)
        assert result["fulfilled"] == 0
        assert any("债务状态只能以" in issue for issue in result["gates"][0]["issues"])


def test_debt_state_at_threshold_records_debt(tmp_path):
    gate = next(g for g in CATALOG["gates"] if g["id"] == "review.dedup")
    evidence = dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50,
                    rg_available=False, cheap_available=False, enumeration_unverified=True)
    result = no_mcp_report(tmp_path, gate, evidence, eligible=True,
                           decision="unavailable_before_call")
    assert coverage.report_is_pass(result, False, True), result
    assert result["enumeration_debt"] == 1


def test_row_contract_history_boundary_covers_upgrade_window():
    # 采纳时存在升级前未终结 attempt：边界回退到其 start，升级后、采纳前写入的
    # trace 按当前合同解释（不被伪装成历史行）。
    meta = dict(mcp_gate_contract_version=2,
                mcp_gate_contract_adopted_at="2026-11-17T12:00:00+00:00",
                mcp_gate_contract_history_boundary="2026-11-17T08:00:00+00:00")
    assert coverage.row_contract(dict(at="2026-11-17T07:00:00Z"), meta) == 1
    assert coverage.row_contract(dict(at="2026-11-17T09:00:00Z"), meta) == 2
    assert coverage.row_contract(dict(at="2026-11-17T13:00:00Z"), meta) == 2
    # 无 history_boundary 时回退采纳时刻。
    plain = dict(mcp_gate_contract_version=2,
                 mcp_gate_contract_adopted_at="2026-11-17T12:00:00+00:00")
    assert coverage.row_contract(dict(at="2026-11-17T11:00:00Z"), plain) == 1
    assert coverage.row_contract(dict(at="2026-11-17T13:00:00Z"), plain) == 2


@pytest.mark.skipif(not hasattr(time, "tzset"),
                    reason="原生 Windows 无 time.tzset；无时区解释一致性的时区切换用例仅在 POSIX 运行")
def test_naive_row_at_uses_local_timezone_like_control_plane():
    # P1 回归：trace 合同不强制时区，无时区值必须与控制面事件时间（now_iso 本地
    # naive）同约定解释——按 UTC 会在 UTC+8 误判历史行为 v2、UTC-8 误判新行为 v1。
    meta = dict(mcp_gate_contract_version=2,
                mcp_gate_contract_adopted_at="2026-11-17T06:00:00+00:00",
                mcp_gate_contract_history_boundary="2026-11-17T06:00:00+00:00")
    cases = (
        ("Asia/Shanghai", "2026-11-17T05:00:00", 1),  # 本地+08 = 21:00Z(11-16) 采纳前
        ("Asia/Shanghai", "2026-11-17T14:00:00", 2),  # 本地+08 = 06:00Z 边界时刻
        ("Etc/GMT+8", "2026-11-17T00:00:00", 2),      # 本地-08 = 08:00Z 采纳后
        ("Etc/GMT+8", "2026-11-16T21:00:00", 1),      # 本地-08 = 05:00Z 采纳前
    )
    old_tz = os.environ.get("TZ")
    try:
        for tz, at, expected in cases:
            os.environ["TZ"] = tz
            time.tzset()
            assert coverage.row_contract(dict(at=at), meta) == expected, (tz, at)
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def test_ticket_enumeration_debt_balance_and_rework():
    # 工单级余额：每 gate 最后一条携带债务状态则计数；通过资格与证据校验的
    # 有效最终记录才清偿（真实重做即归零）。
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2)
    base = dict(schema_version=2, ticket_id="t", step="review", at="2026-11-17T06:00:00Z")
    debt = dict(base, gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="unavailable",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call")
    redo = dict(debt, decision="called", attempted=True, result="success",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                      rg_available=True, cheap_available=True,
                      repo_review_scopes={"/fixture": ["."]}))
    historical = dict(base, gate_id="review.dedup", tool="extract", eligible=False,
        attempted=False, result="success",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                      rg_available=True, cheap_available=False),
        decision="skipped_not_eligible")
    verified_skip = dict(historical,
        evidence=dict(affected_repo_roots=["/fixture"], function_count=49, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_verified=True))
    other = dict(base, gate_id="review.result_summary", tool="summarize", eligible=True,
        attempted=True, result="success",
        evidence=dict(result_source="02_review.md", review_rounds=1),
        decision="called")
    assert coverage.ticket_enumeration_debt([debt, other], meta) == 1
    assert coverage.ticket_enumeration_debt([debt, other, redo], meta) == 0  # 重做清偿
    assert coverage.ticket_enumeration_debt([historical], meta) == 0         # v1 历史行
    assert coverage.ticket_enumeration_debt([verified_skip], meta) == 0      # 可信枚举 skip
    assert coverage.ticket_enumeration_debt([], meta) == 0


def test_ticket_enumeration_debt_requires_valid_redo():
    # P1 回归：清偿必须来自通过资格与证据校验的有效最终记录——流程要求调用前
    # 先写 pending，中断/未完成/非法的重做行不改写债务状态。
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2)
    base = dict(schema_version=2, ticket_id="t", step="review", at="2026-11-17T06:00:00Z")
    debt = dict(base, gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="unavailable",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call")
    pending = dict(base, gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="success",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False),
        decision="pending")
    cache_hit_debt = dict(debt, decision="cache_hit")
    assert coverage.ticket_enumeration_debt([debt, pending], meta) == 1
    assert coverage.ticket_enumeration_debt([debt, cache_hit_debt], meta) == 1


def test_debt_clearing_requires_verifiable_manual_evidence(tmp_path):
    # P1 回归：「函数已枚举」不等于「去重债务已清偿」——unavailable 清偿记录的
    # 人工等价证据必须机器可验证（evidence_ref 指向工单目录内真实存在的文件）。
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    base = dict(schema_version=2, ticket_id="t", step="review", at="2026-11-17T06:00:00Z")
    debt = dict(base, gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="unavailable",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call")
    fake_clearing = dict(base, at="2026-11-17T07:00:00Z", gate_id="review.dedup",
        tool="extract", eligible=True, attempted=False, result="unavailable",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                      rg_available=False, cheap_available=False),
        decision="unavailable_before_call",
        availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                          fallback="manual", evidence_ref="dedup/manual_report.json"))
    # 证据文件不存在：不清偿（余额保持 1）。
    assert coverage.ticket_enumeration_debt(
        [debt, fake_clearing], meta, out_dir=tmp_path) == 1
    # 空文件不是人工审查证据。
    evidence_dir = tmp_path / "dedup"
    evidence_dir.mkdir()
    (evidence_dir / "manual_report.json").write_text("{}\n", encoding="utf-8")
    assert coverage.ticket_enumeration_debt(
        [debt, fake_clearing], meta, out_dir=tmp_path) == 1
    # 越界引用不可信：即使文件存在也不清偿。
    outside = dict(fake_clearing, at="2026-11-17T08:00:00Z",
                   availability=dict(tool_visible=False, reason="not_exposed",
                                     discovery_ref="s", fallback="manual",
                                     evidence_ref="../outside_report.json"))
    assert coverage.ticket_enumeration_debt(
        [debt, outside], meta, out_dir=tmp_path) == 1
    # 无 out_dir 上下文时无法验证人工证据 → 保守不清偿。
    assert coverage.ticket_enumeration_debt([debt, fake_clearing], meta) == 1


def test_verified_below_threshold_clears_enumeration_debt(tmp_path):
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z")
    debt = dict(base, eligible=True, attempted=False, result="unavailable",
                decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=["/fixture"], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True))
    skip = dict(base, eligible=False, attempted=False, result="not_applicable",
                decision="skipped_not_eligible",
                evidence=dict(affected_repo_roots=["/fixture"], function_count=49,
                              threshold=50, rg_available=True, cheap_available=False,
                              repo_review_scopes={"/fixture": ["."]}))
    assert coverage.ticket_enumeration_debt([debt, skip], meta, out_dir=tmp_path) == 0
    # 未声明范围的清偿记录不构成覆盖（实际范围未知）。
    undeclared = dict(skip, evidence=dict(skip["evidence"]))
    undeclared["evidence"].pop("repo_review_scopes")
    assert coverage.ticket_enumeration_debt([debt, undeclared], meta,
                                            out_dir=tmp_path) == 1


def test_debt_rework_must_cover_original_repositories(tmp_path):
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z")
    debt = dict(base, eligible=True, attempted=False, result="unavailable",
                decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=["/repo-a", "/repo-b"], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True))
    skip = dict(base, eligible=False, attempted=False, result="not_applicable",
                decision="skipped_not_eligible",
                evidence=dict(affected_repo_roots=["/repo-b"], function_count=5,
                              threshold=50, rg_available=True, cheap_available=False,
                              repo_review_scopes={"/repo-b": ["."]}))
    assert coverage.ticket_enumeration_debt([debt, skip], meta, out_dir=tmp_path) == 1
    skip["evidence"]["affected_repo_roots"] = ["/repo-b", "/repo-a"]
    skip["evidence"]["repo_review_scopes"] = {"/repo-a": ["."], "/repo-b": ["."]}
    assert coverage.ticket_enumeration_debt([debt, skip], meta, out_dir=tmp_path) == 0


def test_manual_debt_clearing_requires_current_complete_report(tmp_path, monkeypatch):
    monkeypatch.setattr(coverage, "review_manual_provenance", lambda *args: [])
    workspace = tmp_path / "project"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    source = workspace / "app.py"
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(50)), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(schema_version=1, ticket_id="t", attempt="a", review_scope=["."],
                  unobserved=[], sources=[dict(path="app.py", sha256=digest,
                    catalog_method="python_ast", functions=[dict(name=f"f{i}", line=i + 1,
                    family="all") for i in range(50)])],
                  families=[dict(name="all", members=[f"app.py:{i + 1}:f{i}" for i in range(50)],
                    conclusion="different", reason="逐项检查", evidence=[f"app.py:{i + 1}" for i in range(50)])])
    out = tmp_path / "ticket"
    out.mkdir()
    evidence_file = out / "report.json"
    evidence_file.write_text(json.dumps(report), encoding="utf-8")
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(workspace))
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True,
                attempted=False, result="unavailable", decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                threshold=50, rg_available=False, cheap_available=False, enumeration_unverified=True))
    redo = dict(base, attempt="a", evidence=dict(affected_repo_roots=[str(workspace)],
                function_count=50, threshold=50, rg_available=True, cheap_available=False,
                repo_review_scopes={str(workspace): ["."]}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref="report.json"))
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0
    foreign_attempt = dict(report, attempt="other")
    evidence_file.write_text(json.dumps(foreign_attempt), encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 1
    evidence_file.write_text(json.dumps({"manual_dedup": report}), encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0
    source.write_text(source.read_text() + "def changed(): pass\n", encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 1


def test_manual_debt_clearing_uses_each_repository_review_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(coverage, "review_manual_provenance", lambda *args: [])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    out = tmp_path / "ticket"
    out.mkdir()
    roots, reports = [], []
    for name, count in (("repo-a", 50), ("repo-b", 3)):
        repo = workspace / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "src").mkdir()
        source = repo / "src" / "app.py"
        source.write_text("".join(f"def f{i}(): pass\n" for i in range(count)), encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        report = dict(schema_version=1, ticket_id="t", attempt="a", review_scope=["src"],
                      unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                      catalog_method="python_ast", functions=[dict(name=f"f{i}", line=i + 1,
                      family="all") for i in range(count)])],
                      families=[dict(name="all", members=[f"src/app.py:{i + 1}:f{i}" for i in range(count)],
                      conclusion="different", reason="逐项检查",
                      evidence=[f"src/app.py:{i + 1}" for i in range(count)])])
        roots.append(str(repo))
        reports.append(dict(repo_root=str(repo), manual_dedup=report))
    path = out / "report.json"
    path.write_text(json.dumps(dict(repositories=reports)), encoding="utf-8")
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(workspace))
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True,
                attempted=False, result="unavailable", decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=roots, function_count=0,
                threshold=50, rg_available=False, cheap_available=False,
                enumeration_unverified=True,
                repo_review_scopes={root: ["src"] for root in roots}))
    redo = dict(base, attempt="a", evidence=dict(affected_repo_roots=roots, function_count=50,
                threshold=50, rg_available=True, cheap_available=False,
                repo_review_scopes={root: ["src"] for root in roots}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref="report.json"))
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0
    spec = importlib.util.spec_from_file_location("scope_control", ROOT / "tools/icode_control.py")
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    monkeypatch.setattr(control.gate_coverage_module(), "review_manual_provenance", lambda *args: [])
    (out / ".mcp_gate_trace.jsonl").write_text(
        "\n".join(json.dumps(row) for row in (debt, redo)) + "\n", encoding="utf-8")
    assert control.ticket_enumeration_debt(out, meta) == 0
    path.write_text(json.dumps(dict(repositories=reports[:1])), encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 1
    path.write_text(json.dumps(dict(repositories=reports)), encoding="utf-8")
    redo["evidence"]["function_count"] = 53
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 1
    redo["evidence"]["function_count"] = 50
    (Path(roots[1]) / "src/app.py").write_text("def changed(): pass\n", encoding="utf-8")
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 1


def test_manual_debt_clearing_accepts_declared_module_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(coverage, "review_manual_provenance", lambda *args: [])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "src").mkdir()
    source = workspace / "src" / "app.py"
    source.write_text("".join(f"def f{i}(): pass\n" for i in range(50)), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(schema_version=1, ticket_id="t", attempt="a", review_scope=["src"],
                  unobserved=[], sources=[dict(path="src/app.py", sha256=digest,
                  catalog_method="python_ast", functions=[dict(name=f"f{i}", line=i + 1,
                  family="all") for i in range(50)])],
                  families=[dict(name="all", members=[f"src/app.py:{i + 1}:f{i}" for i in range(50)],
                  conclusion="different", reason="逐项检查", evidence=[f"src/app.py:{i + 1}" for i in range(50)])])
    out = tmp_path / "ticket"
    out.mkdir()
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t", project_path=str(workspace))
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=[str(workspace)], function_count=0,
                threshold=50, rg_available=False, cheap_available=False,
                enumeration_unverified=True,
                repo_review_scopes={str(workspace): ["src"]}))
    redo = dict(base, attempt="a", evidence=dict(affected_repo_roots=[str(workspace)], function_count=50,
                threshold=50, rg_available=True, cheap_available=False,
                repo_review_scopes={str(workspace): ["src"]}),
                availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                  fallback="manual", evidence_ref="report.json"))
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0
    (workspace / "outside.py").write_text("def changed(): pass\n", encoding="utf-8")
    meta["code_files"] = ["outside.py"]
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 1
    meta["code_files"] = ["src/app.py"]
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=out) == 0


def test_debt_clearing_rejects_foreign_ticket_records(tmp_path):
    # P2 回归：清偿记录必须属于当前工单——他单的合法记录不得清零本单债务。
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    base = dict(schema_version=2, ticket_id="t", step="review", at="2026-11-17T06:00:00Z")
    debt = dict(base, gate_id="review.dedup", tool="extract", eligible=True,
        attempted=False, result="unavailable",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                      rg_available=False, cheap_available=False, enumeration_unverified=True),
        decision="unavailable_before_call")
    foreign = dict(base, ticket_id="other-ticket", at="2026-11-17T07:00:00Z",
        gate_id="review.dedup", tool="extract", eligible=True, attempted=True,
        result="success",
        evidence=dict(affected_repo_roots=["/fixture"], function_count=266, threshold=50,
                      rg_available=True, cheap_available=True),
        decision="called")
    assert coverage.ticket_enumeration_debt([debt, foreign], meta, out_dir=tmp_path) == 1


# ---- P1 回归：deepcheck 工具不可用但人工复检未完成，不得清偿 review 债务 ----

def test_unavailable_deepcheck_without_evidence_cannot_clear_debt(tmp_path):
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    base = dict(schema_version=2, ticket_id="t", step="review", at="2026-11-17T06:00:00Z")
    debt = dict(base, gate_id="review.dedup", tool="extract", eligible=True,
                attempted=False, result="unavailable",
                decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=["/fixture"], function_count=0, threshold=50,
                              rg_available=False, cheap_available=False,
                              enumeration_unverified=True))
    unavailable_deepcheck = dict(base, step="deepcheck", gate_id="deepcheck.dedup",
                tool="extract", at="2026-11-17T08:00:00Z", eligible=True,
                attempted=False, result="unavailable", decision="unavailable_before_call",
                evidence=dict(mode="full", phase="dedup", affected_repo_roots=["/fixture"],
                              function_count=0, threshold=50),
                availability=dict(tool_visible=False, reason="not_exposed",
                                  discovery_ref="s", fallback="manual",
                                  evidence_ref="dedup/missing_report.json"))
    # 人工复检证据文件不存在 → 不覆盖，债务保留。
    assert coverage.ticket_enumeration_debt(
        [debt, unavailable_deepcheck], meta, out_dir=tmp_path) == 1
    # 真实工具成功（called）→ 覆盖同仓库范围，清偿。
    called = dict(base, step="deepcheck", gate_id="deepcheck.dedup", tool="extract",
                  at="2026-11-17T09:00:00Z", eligible=True, attempted=True,
                  result="success", decision="called",
                  evidence=dict(mode="full", phase="dedup",
                                affected_repo_roots=["/fixture"],
                                function_count=120, threshold=50,
                                repo_review_scopes={"/fixture": ["."]}))
    called.pop("availability", None)
    assert coverage.ticket_enumeration_debt(
        [debt, called], meta, out_dir=tmp_path) == 0
    # 未声明范围的机器成功记录实际范围未知 → 不构成覆盖。
    undeclared = dict(called, evidence=dict(called["evidence"]))
    undeclared["evidence"].pop("repo_review_scopes")
    assert coverage.ticket_enumeration_debt([debt, undeclared], meta,
                                            out_dir=tmp_path) == 1


# ---- P1 回归：快照回退必须依赖受控的完整校验回执 ----

def test_snapshot_fallback_requires_controlled_verification_receipt(tmp_path):
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True,
                attempted=False, result="unavailable",
                decision="unavailable_before_call")
    debt = dict(base, evidence=dict(affected_repo_roots=["/fixture"], function_count=0,
                                    threshold=50, rg_available=False, cheap_available=False,
                                    enumeration_unverified=True))
    # 尚无任何受控回执时，即使报告摘要绑定齐全也不清偿（摘要不等于曾经有效）。
    report = tmp_path / "review_dedup_evidence.json"
    report.write_text('{"schema_version": 1}', encoding="utf-8")
    bound = dict(base, attempt="review-a",
                 evidence=dict(affected_repo_roots=["/fixture"], function_count=50,
                               threshold=50, rg_available=True, cheap_available=False,
                               repo_review_scopes={"/fixture": ["src"]}),
                 availability=dict(tool_visible=False, reason="not_exposed",
                                   discovery_ref="s", fallback="manual",
                                   evidence_ref="review_dedup_evidence.json",
                                   evidence_sha256=hashlib.sha256(report.read_bytes()).hexdigest()))
    assert coverage.ticket_enumeration_debt([debt, bound], meta, out_dir=tmp_path) == 1


def test_deepcheck_manual_recheck_clears_review_debt(tmp_path):
    """P1 回归：无 MCP 时人工 deepcheck 复检（本步骤 attempt + 当前源码完整校验）
    必须能清偿 review 债务；不得套用 review 专用的计划/产物绑定。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    base = dict(schema_version=2, ticket_id="t", at="2026-11-17T06:00:00Z")
    debt = dict(base, step="review", gate_id="review.dedup", tool="extract", eligible=True,
                attempted=False, result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True))
    # deepcheck 人工复检：unavailable + 证据不可验证 → 不覆盖。
    unavailable = dict(base, step="deepcheck", gate_id="deepcheck.dedup", tool="extract",
                       at="2026-11-17T08:00:00Z", eligible=True, attempted=False,
                       result="unavailable", decision="unavailable_before_call",
                       attempt="deepcheck-a",
                       evidence=dict(mode="full", phase="dedup",
                                     affected_repo_roots=[str(tmp_path)],
                                     function_count=0, threshold=50),
                       availability=dict(tool_visible=False, reason="not_exposed",
                                         discovery_ref="s", fallback="manual",
                                         evidence_ref="deepcheck_dedup_evidence.json"))
    assert coverage.ticket_enumeration_debt([debt, unavailable], meta, out_dir=tmp_path) == 1


def test_declared_evidence_digest_must_match_file(tmp_path):
    """回归：声明了 availability.evidence_sha256 就必须与文件一致——
    否则「当前源码校验通过」会掩盖证据身份失效（fail closed）。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    report = tmp_path / "review_dedup_evidence.json"
    report.write_text("{}", encoding="utf-8")
    base = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call")
    row = dict(base, attempt="review-a",
               evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                             threshold=50, rg_available=True, cheap_available=False,
                             repo_review_scopes={str(tmp_path): ["."]}),
               availability=dict(tool_visible=False, reason="not_exposed", discovery_ref="s",
                                 fallback="manual", evidence_ref=report.name,
                                 evidence_sha256="0" * 64))
    assert coverage.manual_dedup_evidence_verifiable(row, meta, tmp_path) is False


def test_repo_root_normalization_handles_path_aliases(tmp_path):
    """回归：相对根与绝对根（含 macOS /var ↔ /private/var 别名）必须匹配同一仓库，
    否则同一仓库的有效 deepcheck 复检无法清偿 review 债务。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=["."], function_count=0, threshold=50,
                              rg_available=False, cheap_available=False,
                              enumeration_unverified=True))
    covered = dict(schema_version=2, ticket_id="t", step="deepcheck",
                   gate_id="deepcheck.dedup", tool="extract", at="2026-11-17T08:00:00Z",
                   eligible=True, attempted=True, result="success", decision="called",
                   evidence=dict(mode="full", phase="dedup",
                                 affected_repo_roots=[str(tmp_path)],
                                 function_count=120, threshold=50,
                                 repo_review_scopes={str(tmp_path): ["."]}))
    assert coverage.ticket_enumeration_debt([debt, covered], meta, out_dir=tmp_path) == 0
    # 范围外仓库不覆盖。
    other = dict(covered, evidence=dict(covered["evidence"],
                                        affected_repo_roots=["/elsewhere"]))
    assert coverage.ticket_enumeration_debt([debt, other], meta, out_dir=tmp_path) == 1


def test_step_manual_provenance_requires_finished_attempt_and_receipt(tmp_path, monkeypatch):
    """P1 回归：后续步骤的人工复检来源必须真实结束（success/degraded）且报告已
    在该 attempt 内登记；未结束、失败或未登记的回执都不得用于清偿。"""
    import types

    target = tmp_path / "deepcheck_dedup_evidence.json"
    target.write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    def events_with(finish_outcome=None, artifact=True):
        events = [{"event_type": "step_started", "timestamp": "2026-11-17T09:00:00",
                   "payload": {"step": "deepcheck", "attempt": "deepcheck-a"}}]
        if artifact:
            events.append({"event_type": "artifact_written", "timestamp": "2026-11-17T09:01:00",
                           "payload": {"step": "deepcheck", "attempt": "deepcheck-a",
                                       "path": str(target), "sha256": digest}})
        if finish_outcome is not None:
            events.append({"event_type": "step_finished", "timestamp": "2026-11-17T09:02:00",
                           "payload": {"step": "deepcheck", "attempt": "deepcheck-a",
                                       "outcome": finish_outcome}})
        return events

    def run(events):
        fake = types.SimpleNamespace(verify_event_chain=lambda *a, **k: (events, []))
        monkeypatch.setattr(coverage, "control_module", lambda: fake)
        return coverage.step_manual_provenance(
            tmp_path, {"ticket_id": "t"}, "deepcheck", "deepcheck-a",
            target, target.read_bytes())

    assert run(events_with(finish_outcome=None)) is False          # 未结束
    assert run(events_with(finish_outcome="blocked")) is False     # 失败/中断
    assert run(events_with(finish_outcome="success", artifact=False)) is False  # 未登记
    assert run(events_with(finish_outcome="success")) is True       # 完成且已登记
    assert run(events_with(finish_outcome="degraded")) is True      # 降级完成亦可


def test_deepcheck_scope_must_cover_original_debt_scope(tmp_path):
    """P1 回归：deepcheck 复检须按仓库逐项覆盖原债务范围；范围未知时保留债务。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={str(tmp_path): ["other"]}))
    base = dict(schema_version=2, ticket_id="t", step="deepcheck",
                gate_id="deepcheck.dedup", tool="extract", at="2026-11-17T08:00:00Z",
                eligible=True, attempted=True, result="success", decision="called")

    def covered(scopes):
        return dict(base, evidence=dict(mode="full", phase="dedup",
                                        affected_repo_roots=[str(tmp_path)],
                                        function_count=120, threshold=50,
                                        repo_review_scopes={str(tmp_path): scopes}))
    # 复检范围只有 src，原债务范围是 other → 不清偿。
    assert coverage.ticket_enumeration_debt(
        [debt, covered(["src"])], meta, out_dir=tmp_path) == 1
    # 覆盖原债务范围 → 清偿。
    assert coverage.ticket_enumeration_debt(
        [debt, covered(["other"])], meta, out_dir=tmp_path) == 0
    # 覆盖超集 → 清偿。
    assert coverage.ticket_enumeration_debt(
        [debt, covered(["other", "src"])], meta, out_dir=tmp_path) == 0
    # 复检范围未知（多仓缺省）→ 保留债务。
    debt_multi = dict(debt, evidence=dict(debt["evidence"],
                                          affected_repo_roots=[str(tmp_path), "/second"]))
    unknown = dict(base, evidence=dict(mode="full", phase="dedup",
                                       affected_repo_roots=[str(tmp_path), "/second"],
                                       function_count=120, threshold=50))
    assert coverage.ticket_enumeration_debt(
        [debt_multi, unknown], meta, out_dir=tmp_path) == 1


def test_scope_text_normalization_and_strict_typing(tmp_path):
    """回归：范围写法归一（src/ ≡ src）与严格类型——同一范围不因写法差异误判未覆盖，
    非字符串/空范围视为未知并保留债务。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={str(tmp_path): ["src/"]}))
    covered = dict(schema_version=2, ticket_id="t", step="deepcheck",
                   gate_id="deepcheck.dedup", tool="extract", at="2026-11-17T08:00:00Z",
                   eligible=True, attempted=True, result="success", decision="called",
                   evidence=dict(mode="full", phase="dedup",
                                 affected_repo_roots=[str(tmp_path)],
                                 function_count=120, threshold=50,
                                 repo_review_scopes={str(tmp_path): ["./src"]}))
    assert coverage.ticket_enumeration_debt([debt, covered], meta, out_dir=tmp_path) == 0
    bad_scope = dict(covered, evidence=dict(covered["evidence"],
                                            repo_review_scopes={str(tmp_path): ["src", 7]}))
    assert coverage.ticket_enumeration_debt([debt, bad_scope], meta, out_dir=tmp_path) == 1


def test_foreign_ticket_debt_rows_do_not_inflate_balance(tmp_path):
    """回归：他单 ticket_id 的债务行不得计入本工单余额（否则余额虚高且无法清偿）。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t")
    foreign = dict(schema_version=2, ticket_id="other", step="review",
                   gate_id="review.dedup", tool="extract", at="2026-11-17T06:00:00Z",
                   eligible=True, attempted=False, result="unavailable",
                   decision="unavailable_before_call",
                   evidence=dict(affected_repo_roots=["/r"], function_count=0,
                                 threshold=50, rg_available=False, cheap_available=False,
                                 enumeration_unverified=True))
    assert coverage.ticket_enumeration_debt([foreign], meta, out_dir=tmp_path) == 0
    own = dict(foreign, ticket_id="t")
    assert coverage.ticket_enumeration_debt([own], meta, out_dir=tmp_path) == 1


def test_review_rework_must_cover_declared_debt_scope(tmp_path):
    """P1 回归：review 重做也要逐仓比较范围——计划覆盖不等于覆盖原债务范围。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={str(tmp_path): ["other"]}))
    redo = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T07:00:00Z", eligible=True, attempted=True,
                result="success", decision="called",
                evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=120,
                              threshold=50, rg_available=True, cheap_available=True,
                              repo_review_scopes={str(tmp_path): ["src"]}))
    # 重做只覆盖 src，原债务范围是 other → 不清偿。
    assert coverage.ticket_enumeration_debt([debt, redo], meta, out_dir=tmp_path) == 1
    # 覆盖原债务范围 → 清偿。
    covered = dict(redo, evidence=dict(redo["evidence"],
                                       repo_review_scopes={str(tmp_path): ["other"]}))
    assert coverage.ticket_enumeration_debt([debt, covered], meta, out_dir=tmp_path) == 0
    # 覆盖超集（目录包含）→ 清偿。
    superset = dict(redo, evidence=dict(redo["evidence"],
                                        repo_review_scopes={str(tmp_path): ["."]}))
    assert coverage.ticket_enumeration_debt([debt, superset], meta, out_dir=tmp_path) == 0


def test_consecutive_debt_scopes_accumulate(tmp_path):
    """P1 回归：同一仓库连续产生 a、b 两笔债务时范围必须累积；
    只复检 b 不能清偿 a。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))

    def debt_row(scope, at):
        return dict(schema_version=2, ticket_id="t", step="review",
                    gate_id="review.dedup", tool="extract", at=at, eligible=True,
                    attempted=False, result="unavailable",
                    decision="unavailable_before_call",
                    evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                                  threshold=50, rg_available=False, cheap_available=False,
                                  enumeration_unverified=True,
                                  repo_review_scopes={str(tmp_path): [scope]}))

    def deepcheck(scope):
        return dict(schema_version=2, ticket_id="t", step="deepcheck",
                    gate_id="deepcheck.dedup", tool="extract", at="2026-11-17T09:00:00Z",
                    eligible=True, attempted=True, result="success", decision="called",
                    evidence=dict(mode="full", phase="dedup",
                                  affected_repo_roots=[str(tmp_path)],
                                  function_count=120, threshold=50,
                                  repo_review_scopes={str(tmp_path): [scope]}))
    rows = [debt_row("a", "2026-11-17T06:00:00Z"), debt_row("b", "2026-11-17T06:30:00Z")]
    # 只复检 b → a 仍未清偿。
    assert coverage.ticket_enumeration_debt(rows + [deepcheck("b")], meta,
                                            out_dir=tmp_path) == 1
    # 复检 a 与 b → 全部清偿。
    assert coverage.ticket_enumeration_debt(
        rows + [deepcheck("a"), deepcheck("b")], meta, out_dir=tmp_path) == 0
    # 复检全仓（目录包含 a 与 b）→ 全部清偿。
    assert coverage.ticket_enumeration_debt(rows + [deepcheck(".")], meta,
                                            out_dir=tmp_path) == 0


def test_wider_scope_clears_subdirectory_debt(tmp_path):
    """P2 回归：更大范围（祖先目录/全仓）覆盖子目录债务；不相关或越界路径不覆盖。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[str(tmp_path)], function_count=0,
                              threshold=50, rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={str(tmp_path): ["src/lib"]}))

    def deepcheck(scope):
        return dict(schema_version=2, ticket_id="t", step="deepcheck",
                    gate_id="deepcheck.dedup", tool="extract", at="2026-11-17T09:00:00Z",
                    eligible=True, attempted=True, result="success", decision="called",
                    evidence=dict(mode="full", phase="dedup",
                                  affected_repo_roots=[str(tmp_path)],
                                  function_count=120, threshold=50,
                                  repo_review_scopes={str(tmp_path): [scope]}))
    assert coverage.ticket_enumeration_debt([debt, deepcheck("src")], meta,
                                            out_dir=tmp_path) == 0    # 祖先目录
    assert coverage.ticket_enumeration_debt([debt, deepcheck(".")], meta,
                                            out_dir=tmp_path) == 0    # 全仓
    assert coverage.ticket_enumeration_debt([debt, deepcheck("src/lib")], meta,
                                            out_dir=tmp_path) == 0    # 相等
    assert coverage.ticket_enumeration_debt([debt, deepcheck("other")], meta,
                                            out_dir=tmp_path) == 1    # 不相关
    assert coverage.ticket_enumeration_debt([debt, deepcheck("src/lib/inner")], meta,
                                            out_dir=tmp_path) == 1    # 更窄不覆盖
    assert coverage.ticket_enumeration_debt([debt, deepcheck("../outside")], meta,
                                            out_dir=tmp_path) == 1    # 越界


def test_scope_covers_directory_containment_units():
    """范围包含判定的单元矩阵（目录语义 + 越界拒绝）。"""
    assert coverage.scope_covers(".", "src")
    assert coverage.scope_covers("src", "src/lib")
    assert coverage.scope_covers("src/lib", "src/lib")
    assert not coverage.scope_covers("src", "src2")        # 前缀但不是子目录
    assert not coverage.scope_covers("src/lib", "src")     # 更窄
    assert not coverage.scope_covers("src", ".")           # 子目录不覆盖全仓
    assert not coverage.scope_covers("../x", ".")          # 越界
    assert not coverage.scope_covers(".", "../x")          # 越界需求
    assert coverage.scope_covers("./src/", "src/")         # 写法归一


def test_scope_logic_tolerates_abnormal_trace_shapes(tmp_path):
    """回归：异常 trace（非字符串/空 roots、空或错键 scopes）不得崩溃，
    且一律保守保留债务。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    root = str(tmp_path)

    def debt(roots=None, scopes=None):
        ev = dict(affected_repo_roots=roots if roots is not None else [root],
                  function_count=0, threshold=50, rg_available=False,
                  cheap_available=False, enumeration_unverified=True)
        if scopes is not None:
            ev["repo_review_scopes"] = scopes
        return dict(schema_version=2, ticket_id="t", step="review",
                    gate_id="review.dedup", tool="extract",
                    at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                    result="unavailable", decision="unavailable_before_call",
                    evidence=ev)

    def redo(roots=None, scopes=None):
        ev = dict(affected_repo_roots=roots if roots is not None else [root],
                  function_count=120, threshold=50, rg_available=True,
                  cheap_available=True)
        if scopes is not None:
            ev["repo_review_scopes"] = scopes
        return dict(schema_version=2, ticket_id="t", step="review",
                    gate_id="review.dedup", tool="extract",
                    at="2026-11-17T07:00:00Z", eligible=True, attempted=True,
                    result="success", decision="called", evidence=ev)
    balance = lambda rows: coverage.ticket_enumeration_debt(rows, meta, out_dir=tmp_path)
    assert balance([debt(), redo(roots=[root, 7])]) == 1      # 非字符串不放行
    assert balance([debt(), redo(roots=[])]) == 1             # 空 roots 不放行
    assert balance([debt(roots=[root, None])]) == 1           # 债务 roots 异常
    assert balance([debt(), redo(scopes={"/elsewhere": ["src"]})]) == 1  # 键不匹配
    assert balance([debt(scopes={root: []})]) == 1            # 空范围


def test_unscoped_debt_requires_full_repo_coverage(tmp_path):
    """P1 回归：债务行未声明范围时按全仓处理——局部复检（src）不得清偿整仓债务，
    只有覆盖全仓（.）或祖先范围的复检才能清偿。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    root = str(tmp_path)
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[root], function_count=0, threshold=50,
                              rg_available=False, cheap_available=False,
                              enumeration_unverified=True))

    def deepcheck(scopes=None, decision="called"):
        ev = dict(mode="full", phase="dedup", affected_repo_roots=[root],
                  function_count=120, threshold=50)
        if scopes is not None:
            ev["repo_review_scopes"] = {root: scopes}
        return dict(schema_version=2, ticket_id="t", step="deepcheck",
                    gate_id="deepcheck.dedup", tool="extract",
                    at="2026-11-17T08:00:00Z", eligible=True, attempted=True,
                    result="success", decision=decision, evidence=ev)
    # 局部 src 复检 → 不得清偿整仓债务。
    assert coverage.ticket_enumeration_debt([debt, deepcheck(["src"])], meta,
                                            out_dir=tmp_path) == 1
    # 显式声明全仓才清偿。
    assert coverage.ticket_enumeration_debt([debt, deepcheck(["."])], meta,
                                            out_dir=tmp_path) == 0
    # 清偿侧未声明范围（实际范围未知）→ 不构成覆盖（P1 回归）。
    assert coverage.ticket_enumeration_debt([debt, deepcheck()], meta,
                                            out_dir=tmp_path) == 1


def test_missing_ticket_id_is_conservative_for_debt_but_not_for_clearing(tmp_path):
    """回归：行缺 ticket_id 时无法排除归属——债务侧保守计入（不漏债），
    清偿侧在 metadata 已声明身份时不得作为凭据（不放行）。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    root = str(tmp_path)
    base = dict(schema_version=2, step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z")
    debt_no_ticket = dict(base, ticket_id=None, eligible=True, attempted=False,
                          result="unavailable", decision="unavailable_before_call",
                          evidence=dict(affected_repo_roots=[root], function_count=0,
                                        threshold=50, rg_available=False,
                                        cheap_available=False,
                                        enumeration_unverified=True))
    # 缺 ticket_id 的债务行仍计入本单余额（保守）。
    assert coverage.ticket_enumeration_debt([debt_no_ticket], meta, out_dir=tmp_path) == 1
    # 明确他单的债务行不计入。
    foreign = dict(debt_no_ticket, ticket_id="other")
    assert coverage.ticket_enumeration_debt([foreign], meta, out_dir=tmp_path) == 0
    # 缺 ticket_id 的 called 行不得作为清偿凭据。
    clearing_no_ticket = dict(base, ticket_id=None, eligible=True, attempted=True,
                              result="success", decision="called",
                              evidence=dict(affected_repo_roots=[root], function_count=120,
                                            threshold=50, rg_available=True,
                                            cheap_available=True))
    assert coverage.ticket_enumeration_debt(
        [debt_no_ticket, clearing_no_ticket], meta, out_dir=tmp_path) == 1


def test_scope_covers_rejects_empty_and_non_string():
    """回归：空/非字符串范围不得被解释为全仓。"""
    assert coverage.scope_covers("", "src") is False
    assert coverage.scope_covers(".", "") is False
    assert coverage.scope_covers("   ", ".") is False
    assert coverage.scope_covers(None, "src") is False
    assert coverage.scope_covers("src", None) is False


def test_undeclared_clearing_scope_cannot_clear_known_debt_scope(tmp_path):
    """P1 回归：清偿/复检记录未声明范围时实际范围未知——不得清偿任何范围债务
    （债务侧仍按全仓保守要求覆盖）。"""
    meta = dict(mcp_gate_contract_version=2, mcp_gate_schema_version=2, ticket_id="t",
                project_path=str(tmp_path))
    root = str(tmp_path)
    debt = dict(schema_version=2, ticket_id="t", step="review", gate_id="review.dedup",
                tool="extract", at="2026-11-17T06:00:00Z", eligible=True, attempted=False,
                result="unavailable", decision="unavailable_before_call",
                evidence=dict(affected_repo_roots=[root], function_count=0, threshold=50,
                              rg_available=False, cheap_available=False,
                              enumeration_unverified=True,
                              repo_review_scopes={root: ["other"]}))
    undeclared_called = dict(schema_version=2, ticket_id="t", step="deepcheck",
                             gate_id="deepcheck.dedup", tool="extract",
                             at="2026-11-17T08:00:00Z", eligible=True, attempted=True,
                             result="success", decision="called",
                             evidence=dict(mode="full", phase="dedup",
                                           affected_repo_roots=[root],
                                           function_count=120, threshold=50))
    # 未声明范围的成功记录 → 不清偿（审查复现场景）。
    assert coverage.ticket_enumeration_debt([debt, undeclared_called], meta,
                                            out_dir=tmp_path) == 1
    # 显式声明不相关范围 → 不清偿。
    unrelated = dict(undeclared_called, evidence=dict(
        undeclared_called["evidence"],
        repo_review_scopes={root: ["src"]}))
    assert coverage.ticket_enumeration_debt([debt, unrelated], meta,
                                            out_dir=tmp_path) == 1
    # 显式声明覆盖范围 → 清偿。
    covering = dict(undeclared_called, evidence=dict(
        undeclared_called["evidence"],
        repo_review_scopes={root: ["."]}))
    assert coverage.ticket_enumeration_debt([debt, covering], meta,
                                            out_dir=tmp_path) == 0
    # review 重做同样必须显式声明范围。
    redo_undeclared = dict(schema_version=2, ticket_id="t", step="review",
                           gate_id="review.dedup", tool="extract",
                           at="2026-11-17T07:00:00Z", eligible=True, attempted=True,
                           result="success", decision="called",
                           evidence=dict(affected_repo_roots=[root], function_count=120,
                                         threshold=50, rg_available=True,
                                         cheap_available=True))
    assert coverage.ticket_enumeration_debt([debt, redo_undeclared], meta,
                                            out_dir=tmp_path) == 1
