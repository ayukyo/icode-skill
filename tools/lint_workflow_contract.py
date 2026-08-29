#!/usr/bin/env python3
"""
lint_workflow_contract.py —— workflow gate（工作流硬门禁）运行时校验器

按 WORKFLOW_OPTIMIZATION_PROPOSAL.md §10「完成判据」实施 P0 四类硬门禁 + P1 生命周期验收：
- 语义决策门禁（semantic_decision_gate）：
    存在 semantic_decisions[].status != resolved（非 diagnosis-only）→ plan/code/patch 阻断。
    禁止以「最保守/最安全/通常如此」代替用户选择；诊断命令可结束但必须显式标 diagnosis-only。
- 身份变化影响门禁（identity_change_gate）：
    impact_contract.identity_change == true 且 completeness != complete → code/deploy/audit-verified 阻断。
- 需求增量强制升级（requirement_delta_escalation）：
    存在 severity=major 且 needs_replan=true 且未 resolved → 当前阶段阻断并路由回 plan/review；
    patch 不得静默吸收重大增量。
- 快速模式风险自动升级（fast_risk_upgrade）：
    metadata.mode == fast 且命中任一风险标志，但 risk_profile.effective_mode != full 且 override != true → 违规。
- 生命周期验收合同（acceptance_contract）：
    涉及权威状态/实体身份变化时，验收矩阵必须覆盖 required_phases × required_consumers 全部必填单元；
    只验证直接查询不能 delivery_verdict=verified。

机器真源：mcp/workflow-gate/gates.json（触发条件/阻断步骤/必填单元只从这里读，禁止脚本内各自写一套）。

兼容与迁移（两阶段）：
- 阶段一（默认提示模式）：旧工单缺新字段补默认值并标 legacy-untracked，只读审计不阻断；
- 阶段二（--strict 强制模式）：缺字段判失败。

退出码：
    0 = 通过（所有 in-scope gate 均无阻断）
    1 = 有阻断/违规（semantic 未解决 / identity 影响清单不完整 / major delta 未回流 /
        fast 风险未升级 / 验收矩阵不完整仍 verified / schema 错误）
    2 = 参数或目录错误
    旧工单（缺 workflow_gate_schema_version）默认退出 0 并打印 legacy-untracked；
    --strict 时退出 1。

用法:
    python3 tools/lint_workflow_contract.py <out_dir>
    python3 tools/lint_workflow_contract.py <out_dir> --step code --strict
    python3 tools/lint_workflow_contract.py <out_dir> --json
"""
import sys
import json
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 合法词表（与 gates.json 保持一致；脚本内常量仅用于快速失败与可读性）
STATUS_ENUM = {"resolved", "open", "pending", "rejected"}
IMPACT_STATUS = {"affected", "not-affected", "unassessed"}
COMPLETENESS = {"complete", "incomplete"}
DELTA_CLASSIFICATIONS = {
    "clarification_only", "a_now_with_evidence", "needs_user_confirm", "needs_replan",
}
ACCEPTANCE_PHASES = ["immediate", "converged", "restart", "replay"]
ACCEPTANCE_CONSUMERS = [
    "direct_query", "aggregate_selector", "persistent_reference", "external_projection",
]
FAST_RISK_TRIGGERS = [
    "cross_component", "persistent_identity_change", "dependency_migration",
    "async_observer_or_cache", "restart_replay_semantics", "external_consumer_change",
    "real_env_verification", "unresolved_semantic_decision", "major_requirement_delta",
]
# step → 需要检查的 gate 集合（gates.json blocked_steps 为真源；此处聚合便于一步判定）
STEP_GATES = {
    "plan": ["semantic_decision", "requirement_delta"],
    "code": ["semantic_decision", "identity_change", "requirement_delta"],
    "patch": ["semantic_decision", "requirement_delta"],
    "merge": ["requirement_delta"],
    "deploy": ["identity_change", "acceptance"],
    "audit-verified": ["identity_change", "acceptance"],
    "fast": ["fast_risk"],
}
ALL_GATES = ["semantic_decision", "identity_change", "requirement_delta", "fast_risk", "acceptance"]

MAX_EVIDENCE_VALUE_CHARS = 2048
MAX_LINE_CHARS = 4096
SENSITIVE_KEYWORDS = [
    "api_key", "apikey", "cookie", "password", "passwd",
    "secret", "authorization", "bearer",
]


def load_json(path: Path) -> Any:
    """读取并解析 JSON，失败抛 ValueError（由调用方转成报告）"""
    return json.loads(path.read_text(encoding="utf-8"))


def find_gates_catalog() -> Tuple[Optional[Dict], Optional[str]]:
    """定位 workflow-gate 的 gates.json。返回 (data, error)"""
    candidates = [
        Path(__file__).resolve().parent.parent / "mcp" / "workflow-gate" / "gates.json",
        Path.cwd() / "mcp" / "workflow-gate" / "gates.json",
    ]
    for cand in candidates:
        if cand.exists():
            try:
                return load_json(cand), None
            except Exception as exc:  # noqa: BLE001
                return None, f"gates.json 解析失败: {exc}"
    return None, f"未找到 mcp/workflow-gate/gates.json（查找位置: {candidates}）"


def scan_sensitive(obj: Any, path: str = "$") -> List[str]:
    """递归扫描 metadata 内敏感数据与超长文本。返回违规描述列表。"""
    issues: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(kw in kl for kw in SENSITIVE_KEYWORDS):
                issues.append(f"{path}.{k} 命中敏感键名")
            issues.extend(scan_sensitive(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            issues.extend(scan_sensitive(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        low = obj.lower()
        for kw in SENSITIVE_KEYWORDS:
            if kw in low:
                issues.append(f"{path} 含敏感词 '{kw}'")
                break
        if len(obj) > MAX_EVIDENCE_VALUE_CHARS:
            issues.append(f"{path} 单值 {len(obj)} 字符，疑似完整正文（> {MAX_EVIDENCE_VALUE_CHARS}）")
    return issues


def validate_semantic_decisions(metadata: Dict, catalog: Dict, blocking_impl: bool) -> List[str]:
    """语义决策门禁：存在未 resolved 决策 → plan/code/patch 阻断。

    blocking_impl=True 表示本次调用正进入实现步骤（--step plan/code/patch）——
    此时即使 diagnosis-only 也不得进入实现；False 表示全量扫描/诊断，diagnosis-only 允许结束（标「仅诊断」）。
    """
    issues: List[str] = []
    decisions = metadata.get("semantic_decisions", [])
    diagnosis_only = metadata.get("diagnosis_only", False)
    terminal = catalog["semantic_decision_gate"]["terminal_states"][0]
    if not isinstance(decisions, list):
        issues.append("semantic_decisions 非数组")
        return issues
    unresolved = False
    for i, d in enumerate(decisions):
        if not isinstance(d, dict):
            issues.append(f"semantic_decisions[{i}] 非对象")
            continue
        status = d.get("status")
        if status is None:
            issues.append(f"semantic_decisions[{i}] 缺 status 字段")
            continue
        if status not in STATUS_ENUM:
            issues.append(f"semantic_decisions[{i}] status={status} 不在合法词表 {sorted(STATUS_ENUM)}")
        if status != terminal:
            unresolved = True
            if blocking_impl or not diagnosis_only:
                issues.append(
                    f"semantic_decisions[{i}] status={status} != {terminal}"
                    f"（未解决语义决策，plan/code/patch 不得继续；禁止以'最保守/通常如此'代替用户选择）"
                )
            # 非阻断路径（全量扫描 + diagnosis-only）：本 gate 记为 pass，但标记仅诊断（由调用方记 warning）
        if not d.get("selected") and status == terminal:
            issues.append(f"semantic_decisions[{i}] status=resolved 但缺 selected（用户已明确选择必须写入结构化合同）")
    # diagnosis-only + 有未解决决策 + 非进入实现：通过但带 warning
    if unresolved and diagnosis_only and not blocking_impl and not issues:
        pass  # 由调用方附加 warning
    return issues


def validate_identity_change(metadata: Dict, catalog: Dict) -> List[str]:
    """身份变化影响门禁：identity_change=true 且 completeness!=complete → 阻断。"""
    issues: List[str] = []
    ic = metadata.get("impact_contract")
    if ic is None:
        # 未声明身份变化 → 不阻断（向后兼容）；若字段存在则必须完整
        return issues
    if not isinstance(ic, dict):
        issues.append("impact_contract 非对象")
        return issues
    identity_change = ic.get("identity_change", False)
    if not isinstance(identity_change, bool):
        issues.append("impact_contract.identity_change 非布尔")
        return issues
    if not identity_change:
        return issues  # 不涉及身份变化，无需影响清单
    completeness = ic.get("completeness")
    if completeness is None:
        issues.append("impact_contract.identity_change=true 但缺 completeness（必须声明 complete/incomplete）")
        return issues
    if completeness not in COMPLETENESS:
        issues.append(f"impact_contract.completeness={completeness} 不在合法词表 {sorted(COMPLETENESS)}")
        return issues
    if completeness != "complete":
        issues.append(
            f"impact_contract.identity_change=true 且 completeness={completeness}"
            f"（身份变化影响清单未填完整，禁止进入编码或部署）"
        )
        return issues
    # completeness=complete 时核对 9 维清单每项必答（不允许留空）
    checklist = catalog["identity_change_gate"]["checklist"]
    for item in checklist:
        key = item["key"]
        val = ic.get(key)
        if val is None:
            issues.append(f"impact_contract.{key} 缺影响结论（identity_change=true 且 completeness=complete 时 9 维每项必答）")
        elif isinstance(val, dict):
            st = val.get("status")
            if st is None:
                issues.append(f"impact_contract.{key} 缺 status")
            elif st not in IMPACT_STATUS:
                issues.append(f"impact_contract.{key}.status={st} 不在合法词表 {sorted(IMPACT_STATUS)}")
            if not val.get("evidence"):
                issues.append(f"impact_contract.{key} 缺 evidence（每项必答且必须带证据）")
        else:
            issues.append(f"impact_contract.{key} 应为对象 {{status, evidence}}")
    return issues


def validate_requirement_deltas(metadata: Dict, catalog: Dict) -> List[str]:
    """需求增量强制升级：severity=major 且 needs_replan=true 未 resolved → 阻断。"""
    issues: List[str] = []
    deltas = metadata.get("requirement_deltas", [])
    major = catalog["requirement_delta_escalation"]["major_severities"][0]
    if not isinstance(deltas, list):
        issues.append("requirement_deltas 非数组")
        return issues
    for i, d in enumerate(deltas):
        if not isinstance(d, dict):
            issues.append(f"requirement_deltas[{i}] 非对象")
            continue
        severity = d.get("severity")
        needs_replan = d.get("needs_replan", False)
        status = d.get("status", "open")
        classification = d.get("classification")
        if classification is not None and classification not in DELTA_CLASSIFICATIONS:
            issues.append(
                f"requirement_deltas[{i}].classification={classification}"
                f" 不在合法词表 {sorted(DELTA_CLASSIFICATIONS)}"
            )
        if severity == major and needs_replan and status != "resolved":
            issues.append(
                f"requirement_deltas[{i}] severity=major 且 needs_replan=true 且 status={status}"
                f"（重大增量未回流重定稿：必须 需求增量→更新范围合同→补齐语义决策→重建影响合同"
                f"→更新验收矩阵→重新评审 后才能继续实现；patch 不得静默吸收）"
            )
    return issues


def validate_fast_risk(metadata: Dict, catalog: Dict) -> List[str]:
    """快速模式风险自动升级：mode=fast 且命中风险但未升级/未 override → 违规。"""
    issues: List[str] = []
    mode = metadata.get("mode", "full")
    if mode != "fast":
        return issues
    rp = metadata.get("risk_profile")
    if rp is None:
        # fast 无 risk_profile：无风险声明视为无风险，但缺字段在 strict 下由外层补 legacy/强制判定
        return issues
    if not isinstance(rp, dict):
        issues.append("risk_profile 非对象")
        return issues
    triggers = rp.get("triggers", [])
    override = rp.get("override", False)
    effective_mode = rp.get("effective_mode")
    flags = rp.get("risk_flags") or {}
    hit = False
    if isinstance(flags, dict):
        hit = any(flags.get(t, False) for t in FAST_RISK_TRIGGERS)
    if isinstance(triggers, list):
        hit = hit or any(t in FAST_RISK_TRIGGERS for t in triggers)
    if hit:
        if effective_mode != catalog["fast_risk_upgrade"]["effective_mode_full"] and override is not True:
            issues.append(
                f"mode=fast 命中风险标志但 effective_mode={effective_mode}（应为 full）"
                f"且 override != true（未记录风险接受事实）——须自动升级 full 或用户显式 override 并记录接受"
            )
    return issues


def matrix_complete(ac: Dict, catalog: Dict) -> Tuple[bool, List[str]]:
    """验收矩阵完整性：必填 phases × consumers 全部有非空 cell。返回 (完整?, 缺失清单)。"""
    phases = catalog["constants"]["acceptance_phases"]
    consumers = catalog["constants"]["acceptance_consumers"]
    matrix = ac.get("matrix", [])
    if not isinstance(matrix, list):
        return False, ["acceptance_contract.matrix 非数组"]
    filled = set()
    for cell in matrix:
        if not isinstance(cell, dict):
            continue
        ph, cs = cell.get("phase"), cell.get("consumer")
        expected = cell.get("expected")
        if ph in phases and cs in consumers and expected:
            filled.add((ph, cs))
    missing = [(p, c) for p in phases for c in consumers if (p, c) not in filled]
    return (len(missing) == 0), [f"缺少必填单元 phase={p}, consumer={c}" for p, c in missing]


def validate_acceptance(metadata: Dict, catalog: Dict) -> List[str]:
    """生命周期验收合同：涉及权威状态/身份变化时矩阵必须完整；verified 要求矩阵完整。"""
    issues: List[str] = []
    ac = metadata.get("acceptance_contract")
    if ac is None:
        return issues  # 未声明生命周期场景 → 不强制（向后兼容）
    if not isinstance(ac, dict):
        issues.append("acceptance_contract 非对象")
        return issues
    lifecycle_scopes = catalog["acceptance_contract"]["lifecycle_scopes"]
    requires_lifecycle = ac.get("requires_lifecycle", False)
    ic = metadata.get("impact_contract") or {}
    identity_change = ic.get("identity_change", False) if isinstance(ic, dict) else False
    authoritative_change = ac.get("authoritative_state_change", False)
    in_scope = (
        requires_lifecycle or identity_change or authoritative_change
        or any(k in lifecycle_scopes for k in (ac.get("scope") or []))
    )
    if not in_scope:
        return issues
    complete, missing = matrix_complete(ac, catalog)
    if not complete:
        issues.append(
            "acceptance_contract 涉及生命周期/身份变化但验收矩阵不完整"
            f"（缺 {len(missing)} 个必填单元：{'；'.join(missing[:6])}"
            f"{'…' if len(missing) > 6 else ''}）——只验证直接查询不能标记完成"
        )
        return issues
    # 矩阵完整时仍校验 matrix 内 expected 只指向存活实体语义（空 expected 已在 matrix_complete 视为缺失）
    return issues


def build_report(out_dir: Path, step_filter: Optional[str], metadata: Dict,
                 catalog: Dict, legacy: bool, strict: bool) -> Dict:
    """构造校验报告。"""
    gates_to_check = STEP_GATES.get(step_filter, ALL_GATES) if step_filter else ALL_GATES
    report = {
        "out_dir": str(out_dir),
        "ticket_id": metadata.get("ticket_id", ""),
        "legacy": legacy,
        "schema_version": metadata.get("workflow_gate_schema_version"),
        "strict": strict,
        "blocked": 0,
        "warnings": 0,
        "gates": [],
        "sensitive_data": 0,
    }
    gate_runners = {
        "semantic_decision": (validate_semantic_decisions, "semantic_decision_gate"),
        "identity_change": (validate_identity_change, "identity_change_gate"),
        "requirement_delta": (validate_requirement_deltas, "requirement_delta_escalation"),
        "fast_risk": (validate_fast_risk, "fast_risk_upgrade"),
        "acceptance": (validate_acceptance, "acceptance_contract"),
    }
    blocking_impl = step_filter in ("plan", "code", "patch")
    for gate in gates_to_check:
        if gate not in gate_runners:
            continue
        fn, _cfg = gate_runners[gate]
        if gate == "semantic_decision":
            issues = fn(metadata, catalog, blocking_impl)
        else:
            issues = fn(metadata, catalog)
        # legacy：缺对应字段（未列入 metadata）且非 strict → 警告不阻断
        missing_field = None
        if gate == "semantic_decision" and "semantic_decisions" not in metadata:
            missing_field = "semantic_decisions"
        elif gate == "identity_change" and "impact_contract" not in metadata:
            missing_field = "impact_contract"
        elif gate == "requirement_delta" and "requirement_deltas" not in metadata:
            missing_field = "requirement_deltas"
        elif gate == "fast_risk" and "risk_profile" not in metadata:
            missing_field = "risk_profile"
        elif gate == "acceptance" and "acceptance_contract" not in metadata:
            missing_field = "acceptance_contract"
        if missing_field is not None:
            if legacy and not strict:
                report["warnings"] += 1
                report["gates"].append({
                    "gate": gate, "status": "legacy-untracked",
                    "issues": [], "warnings": [f"缺 {missing_field}（legacy-untracked，提示模式不阻断；--strict 阻断）"],
                })
                continue
            if strict:
                report["blocked"] += 1
                report["gates"].append({
                    "gate": gate, "status": "blocked",
                    "issues": [f"缺 {missing_field}（--strict 强制模式判失败）"], "warnings": [],
                })
                continue
        if issues:
            report["blocked"] += 1
            report["gates"].append({"gate": gate, "status": "blocked", "issues": issues, "warnings": []})
        else:
            warnings: List[str] = []
            # diagnosis-only：全量扫描下未解决语义决策允许结束，但必须显式标记「仅诊断」
            if (
                gate == "semantic_decision"
                and not blocking_impl
                and metadata.get("diagnosis_only") is True
                and any(
                    isinstance(d, dict) and d.get("status") != "resolved"
                    for d in metadata.get("semantic_decisions", [])
                )
            ):
                warnings.append("diagnosis-only：本轮只交付诊断结论（标记 '仅诊断'），不得进入实现阶段")
                report["warnings"] += 1
            report["gates"].append({"gate": gate, "status": "pass", "issues": [], "warnings": warnings})

    # 敏感数据扫描（只扫 metadata 相关合同字段，不扫全文正文）
    sens = 0
    for gate in report["gates"]:
        if gate["gate"] == "acceptance":
            continue
        for key in ("semantic_decisions", "impact_contract", "requirement_deltas", "risk_profile"):
            if key in metadata:
                sens += len(scan_sensitive(metadata[key]))
    report["sensitive_data"] = sens
    return report


def report_is_pass(report: Dict, legacy: bool, strict: bool) -> bool:
    """判定整体是否通过。"""
    if legacy and strict:
        return False
    return report["blocked"] == 0 and report["sensitive_data"] == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="lint icode 工单 workflow gate（工作流硬门禁）契约")
    ap.add_argument("out_dir", help="工单目录路径（如 demo/.icode_output/.workflow_sim/s1）")
    ap.add_argument("--step", default=None, help="只校验指定步骤门禁（plan/code/patch/merge/deploy/audit-verified/fast）")
    ap.add_argument("--strict", action="store_true", help="强制模式：旧工单缺新字段判失败")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_dir():
        print(f"❌ 目录不存在: {out_dir}", file=sys.stderr)
        return 2
    if args.step is not None and args.step not in STEP_GATES:
        print(f"❌ --step 非法: {args.step}（可选 {sorted(STEP_GATES)}）", file=sys.stderr)
        return 2

    metadata_path = out_dir / ".ico_metadata.json"
    if not metadata_path.exists():
        print(f"❌ 无 .ico_metadata.json: {metadata_path}", file=sys.stderr)
        return 2
    try:
        metadata = load_json(metadata_path)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ .ico_metadata.json 解析失败: {exc}", file=sys.stderr)
        return 2
    if not isinstance(metadata, dict):
        print("❌ .ico_metadata.json 非 JSON 对象", file=sys.stderr)
        return 2

    legacy = "workflow_gate_schema_version" not in metadata
    catalog, catalog_err = find_gates_catalog()
    if catalog is None:
        print(f"❌ {catalog_err}", file=sys.stderr)
        return 2

    report = build_report(out_dir, args.step, metadata, catalog, legacy, args.strict)
    passed = report_is_pass(report, legacy, args.strict)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if legacy and args.strict:
            return 1
        return 0 if passed else 1

    # Markdown 报告
    print(f"\n# workflow gate（工作流硬门禁）校验报告\n")
    print(f"工单目录: `{out_dir}`")
    print(f"ticket_id: {report['ticket_id'] or '-'} | schema: {report['schema_version'] or 'legacy-untracked'}")
    if legacy:
        print("⚠️ legacy-untracked：旧工单缺 workflow_gate_schema_version，默认不阻断；--strict 时阻断")
    print("")
    print("| gate | status | 问题 |")
    print("|------|--------|------|")
    for g in report["gates"]:
        issues = "; ".join(g["issues"]) if g["issues"] else ("; ".join(g["warnings"]) if g["warnings"] else "-")
        print(f"| {g['gate']} | {g['status']} | {issues} |")
    print("")
    print(f"blocked: {report['blocked']} | warnings: {report['warnings']} | sensitive_data: {report['sensitive_data']}")

    if legacy and args.strict:
        return 1
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
