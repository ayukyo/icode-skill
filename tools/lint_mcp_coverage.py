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
- 资格语义按合同版本解释（gates.json `contract_version` 为机器真源）：
    * trace 行可显式声明 `contract_version`（int 1|2）；缺省继承 metadata 的
      `mcp_gate_contract_version`（vNext 出生由 icode_control 写入）；两者皆无 = 合同 v1。
    * 合同 v1 = 旧语义：cheap_available/rg_available 参与 eligibility（保留历史记录的
      原始解释，不追溯改判存量工单）。
    * 合同 v2 = 新语义：eligible 只由业务事实（阈值/范围/轮次/模式）确定性计算；
      cheap_available 与工具暴露状态只决定 decision，不改变 eligible。达到业务阈值而
      工具不可用 → eligible=true + unavailable_before_call/degraded_after_attempt
      （或有效 cache_hit），禁止 skipped_not_eligible。review.dedup 声称低于阈值必须
      基于可信枚举（rg_available=true 或 enumeration_verified=true），枚举不可信时
      不得把去重记成业务不适用，须按 eligible=true 留痕并保留债务。
    * 合同 v2 工单（metadata=2）禁止把 trace 行降级为 contract_version=1。
- eligible=true  的 gate 只允许 decision ∈ {called, cache_hit, degraded_after_attempt, unavailable_before_call}；
  用了 skip 类 decision = invalid_skip（独立计数，进入 coverage 分母，违规）。
  degraded_after_attempt 必须 attempted=true 且 result ∈ {error, empty, timeout}。
  unavailable_before_call 必须 attempted=false、result=unavailable，并提供工具发现与降级证据。
  注意：本文件的 result 词表与 .thinking_gate_trace.jsonl 不同（后者对同一 decision 要求
  result=degraded，见 references/thinking_core.md「思考工具未暴露的 trace 合同」），
  两套 trace 不要互相套用取值。
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
import datetime
import hashlib
import importlib.util
import posixpath
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# 合法 decision 词表（与优化方案 §7.2 一致）
DECISIONS_ELIGIBLE_TRUE = {"called", "cache_hit", "degraded_after_attempt", "unavailable_before_call"}
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


_CONTROL_MODULE = None


def control_module():
    global _CONTROL_MODULE
    if _CONTROL_MODULE is None:
        spec = importlib.util.spec_from_file_location("readonly_control", Path(__file__).with_name("icode_control.py"))
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        _CONTROL_MODULE = loaded
    return _CONTROL_MODULE


def load_json(path: Path) -> Any:
    """读取并解析 JSON，失败抛 ValueError（由调用方转成报告）"""
    if path.name == ".ico_metadata.json":
        try:
            return control_module().read_metadata_readonly(path.parent)
        except control_module().ControlError as exc:
            raise ValueError(f"metadata blocked: {exc}") from exc
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
    if step == "verify":
        # Existing tickets did not track verify gates. New standalone attempts
        # are checked explicitly by step finish, without inventing old traces.
        return any(r.get("step") == "verify" for r in trace_rows)
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
    if "contract_version" in row and (type(row["contract_version"]) is not int
            or row["contract_version"] not in (1, 2)):
        issues.append(f"{tag} contract_version 非受支持合同版本")
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
    if decision == "unavailable_before_call":
        availability = row.get("availability")
        if row.get("attempted") is not False or row.get("result") != "unavailable":
            issues.append(f"{tag} unavailable_before_call 必须 attempted=false/result=unavailable")
        if not isinstance(availability, dict):
            issues.append(f"{tag} unavailable_before_call 缺工具可用性证据")
        else:
            for field in ("discovery_ref", "reason", "fallback", "evidence_ref"):
                if not isinstance(availability.get(field), str) or not availability[field].strip():
                    issues.append(f"{tag} availability 缺 {field}")
            reason = availability.get("reason")
            if reason == "policy_denied":
                if availability.get("tool_visible") is not True:
                    issues.append(f"{tag} policy_denied 要求工具可见")
                if not isinstance(availability.get("policy_ref"), str) or not availability["policy_ref"].strip():
                    issues.append(f"{tag} policy_denied 缺策略证据")
            elif reason in ("not_exposed", "discovery_failed"):
                if availability.get("tool_visible") is not False:
                    issues.append(f"{tag} 未暴露工具必须不可见")
            else:
                issues.append(f"{tag} availability.reason 非法")
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

            def check_field(field: str, required: bool) -> None:
                if field not in evidence:
                    if required and row.get("schema_version") == 2:
                        issues.append(f"{tag} evidence 缺 catalog 字段 {field}")
                    return
                value = evidence[field]
                kind = catalog_gate.get("evidence_types", {}).get(field)
                valid = {
                    "boolean": lambda: isinstance(value, bool),
                    "integer": lambda: type(value) is int and value >= 0,
                    "string": lambda: isinstance(value, str) and bool(value.strip()),
                    "paths": lambda: isinstance(value, list) and all(isinstance(p, str) and p.strip() for p in value),
                    "object": lambda: isinstance(value, dict),
                }.get(kind)
                if valid is not None and not valid():
                    issues.append(f"{tag} evidence.{field} 类型/值不符合 {kind}")

            for field in fields:
                check_field(field, True)
            # 可选证据字段（如 review.dedup 的 enumeration_verified）：出现才校验类型，
            # 不进必填清单，避免追溯性判非法存量 v2 行。
            for field in catalog_gate.get("optional_evidence_fields", []):
                check_field(field, False)
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


SUPPORTED_CONTRACTS = (1, 2)
# 「枚举未知、保留债务」状态唯一允许的留痕 decision：未解决债务不得伪装成
# 工具成功（called/cache_hit 需要可信枚举），更不得 skip。
ENUMERATION_DEBT_DECISIONS = {"unavailable_before_call", "degraded_after_attempt"}


def parse_iso_timestamp(value: Any) -> Optional["datetime.datetime"]:
    """解析 ISO-8601 时刻；naive 值按本地时区解释（与控制面 now_iso 的事件时间
    同机同约定——trace 写入者与控制面共用同一台机器时钟），无法解析返回 None。
    trace 合同不强制带时区，因此无时区记录必须有确定解释：按 UTC 会把本地时间
    平移一个时区偏移，导致采纳边界两侧的记录被误判（UTC+8 误判 v2 / UTC-8 误判
    v1）；按本地解释与控制面事件时间一致，历史与续跑记录的判定在两种环境都正确。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def row_contract(row: Dict, metadata: Dict) -> int:
    """解析一条 trace 行适用的资格合同版本。

    行内显式 `contract_version` 优先（旧工单续跑按新合同真实重做时逐行声明）；
    缺省继承 metadata `mcp_gate_contract_version`（vNext 出生写入；存量工单续跑由
    控制面在下一次 metadata 写入时采纳并记录 `mcp_gate_contract_adopted_at`）。
    历史边界取 `mcp_gate_contract_history_boundary`（采纳时若存在升级前未终结的
    attempt，则回退到其最早 start 时刻，覆盖升级后、采纳前写入的 trace），缺省为
    采纳时刻：边界之后的行（含无法解析时间的行，保守取当前合同）按 metadata 合同
    解释，边界之前的历史行保留合同 v1 原始解释，不依赖写流程记得标注版本号。
    两者皆无 = 合同 v1 旧语义。
    """
    row_version = row.get("contract_version")
    if type(row_version) is int and row_version in SUPPORTED_CONTRACTS:
        return row_version
    metadata_version = metadata.get("mcp_gate_contract_version")
    if type(metadata_version) is int and metadata_version in SUPPORTED_CONTRACTS:
        adopted_at = parse_iso_timestamp(metadata.get("mcp_gate_contract_adopted_at"))
        if adopted_at is not None:
            boundary = parse_iso_timestamp(metadata.get("mcp_gate_contract_history_boundary"))
            if boundary is None:
                boundary = adopted_at
            row_at = parse_iso_timestamp(row.get("at"))
            if row_at is not None and row_at < boundary:
                return 1  # 历史边界之前的历史记录：保留原始解释，不追溯改判。
        return metadata_version
    return 1


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
        "verify.listen_log_summary": "long_text_threshold_bytes",
    }
    issues = []
    key = threshold_keys.get(gid)
    if key and (type(constants.get(key)) is not int or constants[key] < 0):
        return [f"{gid}: catalog 缺有效阈值 {key}"], None
    if key and "threshold" in evidence and evidence["threshold"] != constants[key]:
        issues.append(f"{gid}: evidence.threshold 与 catalog {key} 不一致")
    profile = metadata.get("risk_profile")
    effective = profile.get("effective_mode") if isinstance(profile, dict) else None
    if isinstance(profile, dict) and profile.get("policy_version") == 2:
        spec = importlib.util.spec_from_file_location("coverage_fast_policy", Path(__file__).with_name("fast_policy.py"))
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        effective = policy.effective_mode(metadata)
    elif effective not in ("fast", "full"):
        effective = metadata.get("mode")
    if "mode" in evidence and effective in ("fast", "full") and evidence["mode"] != effective:
        issues.append(f"{gid}: evidence.mode 与有效模式 {effective} 不一致")
    if "mode" in evidence and evidence["mode"] not in ("fast", "full"):
        issues.append(f"{gid}: evidence.mode 非受支持模式")
    expected_phase = {"deepcheck.fixed_scan": "fixed", "deepcheck.dedup": "dedup"}.get(gid)
    if expected_phase and row["decision"] != "skipped_stage_not_reached" and evidence["phase"].lower() != expected_phase:
        issues.append(f"{gid}: evidence.phase 与门禁阶段 {expected_phase} 不一致")
    # 合同版本解析与降级防护独立于 eligibility 分支（不占用 if/elif 链，保证 expected 必然计算）。
    contract = row_contract(row, metadata)
    if metadata.get("mcp_gate_contract_version") == 2 and row.get("contract_version") == 1:
        issues.append(f"{gid}: 合同 v2 工单禁止将 trace 行降级为 contract_version=1")
    if row["decision"] == "skipped_stage_not_reached":
        expected = False  # validate_row restricts this to an unentered fast phase.
    # 合同 v2：eligible = 业务义务资格（阈值/范围/轮次/模式），与 deepcheck.dedup、step 正文、
    # inspection_coverage 的阈值独立核验一致。cheap_available/rg_available 只是必须记录的
    # 可用性/枚举事实，决定 decision 而非 eligibility：达阈值而工具不可用 → eligible=true +
    # unavailable_before_call/degraded_after_attempt（或有效 cache_hit），禁止 skipped_not_eligible。
    # 合同 v1：保留旧语义（cheap_available/rg_available 参与 eligibility），存量历史记录不追溯改判。
    elif gid == "log.comments_extract":
        expected = evidence["tb_comments_count"] >= constants[key]
        if contract < 2:
            expected = expected and bool(evidence.get("cheap_available"))
    elif gid == "log.long_log_summary":
        expected = evidence["candidate_text_bytes"] >= constants[key]
        if contract < 2:
            expected = expected and bool(evidence.get("cheap_available"))
    elif gid == "review.dedup":
        below_threshold = evidence["function_count"] < constants[key]
        roots_present = bool(evidence["affected_repo_roots"])
        expected = roots_present and not below_threshold
        if contract < 2:
            # 合同 v1：工具可用性参与资格（历史解释）。
            expected = expected and evidence["rg_available"] and evidence["cheap_available"]
        elif evidence.get("enumeration_unverified") is True:
            # 「枚举未知、保留债务」状态与尝试值是否达到阈值是两个独立条件：枚举
            # 不可信时资格无法从函数数推导，业务义务推定成立（expected=true）；
            # 无论尝试值高低，都只能以 unavailable_before_call/degraded_after_attempt
            # 留痕并保留债务——called/cache_hit 宣称成功需可信枚举，skip 类非法。
            expected = True
            if row["decision"] not in ENUMERATION_DEBT_DECISIONS:
                issues.append(
                    f"{gid}: enumeration_unverified=true 债务状态只能以 "
                    "unavailable_before_call/degraded_after_attempt 留痕并保留枚举/去重债务，"
                    f"decision={row['decision']} 非法")
        elif below_threshold and roots_present:
            # 合同 v2：声称低于阈值必须基于可信枚举——ripgrep 可用，或替代枚举已核验。
            if not evidence.get("rg_available") and not evidence.get("enumeration_verified"):
                issues.append(
                    f"{gid}: rg_available=false 且无 enumeration_verified，function_count 未经受支持枚举核验，"
                    "不能据此判业务不适用；枚举不可用时写 enumeration_unverified=true 并按 eligible=true 以 "
                    "unavailable_before_call/degraded_after_attempt 留痕并保留枚举/去重债务")
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
    elif gid in ("patch.listen_log_summary", "verify.listen_log_summary"):
        expected = evidence["listen_mode"] and evidence["incremental_bytes"] >= constants[key]
    else:
        return issues + [f"{gid}: 未登记确定性 eligibility 条件"], None
    if expected != row["eligible"]:
        issues.append(f"{gid}: eligible 与结构化事实不一致（应为 {expected}）")
    return issues, expected


def normalize_scope_text(scope: str) -> str:
    """审查范围写法归一：统一分隔符、折叠 ./ 与 ../、去尾斜杠。"""
    text = scope.strip().replace("\\", "/")
    normalized = posixpath.normpath(text)
    return "." if normalized in ("", ".") else normalized


def scope_covers(covering: str, required: str) -> bool:
    """covering 范围是否覆盖 required 范围（目录包含关系）。

    全仓 "." 覆盖任意范围内路径；祖先目录覆盖其后代（"src" 覆盖 "src/lib"）；
    相等覆盖。越界路径（以 ".." 开头）与同层不相关路径不覆盖。
    """
    if not isinstance(covering, str) or not isinstance(required, str) \
            or not covering.strip() or not required.strip():
        return False  # 空/非字符串范围非法，不得解释为全仓
    covering, required = normalize_scope_text(covering), normalize_scope_text(required)
    if covering.startswith("..") or required.startswith(".."):
        return False  # 越界路径不构成覆盖
    if required == ".":
        return covering == "."
    if covering == ".":
        return True
    return required == covering or required.startswith(covering + "/")


def scopes_cover(covering_scopes, required_scopes) -> bool:
    """required 的每个范围都被 covering 中某一范围覆盖。"""
    return all(any(scope_covers(c, r) for c in covering_scopes) for r in required_scopes)


def row_belongs_to_ticket(row: Dict, metadata: Dict) -> bool:
    """trace 行是否可能属于本工单。

    只有「行与 metadata 都有 ticket_id 且不相等」才是明确他单；缺失任一侧时
    无法排除归属（保守：不漏记债务）。
    """
    row_ticket, meta_ticket = row.get("ticket_id"), metadata.get("ticket_id")
    if isinstance(row_ticket, str) and row_ticket and isinstance(meta_ticket, str) and meta_ticket:
        return row_ticket == meta_ticket
    return True


def has_enumeration_debt_state(row: Dict, metadata: Dict) -> bool:
    """该 trace 行是否携带「枚举未知、保留债务」状态（当前合同下的 review.dedup）。

    不约束 eligible/decision：非法组合（如 cache_hit）由资格校验单独判非法，
    但债务状态本身仍要在报告中可见。明确他单行不计入；缺 ticket_id 的行无法
    排除归属，按本工单计入（保守不漏债）。
    """
    evidence = row.get("evidence")
    return (row.get("schema_version") == 2 and row.get("gate_id") == "review.dedup"
            and row_belongs_to_ticket(row, metadata)
            and isinstance(evidence, dict)
            and evidence.get("enumeration_unverified") is True
            and row_contract(row, metadata) >= 2)


def gate_row_is_valid_final(row: Dict, gate: Dict, constants: Dict, metadata: Dict) -> bool:
    """该行是否为通过资格与证据校验的有效最终记录（只有它才能清偿此前债务）。

    工单身份（ticket_id 与 metadata 一致）、结构校验（validate_row）、资格重算
    一致（eligibility_issues）、eligible/decision 组合合法四者齐备才算有效；
    pending/中断/非法行不改写债务状态。仅接受当前 schema v2 行——v1 行不满足
    当前证据合同，不得作为清偿凭据。
    """
    if row.get("schema_version") != 2:
        return False
    if not row_belongs_to_ticket(row, metadata):
        return False  # 明确他单记录不得清偿本工单债务
    meta_ticket = metadata.get("ticket_id")
    if isinstance(meta_ticket, str) and meta_ticket and row.get("ticket_id") != meta_ticket:
        return False  # 本单已声明身份时，缺 ticket_id 的行不得作为清偿凭据
    if validate_row(row, gate, 0):
        return False
    if row.get("eligible") is True and str(row.get("decision")) in DECISIONS_ELIGIBLE_FALSE:
        return False
    cond_issues, expected = eligibility_issues(row, gate, constants, metadata)
    if cond_issues:
        return False
    return expected == row.get("eligible")


def review_manual_provenance(out_dir: Path, metadata: Dict, attempt: str,
                             target: Path, report_bytes: bytes) -> Optional[List[str]]:
    """绑定当前 review、报告回执以及同一计划 attempt 的受控候选文件清单。"""
    try:
        control = control_module()
        events, problems = control.verify_event_chain(out_dir, metadata)
        if problems:
            return None
        starts = [(i, event) for i, event in enumerate(events)
                  if event.get("event_type") == "step_started"
                  and (event.get("payload") or {}).get("step") == "review"]
        if not starts or starts[-1][1]["payload"].get("attempt") != attempt:
            return None
        review_index, review_start = starts[-1]
        later = events[review_index + 1:]
        finish = next((i for i, event in enumerate(later)
                       if event.get("event_type") == "step_finished"
                       and (event.get("payload") or {}).get("attempt") == attempt), None)
        if finish is not None and (later[finish]["payload"].get("outcome") == "blocked"):
            return None
        review_events = later if finish is None else later[:finish]
        digest = hashlib.sha256(report_bytes).hexdigest()
        if not any(event.get("event_type") == "artifact_written"
                   and (event.get("payload") or {}).get("step") == "review"
                   and event["payload"].get("attempt") == attempt
                   and event["payload"].get("output") == "review_dedup_evidence"
                   and event["payload"].get("path") == str(target)
                   and event["payload"].get("sha256") == digest for event in review_events):
            return None
        plan_file = out_dir / "01_plan.md"
        candidates_file = out_dir / "plan_candidates.json"
        if plan_file.is_symlink() or candidates_file.is_symlink():
            return None
        plan_bytes = plan_file.read_bytes()
        candidate_bytes = candidates_file.read_bytes()
        protected = review_start["payload"].get("protected") or {}
        for port, data in (("plan", plan_bytes), ("plan_candidates", candidate_bytes)):
            if (protected.get(port) or {}).get("sha256") != hashlib.sha256(data).hexdigest():
                return None
        plan_starts = [(i, event) for i, event in enumerate(events[:review_index])
                       if event.get("event_type") == "step_started"
                       and (event.get("payload") or {}).get("step") == "plan"]
        if not plan_starts:
            return None
        plan_index, plan_start = plan_starts[-1]
        plan_attempt = plan_start["payload"].get("attempt")
        plan_events = events[plan_index + 1:review_index]
        finish_index = next((i for i, event in enumerate(plan_events)
                             if event.get("event_type") == "step_finished"
                             and (event.get("payload") or {}).get("attempt") == plan_attempt), None)
        if finish_index is None or plan_events[finish_index]["payload"].get("outcome") != "success":
            return None
        for port, path, data in (("plan", plan_file, plan_bytes),
                                 ("plan_candidates", candidates_file, candidate_bytes)):
            digest = hashlib.sha256(data).hexdigest()
            if not any(event.get("event_type") == "artifact_written"
                       and (event.get("payload") or {}).get("step") == "plan"
                       and event["payload"].get("attempt") == plan_attempt
                       and event["payload"].get("output") == port
                       and event["payload"].get("path") == str(path.resolve())
                       and event["payload"].get("sha256") == digest
                       for event in plan_events[:finish_index]):
                return None
        candidates = json.loads(candidate_bytes)
        plan_text = plan_bytes.decode("utf-8")
        sections = re.findall(r"<!-- icode:plan-candidates -->\s*```json\s*(.*?)\s*```\s*<!-- /icode:plan-candidates -->",
                              plan_text, flags=re.DOTALL)
        if len(sections) != 1 or json.loads(sections[0]) != candidates:
            return None  # 结构化清单须与已冻结计划中的候选文件章节一致
        files = candidates.get("files") if isinstance(candidates, dict) else None
        if (type(candidates.get("schema_version")) is not int or candidates.get("schema_version") != 1
                or candidates.get("ticket_id") != metadata.get("ticket_id")
                or candidates.get("attempt") != plan_attempt or not isinstance(files, list) or not files
                or any(not isinstance(name, str) or not name.strip() for name in files)):
            return None
        return files
    except (OSError, ValueError, TypeError, KeyError, AttributeError, control_module().ControlError):
        return None


def step_manual_provenance(out_dir: Path, metadata: Dict, step: str, attempt: str,
                           target: Path, report_bytes: bytes) -> bool:
    """该步骤 attempt 下的人工证据来源证明。

    review：沿用强绑定（最新 review attempt + review_dedup_evidence 回执 +
    计划清单回执），见 review_manual_provenance。
    deepcheck 等后续复检：证据描述的是**修改后**的当前源码，因此可对当前源码
    完整重验；此处只认证 attempt 属于该步骤、是最近一次启动且未 blocked，
    不套用 review 专用的计划/产物绑定。
    """
    if step == "review":
        return review_manual_provenance(out_dir, metadata, attempt, target, report_bytes) is not None
    try:
        control = control_module()
        events, problems = control.verify_event_chain(Path(out_dir), metadata)
        if problems:
            return False
        starts = [(i, event) for i, event in enumerate(events)
                  if event.get("event_type") == "step_started"
                  and (event.get("payload") or {}).get("step") == step]
        if not starts or (starts[-1][1].get("payload") or {}).get("attempt") != attempt:
            return False
        index = starts[-1][0]
        later = events[index + 1:]
        finish = next((event for event in later
                       if event.get("event_type") == "step_finished"
                       and (event.get("payload") or {}).get("attempt") == attempt), None)
        # 复检必须真实结束且未失败：中断/blocked/失败的 attempt 不得用于清偿。
        if finish is None:
            return False
        if (finish.get("payload") or {}).get("outcome") not in ("success", "degraded"):
            return False
        finish_index = next(i for i, event in enumerate(later) if event is finish)
        digest = hashlib.sha256(report_bytes).hexdigest()
        for event in later[:finish_index]:
            if event.get("event_type") != "artifact_written":
                continue
            payload = event.get("payload") or {}
            if (payload.get("attempt") == attempt
                    and payload.get("path") == str(target)
                    and payload.get("sha256") == digest):
                return True  # 报告在该 attempt 内登记（受控回执绑定）
        return False
    except Exception:  # noqa: BLE001 - 无法认证事件链时不得据此清偿
        return False


def manual_dedup_evidence_verifiable(row: Dict, metadata: Dict, out_dir: Optional[Path]) -> bool:
    """清偿去重债务的人工等价证据必须机器可验证。

    called/cache_hit 是有效的工具结果（结构校验已含 attempted/input_digest），
    不需要额外文件。unavailable_before_call/degraded_after_attempt 的人工等价
    审查须绑定当前工单、attempt、受影响仓库并通过人工去重合同：优先对当前源码
    全量校验；源码快照漂移（code 阶段正常修改后的预期状态）时回退为清偿行绑定的
    报告摘要 + 报告内部一致性校验，使 review 时的清偿不随后续源码修改失效
    （代码变更后的复检要求由 deepcheck.dedup 有效记录覆盖）。
    """
    decision = str(row.get("decision"))
    if decision in ("called", "cache_hit"):
        return True
    if decision == "skipped_not_eligible":
        # 资格校验已确认可信枚举低于阈值，无需人工去重。
        return True
    if decision not in ENUMERATION_DEBT_DECISIONS:
        return False
    if out_dir is None:
        return False  # 无工单目录上下文时无法验证 → 不清偿
    availability = row.get("availability")
    if not isinstance(availability, dict):
        return False
    ref = availability.get("evidence_ref")
    if not isinstance(ref, str) or not ref.strip():
        return False
    ref_path = ref.split("#", 1)[0].strip()
    if not ref_path:
        return False
    base = Path(out_dir).resolve()
    referenced = base / ref_path
    if referenced.is_symlink():
        return False
    target = referenced.resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return False  # 越界引用不可信
    if not target.is_file():
        return False
    try:
        workspace_value = metadata.get("project_path")
        attempt = row.get("attempt")
        roots = row["evidence"]["affected_repo_roots"]
        if not isinstance(workspace_value, str) or not workspace_value or not isinstance(attempt, str) or not attempt or not roots:
            return False
        workspace = Path(workspace_value).resolve()
        scope_map = row["evidence"].get("repo_review_scopes")
        if scope_map is None and len(roots) == 1:
            # 兼容旧版单仓全范围报告。
            scope_map = {roots[0]: ["."]}
        if not isinstance(scope_map, dict) or set(scope_map) != set(roots):
            return False
        report_bytes = target.read_bytes()
        # 声明了报告摘要就必须与文件一致：绑定被破坏（填错/报告被替换）时
        # 不得以「当前源码校验通过」掩盖证据身份失效，fail closed。
        if "evidence_sha256" in availability:
            recorded_sha = availability.get("evidence_sha256")
            if not isinstance(recorded_sha, str) or re.fullmatch(r"[0-9a-f]{64}", recorded_sha) is None \
                    or hashlib.sha256(report_bytes).hexdigest() != recorded_sha:
                return False
        step = str(row.get("step") or "review")
        if step == "review":
            # review 需要强绑定（计划清单 + 产物回执），provenance 同时给出候选清单。
            plan_files = review_manual_provenance(base, metadata, attempt, target, report_bytes)
            if plan_files is None:
                return False
        else:
            # 后续复检报告描述当前源码，只需认证本步骤 attempt 归属。
            if not step_manual_provenance(base, metadata, step, attempt, target, report_bytes):
                return False
            plan_files = []
        report = json.loads(report_bytes)
        if isinstance(report, dict) and "repositories" in report:
            entries = report["repositories"]
            if not isinstance(entries, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("repo_root"), str) for entry in entries):
                return False
            reports = {entry["repo_root"]: entry.get("manual_dedup") for entry in entries}
            if len(reports) != len(entries) or set(reports) != set(roots):
                return False
        elif len(roots) == 1:
            reports = {roots[0]: report.get("manual_dedup", report) if isinstance(report, dict) else report}
        else:
            return False
        spec = importlib.util.spec_from_file_location("coverage_manual_dedup", Path(__file__).with_name("manual_dedup_evidence.py"))
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        full_verified = True
        snapshot_drift = False
        counts = []
        reviewed_scopes = []
        reviewed_sources = set()
        for root in roots:
            root_path = Path(root)
            repo = (root_path if root_path.is_absolute() else workspace / root_path).resolve()
            scopes = scope_map[root]
            sources = helper.collect_sources(repo, scopes)
            report = reports[root]
            if helper.validate_report(report, repo, metadata.get("ticket_id"), attempt, scopes, sources):
                full_verified = False
                # 校验失败时区分快照漂移与结构性伪造：报告记录的源码集合/摘要
                # 与当前收集不一致 = code 阶段正常修改源码后的预期状态（允许
                # 快照绑定回退）；完全一致却校验失败 = 结构性伪造，不回退。
                report_sources = {s.get("path"): s.get("sha256") for s in (report.get("sources") or [])
                                  if isinstance(s, dict)}
                if report_sources != sources and step == "review":
                    snapshot_drift = True
                continue
            reviewed_scopes.extend((repo / scope).resolve() for scope in scopes)
            reviewed_sources.update((repo / source).resolve() for source in sources)
            counts.append(sum(len(source["functions"]) for source in report["sources"]))
        if full_verified:
            code_files = metadata.get("code_files") or []
            if not isinstance(code_files, list):
                return False
            for name in [*plan_files, *code_files]:
                if not isinstance(name, str) or not name.strip():
                    return False
                path = Path(name)
                path = (path if path.is_absolute() else workspace / path).resolve()
                if path.suffix.lower() not in helper._SOURCE_SUFFIXES:
                    if path.suffix.lower() in helper._NON_SOURCE_SUFFIXES or path.name in helper._NON_SOURCE_NAMES:
                        continue  # 文档属于审查范围，但不进入函数目录
                    return False
                if not any(path == scope or path.is_relative_to(scope) for scope in reviewed_scopes):
                    return False
                if path.is_file() and path not in reviewed_sources:
                    return False  # 已声明的源码不能因忽略规则漏出审查目录
            return max(counts) == row["evidence"]["function_count"]
        if not snapshot_drift:
            return False  # 快照一致却校验失败 → 结构性伪造，不回退
        return _manual_report_snapshot_consistent(helper, reports, row, metadata, roots,
                                                  scope_map, workspace, target, plan_files, base)
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        return False


def review_manual_verification_receipt(out_dir, attempt, report_sha256, metadata,
                                       output_id="review_dedup_evidence"):
    """取受控登记的人工去重完整校验回执（artifact 事件，事件链保护）。

    返回该 attempt 下所有**合规**回执（按事件顺序，每项的每个条目带
    `report_sha256`）。同一 attempt 可能多次登记（报告完善后重登），因此不
    按第一条返回——调用方用当前报告摘要匹配对应回执（见
    `_manual_report_snapshot_consistent`），避免沿用旧摘要误判。
    `report_sha256` 非空时只保留该摘要的回执。
    """
    try:
        control = control_module()
        events, problems = control.verify_event_chain(Path(out_dir), metadata)
        if problems:
            return None
    except Exception:  # noqa: BLE001 - 无法认证事件链时不得据此清偿
        return None
    collected = []
    for event in events:
        if event.get("event_type") != "artifact_written":
            continue
        payload = event.get("payload") or {}
        if (payload.get("output") != output_id
                or payload.get("attempt") != attempt):
            continue
        if report_sha256 is not None and payload.get("sha256") != report_sha256:
            continue
        record = payload.get("manual_dedup_verification")
        if not isinstance(record, dict) or record.get("version") != 1:
            continue
        if (record.get("ticket_id") != metadata.get("ticket_id")
                or record.get("attempt") != attempt):
            continue
        recorded_sha = record.get("report_sha256")
        if not isinstance(recorded_sha, str) or recorded_sha != payload.get("sha256"):
            continue  # 回执摘要必须与其登记事件绑定
        repositories = record.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            continue
        collected.append([dict(entry, report_sha256=recorded_sha)
                          for entry in repositories if isinstance(entry, dict)])
    return collected or None


def _manual_report_snapshot_consistent(helper, reports, row, metadata, roots, scope_map,
                                       workspace, target, plan_files, out_dir) -> bool:
    """源码快照漂移后的清偿验证：必须有受控的完整校验回执。

    code 阶段正常修改源码后无法再对旧报告重做完整校验，因此清偿依赖登记时刻
    （源码与报告一致时）写入 artifact 事件的 `manual_dedup_verification` 回执：
    报告未被替换、回执属于本工单/attempt/报告摘要、回执记录的仓库范围与函数
    计数和报告内容一致、列出的源码文件仍存在于受影响仓库范围内。
    没有回执（或回执与报告不符）→ 不清偿，保留债务等待后续有效复检覆盖。
    """
    availability = row.get("availability") or {}
    attempt = row.get("attempt")
    if not isinstance(attempt, str) or not attempt:
        return False
    # 摘要来源统一：trace 可只填 evidence_ref（操作文档合同）；受控回执本身
    # 已绑定登记时的报告摘要。两者都在时必须一致。
    declared = availability.get("evidence_sha256")
    if declared is not None and (not isinstance(declared, str)
                                 or re.fullmatch(r"[0-9a-f]{64}", declared) is None):
        return False  # 声明了摘要但格式非法
    try:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return False
    receipt_groups = review_manual_verification_receipt(
        Path(out_dir), attempt, declared, metadata)
    if not receipt_groups:
        return False  # 无受控完整校验回执：摘要只能证明未变，不能证明曾经有效
    # 同一 attempt 可能多次登记：按**当前报告摘要**匹配回执，取最后一条；
    # 绝不沿用更早回执的摘要（否则报告完善后源码一变就误判未清偿）。
    matches = [group for group in receipt_groups
               if group and group[0].get("report_sha256") == digest]
    if not matches:
        return False  # 当前报告没有任何受控登记回执（被替换或未登记）
    receipts = matches[-1]
    recorded = receipts[0].get("report_sha256")
    if not isinstance(recorded, str) or re.fullmatch(r"[0-9a-f]{64}", recorded) is None:
        return False
    if declared is not None and declared != recorded:
        return False  # trace 声明的摘要与受控登记摘要不一致
    # trace 可能记录相对仓库根（如 "."），回执记录解析后的绝对根；两边都按
    # 工程根规范化后再比较，避免同一仓库因写法不同匹配失败。
    def normalize_root(value):
        candidate = Path(value)
        return (candidate if candidate.is_absolute() else Path(workspace) / candidate).resolve()

    receipts_by_root = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            return False
        receipts_by_root[str(normalize_root(str(receipt.get("repo_root"))))] = receipt
    root_keys = {str(normalize_root(str(root))): root for root in roots}
    if set(receipts_by_root) != set(root_keys):
        return False
    try:
        for key, root in root_keys.items():
            report = reports[root]
            receipt = receipts_by_root[key]
            if not isinstance(report, dict):
                return False
            if (report.get("ticket_id") != metadata.get("ticket_id")
                    or report.get("attempt") != attempt
                    or report.get("review_scope") != scope_map[root]
                    or report.get("unobserved") != []):
                return False
            recorded_sources = receipt.get("sources")
            if not isinstance(recorded_sources, dict) or not recorded_sources:
                return False
            report_sources = {s.get("path"): s.get("sha256") for s in (report.get("sources") or [])
                              if isinstance(s, dict)}
            if report_sources != recorded_sources:
                return False  # 回执与当前报告不一致：报告被改写或回执不属此报告
            function_count = sum(len(s.get("functions") or []) for s in (report.get("sources") or [])
                                 if isinstance(s.get("functions"), list))
            if function_count != receipt.get("function_count"):
                return False
            repo = (Path(root) if Path(root).is_absolute() else workspace / Path(root)).resolve()
            planned = {(Path(name) if Path(name).is_absolute() else Path(workspace) / name).resolve()
                       for name in (*plan_files, *(metadata.get("code_files") or []))
                       if isinstance(name, str) and name.strip()}
            for relative in recorded_sources:
                path = Path(relative)
                if path.is_absolute() or ".." in path.parts:
                    return False
                source_path = repo / path
                if not source_path.is_file():
                    # 计划清单明确支持删除源码：计划（或已声明 code_files）内的文件
                    # 允许在 code 阶段被删除，不因此判清偿失效；计划外文件消失仍
                    # 视为无法核实。
                    if source_path.resolve() in planned:
                        continue
                    return False
                if not any(source_path.resolve().is_relative_to((repo / Path(scope)).resolve())
                           for scope in scope_map[root]):
                    return False
        code_files = metadata.get("code_files") or []
        if not isinstance(code_files, list):
            return False
        reviewed_scopes = []
        for root in roots:
            repo = (Path(root) if Path(root).is_absolute() else workspace / Path(root)).resolve()
            reviewed_scopes.extend((repo / Path(scope)).resolve() for scope in scope_map[root])
        for name in [*plan_files, *code_files]:
            if not isinstance(name, str) or not name.strip():
                return False
            path = Path(name)
            path = (path if path.is_absolute() else workspace / path).resolve()
            if path.suffix.lower() not in helper._SOURCE_SUFFIXES:
                if (path.suffix.lower() in helper._NON_SOURCE_SUFFIXES
                        or path.name in helper._NON_SOURCE_NAMES):
                    continue
                return False
            if not any(path == scope or path.is_relative_to(scope) for scope in reviewed_scopes):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def review_dedup_debt(row, gate, constants, metadata, out_dir):
    """枚举未知或满足去重资格却未完成机器可核验的替代审查。"""
    if has_enumeration_debt_state(row, metadata):
        return True
    return (gate is not None
            and row.get("gate_id") == "review.dedup"
            and row_contract(row, metadata) >= 2
            and row.get("eligible") is True
            and row.get("decision") in ENUMERATION_DEBT_DECISIONS
            and gate_row_is_valid_final(row, gate, constants, metadata)
            and not manual_dedup_evidence_verifiable(row, metadata, out_dir))


def ticket_enumeration_debt(trace_rows: List[Dict], metadata: Dict,
                            catalog: Optional[Dict] = None,
                            out_dir: Optional[Path] = None) -> int:
    """工单级未解决枚举/去重债务余额：按每 gate 的时间顺序逐行推演。

    与单次状态推进的门禁报告（只看被推进步骤的 in-scope gates）不同，余额跨步骤
    持续存在：review 产生债务后，后续 plan_finalized/completed 推进的报告虽为 0，
    余额仍为 1。清偿条件是该 gate 随后出现**通过资格与证据校验的有效最终记录**
    （gate_row_is_valid_final）——流程要求调用前先写 pending，因此中断或尚未完成
    的重做（pending/非法行）不改写债务状态，此前债务保持未清偿。
    """
    if catalog is None:
        catalog, _catalog_err = find_gates_catalog()
    gates_by_id = {str(g.get("id")): g for g in (catalog or {}).get("gates", [])}
    constants = (catalog or {}).get("constants", {})
    rows_by_gate: Dict[str, List[Dict]] = {}
    for row in trace_rows:
        if not isinstance(row, dict):
            continue
        gid = str(row.get("gate_id", ""))
        if gid:
            rows_by_gate.setdefault(gid, []).append(row)
    # 仓库根统一规范化：trace 允许相对工程根的写法（如 "."），与绝对路径必须
    # 能匹配同一仓库，否则范围覆盖判断会失真。
    workspace_value = metadata.get("project_path")
    workspace_base = (Path(workspace_value).resolve()
                      if isinstance(workspace_value, str) and workspace_value else None)

    def normalize_root(root: str) -> str:
        candidate = Path(root)
        # 绝对路径同样 resolve：macOS 的 /var 与 /private/var 等别名必须归一，
        # 否则同一仓库的相对/绝对写法会匹配失败。
        if candidate.is_absolute() or workspace_base is None:
            return str(candidate.resolve())
        return str((workspace_base / candidate).resolve())

    # 按 trace 时间顺序全局推演：deepcheck.dedup 的有效复检记录（针对修改后
    # 源码）可以覆盖同仓库范围的未清偿 review 债务。
    balance = 0
    debt_open = False
    debt_roots = set()
    debt_scope_unknown = False
    debt_scopes: Dict[str, frozenset] = {}
    covered_roots = set()
    covered_scopes: Dict[str, set] = {}

    def declared_scopes(evidence, roots):
        """逐仓审查范围的声明状态与内容：("declared", mapping) / ("absent", None) / ("invalid", None)。

        显式 `repo_review_scopes` 必须是「仓库根 → 非空字符串数组」且键与 roots
        一一对应，否则无法判定覆盖（invalid）。未声明时该行不对范围作限定
        （absent）——债务行常因无法枚举而不声明范围，此状态不代表「全仓」。
        scope 写法按 normalize_scope_text 归一（"src/" 与 "./src" 同为 "src"）。
        """
        scope_map = evidence.get("repo_review_scopes")
        normalized = {normalize_root(root): root for root in roots}
        if scope_map is None:
            # 未声明范围（仅债务侧使用）：按全仓保守要求覆盖；清偿/复检侧
            # 不使用该映射（见 ticket_enumeration_debt），必须显式声明范围。
            return ("absent", {root_key: frozenset(["."]) for root_key in normalized})
        if not isinstance(scope_map, dict) or not scope_map:
            return ("invalid", None)
        result = {}
        for raw_root, scopes in scope_map.items():
            if not isinstance(raw_root, str) or not isinstance(scopes, list) or not scopes:
                return ("invalid", None)
            if any(not isinstance(item, str) or not item.strip() for item in scopes):
                return ("invalid", None)
            result[normalize_root(raw_root)] = frozenset(normalize_scope_text(item) for item in scopes)
        if set(result) != set(normalized) or len(result) != len(roots):
            return ("invalid", None)
        return ("declared", result)
    for row in trace_rows:
        if not isinstance(row, dict):
            continue
        gid = str(row.get("gate_id", ""))
        if not gid:
            continue
        gate = gates_by_id.get(gid)
        if review_dedup_debt(row, gate, constants, metadata, out_dir):
            debt_open = True  # 债务产生（即便该行组合非法，债务也未清偿）
            roots = row["evidence"].get("affected_repo_roots")
            if not isinstance(roots, list) or not roots or any(not isinstance(root, str) or not root.strip() for root in roots):
                debt_scope_unknown = True
            else:
                debt_roots.update(normalize_root(root) for root in roots)
                state, scopes = declared_scopes(row["evidence"], roots)
                if state == "invalid":
                    debt_scope_unknown = True
                else:
                    # 同一仓库连续产生债务时按范围累积：先前未清偿范围不因后续
                    # 债务行范围更小而丢失。未声明范围的债务按全仓累积（保守）。
                    for root_key, scope_set in scopes.items():
                        debt_scopes[root_key] = debt_scopes.get(root_key, frozenset()) | scope_set
                covered_roots = set()  # 新一轮债务：既有复检覆盖不作数
                covered_scopes = {}
            continue
        if gate is None:
            continue
        if not gate_row_is_valid_final(row, gate, constants, metadata):
            continue
        if gid == "review.dedup":
            if debt_open:
                roots = row["evidence"].get("affected_repo_roots", [])
                row_roots = ({normalize_root(root) for root in roots
                              if isinstance(root, str) and root.strip()}
                             if isinstance(roots, list) else set())
                # review 重做同样必须逐仓覆盖原债务范围：计划文件覆盖只能证明
                # 复审覆盖了计划，不能证明覆盖了原债务范围。
                if not debt_roots.issubset(row_roots):
                    continue  # 重做范围未覆盖原始债务仓库 → 不清偿
                if debt_scope_unknown:
                    continue  # 原债务范围无法确定 → 保守保留债务
                valid_roots = ([root for root in roots
                                if isinstance(root, str) and root.strip()]
                               if isinstance(roots, list) else [])
                redo_state, redo_scopes = declared_scopes(row["evidence"], valid_roots) \
                    if valid_roots else ("invalid", None)
                if redo_state != "declared":
                    # 重做未声明范围（或声明非法）时无法证明覆盖原债务范围：
                    # 清偿侧必须显式声明 repo_review_scopes。
                    continue
                # 重做必须逐仓覆盖原债务范围（目录包含关系）。
                covered_by_redo = True
                for root in debt_roots:
                    if root not in debt_scopes:
                        continue
                    if root not in redo_scopes \
                            or not scopes_cover(redo_scopes[root], debt_scopes[root]):
                        covered_by_redo = False
                        break
                if not covered_by_redo:
                    continue  # 重做范围未覆盖原债务范围 → 不清偿
                if not manual_dedup_evidence_verifiable(row, metadata, out_dir):
                    continue  # 不可验证（快照漂移且未绑定/证据不可信）→ 等待后续复检覆盖
                debt_open = False
                debt_roots.clear()
                debt_scope_unknown = False
                debt_scopes = {}
        elif gid == "deepcheck.dedup":
            # 后续复检只有在**真实完成**时才覆盖相同仓库范围：工具真实成功
            # （called/cache_hit 且 result=success，已由有效最终记录校验保证），
            # 或人工等价复检证据经完整校验。工具不可用而人工复检未完成
            # （unavailable/degraded 且证据不可验证）不构成覆盖，债务保留。
            decision = str(row.get("decision"))
            if decision in ("called", "cache_hit"):
                covers = True
            elif decision in ENUMERATION_DEBT_DECISIONS:
                covers = manual_dedup_evidence_verifiable(row, metadata, out_dir)
            else:
                covers = False
            if covers:
                roots = row["evidence"].get("affected_repo_roots", [])
                if isinstance(roots, list):
                    valid_roots = [root for root in roots
                                   if isinstance(root, str) and root.strip()]
                    state, scopes = declared_scopes(row["evidence"], valid_roots)
                    # 只有显式声明范围才构成覆盖：deepcheck.dedup 的机器合同不
                    # 要求 repo_review_scopes，未声明时实际范围未知，不能据此
                    # 清偿同仓库任意范围的债务。
                    if state == "declared" and scopes:
                        for root_key, scope_set in scopes.items():
                            covered_roots.add(root_key)
                            covered_scopes.setdefault(root_key, set()).update(scope_set)
    if debt_open:
        # 每个债务仓库都必须在已覆盖仓库内，且其声明的范围必须被复检范围覆盖；
        # 缺声明（不应发生）按未覆盖处理，绝不默认空集通过。
        covers_all = (not debt_scope_unknown
                      and debt_roots.issubset(covered_roots)
                      and all(scopes_cover(covered_scopes.get(root, set()), debt_scopes[root])
                              for root in debt_roots if root in debt_scopes))
        if not covers_all:
            balance += 1  # 存在未被后续有效复检覆盖的范围 → 债务仍未清偿
    return balance


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
        "contract_version": metadata.get("mcp_gate_contract_version", 1),
        "total_gates_in_scope": 0,
        "missing_gate": 0,
        "eligible": 0,
        "fulfilled": 0,
        "called": 0,
        "cache_hit": 0,
        "degraded_after_attempt": 0,
        "unavailable_before_call": 0,
        "invalid_skip": 0,
        "skipped_not_eligible": 0,
        "skipped_stage_not_reached": 0,
        "enumeration_debt": 0,
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
    known_steps = {g.get("step") for g in gates} | set(catalog.get("not_applicable_steps", []))
    if step_filter and step_filter not in known_steps:
        report["schema_errors"] += 1
        report["trace_errors"].append(f"未知 step: {step_filter}")
    if type(version) is not int or version not in (1, 2):
        report["schema_errors"] += 1
        report["trace_errors"].append("metadata mcp_gate_schema_version 非受支持版本")
    contract_version = metadata.get("mcp_gate_contract_version")
    if contract_version is not None and (type(contract_version) is not int
            or contract_version not in (1, 2)):
        report["schema_errors"] += 1
        report["trace_errors"].append("metadata mcp_gate_contract_version 非受支持合同版本")
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
        if review_dedup_debt(row, gate, catalog.get("constants", {}), metadata, out_dir):
            # 报告显式暴露未解决的枚举/去重债务（不改变通过判定：按合同留痕的
            # unavailable/degraded 记录是合法履行，但债务必须在报告中可见）。
            report["enumeration_debt"] += 1
            gs["enumeration_debt"] = True
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
    report["scope_status"] = "applicable" if report["total_gates_in_scope"] else "not_applicable"
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
    known_steps = {g.get("step") for g in catalog.get("gates", [])} | set(catalog.get("not_applicable_steps", []))
    if args.step and args.step not in known_steps:
        print(json.dumps({"error": "unknown_step", "step": args.step}, ensure_ascii=False))
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
    print(f"ticket_id: {report['ticket_id'] or '-'} | schema: {report['schema_version'] or 'legacy-untracked'} | contract: {report['contract_version']}")
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
    print(f"called: {report['called']} | cache_hit: {report['cache_hit']} | degraded_after_attempt: {report['degraded_after_attempt']} | unavailable_before_call: {report['unavailable_before_call']}")
    print(f"historical evidence_untracked: {report['evidence_untracked']}（工具coverage不等于语义审查覆盖）")
    print(f"skipped_not_eligible: {report['skipped_not_eligible']} | skipped_stage_not_reached: {report['skipped_stage_not_reached']}")
    print(f"enumeration_debt: {report['enumeration_debt']}（枚举未知、保留债务的 gate 数，需在工具恢复后真实重做）")
    print(f"missing_gate: {report['missing_gate']} | invalid_skip: {report['invalid_skip']} | schema_errors: {report['schema_errors']} | sensitive_data: {report['sensitive_data']}")
    for terr in report.get("trace_errors", []):
        print(f"  ⚠️ trace 行错误: {terr}")

    if legacy:
        return 1 if require else 0
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
