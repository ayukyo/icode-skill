#!/usr/bin/env python3
"""
lint_thinking_gate.py —— reasoning gate（分级思考）运行时校验器

按 ICODE_SEQUENTIAL_THINKING_OPTIMIZATION.md §7「thinking gate trace」实施：
- 不再以「某 step 是否出现 sequential-thinking 字符串 / 调用 ≥3 次」作为思考合规证据；
- 改为读取工单运行痕迹做机器可校验审计：
    * .ico_metadata.json            —— ticket 状态 / completed_steps / mode / patch 范围
    * .thinking_gate_trace.jsonl    —— 每个 step 的最终分级判定记录（可机读）
    * mcp/reasoning-gate/gates.json —— reasoning gate 机器真源（默认等级/升级触发器只从这里读）

分级模型（L0~L3）：
- L0 确定性执行：机制 = deterministic_checks，不调用 sequential-thinking。
- L1 简短决策：机制 = decision_record（写 .decision_anchors.json 决策摘要），不调用 sequential-thinking。
- L2 复杂推理：机制 = sequential-thinking（3~5 步），attempted=true，result=success|degraded。
- L3 高风险对抗：机制 = sequential-thinking+adversarial，L2 基础上加独立对抗验证。

判定模型：
- 每个 in-scope 且 requires_trace=true 的 step 必须有最终 trace 行；没有 = missing（违规）。
- tier 必须 >= default_tier（默认等级只是起点，只能向上升级，不能仅凭命令名向下降级）。
- tier > default_tier 时 triggers 必须非空且全部来自 catalog 升级触发器枚举（禁止自然语言扩张）。
- L2/L3 必须 mechanism ∈ {sequential-thinking, sequential-thinking+adversarial} 且 attempted=true；
  result=degraded 时必须有 degraded_reason（真实调用失败证据），不能伪造 attempted=true。
- L3 必须 mechanism=sequential-thinking+adversarial（独立对抗者，不能自问自答替代）。
- L0/L1 出现 sequential-thinking 调用：记录 over_invoked=true（灰度观察项），默认不阻断；
  --strict 时升级为阻断（灰度收敛后启用）。
- trace 禁止保存：thought 正文、密钥、Cookie、设备凭据、大段日志正文。

退出码：
    0 = 通过（所有 in-scope requires_trace step 均合规）
    1 = 有违规（missing / tier 降级 / L2/L3 未履行 / 触发词非法 / schema 错误 / sensitive / strict 下 over_invoked）
    2 = 参数或目录错误
    旧工单（metadata 无 thinking_gate_schema_version）默认退出 0 并打印 legacy-untracked；
    --require-trace / --strict 时退出 1。

用法:
    python3 tools/lint_thinking_gate.py <out_dir>
    python3 tools/lint_thinking_gate.py <out_dir> --step review --strict
    python3 tools/lint_thinking_gate.py <out_dir> --json
"""
import sys
import json
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 合法词表（与 gates.json 保持一致；这里作为脚本内常量用于快速失败与可读性）
TIERS = {"L0", "L1", "L2", "L3"}
MECHANISMS = {
    "deterministic_checks",
    "decision_record",
    "sequential-thinking",
    "sequential-thinking+adversarial",
}
RESULTS = {"success", "degraded", "blocked"}
TIER_ORDER = ["L0", "L1", "L2", "L3"]

# 敏感数据扫描：禁止出现在 trace 中的键名/子串（不区分大小写）
SENSITIVE_KEYWORDS = [
    "api_key", "apikey", "cookie", "password", "passwd",
    "secret", "authorization", "bearer",
]
# thought 正文禁止进入 trace：显式禁止该字段名
FORBIDDEN_FIELDS = ["thought", "thought_text", "raw_thought", "chain_of_thought"]
# 单值超长视为"完整日志/正文"进入 trace（敏感/噪声，禁止）
MAX_EVIDENCE_VALUE_CHARS = 2048
MAX_LINE_CHARS = 4096

REQUIRED_FIELDS = [
    "schema_version", "ticket_id", "step", "tier", "default_tier",
    "triggers", "mechanism", "attempted", "result", "degraded_reason",
    "over_invoked", "at",
]

# completed_steps 数字键 → 步骤名（与 catalog steps 键对应）
STEP_KEY_MAP = {
    "0": "init", "1": "plan", "2": "review", "3": "merge", "4": "code",
    "5": "deepcheck", "6": "audit", "log": "log",
}


def load_json(path: Path) -> Any:
    """读取并解析 JSON，失败抛 ValueError（由调用方转成报告）"""
    return json.loads(path.read_text(encoding="utf-8"))


def find_gates_catalog() -> Tuple[Optional[Dict], Optional[str]]:
    """定位 reasoning-gate 的 gates.json。返回 (data, error)"""
    candidates = [
        Path(__file__).resolve().parent.parent / "mcp" / "reasoning-gate" / "gates.json",
        Path.cwd() / "mcp" / "reasoning-gate" / "gates.json",
    ]
    for cand in candidates:
        if cand.exists():
            try:
                return load_json(cand), None
            except Exception as exc:  # noqa: BLE001
                return None, f"gates.json 解析失败: {exc}"
    return None, f"未找到 mcp/reasoning-gate/gates.json（查找位置: {candidates}）"


def read_trace_lines(out_dir: Path) -> Tuple[List[Dict], List[str]]:
    """读取 .thinking_gate_trace.jsonl。返回 (rows, errors)。文件不存在返回 ([], [])。"""
    trace_path = out_dir / ".thinking_gate_trace.jsonl"
    if not trace_path.exists():
        return [], []
    rows: List[Dict] = []
    errors: List[str] = []
    for lineno, raw in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) > MAX_LINE_CHARS:
            errors.append(
                f".thinking_gate_trace.jsonl:{lineno} 行长度 {len(raw)} > {MAX_LINE_CHARS} 字符（疑似完整正文/大数据块）"
            )
        try:
            obj = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            errors.append(f".thinking_gate_trace.jsonl:{lineno} JSON 解析失败: {exc}")
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            errors.append(f".thinking_gate_trace.jsonl:{lineno} 非对象行")
    return rows, errors


def tier_index(tier: str) -> int:
    """等级顺序索引；非法返回 -1。"""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


def step_in_scope(step: str, metadata: Dict, trace_rows: List[Dict]) -> bool:
    """判断 step 是否属于本工单生命周期（供无 --step 全量扫描用）。"""
    completed = metadata.get("completed_steps") or []
    if not isinstance(completed, list):
        completed = [str(completed)]
    completed = [str(x) for x in completed]
    if step == "patch":
        if metadata.get("patch_scoped") is True:
            return True
        return any(isinstance(r, dict) and str(r.get("step", "")) == "patch" for r in trace_rows)
    # 其余步骤：completed_steps 数字键命中即 in-scope
    for key, name in STEP_KEY_MAP.items():
        if name == step and key in completed:
            return True
    return False


def scan_sensitive(obj: Any, path: str = "$") -> List[str]:
    """递归扫描 trace 行内敏感数据与超长文本。返回违规描述列表。"""
    issues: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(kw in kl for kw in SENSITIVE_KEYWORDS):
                issues.append(f"{path}.{k} 命中敏感键名")
            if any(fwd in kl for fwd in FORBIDDEN_FIELDS):
                issues.append(f"{path}.{k} 命中禁止字段（thought 正文不得进 trace）")
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
        for fwd in FORBIDDEN_FIELDS:
            if fwd in low:
                issues.append(f"{path} 含禁止字段 '{fwd}'（thought 正文不得进 trace）")
                break
        if len(obj) > MAX_EVIDENCE_VALUE_CHARS:
            issues.append(f"{path} 单值 {len(obj)} 字符，疑似完整正文（> {MAX_EVIDENCE_VALUE_CHARS}）")
    return issues


def validate_row(row: Dict, catalog_step: Optional[Dict], catalog: Dict,
                 line_idx: int, strict: bool) -> Tuple[List[str], bool]:
    """校验单条 trace 行内部一致性。返回 (issues, over_invoked)。"""
    issues: List[str] = []
    over_invoked = False
    tag = f"trace[{line_idx}] {row.get('step')}"
    missing = [f for f in REQUIRED_FIELDS if f not in row]
    if missing:
        issues.append(f"{tag} 缺字段: {', '.join(missing)}")
        return issues, False  # 缺关键字段，无法继续

    tier = str(row["tier"])
    default_tier = str(row["default_tier"])
    mechanism = str(row["mechanism"])
    result = str(row["result"])

    if tier not in TIERS:
        issues.append(f"{tag} tier={tier} 不在合法词表 {sorted(TIERS)}")
    if default_tier not in TIERS:
        issues.append(f"{tag} default_tier={default_tier} 不在合法词表 {sorted(TIERS)}")
    if mechanism not in MECHANISMS:
        issues.append(f"{tag} mechanism={mechanism} 不在合法词表")
    if result not in RESULTS:
        issues.append(f"{tag} result={result} 不在合法词表 {sorted(RESULTS)}")
    if not isinstance(row.get("triggers"), list):
        issues.append(f"{tag} triggers 非数组")
    if not isinstance(row.get("attempted"), bool):
        issues.append(f"{tag} attempted 非布尔")
    if not isinstance(row.get("over_invoked"), bool):
        issues.append(f"{tag} over_invoked 非布尔")

    # 等级顺序：tier 必须 >= default_tier（默认等级只是起点，只能升级不能降级）
    ti, di = tier_index(tier), tier_index(default_tier)
    if ti >= 0 and di >= 0:
        if ti < di:
            issues.append(f"{tag} tier={tier} 低于 default_tier={default_tier}（不能仅凭命令名向下降级）")
        elif ti > di:
            triggers = row.get("triggers")
            if not triggers:
                issues.append(f"{tag} tier 升级到 {tier} 但 triggers 为空（升级必须有确定性触发原因）")

    # 触发器词表校验（必须来自 catalog 稳定枚举）
    if isinstance(row.get("triggers"), list):
        valid_triggers = set()
        for bucket in catalog.get("escalation_triggers", {}).values():
            if isinstance(bucket, list):
                valid_triggers.update(bucket)
        for tr in row["triggers"]:
            if tr not in valid_triggers:
                issues.append(f"{tag} triggers 含非法枚举: {tr}（禁止随意自然语言扩张）")

    # 机制与等级匹配
    expected_mech = catalog.get("mechanisms_by_tier", {}).get(tier)
    if tier in ("L0", "L1") and mechanism in ("sequential-thinking", "sequential-thinking+adversarial"):
        over_invoked = True
        if strict:
            issues.append(f"{tag} {tier} 调用 sequential-thinking（over_invoked，--strict 升级为阻断）")
    else:
        if expected_mech and mechanism != expected_mech and tier in ("L2", "L3"):
            issues.append(f"{tag} tier={tier} 但 mechanism={mechanism}，应为 {expected_mech}")

    # L2/L3 履行要求
    if tier in ("L2", "L3"):
        if row.get("attempted") is not True:
            issues.append(f"{tag} {tier} 必须 attempted=true（不能伪造未调用的成功）")
        if result == "degraded":
            if row.get("attempted") is not True:
                issues.append(f"{tag} degraded 但 attempted != true")
            if not row.get("degraded_reason"):
                issues.append(f"{tag} degraded 但 degraded_reason 为空（必须有真实调用失败证据）")
        elif result == "success":
            if row.get("attempted") is not True:
                issues.append(f"{tag} success 但 attempted != true（伪造）")
        else:
            issues.append(f"{tag} {tier} result={result} 非法（应为 success/degraded）")
        if tier == "L3" and mechanism != "sequential-thinking+adversarial":
            issues.append(f"{tag} L3 必须 mechanism=sequential-thinking+adversarial（独立对抗者不能自问自答替代）")

    # L0/L1 结果合法值
    if tier in ("L0", "L1") and result not in ("success", "blocked"):
        issues.append(f"{tag} {tier} result={result} 非法（应为 success/blocked）")

    # over_invoked 一致性：L0/L1 + sequential-thinking 机制 → over_invoked 应为 true
    if over_invoked and row.get("over_invoked") is not True:
        issues.append(f"{tag} 机制含 sequential-thinking 但 over_invoked 未标记 true")

    # 与 catalog default_tier 一致性（step 在 catalog 内）
    if catalog_step:
        cat_tier = catalog_step.get("default_tier")
        if cat_tier and default_tier != cat_tier:
            issues.append(
                f"{tag} default_tier={default_tier} 与 gates.json 定义 {cat_tier} 不一致"
            )

    # 敏感数据 / 禁止字段
    issues.extend(scan_sensitive(row))
    return issues, over_invoked


def build_report(out_dir: Path, step_filter: Optional[str], legacy: bool,
                 metadata: Dict, trace_rows: List[Dict], catalog: Dict,
                 trace_errors: List[str], strict: bool) -> Dict:
    """构造校验报告。"""
    steps = catalog.get("steps", {})
    # 每个 step 取最后一条 trace 行作为当前状态（允许重跑追加）
    last_by_step: Dict[str, Dict] = {}
    for r in trace_rows:
        s = str(r.get("step", ""))
        if s:
            last_by_step[s] = r

    report = {
        "out_dir": str(out_dir),
        "ticket_id": metadata.get("ticket_id", ""),
        "legacy": legacy,
        "schema_version": metadata.get("thinking_gate_schema_version"),
        "total_steps_in_scope": 0,
        "missing": 0,
        "over_invoked": 0,
        "schema_errors": len(trace_errors),
        "sensitive_data": 0,
        "steps": [],
        "trace_errors": trace_errors,
    }

    for step, cfg in steps.items():
        if step_filter and step != step_filter:
            continue
        requires_trace = cfg.get("requires_trace", True)
        in_scope = step_filter is not None or step_in_scope(step, metadata, trace_rows)
        if not in_scope:
            continue
        if not requires_trace:
            # 只读命令无需 trace（status/list/help）
            continue
        report["total_steps_in_scope"] += 1
        ss = {
            "step": step,
            "default_tier": cfg.get("default_tier"),
            "requires_trace": True,
            "status": "missing",
            "trace_row": None,
            "issues": [],
            "over_invoked": False,
        }
        if step not in last_by_step:
            report["missing"] += 1
            ss["status"] = "missing"
            ss["issues"].append("in-scope requires_trace step 无最终 trace 行")
            report["steps"].append(ss)
            continue
        row = last_by_step[step]
        ss["trace_row"] = row
        issues, over = validate_row(row, cfg, catalog, 0, strict)
        ss["over_invoked"] = over
        if over:
            report["over_invoked"] += 1
        if issues:
            ss["status"] = "invalid"
            ss["issues"].extend(issues)
            report["schema_errors"] += 1
        else:
            ss["status"] = "fulfilled"
        report["steps"].append(ss)

    # 敏感数据计数（build_report 内 schema_errors 已含，这里单独汇总行级）
    sens = 0
    for s in report["steps"]:
        if any("敏感" in i or "疑似完整正文" in i or "禁止字段" in i for i in s.get("issues", [])):
            sens += 1
    report["sensitive_data"] = sens
    return report


def report_is_pass(report: Dict, legacy: bool, require_trace: bool) -> bool:
    """判定整体是否通过。"""
    if legacy and require_trace:
        return False
    return not (
        report["missing"] > 0
        or report["schema_errors"] > 0
        or report["sensitive_data"] > 0
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="lint icode 工单 reasoning gate（分级思考）覆盖率")
    ap.add_argument("out_dir", help="工单目录路径（如 demo/.icode_output/.icode_output_1）")
    ap.add_argument("--step", default=None, help="只校验指定 step 的分级判定")
    ap.add_argument("--strict", action="store_true", help="严格模式：旧工单无 schema 判失败 + L0/L1 over_invoked 升级为阻断")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--require-trace", action="store_true", help="旧工单无 schema 时判失败")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_dir():
        print(f"❌ 目录不存在: {out_dir}", file=sys.stderr)
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

    legacy = "thinking_gate_schema_version" not in metadata
    trace_rows, trace_errors = read_trace_lines(out_dir)
    catalog, catalog_err = find_gates_catalog()
    if catalog is None:
        print(f"❌ {catalog_err}", file=sys.stderr)
        return 2

    report = build_report(out_dir, args.step, legacy, metadata, trace_rows,
                          catalog, trace_errors, args.strict)

    require = args.require_trace or args.strict
    passed = report_is_pass(report, legacy, require)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if legacy:
            return 1 if require else 0
        return 0 if passed else 1

    # Markdown 报告
    print(f"\n# reasoning gate（分级思考）校验报告\n")
    print(f"工单目录: `{out_dir}`")
    print(f"ticket_id: {report['ticket_id'] or '-'} | schema: {report['schema_version'] or 'legacy-untracked'}")
    if legacy:
        print("⚠️ legacy-untracked：旧工单无 thinking_gate_schema_version，默认不阻断；--require-trace/--strict 时阻断")
    print("")
    print("| step | default | tier | mechanism | attempted | result | over_invoked | 问题 |")
    print("|------|---------|------|-----------|-----------|--------|--------------|------|")
    for s in report["steps"]:
        row = s["trace_row"] or {}
        issues = "; ".join(s["issues"]) if s["issues"] else "-"
        print(f"| {s['step']} | {s['default_tier']} | {row.get('tier', '-')} | {row.get('mechanism', '-')} | {row.get('attempted', '-')} | {row.get('result', '-')} | {s['over_invoked']} | {issues} |")
    print("")
    print(f"in-scope steps (requires_trace): {report['total_steps_in_scope']}")
    print(f"missing: {report['missing']} | schema_errors: {report['schema_errors']} | sensitive_data: {report['sensitive_data']}")
    print(f"over_invoked（灰度观察项，默认不阻断）: {report['over_invoked']}")
    for terr in report.get("trace_errors", []):
        print(f"  ⚠️ trace 行错误: {terr}")

    if legacy:
        return 1 if require else 0
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
