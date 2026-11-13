#!/usr/bin/env python3
"""Compact, read-only ticket facts; never writes or starts a crosscheck round."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys


spec = importlib.util.spec_from_file_location("context_delivery", Path(__file__).with_name("icode_delivery.py"))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def build_ticket_context(out_dir, *, purpose="general"):
    """Fresh independent review must branch BEFORE any prior CC file is read."""
    if purpose not in {"general", "crosscheck_fresh"}:
        raise ValueError("unknown ticket context purpose")
    report = delivery.build_delivery_report(out_dir, include_crosscheck=purpose != "crosscheck_fresh")
    result = {key: report[key] for key in ("schema_version", "read_only", "derived", "ok", "generated_at",
        "ticket_id", "metadata_revision", "goal", "mode", "status", "delivery_verdict", "candidate",
        "latest_patch", "next_actions", "materials", "issues")}
    result["purpose"] = purpose
    latest_patch = result.get("latest_patch")
    if isinstance(latest_patch, dict) and isinstance(latest_patch.get("files"), list):
        result["latest_patch"] = dict(latest_patch, files=latest_patch["files"][:20], file_count=len(latest_patch["files"]))
    result["internal_review"] = {key: report["internal_review"][key] for key in ("state", "ok", "reasons")
                                 if key in report["internal_review"]}
    verification = report["verification"]
    debt = verification["debt"]
    result["verification"] = {"state": verification["state"], "ok": verification["ok"],
        "run_count": len(verification["runs"]), "debt_status": debt.get("tracking_status"),
        "pending_count": debt.get("pending_units"), "reasons": verification.get("reasons") or []}
    result["changes"] = {key: {"count": len(report["submission_inventory"][key]),
        "paths": report["submission_inventory"][key][:20]} for key in ("intended", "side_effects", "unknown")}
    if purpose == "crosscheck_fresh":
        result["changes"] = {"state": "inventory_withheld_for_fresh",
                             "declared_paths": report["declared_changes"][:20]}
    result["crosscheck"] = {key: report["crosscheck"][key] for key in (
        "state", "required", "round", "candidate_id", "recorded_candidate_id", "tracking_status")
        if key in report["crosscheck"]}
    result["overview"] = {"state": "withheld_for_fresh"}
    if purpose == "general":
        saved = Path(out_dir) / "delivery_validation_report.md"
        result["overview"] = {"state": "missing"}
        if saved.exists() or saved.is_symlink():
            try:
                # Only the bounded binding header is needed. Never ingest the
                # old report body (which can contain optional review results).
                if any(p.is_symlink() for p in (saved, *saved.parents)) or saved.stat().st_nlink != 1:
                    raise ValueError("unsafe saved overview")
                with saved.open("rb") as stream:
                    header = stream.read(16384).decode("utf-8-sig", errors="replace")
                match = re.search(r"<!-- icode-overview-binding (\{[^\n]*\}) -->", header)
                snapshot = json.loads(match.group(1)) if match else {}
                result["overview"] = compare_overview(snapshot, result)
                result["overview"]["generated_at"] = snapshot.get("generated_at")
            except (ValueError, OSError, UnicodeError):
                result["overview"] = {"state": "blocked", "reasons": ["unreadable_overview_binding"]}
    return result


def compare_overview(snapshot, current):
    """Compare projection identity; current never implies verified evidence."""
    if not isinstance(snapshot, dict) or not snapshot.get("metadata_revision"):
        return {"state": "untracked", "reasons": ["missing_overview_revision"]}
    reasons = []
    for field in ("ticket_id", "metadata_revision", "candidate"):
        if snapshot.get(field) != current.get(field):
            reasons.append(field + "_changed")
    if "pending_transaction" in current["issues"]:
        reasons.append("pending_transaction")
    return {"state": "stale" if reasons else "current", "reasons": reasons}


def overview_freshness(out_dir, snapshot):
    """Read current authority without ever opening a historical overview/CC."""
    return compare_overview(snapshot, build_ticket_context(out_dir, purpose="crosscheck_fresh"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--purpose", choices=("general", "crosscheck_fresh"), default="general")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build_ticket_context(args.dir, purpose=args.purpose), ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, delivery.ctl.ControlError, delivery.debt.VerificationDebtError) as exc:
        print(json.dumps({"ok": False, "read_only": True, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
