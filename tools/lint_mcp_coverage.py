#!/usr/bin/env python3
"""
lint_mcp_coverage.py —— cheap-research gate 运行时校验器（v2 重构）

按 ICODE_CHEAP_RESEARCH_EXECUTION_GATE_OPTIMIZATION.md 重构：
- 不再要求正式产物存在旧版「MCP 调用段」（正式产物不记录 MCP 调用信息）。
- 不再通过文件中是否出现 `sequential-thinking` 字符串判断真实调用。
- 改为读取工单运行痕迹做机器可校验审计：
    * .ico_metadata.json          —— ticket 状态 / completed_steps / mode / patch 范围
    * .mcp_gate_trace.jsonl       —— 每个 gate 的最终判定记录（可机读）
    * mcp/cheap-research/gates.json —— gate 机器真源（阈值只从这里读）

判定模型：
- 每个 gate 若属于本工单生命周期（按 completed_steps / mode / patch 标志确定 in-scope），
  则必须有最终 trace 行；没有 = missing_gate（违规）。
- eligible=true  的 gate 只允许 decision ∈ {called, cache_hit, degraded_after_attempt}；
  用了 skip 类 decision = invalid_skip（独立计数，进入 coverage 分母，违规）。
  degraded_after_attempt 必须 attempted=true 且 result ∈ {error, empty, timeout}。
- eligible=false 的 gate 只允许 decision ∈ {skipped_not_eligible, skipped_stage_not_reached}，
  且必须带结构化 evidence（不能只写自然语言"没必要"）。
- invalid_skip / missing / schema 错误 / sensitive 均为失败条件；coverage = fulfilled / eligible，
  分母含 invalid_skip 行（不合格履行不得伪装成 100%）。
- trace 内禁止出现敏感数据（api_key/cookie/password/secret/authorization 等）与大段日志正文。

退出码：
    0 = 通过（所有 in-scope eligible gate 均 fulfilled）
    1 = 有违规（missing / invalid / degraded-without-attempt / trace schema error / sensitive）
    2 = 参数或目录错误
    旧工单（metadata 无 mcp_gate_schema_version）默认退出 0 并打印 legacy-untracked；
    --require-trace / --strict 时退出 1。

用法:
    python3 tools/lint_mcp_coverage.py <out_dir>
    python3 tools/lint_mcp_coverage.py <out_dir> --step review --strict
    python3 tools/lint_mcp_coverage.py <out_dir> --json
"""
import sys
import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# 合法 decision 词表（与优化方案 §7.2 一致）
DECISIONS_ELIGIBLE_TRUE = {"called", "cache_hit", "degraded_after_attempt"}
DECISIONS_ELIGIBLE_FALSE = {"skipped_not_eligible", "skipped_stage_not_reached"}
DEGRADED_RESULTS = {"error", "empty", "timeout"}

# 敏感数据扫描：禁止出现在 trace 中的键名（不区分大小写，子串匹配）
SENSITIVE_KEYWORDS = [
    "api_key", "apikey", "cookie", "password", "passwd",
    "secret", "authorization", "bearer",
]
# 单值超长视为"完整日志正文"进入 trace（敏感/噪声，禁止）
MAX_EVIDENCE_VALUE_CHARS = 2048
MAX_LINE_CHARS = 4096

REQUIRED_FIELDS = [
    "schema_version", "ticket_id", "step", "gate_id", "tool",
    "eligible", "evidence", "decision", "attempted", "result", "at",
]


def load_json(path: Path) -> Any:
    """读取并解析 JSON，失败抛 ValueError（由调用方转成报告）"""
    return json.loads(path.read_text(encoding="utf-8"))


def find_gates_catalog() -> Tuple[Optional[Dict], Optional[str]]:
    """定位 gates.json。优先相对仓库根，其次脚本所在目录上级。返回 (data, error)"""
    candidates = [
        Path(__file__).resolve().parent.parent / "mcp" / "cheap-research" / "gates.json",
        Path.cwd() / "mcp" / "cheap-research" / "gates.json",
    ]
    for cand in candidates:
        if cand.exists():
            try:
                return load_json(cand), None
            except Exception as exc:  # noqa: BLE001
                return None, f"gates.json 解析失败: {exc}"
    return None, f"未找到 mcp/cheap-research/gates.json（查找位置: {candidates})"


def read_trace_lines(out_dir: Path) -> Tuple[List[Dict], List[str]]:
    """读取 .mcp_gate_trace.jsonl。返回 (rows, errors)。文件不存在返回 ([], [])。"""
    trace_path = out_dir / ".mcp_gate_trace.jsonl"
    if not trace_path.exists():
        return [], []
    rows: List[Dict] = []
    errors: List[str] = []
    for lineno, raw in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) > MAX_LINE_CHARS:
            # 单行超长 = 疑似完整日志/大数据块塞进 trace（违反"不保存完整日志正文"）
            errors.append(
                f".mcp_gate_trace.jsonl:{lineno} 行长度 {len(raw)} > {MAX_LINE_CHARS} 字符（疑似完整日志/大数据块）"
            )
        try:
            obj = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            errors.append(f".mcp_gate_trace.jsonl:{lineno} JSON 解析失败: {exc}")
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            errors.append(f".mcp_gate_trace.jsonl:{lineno} 非对象行")
    return rows, errors


def step_in_scope(step: str, metadata: Dict, trace_rows: List[Dict]) -> bool:
    """判断 gate 是否属于本工单生命周期。"""
    completed = metadata.get("completed_steps") or []
    if not isinstance(completed, list):
        completed = [str(completed)]
    completed = [str(x) for x in completed]
    if step == "log":
        return "log" in completed
    if step == "review":
        return "2" in completed
    if step == "merge":
        return "3" in completed
    if step == "deepcheck":
        return "5" in completed
    if step == "audit":
        return "6" in completed
    if step == "patch":
        if metadata.get("patch_scoped") is True:
            return True
        # 或 trace 中已有 patch gate 记录
        return any(isinstance(r, dict) and str(r.get("gate_id", "")).startswith("patch.")
                   for r in trace_rows)
    return True


def scan_sensitive(obj: Any, path: str = "$") -> List[str]:
    """递归扫描 trace 行内敏感数据与超长文本。返回违规描述列表。"""
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
            issues.append(f"{path} 单值 {len(obj)} 字符，疑似完整日志正文（> {MAX_EVIDENCE_VALUE_CHARS}）")
    return issues


def validate_row(row: Dict, catalog_gate: Optional[Dict], line_idx: int) -> List[str]:
    """校验单条 trace 行内部一致性，返回违规列表。"""
    issues: List[str] = []
    tag = f"trace[{line_idx}] {row.get('gate_id')}"
    missing = [f for f in REQUIRED_FIELDS if f not in row]
    if missing:
        issues.append(f"{tag} 缺字段: {', '.join(missing)}")
        return issues  # 缺关键字段，无法继续
    # 类型校验
    eligible_is_bool = isinstance(row["eligible"], bool)
    if not eligible_is_bool:
        issues.append(f"{tag} eligible 非布尔（无法判定，build_report 计 schema 错误）")
    if not isinstance(row["evidence"], dict):
        issues.append(f"{tag} evidence 非对象")
    if not isinstance(row["attempted"], bool):
        issues.append(f"{tag} attempted 非布尔")
    if type(row["schema_version"]) is not int or row["schema_version"] not in (1, 2):
        issues.append(f"{tag} schema_version 非受支持版本")
    if row.get("decision") in ("called", "cache_hit") and not row.get("evidence"):
        issues.append(f"{tag} 履行声明必须带非空evidence")
    decision = str(row["decision"])
    vocab_ok = decision in DECISIONS_ELIGIBLE_TRUE | DECISIONS_ELIGIBLE_FALSE
    if not vocab_ok:
        issues.append(f"{tag} decision={decision} 不在合法词表")
    # eligible 依赖的交叉校验仅在 eligible 确为布尔时进行；
    # eligible=true 但 decision 用了 skip 类 = 非法跳过（独立计数 invalid_skip，见 build_report）
    if eligible_is_bool:
        eligible = row["eligible"]
        if not eligible and vocab_ok and decision not in DECISIONS_ELIGIBLE_FALSE:
            issues.append(f"{tag} eligible=false 但 decision={decision} 非法")
        if not eligible and decision in DECISIONS_ELIGIBLE_FALSE:
            if not isinstance(row.get("evidence"), dict) or len(row["evidence"]) == 0:
                issues.append(f"{tag} eligible=false 必须带结构化 evidence")
    if decision == "degraded_after_attempt":
        if row.get("attempted") is not True:
            issues.append(f"{tag} degraded_after_attempt 但 attempted != true")
        if str(row.get("result")) not in DEGRADED_RESULTS:
            issues.append(f"{tag} degraded_after_attempt 但 result 不在 {sorted(DEGRADED_RESULTS)}")
    if decision in ("called", "cache_hit"):
        if str(row.get("result")) != "success":
            issues.append(f"{tag} {decision} 但 result != success")
    if decision == "called" and row.get("attempted") is not True:
        issues.append(f"{tag} called 但 attempted != true")
    if decision == "cache_hit":
        for field in ("cache_key", "input_digest"):
            if field == "input_digest" and row.get("schema_version") == 1 and field not in row:
                continue  # historical cache identity is reported as untracked
            if not isinstance(row.get(field), str) or re.fullmatch(r"[0-9a-f]{64}", row[field]) is None:
                issues.append(f"{tag} cache_hit 缺有效 {field} 输入身份")
    # 与 catalog gate 的 tool / step 一致性
    if catalog_gate:
        evidence = row.get("evidence")
        if isinstance(evidence, dict):
            fields = catalog_gate.get("evidence_fields", [])
            # A phase that was never entered has no result counts to report.
            if decision == "skipped_stage_not_reached":
                fields = [field for field in fields if field in ("mode", "phase")]
                if not fields or evidence.get("mode") != "fast" or evidence.get("phase") != "reverse":
                    issues.append(f"{tag} skipped_stage_not_reached 缺 fast 阶段依据")
            for field in fields:
                if field not in evidence:
                    if row.get("schema_version") == 2:
                        issues.append(f"{tag} evidence 缺 catalog 字段 {field}")
                    continue
                value = evidence[field]
                kind = catalog_gate.get("evidence_types", {}).get(field)
                valid = {
                    "boolean": lambda: isinstance(value, bool),
                    "integer": lambda: type(value) is int and value >= 0,
                    "string": lambda: isinstance(value, str) and bool(value.strip()),
                    "paths": lambda: isinstance(value, list) and all(isinstance(p, str) and p.strip() for p in value),
                }.get(kind)
                if valid is not None and not valid():
                    issues.append(f"{tag} evidence.{field} 类型/值不符合 {kind}")
        if str(row.get("tool")) != catalog_gate.get("tool"):
            issues.append(
                f"{tag} tool={row.get('tool')} 与 gates.json 定义 {catalog_gate.get('tool')} 不一致"
            )
        if str(row.get("step")) != catalog_gate.get("step"):
            issues.append(
                f"{tag} step={row.get('step')} 与 gates.json 定义 {catalog_gate.get('step')} 不一致"
            )
    # 敏感数据
    issues.extend(scan_sensitive(row))
    return issues


def eligibility_issues(row: Dict, gate: Dict, constants: Dict, metadata: Dict) -> Tuple[List[str], Optional[bool]]:
    """Recompute v2 deterministic conditions from typed facts, not a self-reported skip."""
    evidence = row["evidence"]
    gid = gate["id"]
    threshold_keys = {
        "log.comments_extract": "tb_comment_extract_min",
        "log.long_log_summary": "long_text_threshold_bytes",
        "review.dedup": "dedup_min_functions",
        "merge.cross_round_summary": "merge_min_rounds",
        "deepcheck.dedup": "dedup_min_functions",
        "patch.context_summary": "long_text_threshold_bytes",
        "patch.listen_log_summary": "long_text_threshold_bytes",
    }
    issues = []
    key = threshold_keys.get(gid)
    if key and (type(constants.get(key)) is not int or constants[key] < 0):
        return [f"{gid}: catalog 缺有效阈值 {key}"], None
    if key and "threshold" in evidence and evidence["threshold"] != constants[key]:
        issues.append(f"{gid}: evidence.threshold 与 catalog {key} 不一致")
    profile = metadata.get("risk_profile")
    effective = profile.get("effective_mode") if isinstance(profile, dict) else None
    if effective not in ("fast", "full"):
        effective = metadata.get("mode")
    if "mode" in evidence and effective in ("fast", "full") and evidence["mode"] != effective:
        issues.append(f"{gid}: evidence.mode 与有效模式 {effective} 不一致")
    if "mode" in evidence and evidence["mode"] not in ("fast", "full"):
        issues.append(f"{gid}: evidence.mode 非受支持模式")
    expected_phase = {"deepcheck.fixed_scan": "fixed", "deepcheck.dedup": "dedup"}.get(gid)
    if expected_phase and row["decision"] != "skipped_stage_not_reached" and evidence["phase"].lower() != expected_phase:
        issues.append(f"{gid}: evidence.phase 与门禁阶段 {expected_phase} 不一致")
    if row["decision"] == "skipped_stage_not_reached":
        expected = False  # validate_row restricts this to an unentered fast phase.
    elif gid == "log.comments_extract":
        expected = evidence["cheap_available"] and evidence["tb_comments_count"] >= constants[key]
    elif gid == "log.long_log_summary":
        expected = evidence["cheap_available"] and evidence["candidate_text_bytes"] >= constants[key]
    elif gid == "review.dedup":
        expected = bool(evidence["affected_repo_roots"]) and evidence["rg_available"] and evidence["cheap_available"] and evidence["function_count"] >= constants[key]
    elif gid == "review.result_summary":
        expected = bool(evidence["result_source"]) and evidence["review_rounds"] > 0
    elif gid == "merge.cross_round_summary":
        expected = evidence["review_rounds"] >= constants[key]
    elif gid == "deepcheck.fixed_scan":
        expected = evidence["mode"] == "full" and evidence["phase"].lower() == "fixed" and evidence["function_points_count"] > 0
    elif gid == "deepcheck.dedup":
        expected = evidence["mode"] == "full" and evidence["phase"].lower() == "dedup" and evidence["function_count"] >= constants[key]
    elif gid == "audit.repo_facts":
        expected = bool(evidence["affected_repo_roots"])
    elif gid == "audit.plan_diff":
        expected = bool(evidence["plan_source"]) and bool(evidence["code_sources"])
    elif gid == "patch.context_summary":
        expected = evidence["cross_session"] and evidence["candidate_text_bytes"] >= constants[key]
    elif gid == "patch.listen_log_summary":
        expected = evidence["listen_mode"] and evidence["incremental_bytes"] >= constants[key]
    else:
        return issues + [f"{gid}: 未登记确定性 eligibility 条件"], None
    if expected != row["eligible"]:
        issues.append(f"{gid}: eligible 与结构化事实不一致（应为 {expected}）")
    return issues, expected


def build_report(out_dir: Path, step_filter: Optional[str], legacy: bool,
                 metadata: Dict, trace_rows: List[Dict], catalog: Dict,
                 trace_errors: List[str]) -> Dict:
    """构造校验报告。"""
    gates = catalog.get("gates", [])
    # 每个 gate 取最后一条 trace 行作为当前状态（允许重跑追加）
    last_by_gate: Dict[str, Dict] = {}
    for r in trace_rows:
        gid = str(r.get("gate_id", ""))
        if gid:
            last_by_gate[gid] = r

    report = {
        "out_dir": str(out_dir),
        "ticket_id": metadata.get("ticket_id", ""),
        "legacy": legacy,
        "schema_version": metadata.get("mcp_gate_schema_version"),
        "total_gates_in_scope": 0,
        "missing_gate": 0,
        "eligible": 0,
        "fulfilled": 0,
        "called": 0,
        "cache_hit": 0,
        "degraded_after_attempt": 0,
        "invalid_skip": 0,
        "skipped_not_eligible": 0,
        "skipped_stage_not_reached": 0,
        "schema_errors": len(trace_errors),
        "sensitive_data": 0,
        "coverage": 1.0,
        "evidence_untracked": 0,
        "trace_errors": trace_errors,
        "gates": [],
    }

    eligible_total = 0
    fulfilled_total = 0
    version = metadata.get("mcp_gate_schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        report["schema_errors"] += 1
        report["trace_errors"].append("metadata mcp_gate_schema_version 非受支持版本")
    for gate in gates:
        gid = gate["id"]
        gstep = gate.get("step", "")
        if step_filter and gstep != step_filter:
            continue
        # 显式 --step 时视为 in-scope（由调用方保证目标步骤已到达生命周期内；
        # 例如 cmd_transition 在 push completed_steps 之前运行 linter，
        # 若在此处复用 step_in_scope 会把本步骤 gate 判为 out-of-scope 而跳过——
        # 与 lint_thinking_gate 的 `step_filter is not None or step_in_scope(...)` 对齐）
        in_scope = step_filter is not None or step_in_scope(gstep, metadata, trace_rows)
        if not in_scope:
            continue
        report["total_gates_in_scope"] += 1
        gs = {
            "gate_id": gid,
            "step": gstep,
            "tool": gate.get("tool"),
            "in_scope": True,
            "status": "missing",
            "trace_row": None,
            "issues": [],
        }
        if gid not in last_by_gate:
            report["missing_gate"] += 1
            gs["status"] = "missing"
            gs["issues"].append("in-scope gate 无最终 trace 行")
            report["gates"].append(gs)
            continue
        row = last_by_gate[gid]
        gs["trace_row"] = row
        issues = validate_row(row, gate, 0)
        computed_eligible = None
        if not issues and row.get("schema_version") == 2:
            condition_issues, computed_eligible = eligibility_issues(row, gate, catalog.get("constants", {}), metadata)
            issues.extend(condition_issues)
        if type(version) is int and version >= 2 and row.get("schema_version") != 2:
            issues.append("新v2工单禁止降级为v1 trace规避证据合同")
        if row.get("ticket_id") != metadata.get("ticket_id", row.get("ticket_id")):
            issues.append("trace ticket_id与工单不一致")
        historical_evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        if row.get("schema_version") == 1 and (any(field not in historical_evidence
                for field in gate.get("evidence_fields", [])) or
                (row.get("decision") == "cache_hit" and not row.get("input_digest"))):
            report["evidence_untracked"] += 1
            gs["evidence_status"] = "historical_evidence_untracked"
        eligible = computed_eligible if computed_eligible is not None else row.get("eligible") if isinstance(row.get("eligible"), bool) else None
        decision = str(row.get("decision", ""))
        if eligible is True:
            report["eligible"] += 1
            eligible_total += 1
            if decision in DECISIONS_ELIGIBLE_TRUE:
                if issues:
                    # 结构违规（如 degraded-without-attempt / called 但 result!=success）
                    gs["status"] = "invalid"
                    gs["issues"].extend(issues)
                else:
                    fulfilled_total += 1
                    report["fulfilled"] += 1
                    gs["status"] = "fulfilled"
                    report[decision] += 1
            else:
                # eligible=true 却用了 skip 类 decision = 非法跳过（独立于 schema 错误计数）
                report["invalid_skip"] += 1
                gs["status"] = "invalid"
                gs["issues"].append(
                    f"eligible=true 但 decision={decision}（非法跳过，不得作为 skip）"
                )
                if issues:
                    gs["issues"].extend(issues)
        elif eligible is False:
            if issues:
                gs["status"] = "invalid"
                gs["issues"].extend(issues)
            elif decision == "skipped_not_eligible":
                report["skipped_not_eligible"] += 1
                gs["status"] = "skipped"
            elif decision == "skipped_stage_not_reached":
                report["skipped_stage_not_reached"] += 1
                gs["status"] = "skipped"
        else:
            gs["status"] = "invalid"
            gs["issues"].append("eligible 字段缺失或非布尔，无法判定")
            if issues:
                gs["issues"].extend(issues)
        if issues:
            report["schema_errors"] += 1
        report["gates"].append(gs)

    if eligible_total:
        report["coverage"] = round(fulfilled_total / eligible_total, 4)
    return report


def report_is_pass(report: Dict, legacy: bool, require_trace: bool) -> bool:
    """判定整体是否通过。"""
    if legacy and require_trace:
        return False
    return not (
        report["missing_gate"] > 0
        or report["invalid_skip"] > 0
        or report["schema_errors"] > 0
        or report["sensitive_data"] > 0
        or report["coverage"] < 1.0
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="lint icode 工单 cheap-research gate 覆盖率")
    ap.add_argument("out_dir", help="工单目录路径（如 demo/.icode_output/.icode_output_1）")
    ap.add_argument("--step", default=None, help="只校验指定 step 的 gate")
    ap.add_argument("--strict", action="store_true", help="严格模式：旧工单无 schema 也判失败")
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

    legacy = "mcp_gate_schema_version" not in metadata
    trace_rows, trace_errors = read_trace_lines(out_dir)
    catalog, catalog_err = find_gates_catalog()
    if catalog is None:
        print(f"❌ {catalog_err}", file=sys.stderr)
        return 2

    report = build_report(out_dir, args.step, legacy, metadata, trace_rows, catalog, trace_errors)

    # 敏感数据计数（build_report 内 schema_errors 已含部分，这里单独汇总行级）
    sens = 0
    for r in report["gates"]:
        if any("敏感" in i or "疑似完整日志正文" in i for i in r.get("issues", [])):
            sens += 1
    report["sensitive_data"] = sens

    require = args.require_trace or args.strict
    passed = report_is_pass(report, legacy, require)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if legacy:
            return 1 if require else 0
        return 0 if passed else 1

    # Markdown 报告
    print(f"\n# cheap-research gate 覆盖率检查报告\n")
    print(f"工单目录: `{out_dir}`")
    print(f"ticket_id: {report['ticket_id'] or '-'} | schema: {report['schema_version'] or 'legacy-untracked'}")
    if legacy:
        print("⚠️ legacy-untracked：旧工单无 mcp_gate_schema_version，默认不阻断；--require-trace/--strict 时阻断")
    print("")
    print("| gate | step | tool | status | eligible | decision | 问题 |")
    print("|------|------|------|--------|----------|----------|------|")
    for g in report["gates"]:
        row = g["trace_row"] or {}
        issues = "; ".join(g["issues"]) if g["issues"] else "-"
        print(f"| {g['gate_id']} | {g['step']} | {g['tool']} | {g['status']} | {row.get('eligible', '-')} | {row.get('decision', '-')} | {issues} |")
    print("")
    print(f"in-scope gates: {report['total_gates_in_scope']}")
    print(f"eligible: {report['eligible']} | fulfilled: {report['fulfilled']} | coverage: {report['coverage']}")
    print(f"called: {report['called']} | cache_hit: {report['cache_hit']} | degraded_after_attempt: {report['degraded_after_attempt']}")
    print(f"historical evidence_untracked: {report['evidence_untracked']}（工具coverage不等于语义审查覆盖）")
    print(f"skipped_not_eligible: {report['skipped_not_eligible']} | skipped_stage_not_reached: {report['skipped_stage_not_reached']}")
    print(f"missing_gate: {report['missing_gate']} | invalid_skip: {report['invalid_skip']} | schema_errors: {report['schema_errors']} | sensitive_data: {report['sensitive_data']}")
    for terr in report.get("trace_errors", []):
        print(f"  ⚠️ trace 行错误: {terr}")

    if legacy:
        return 1 if require else 0
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
