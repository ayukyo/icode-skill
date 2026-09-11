#!/usr/bin/env python3
"""校验 /icode init --guide 的外部指南与内部证据账本。"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


CLAIM_KINDS = {
    "current_fact",
    "historical_evidence",
    "engineering_recommendation",
    "unverified",
}
QUANTITATIVE_CATEGORIES = {
    "acceptance_threshold",
    "observed_result",
    "defect_rate",
    "tool_default",
}
VERIFICATION_KINDS = {"static", "runtime"}
WRITE_MODES = {"none", "overwrite", "append", "mixed", "unknown"}
DEFAULT_FORBIDDEN_TERMS = [
    ".icode_output", ".ico_metadata.json", ".ico_events.jsonl",
    ".decision_anchors.json", ".thinking_gate_trace.jsonl", ".mcp_gate_trace.jsonl",
    "00_init.md", "01_plan.md", "02_review.md", "03_plan_final.md",
    "04_code_review_fix.md", "05_deepcheck.md", "06_audit.md", "08_patch.md",
    "log_analysis.md", "review_round_", "guide.audit.json", "/icode",
    "ticket_id", "completed_steps", "clean_rounds", "has_new_issues",
    "pending_verification", "adversarial_verification", "agentId", "Agent ID",
    "子代理", "证据质疑者", "替代解释者", "充分性质疑者", "质疑者",
    "步骤0", "步骤 0", "审查轮次", "工单",
]
DEFAULT_FORBIDDEN_PATTERNS = (
    (re.compile(r"\bstatus\s*=\s*(?:init_in_progress|plan_done|review_(?:in_progress|done)|"
                r"plan_finalized|code_(?:in_progress|done)|deepcheck_(?:in_progress|done)|"
                r"completed|log_(?:in_progress|done)|debug_(?:in_progress|done))\b",
                re.IGNORECASE), "ICODE status=状态"),
    (re.compile(r"步骤\s*[0-9一二三四五六七八九十]+"), "ICODE 步骤编号"),
)
REQUIRED_MANUAL_CHECKS = {
    "jargon_explained",
    "diagrams_explained",
    "end_to_end_example",
    "fact_recommendation_boundary",
}
TOOL_STRING_FIELDS = {"name", "source_path", "run", "cwd", "write_mode", "verification"}
TOOL_LIST_FIELDS = {"prerequisites", "config", "outputs", "hardcoded_constraints"}
SEMANTIC_SECTIONS = {
    "架构": ("架构", "architecture"),
    "接口": ("接口", "api", "interface"),
    "流程/交付": ("流程", "提测", "交付", "process", "delivery", "handoff"),
    "测试": ("测试", "验证", "test", "verification", "quality"),
    "环境/部署": ("环境", "部署", "安装", "environment", "deploy", "install"),
    "边界/待确认": ("边界", "待确认", "限制", "boundary", "limitation", "unknown"),
}
QUANTITY_PATTERN = re.compile(
    r"(?:"
    r"(?:概率|复现率|成功率|失败率)\s*(?:为|=|:|：)?\s*\d+(?:\.\d+)?(?:%|％)?"
    r"|(?<![A-Za-z0-9_])\d+\s*/\s*\d+"
    r"|(?<![A-Za-z0-9_])\d+(?:\.\d+)?\s*(?:百|千|万|亿)?\s*"
    r"(?:%|％|次|轮|小时|分钟|毫秒|秒|ms|hz|fps|路|台|个|mb|gb|kbps|mbps)"
    r")",
    re.IGNORECASE,
)
FACT_CUE_PATTERN = re.compile(
    r"当前|现有|历史|曾经|曾|建议|推荐|待确认|未验证|尚未|"
    r"仓库|代码|配置|脚本|默认|位于|支持|负责|采用|"
    r"\bcurrent\b|\bexisting\b|\bhistorical\b|\brecommend(?:ed|ation)?\b|"
    r"\bunverified\b|\brepository\b|\bscript\b|\bdefault\b|\bsupports?\b",
    re.IGNORECASE,
)
KIND_LANGUAGE_PATTERNS = {
    "historical_evidence": re.compile(r"历史|当时|曾经|曾|过去|旧版|旧版本|historical|previous",
                                       re.IGNORECASE),
    "engineering_recommendation": re.compile(r"建议|推荐|可考虑|recommend|should consider",
                                               re.IGNORECASE),
    "unverified": re.compile(r"待确认|未验证|尚未|不确定|仍需.{0,12}验证|需.{0,12}确认|"
                             r"unverified|not verified|to be confirmed|unknown",
                             re.IGNORECASE),
}
RECEIPT_GENESIS = "0" * 64
COMMAND_MENTION_PATTERNS = (
    re.compile(r"运行\s+`?([A-Za-z][A-Za-z0-9_.-]*)`?"),
    re.compile(r"执行\s+`?([A-Za-z][A-Za-z0-9_.-]*)`?"),
    re.compile(r"\brun\s+`?([A-Za-z][A-Za-z0-9_.-]*)`?", re.IGNORECASE),
)


def load_text(path, label, errors):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{label}不可读或不是 UTF-8: {path}: {exc}")
        return ""


def load_audit(path, errors):
    text = load_text(path, "audit", errors)
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"audit JSON 解析失败: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append("audit 顶层必须是 JSON 对象")
        return {}
    return value


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def resolve_audit_path(value, audit_path):
    path = Path(value)
    return path if path.is_absolute() else audit_path.parent / path


def workspace_for_source(source_init):
    source = Path(source_init).resolve()
    for parent in source.parents:
        if parent.name == ".icode_output":
            return parent.parent
    return source.parent


def reference_file(ref, workspace, ticket_root):
    """把 path:line / path#sheet 定位为本地文件；返回 (path, line)。"""
    if not nonempty_string(ref) or re.match(r"^[a-z]+://", ref, re.IGNORECASE):
        return None, None
    raw = ref.split("#", 1)[0]
    line = None
    line_match = re.match(r"^(.*?):([1-9]\d*)(?::[1-9]\d*)?$", raw)
    if line_match:
        raw = line_match.group(1)
        line = int(line_match.group(2))
    path = Path(raw)
    candidates = [path] if path.is_absolute() else [workspace / path, ticket_root / path]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved, line
    return None, line


def validate_source_ref(ref, prefix, workspace, ticket_root, errors):
    path, line = reference_file(ref, workspace, ticket_root)
    if path is None:
        errors.append(f"{prefix} 不可回读本地证据: {ref}")
        return
    if line is not None:
        try:
            line_count = len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError):
            errors.append(f"{prefix} 带行号但证据不是可读文本: {ref}")
            return
        if line > line_count:
            errors.append(f"{prefix} 行号越界（共 {line_count} 行）: {ref}")


def heading_titles(guide):
    return [m.group(1).strip().casefold()
            for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", guide, re.MULTILINE)]


def validate_semantic_content(guide, errors):
    titles = heading_titles(guide)
    for label, keywords in SEMANTIC_SECTIONS.items():
        if not any(any(keyword.casefold() in title for keyword in keywords) for title in titles):
            errors.append(f"guide 缺少独立于 audit 自报的语义章节: {label}")

    diagrams = re.findall(
        r"```(?:mermaid|text)?\s*\n(?:(?!```).)*(?:-->|->|→)(?:(?!```).)*```",
        guide, re.IGNORECASE | re.DOTALL)
    if len(diagrams) < 2:
        errors.append("guide 必须分别包含含箭头的架构图和流程图")
    explanations = re.findall(
        r"怎么看这张图|如何阅读(?:这张)?图|how to read (?:this )?diagram",
        guide, re.IGNORECASE)
    if len(explanations) < 2:
        errors.append("guide 的架构图和流程图后都必须有读图说明")
    if not any(any(key in title for key in ("术语", "名词", "glossary")) for title in titles):
        errors.append("guide 缺少术语速查/解释章节")
    if not re.search(r"端到端(?:例子|示例)|end[- ]to[- ]end example", guide, re.IGNORECASE):
        errors.append("guide 缺少端到端示例")


def audit_basis_sha256(audit):
    basis = dict(audit)
    basis.pop("clean_rounds", None)
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def receipt_sha256(round_item):
    fields = (
        "round", "result", "focus", "recorded_by", "recorded_at",
        "guide_sha256", "audit_basis_sha256", "previous_receipt_sha256",
    )
    payload = {key: round_item.get(key) for key in fields}
    raw = ("icode-guide-round-receipt-v1\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_round_receipt(round_item, round_number, previous_receipt, errors):
    prefix = f"clean_rounds[{round_number - 1}]"
    if not isinstance(round_item, dict):
        errors.append(f"{prefix} 必须是对象")
        return
    if round_item.get("round") != round_number:
        errors.append(f"{prefix}.round 必须为 {round_number}")
    if round_item.get("result") != "pass":
        errors.append(f"{prefix}.result 必须为 pass")
    if round_item.get("recorded_by") != "lint_guide_contract.py":
        errors.append(f"{prefix} 必须由 lint_guide_contract.py 记录")
    recorded_at = round_item.get("recorded_at")
    try:
        parsed_at = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
        if parsed_at.utcoffset() is None:
            raise ValueError("missing timezone")
    except (TypeError, ValueError):
        errors.append(f"{prefix}.recorded_at 必须是带时区的 ISO-8601 时间")
    for field in ("guide_sha256", "audit_basis_sha256", "previous_receipt_sha256",
                  "receipt_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(round_item.get(field, ""))):
            errors.append(f"{prefix}.{field} 非法")
    if round_item.get("previous_receipt_sha256") != previous_receipt:
        errors.append(f"{prefix}.previous_receipt_sha256 与前一轮回执不一致")
    if round_item.get("receipt_sha256") != receipt_sha256(round_item):
        errors.append(f"{prefix}.receipt_sha256 校验失败，禁止手填回执")


def validate_clean_rounds(audit, guide, errors):
    clean_rounds = audit.get("clean_rounds")
    if not isinstance(clean_rounds, list) or len(clean_rounds) < 2:
        errors.append("audit.clean_rounds 至少记录两轮由 linter 生成的清洁审计")
        return
    previous_receipt = RECEIPT_GENESIS
    for index, round_item in enumerate(clean_rounds[:2]):
        validate_round_receipt(round_item, index + 1, previous_receipt, errors)
        if isinstance(round_item, dict):
            previous_receipt = round_item.get("receipt_sha256")
    second = clean_rounds[1] if len(clean_rounds) > 1 and isinstance(clean_rounds[1], dict) else {}
    current_guide_hash = hashlib.sha256(guide.encode("utf-8")).hexdigest()
    if second.get("guide_sha256") != current_guide_hash:
        errors.append("第二轮审计后 guide 已变化，必须重新记录 round 2")
    if second.get("audit_basis_sha256") != audit_basis_sha256(audit):
        errors.append("第二轮审计后 audit 事实账本已变化，必须重新记录 round 2")


def validate(args, check_rounds=True):
    errors = []
    guide_path = Path(args.guide).resolve()
    audit_path = Path(args.audit).resolve()
    guide = load_text(guide_path, "guide", errors)
    audit = load_audit(audit_path, errors)

    if guide and not re.search(r"^#\s+\S", guide, re.MULTILINE):
        errors.append("guide 缺少一级标题（# 标题）")
    if guide_path.name != "guide.md":
        errors.append(f"guide 文件名必须为 guide.md: {guide_path.name}")
    if audit_path.name != "guide.audit.json":
        errors.append(f"audit 文件名必须为 guide.audit.json: {audit_path.name}")
    if guide_path.parent != audit_path.parent or guide_path.parent.name != "deliverables":
        errors.append("guide 与 audit 必须同处 deliverables/ 目录")

    forbidden = list(args.forbid_term or [])
    if not args.allow_internal_terms:
        forbidden = DEFAULT_FORBIDDEN_TERMS + forbidden
    folded_guide = guide.casefold()
    for term in dict.fromkeys(forbidden):
        if term and term.casefold() in folded_guide:
            errors.append(f"guide 泄漏禁用词: {term}")
    if not args.allow_internal_terms:
        for pattern, label in DEFAULT_FORBIDDEN_PATTERNS:
            match = pattern.search(guide)
            if match:
                errors.append(f"guide 泄漏{label}: {match.group(0)}")

    validate_semantic_content(guide, errors)

    if audit.get("schema_version") != 1:
        errors.append("audit.schema_version 必须为 1")
    if audit.get("profile") != "beginner_guide":
        errors.append("audit.profile 必须为 beginner_guide")

    source_init = audit.get("source_init")
    source_path = None
    if not nonempty_string(source_init):
        errors.append("audit.source_init 必须是非空路径")
    else:
        source_path = resolve_audit_path(source_init, audit_path).resolve()
        if not source_path.is_file():
            errors.append(f"audit.source_init 不存在: {source_init}")
        elif source_path != guide_path.parent.parent / "00_init.md":
            errors.append("audit.source_init 必须指向同一工单根目录的 00_init.md")

    guide_ref = audit.get("guide")
    if not nonempty_string(guide_ref):
        errors.append("audit.guide 必须是非空路径")
    else:
        declared_guide = resolve_audit_path(guide_ref, audit_path).resolve()
        if declared_guide != guide_path:
            errors.append(f"audit.guide 与 --guide 不一致: {declared_guide} != {guide_path}")

    sections = audit.get("required_sections")
    if not isinstance(sections, list) or not sections or not all(nonempty_string(x) for x in sections):
        errors.append("audit.required_sections 必须是非空字符串数组")
    else:
        for section in sections:
            pattern = rf"^#{{1,6}}\s+{re.escape(section.strip())}(?:\s|$)"
            if not re.search(pattern, guide, re.MULTILINE):
                errors.append(f"guide 缺少必需章节标题: {section}")

    workspace = (workspace_for_source(source_path)
                 if source_path and source_path.is_file() else guide_path.parent.parent)
    ticket_root = guide_path.parent.parent
    claims = audit.get("claims")
    valid_claims = []
    if not isinstance(claims, list) or not claims:
        errors.append("audit.claims 必须是非空数组")
    else:
        for index, claim in enumerate(claims):
            prefix = f"claims[{index}]"
            if not isinstance(claim, dict):
                errors.append(f"{prefix} 必须是对象")
                continue
            valid_claims.append(claim)
            text = claim.get("text")
            kind = claim.get("kind")
            refs = claim.get("source_refs")
            scope = claim.get("scope")
            if not nonempty_string(text):
                errors.append(f"{prefix}.text 必须是非空字符串")
            elif text not in guide:
                errors.append(f"{prefix}.text 未在 guide 中逐字出现")
            if kind not in CLAIM_KINDS:
                errors.append(f"{prefix}.kind 非法: {kind!r}")
            elif kind in KIND_LANGUAGE_PATTERNS and nonempty_string(text):
                if not KIND_LANGUAGE_PATTERNS[kind].search(text):
                    errors.append(f"{prefix}.kind={kind} 但正文未显式标出对应语气边界")
            if not isinstance(refs, list) or not all(nonempty_string(x) for x in refs):
                errors.append(f"{prefix}.source_refs 必须是字符串数组")
            elif kind in {"current_fact", "historical_evidence"}:
                if not refs:
                    errors.append(f"{prefix} 为事实/历史证据时 source_refs 不得为空")
                for ref_index, ref in enumerate(refs):
                    validate_source_ref(ref, f"{prefix}.source_refs[{ref_index}]",
                                        workspace, ticket_root, errors)
            if kind in {"historical_evidence", "unverified"} and not nonempty_string(scope):
                errors.append(f"{prefix} 为历史/未验证项时必须声明 scope")
            quantitative = claim.get("quantitative")
            if quantitative is not None:
                if not isinstance(quantitative, dict):
                    errors.append(f"{prefix}.quantitative 必须是对象")
                else:
                    if quantitative.get("category") not in QUANTITATIVE_CATEGORIES:
                        errors.append(f"{prefix}.quantitative.category 非法")
                    value = str(quantitative.get("value", ""))
                    unit = quantitative.get("unit")
                    if not nonempty_string(value):
                        errors.append(f"{prefix}.quantitative.value 不得为空")
                    elif nonempty_string(text) and value not in text:
                        errors.append(f"{prefix}.quantitative.value 未出现在 claim.text")
                    if not nonempty_string(unit):
                        errors.append(f"{prefix}.quantitative.unit 不得为空")

    for match in QUANTITY_PATTERN.finditer(guide):
        if not any(isinstance(claim.get("quantitative"), dict)
                   and nonempty_string(claim.get("text"))
                   and claim["text"] in guide
                   and match.group(0).strip() in claim["text"]
                   for claim in valid_claims):
            errors.append(f"guide 数字未在 claims.quantitative 分类: {match.group(0).strip()}")

    claim_texts = [claim.get("text") for claim in valid_claims
                   if nonempty_string(claim.get("text"))]
    in_fence = False
    for line_number, raw_line in enumerate(guide.splitlines(), 1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith("#"):
            continue
        sentence = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", stripped).strip()
        if FACT_CUE_PATTERN.search(sentence) and not any(
                text in sentence or sentence in text for text in claim_texts):
            errors.append(f"guide 第 {line_number} 行含事实/历史/建议/未验证语气但未纳入 claims: {sentence}")

    tool_checks = audit.get("tool_checks")
    if not isinstance(tool_checks, list):
        errors.append("audit.tool_checks 必须是数组")
        tool_checks = []
    elif not tool_checks:
        no_tools_reason = audit.get("no_tools_reason")
        if not nonempty_string(no_tools_reason) or no_tools_reason not in guide:
            errors.append("audit.tool_checks 为空时，必须用 no_tools_reason 在 guide 中明确说明未发现可执行工具")
    for index, tool in enumerate(tool_checks):
        prefix = f"tool_checks[{index}]"
        if not isinstance(tool, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        for field in TOOL_STRING_FIELDS:
            if not nonempty_string(tool.get(field)):
                errors.append(f"{prefix}.{field} 必须是非空字符串")
        for field in TOOL_LIST_FIELDS:
            value = tool.get(field)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                errors.append(f"{prefix}.{field} 必须是字符串数组")
        if tool.get("verification") not in VERIFICATION_KINDS:
            errors.append(f"{prefix}.verification 只能是 static/runtime")
        if tool.get("write_mode") not in WRITE_MODES:
            errors.append(f"{prefix}.write_mode 非法")
        for field in ("name", "source_path", "run", "cwd"):
            value = tool.get(field)
            if nonempty_string(value) and value not in guide:
                errors.append(f"{prefix}.{field} 未出现在 guide 正文")
        source_value = tool.get("source_path")
        if nonempty_string(source_value):
            source_file, _ = reference_file(source_value, workspace, ticket_root)
            if source_file is None:
                errors.append(f"{prefix}.source_path 不可回读: {source_value}")
        if tool.get("verification") == "runtime":
            runtime_refs = tool.get("runtime_evidence_refs")
            if not isinstance(runtime_refs, list) or not runtime_refs:
                errors.append(f"{prefix}.runtime_evidence_refs 在 runtime 验证时不得为空")
            elif all(nonempty_string(ref) for ref in runtime_refs):
                for ref_index, ref in enumerate(runtime_refs):
                    validate_source_ref(ref, f"{prefix}.runtime_evidence_refs[{ref_index}]",
                                        workspace, ticket_root, errors)

    checked_names = {str(tool.get("name", "")).casefold() for tool in tool_checks
                     if isinstance(tool, dict)}
    checked_runs = "\n".join(str(tool.get("run", "")) for tool in tool_checks
                              if isinstance(tool, dict)).casefold()
    for pattern in COMMAND_MENTION_PATTERNS:
        for match in pattern.finditer(guide):
            command = match.group(1).casefold()
            if command not in checked_names and command not in checked_runs:
                errors.append(f"guide 提到未纳入 tool_checks 的运行工具: {match.group(1)}")

    manual = audit.get("manual_checks")
    if not isinstance(manual, dict):
        errors.append("audit.manual_checks 必须是对象")
    else:
        for key in sorted(REQUIRED_MANUAL_CHECKS):
            if manual.get(key) is not True:
                errors.append(f"audit.manual_checks.{key} 必须为 true")

    if check_rounds:
        validate_clean_rounds(audit, guide, errors)
    return errors, guide, audit, guide_path, audit_path


def atomic_write_json(path, value):
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def record_round(args, guide, audit, audit_path):
    round_number = args.record_round
    rounds = audit.get("clean_rounds")
    if not isinstance(rounds, list):
        rounds = []
    if round_number == 1:
        rounds = []
    elif not rounds:
        raise ValueError("记录 round 2 前必须先由 linter 成功记录 round 1")
    if round_number == 2:
        round_one_errors = []
        validate_round_receipt(rounds[0], 1, RECEIPT_GENESIS, round_one_errors)
        if round_one_errors:
            raise ValueError("round 1 回执无效: " + "; ".join(round_one_errors))
    focus = ("来源、数字、工具可执行性" if round_number == 1
             else "新人可读性、图文一致、内部信息清洁")
    entry = {
        "round": round_number,
        "result": "pass",
        "focus": focus,
        "recorded_by": "lint_guide_contract.py",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "guide_sha256": hashlib.sha256(guide.encode("utf-8")).hexdigest(),
        "audit_basis_sha256": audit_basis_sha256(audit),
        "previous_receipt_sha256": (RECEIPT_GENESIS if round_number == 1
                                    else rounds[0]["receipt_sha256"]),
    }
    entry["receipt_sha256"] = receipt_sha256(entry)
    rounds = rounds[:round_number - 1] + [entry]
    audit["clean_rounds"] = rounds
    atomic_write_json(audit_path, audit)


def main():
    parser = argparse.ArgumentParser(description="校验 init --guide 外部指南与证据账本")
    parser.add_argument("--guide", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--forbid-term", action="append", default=[],
                        help="额外禁止出现在外部指南中的词，可重复")
    parser.add_argument("--allow-internal-terms", action="store_true",
                        help="指南主题就是 ICODE 时，关闭默认内部术语禁用表")
    parser.add_argument("--record-round", type=int, choices=[1, 2],
                        help="核心校验通过后，由 linter 原子记录第 1/2 轮审计回执")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    errors, guide, audit, guide_path, audit_path = validate(
        args, check_rounds=args.record_round is None)
    recorded_round = None
    if not errors and args.record_round is not None:
        try:
            record_round(args, guide, audit, audit_path)
            recorded_round = args.record_round
            if args.record_round == 2:
                errors, guide, audit, guide_path, audit_path = validate(args, check_rounds=True)
        except (OSError, ValueError) as exc:
            errors.append(f"审计轮次记录失败: {exc}")

    report = {"ok": not errors, "errors": errors, "guide": str(guide_path),
              "audit": str(audit_path), "recorded_round": recorded_round}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif errors:
        print("guide contract 校验失败:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    elif recorded_round is not None:
        print(f"guide contract 第 {recorded_round} 轮校验通过并已记录")
    else:
        print("guide contract 校验通过（两轮回执有效）")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
