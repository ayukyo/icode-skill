#!/usr/bin/env python3
"""Read-only delivery facts. Optional output is a disposable derived report.

No workflow transition, recovery, lock, candidate capture writer, Git fetch or
crosscheck round is invoked here. A report does not establish new evidence.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ctl = load_module("delivery_control", "tools/icode_control.py")
guard = load_module("delivery_submission", "scripts/submission_guard.py")
debt = load_module("delivery_debt", "tools/verification_debt.py")
crosscheck = load_module("delivery_crosscheck", "tools/icode_crosscheck.py")


def run_summary(run):
    # Retain structured origins; omit the large source manifest/candidate.
    fields = ("run_id", "kind", "outcome", "candidate_id", "environment", "layer", "consumer", "scenario",
              "artifact_identity", "build_source", "baseline", "evidence", "device", "window",
              "supersedes_run_id", "reuse", "build_provenance", "deploy_provenance", "runtime_provenance")
    result = {key: run[key] for key in fields if key in run}
    direct = run.get("direct_test")
    if isinstance(direct, dict):
        code = direct.get("binary_exit_code")
        binary_result = "not_run"
        if direct.get("binary_executed") is True and type(code) is int:
            binary_result = "pass" if code == 0 else "fail"
        result["direct_test"] = dict(direct, binary_result=binary_result)
    return result


def build_delivery_report(out_dir, meta=None):
    out_dir = Path(out_dir).expanduser().resolve()
    meta = ctl.load_metadata(out_dir) if meta is None else meta
    issues = []
    if (out_dir / ctl.TXN_NAME).exists():
        issues.append("pending_transaction")
    enabled = (meta.get("candidate_tracking") or {}).get("mode") == "enabled"
    context = ctl.verification_context(out_dir, meta)
    if enabled:
        issues.extend(context["reasons"])
    elif meta.get("schema_version") == 3:
        _events, problems = ctl.verify_event_chain(out_dir, meta)
        issues.extend(problems)
    review = ctl.candidate_module().delivery_freshness(meta, context["current"], context["events"])
    if enabled and context["reasons"]:
        review = dict(review, ok=False, state="blocked", reasons=context["reasons"])
    verification = ctl.verification_evidence_module().assess(meta, context)
    inventory = guard.build_submission_inventory(meta, out_dir / ctl.METADATA_NAME)
    verification_debt = debt.analyze_ticket(meta, out_dir, context=context)
    try:
        optional_review = crosscheck.crosscheck_freshness(out_dir)
    except crosscheck.CrosscheckError as exc:
        # An invalid optional report cannot certify a result. It remains outside
        # the main delivery gate; corruption is clearly reported for the user.
        optional_review = dict(exc.report(), read_only=True, required=False, state="blocked")
    runs = [run_summary(run) for run in meta.get("verification_runs") or [] if isinstance(run, dict)]
    verification = dict(verification, contract=meta.get("verification_contract"),
                        debt=verification_debt, runs=runs)
    inventory_summary = {"ok": inventory["ok"], "read_only": True,
        "repositories": [{"repo_path": row["repo_path"], "change_count": len(row["changes"])} for row in inventory["repositories"]],
        "intended": [row["path"] for row in inventory["intended"]],
        "side_effects": [row["path"] for row in inventory["side_effects"]],
        "unknown": [row["path"] for row in inventory["unknown"]],
        "suggested_exclusions": inventory["suggested_exclusions"], "violations": inventory["violations"]}
    # Legacy reviews remain untracked observations; only enabled tickets acquire
    # candidate review requirements. Optional crosscheck never enters this gate.
    ok = not issues and inventory["ok"] and verification["ok"] and (review["ok"] if enabled else True)
    return {"schema_version": 1, "read_only": True, "derived": True, "ok": ok,
            "generated_at": datetime.now(timezone.utc).isoformat(), "ticket_id": meta.get("ticket_id"),
            "status": meta.get("status"), "delivery_verdict": meta.get("delivery_verdict"),
            "candidate": {"state": review["state"], "candidate_id": review.get("candidate_id"), "enabled": enabled},
            "internal_review": review, "verification": verification,
            "submission_inventory": inventory_summary, "crosscheck": optional_review, "issues": issues}


def markdown_report(report):
    lines = ["# ICODE delivery report", "", "> 派生只读事实；生成报告不等同于执行验证。", "",
             f"- 工单：`{report['ticket_id']}`；当前状态：`{report['status']}`",
             f"- 候选：`{report['candidate']['candidate_id'] or report['candidate']['state']}`",
             f"- 内部评审：`{report['internal_review']['state']}`",
             f"- 验证：`{report['verification']['state']}`",
             f"- 可选 crosscheck：`{report['crosscheck']['state']}`（不进入主门禁）", ""]
    for group in ("intended", "side_effects", "unknown"):
        paths = report["submission_inventory"][group]
        lines.append(f"- {group}：{json.dumps(paths, ensure_ascii=True)}")
    lines.extend(["", "| Run | Outcome | Environment | Target built | Binary executed | Binary result | Runner discovered | CI registered |",
                  "|---|---|---|---|---|---|---|---|"])
    for run in report["verification"]["runs"]:
        direct = run.get("direct_test") or {}
        values = (run.get("run_id"), run.get("outcome"), run.get("environment"), direct.get("target_built"),
                  direct.get("binary_executed"), direct.get("binary_result"), direct.get("test_runner_discovered"), direct.get("ci_registered"))
        lines.append("| " + " | ".join(str(v if v is not None else "untracked").replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    for run in report["verification"]["runs"]:
        lines.extend(["", f"- `{run.get('run_id')}` 来源：kind=`{run.get('kind')}`；candidate=`{run.get('candidate_id', 'untracked')}`；artifact=`{run.get('artifact_identity', 'untracked')}`；device=`{run.get('device', 'untracked')}`"])
        reuse = run.get("reuse") or {}
        if reuse:
            lines.append(f"  - 复用来源：`{reuse.get('origin_run_id')}`；原因：{reuse.get('reason')}")
        for kind in ("build", "deploy", "runtime"):
            origin = run.get(kind + "_provenance")
            if isinstance(origin, dict):
                selected = {key: origin[key] for key in ("source_candidate_id", "artifacts", "artifact_identity", "device", "evidence_refs", "build_dir") if key in origin}
                lines.append(f"  - {kind}：{json.dumps(selected, ensure_ascii=True, separators=(',', ':'))}")
        if run.get("evidence"):
            lines.append("  - 证据：" + str(run["evidence"]).replace("\n", " "))
    return "\n".join(lines) + "\n"


def validate_outputs(out_dir, output, markdown):
    """Only named disposable reports may be written, never existing artifacts.

    Restrict names rather than trying to guess all source/inspection artifact
    extensions. The common validator also detects symlink and hardlink aliases.
    """
    out_dir = Path(out_dir).expanduser().resolve()
    destinations = [(output, ".json")] + ([(markdown, ".md")] if markdown else [])
    for value, suffix in destinations:
        path = Path(value).expanduser()
        if path.suffix != suffix or not (path.stem in {"delivery_report", "delivery_validation_report"} or path.stem.startswith("delivery_report_")):
            raise ValueError("输出必须为工单内 delivery_report[_名称] 或 delivery_validation_report.json/.md 派生报告")
        if ".crosscheck" in path.resolve().parts:
            raise ValueError("输出不得覆盖原 crosscheck 产物")
    protected = [p for p in out_dir.rglob("*") if p.is_file() and not (p.stem in {"delivery_report", "delivery_validation_report"} or p.stem.startswith("delivery_report_"))]
    meta = ctl.load_metadata(out_dir)
    workspace = ctl.execution_workspace(out_dir, meta)
    project_root = crosscheck.derive_project_root(out_dir, meta)
    crosscheck_root = project_root / ".icode_output/.crosscheck"
    if crosscheck_root.is_dir():
        protected.extend(path for path in crosscheck_root.rglob("*") if path.is_file())
    # Formal delivery roles and controlled artifact receipts remain source even
    # when their filename happens to look like a disposable delivery report.
    delivery_files = meta.get("delivery_files") or {}
    delivery_values = delivery_files.values() if isinstance(delivery_files, dict) else delivery_files
    for item in delivery_values:
        value = item if isinstance(item, str) else item.get("path") or item.get("file") if isinstance(item, dict) else None
        if isinstance(value, str):
            protected.extend((out_dir / value, workspace / value))
    if meta.get("schema_version") == 3 or (meta.get("candidate_tracking") or {}).get("mode") == "enabled":
        events, problems = ctl.verify_event_chain(out_dir, meta)
        if problems or (out_dir / ctl.TXN_NAME).exists():
            raise ValueError("事件链或事务未完成时只能stdout查询，不能写入报告")
        for event in events:
            if event.get("event_type") != "artifact_written":
                continue
            payload = event.get("payload") or {}
            value = payload.get("path")
            if isinstance(value, str):
                protected.extend((out_dir / value, workspace / value))
    # Protect all tracked/untracked source aliases without scanning file bytes.
    if (workspace / ".git").exists():
        raw = guard._inventory_git(workspace, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        protected.extend(workspace / os.fsdecode(p) for p in raw.split(b"\0") if p and ".icode_output" not in Path(os.fsdecode(p)).parts)
    return debt.validate_report_paths(Path(output), Path(markdown) if markdown else None,
                                     allowed_root=out_dir, protected_sources=protected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="工单目录；只读默认输出JSON到stdout")
    parser.add_argument("--output", help="按需写入工单内delivery_report[_名称].json")
    parser.add_argument("--markdown", help="按需写入delivery_report[_名称].md；要求--output")
    args = parser.parse_args(argv)
    try:
        if args.markdown and not args.output:
            raise ValueError("--markdown 要求 --output")
        outputs = validate_outputs(args.dir, args.output, args.markdown) if args.output else None
        report = build_delivery_report(args.dir)
        if outputs:
            debt.write_reports(report, outputs[0], outputs[1], markdown_report)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0  # Query success; report.ok carries delivery qualification.
    except (ValueError, OSError, ctl.ControlError, debt.VerificationDebtError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "read_only": True}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
