"""lite 的纯合同与质量检查。受控写入仍由 icode_control 持有。"""
import copy
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def workflow_contract(meta, gates):
    if meta.get("mode") != "lite":
        return {"state_machine": gates["state_machine"],
                "execution_model": gates["execution_model"],
                "step_order": ["plan", "review", "merge", "code", "deepcheck", "audit"],
                "required_review_steps": ["code", "deepcheck", "audit"],
                "completion_step": "audit"}
    version = meta.get("workflow_version")
    if type(version) is not int or version != 1:
        raise ValueError("未知或缺失 lite workflow_version")
    profile = copy.deepcopy(gates["workflow_profiles"]["lite"][str(version)])
    return profile


def contract_digest(meta):
    # 验证记录可以追加；只对语义合同和需求链签名。
    return digest({k: meta.get(k) for k in (
        "requirement", "requirement_deltas", "scope_contract", "semantic_decisions",
        "acceptance_contract", "verification_contract", "impact_contract")})


def prepare_issues(meta):
    issues = []
    scope = meta.get("scope_contract") or {}
    if not isinstance(scope, dict) or not isinstance(scope.get("plan", {}), dict):
        return ["scope_contract_shape"]
    plan = scope.get("plan") or {}
    for rows in (scope.get("requirements", []), (meta.get("acceptance_contract") or {}).get("criteria", []),
                 (meta.get("verification_contract") or {}).get("required_cells", []),
                 meta.get("semantic_decisions", []), meta.get("requirement_deltas", [])):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return ["lite_contract_collection_shape"]
    for key in ("current_facts", "in_scope", "invariants", "requirements"):
        if not scope.get(key):
            issues.append("scope_contract." + key)
    for key in ("approach", "change_points", "risk_checks", "rollback"):
        if not plan.get(key):
            issues.append("scope_contract.plan." + key)
    requirements = scope.get("requirements") or []
    ids = [r.get("id") for r in requirements if isinstance(r, dict)]
    if not ids or len(ids) != len(requirements) or len(ids) != len(set(ids)) or not all(ids):
        issues.append("requirement_ids")
    sources = {"metadata.requirement"}
    for index, delta in enumerate(meta.get("requirement_deltas") or []):
        if not delta.get("source_ref"):
            issues.append("requirement_delta_source")
        sources.add("requirement_deltas." + str(index))
    for row in requirements:
        if not row.get("text") or row.get("source_ref") not in sources:
            issues.append("requirement_source")
    for decision in meta.get("semantic_decisions") or []:
        resolution = decision.get("resolution") or {}
        kind = resolution.get("kind")
        if decision.get("status") != "resolved" or not decision.get("selected"):
            issues.append("unresolved_decision")
        if kind not in {"user", "existing_constraint", "technical"} or not resolution.get("source_ref"):
            issues.append("decision_source")
        if kind == "user" and (resolution.get("source_ref") not in sources or decision.get("user_confirmed") is not True):
            issues.append("user_decision_source")
        if kind == "technical" and not resolution.get("reason"):
            issues.append("technical_decision_reason")
    criteria = (meta.get("acceptance_contract") or {}).get("criteria") or []
    ac_ids = [r.get("id") for r in criteria if isinstance(r, dict)]
    covered = set()
    if not ac_ids or len(ac_ids) != len(criteria) or len(ac_ids) != len(set(ac_ids)) or not all(ac_ids):
        issues.append("acceptance_criteria")
    for ac in criteria:
        refs = ac.get("requirement_ids") or []
        if not ac.get("expected") or not refs or not set(refs).issubset(ids):
            issues.append("acceptance_requirement_ref")
        covered.update(refs)
    if set(ids) - covered:
        issues.append("uncovered_requirement")
    verification = meta.get("verification_contract") or {}
    cells = verification.get("required_cells") or []
    mapped = set()
    if verification.get("required") is not True or not cells:
        issues.append("required_verification_cells")
    for cell in cells:
        refs = cell.get("criterion_ids") or []
        if not refs or not set(refs).issubset(ac_ids):
            issues.append("verification_criterion_ref")
        mapped.update(refs)
        if cell.get("external") is True and not cell.get("pending_reason"):
            issues.append("external_pending_reason")
    if set(ac_ids) - mapped:
        issues.append("unverified_criterion")
    if scope.get("stop_after") in {"prepare", "plan", "analysis"}:
        issues.append("user_stop_after_prepare")
    return sorted(set(issues))


def delivery_issues(meta, runs):
    """检查原始最新执行；候选资格由控制面的 eligible_latest_runs 统一判定。"""
    issues, pending = [], []
    cells = (meta.get("verification_contract") or {}).get("required_cells") or []
    for cell in cells:
        key = tuple(cell.get(k) for k in ("layer", "consumer", "scenario"))
        relevant = [r for r in runs if tuple(r.get(k) for k in (
            "layer", "consumer", "scenario")) == key]
        latest = relevant[-1] if relevant else None
        if not latest:
            if cell.get("external") is True and cell.get("pending_reason"):
                pending.append(key)
            else:
                issues.append("missing_local_evidence:" + "/".join(key))
            continue
        summary = latest.get("test_summary") or {}
        if latest.get("outcome") != "pass":
            issues.append("latest_verification_not_pass:" + "/".join(key))
        elif not latest.get("evidence") or not latest.get("baseline"):
            issues.append("verification_evidence_missing:" + "/".join(key))
        elif key[0] in {"unit", "host"} and (
                not summary or summary.get("passed", 0) <= 0
                or summary.get("failed", 0) or summary.get("errors", 0)):
            issues.append("behavior_test_not_executed:" + "/".join(key))
    if not cells:
        issues.append("required_verification_cells")
    if not any(c.get("layer") not in {"static", "build"} for c in cells):
        issues.append("behavior_evidence_required")
    return issues, pending


def review_issues(report, meta, candidate_id, attempt, *, allow_rejected=False):
    expected = {"schema_version": 1, "attempt": attempt,
                "candidate_id": candidate_id, "contract_digest": contract_digest(meta)}
    issues = ["review_" + key for key, value in expected.items() if report.get(key) != value]
    if allow_rejected and report.get("verdict") in {"changes_required", "blocked"}:
        return issues
    if report.get("verdict") != "pass":
        issues.append("review_not_pass")
    findings = report.get("findings")
    if not isinstance(findings, list) or any(f.get("blocking", True) and
            f.get("status") != "resolved" for f in findings):
        issues.append("review_open_findings")
    required = {c["id"] for c in (meta.get("acceptance_contract") or {}).get("criteria", [])}
    assessments = report.get("criteria") or []
    if len(assessments) != len(required) or {c.get("id") for c in assessments} != required or any(
            c.get("status") not in {"pass", "pending_external"} or not c.get("evidence")
            for c in assessments):
        issues.append("review_acceptance_coverage")
    if not report.get("requirement_assessment") or not report.get("implementation_assessment"):
        issues.append("review_semantic_assessment")
    return issues


def render_report(meta, review=None):
    scope = meta.get("scope_contract") or {}
    lines = ["# ICODE lite", "", "## 需求与方案", meta["requirement"], "",
             json.dumps(scope, ensure_ascii=False, indent=2), "", "## 实现变更"]
    lines.extend("- " + p for p in meta.get("code_files") or [])
    lines += ["", "## 审核与验收", "状态：" + meta["status"],
              "交付：" + str(meta.get("delivery_verdict", "处理中"))]
    if review:
        lines += ["审核结论：" + review["verdict"], review["requirement_assessment"],
                  review["implementation_assessment"]]
    for criterion in (meta.get("acceptance_contract") or {}).get("criteria", []):
        lines.append("- " + criterion["id"] + "：" + criterion["expected"])
    latest = {}
    for run in meta.get("verification_runs") or []:
        latest[tuple(run.get(k) for k in ("layer", "consumer", "scenario"))] = run
    for cell in (meta.get("verification_contract") or {}).get("required_cells", []):
        key = tuple(cell.get(k) for k in ("layer", "consumer", "scenario"))
        run = latest.get(key)
        result = str(run.get("outcome")) + "；" + str(run.get("evidence")) if run else cell.get("pending_reason", "尚未执行")
        lines.append("- " + "/".join(key) + "：" + result)
    return "\n".join(lines) + "\n"
