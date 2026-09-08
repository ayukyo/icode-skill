#!/usr/bin/env python3
"""只读聚合 ICODE skill-run 观测；重复不足阈值时不输出候选。"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_runs(root, diagnostics=None):
    root = Path(root).resolve()
    rows = []
    for path in sorted(root.rglob(".ico_metadata.json")):
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            if diagnostics is not None:
                diagnostics.append({"path": str(path), "error": f"out_of_scope: {exc}"})
            continue
        if path.is_symlink():
            if diagnostics is not None:
                diagnostics.append({"path": str(path), "error": "symlink_metadata_skipped"})
            continue
        try:
            metadata = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if diagnostics is not None:
                diagnostics.append({"path": str(path), "error": str(exc)})
            continue
        if not isinstance(metadata, dict):
            if diagnostics is not None:
                diagnostics.append({"path": str(path), "error": "metadata_top_level_not_object"})
            continue
        extensions = metadata.get("extensions") or {}
        skills = extensions.get("skills") if isinstance(extensions, dict) else None
        if not isinstance(extensions, dict) or (skills is not None and not isinstance(skills, dict)):
            if diagnostics is not None:
                diagnostics.append({"path": str(path), "error": "invalid_extensions_skills"})
            continue
        runs = ((skills or {}).get("runs") or [])
        if not isinstance(runs, list):
            continue
        for run in runs:
            if not isinstance(run, dict):
                continue
            if run.get("skill") != "unrouted" and not (
                    run.get("adopted") is True
                    and run.get("result") in {"failure", "degraded"}):
                continue
            trigger = run.get("trigger")
            if not isinstance(trigger, str) or not trigger.strip():
                continue
            rows.append((metadata.get("ticket_id"), resolved, run))
    return rows


def build_candidates(rows, minimum):
    grouped = defaultdict(list)
    for ticket_id, path, run in rows:
        grouped[run["trigger"]].append((ticket_id, path, run))
    candidates = []
    for trigger, matches in sorted(grouped.items()):
        if len(matches) < minimum:
            continue
        evidence_refs = sorted({ref for _, _, run in matches
                                for ref in run.get("evidence_refs", [])})
        findings = sorted({finding for _, _, run in matches
                           for finding in run.get("unique_findings", [])})
        elapsed = [run.get("elapsed_ms", 0) for _, _, run in matches]
        tokens = [run.get("estimated_tokens", 0) for _, _, run in matches]
        candidates.append({
            "trigger": trigger,
            "occurrences": len(matches),
            "tickets": sorted({ticket_id for ticket_id, _, _ in matches if ticket_id}),
            "evidence_refs": evidence_refs,
            "unique_findings": findings,
            "average_elapsed_ms": sum(elapsed) // len(elapsed),
            "average_estimated_tokens": sum(tokens) // len(tokens),
            "suggested_action": "review_for_skill_or_existing_route",
        })
    return candidates


def main():
    parser = argparse.ArgumentParser(description="只读提炼重复 skill gap 候选")
    parser.add_argument("--root", required=True)
    parser.add_argument("--min-occurrences", type=int, default=3)
    args = parser.parse_args()
    if args.min_occurrences < 3:
        parser.error("--min-occurrences 不能小于 3")
    rows = load_runs(args.root)
    report = {
        "schema_version": 1,
        "root": str(Path(args.root).resolve()),
        "min_occurrences": args.min_occurrences,
        "observations": len(rows),
        "candidates": build_candidates(rows, args.min_occurrences),
        "read_only": True,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
