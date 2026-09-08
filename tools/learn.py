#!/usr/bin/env python3
"""从项目内 ICODE 观测生成只读学习建议，不修改或发布 Skill。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from propose_skill_candidates import load_runs


SCHEMA_VERSION = 1
CLASSIFICATIONS = {
    "reuse_existing_skill",
    "compose_existing_skills",
    "improve_existing_skill",
    "create_new_skill",
    "automate_in_tooling",
    "no_action",
}
MECHANICAL_TERMS = {
    "deduplicate",
    "duplicate",
    "fingerprint",
    "format",
    "hash",
    "lint",
    "parse",
    "parser",
    "regex",
    "schema",
    "validator",
}


class LearnError(RuntimeError):
    """输入合同或报告写入失败。"""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearnError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LearnError(f"JSON 顶层必须是 object: {path}")
    return value


def _parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LearnError("--since 必须是 ISO 8601 日期或时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_routes(repository_root: Path) -> list[dict[str, Any]]:
    value = _load_json(repository_root / "mcp/workflow-gate/skill-routes.json")
    routes = value.get("routes") or []
    return [route for route in routes if isinstance(route, dict)]


def _load_installed_skills(repository_root: Path) -> set[str]:
    value = _load_json(repository_root / "skill-packs/manifest.json")
    return {
        item["name"]
        for item in value.get("skills") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _trigger_terms(trigger: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9_]+", trigger.lower())
        if token
    }


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _matching_routes(trigger: str, routes: list[dict[str, Any]]) -> list[str]:
    normalized = trigger.lower()
    terms = _trigger_terms(trigger)
    matches = []
    for route in routes:
        triggers = route.get("triggers") or []
        if any(
            isinstance(route_trigger, str)
            and (route_trigger.lower() in terms or route_trigger.lower() in normalized)
            for route_trigger in triggers
        ):
            skill = route.get("skill")
            if isinstance(skill, str) and skill not in matches:
                matches.append(skill)
    return matches


def classify_candidate(
    candidate: dict[str, Any],
    routes: list[dict[str, Any]],
    installed_skills: set[str],
) -> tuple[str, list[str], str]:
    """优先复用、组合或工具化，最后才允许提出新 Skill。"""
    runs = candidate.get("runs") or []
    degraded_runs = [
        run
        for run in runs
        if run.get("adopted") is True
        and run.get("result") in {"failure", "degraded"}
        and run.get("skill") in installed_skills
    ]
    degraded_skills = sorted({run.get("skill") for run in degraded_runs})
    if len(degraded_runs) >= 2 and degraded_skills:
        return (
            "improve_existing_skill",
            degraded_skills,
            "既有 Skill 被采用后仍连续 failure/degraded，应先补合同与评测。",
        )

    route_matches = _matching_routes(str(candidate.get("trigger") or ""), routes)
    if len(route_matches) > 1:
        return (
            "compose_existing_skills",
            route_matches,
            "同一模式命中多个既有路由，应先编排组合而不是复制正文。",
        )
    if len(route_matches) == 1:
        return (
            "reuse_existing_skill",
            route_matches,
            "已有 Skill 路由覆盖该触发，只需复核触发词与输入合同。",
        )

    terms = _trigger_terms(str(candidate.get("trigger") or ""))
    if terms & MECHANICAL_TERMS:
        return (
            "automate_in_tooling",
            [],
            "该模式可由确定性解析、校验或去重工具执行，不应固化为文字 Skill。",
        )
    if int(candidate.get("occurrences") or 0) < 3:
        return (
            "no_action",
            [],
            "独立实例不足 3 个，保留观测但不晋升候选。",
        )
    return (
        "create_new_skill",
        [],
        "达到最小重复阈值且无等价路由；仍须证明跨项目复用并通过 Skill TDD。",
    )


def _related_ticket_observations(metadata_path: Path) -> dict[str, int]:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"claims": 0, "requirement_deltas": 0, "patches": 0, "deviations": 0}
    if not isinstance(metadata, dict):
        return {"claims": 0, "requirement_deltas": 0, "patches": 0, "deviations": 0}
    return {
        "claims": len(metadata.get("claims") or []),
        "requirement_deltas": len(metadata.get("requirement_deltas") or []),
        "patches": len(metadata.get("patch_history") or []),
        "deviations": len(metadata.get("code_deviations") or []),
    }


def build_learning_report(
    project: str | Path,
    ticket: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """返回限定项目范围内的观测、分类、证据引用与人工晋升门。"""
    project_root = Path(project).resolve()
    if not project_root.is_dir() or project_root == Path(project_root.anchor):
        raise LearnError(f"project 必须是已存在的非根目录: {project_root}")
    since_dt = _parse_since(since)
    repository_root = Path(__file__).resolve().parents[1]
    routes = _load_routes(repository_root)
    installed_skills = _load_installed_skills(repository_root)

    parse_failures: list[dict[str, str]] = []
    independent: dict[tuple[str, str], tuple[str | None, Path, dict[str, Any]]] = {}
    for ticket_id, metadata_path, run in load_runs(project_root, diagnostics=parse_failures):
        if ticket and ticket not in {str(ticket_id or ""), metadata_path.parent.name}:
            continue
        run_at = _parse_at(run.get("at"))
        if since_dt is not None and (run_at is None or run_at < since_dt):
            continue
        observation_id = str(ticket_id or metadata_path)
        key = (run["trigger"], observation_id)
        previous = independent.get(key)
        if previous is None:
            independent[key] = (ticket_id, metadata_path, run)
            continue
        previous_at = _parse_at(previous[2].get("at"))
        if previous_at is None or (run_at is not None and run_at >= previous_at):
            independent[key] = (ticket_id, metadata_path, run)

    grouped: dict[str, list[tuple[str | None, Path, dict[str, Any]]]] = defaultdict(list)
    for ticket_id, metadata_path, run in independent.values():
        grouped[run["trigger"]].append((ticket_id, metadata_path, run))

    candidates = []
    for trigger, matches in sorted(grouped.items()):
        runs = [run for _, _, run in matches]
        candidate: dict[str, Any] = {
            "trigger": trigger,
            "occurrences": len(matches),
            "tickets": sorted({str(ticket_id) for ticket_id, _, _ in matches if ticket_id}),
            "evidence_refs": sorted(
                {
                    ref
                    for run in runs
                    for ref in _string_values(run.get("evidence_refs"))
                }
            ),
            "unique_findings": sorted(
                {
                    finding
                    for run in runs
                    for finding in _string_values(run.get("unique_findings"))
                }
            ),
            "average_elapsed_ms": sum(_nonnegative_int(run.get("elapsed_ms")) for run in runs) // len(runs),
            "average_estimated_tokens": sum(
                _nonnegative_int(run.get("estimated_tokens")) for run in runs
            ) // len(runs),
            "runs": runs,
        }
        classification, skills, reason = classify_candidate(candidate, routes, installed_skills)
        if classification not in CLASSIFICATIONS:
            raise LearnError(f"未知分类: {classification}")
        observation_totals = {"claims": 0, "requirement_deltas": 0, "patches": 0, "deviations": 0}
        for metadata_path in {path for _, path, _ in matches}:
            for key, value in _related_ticket_observations(metadata_path).items():
                observation_totals[key] += value
        candidate.pop("runs")
        candidate.update(
            {
                "classification": classification,
                "matched_skills": skills,
                "reason": reason,
                "related_observations": observation_totals,
                "promotion_required": classification
                in {"improve_existing_skill", "create_new_skill"},
            }
        )
        candidates.append(candidate)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial" if parse_failures else "complete",
        "source_scope": {
            "project": str(project_root),
            "ticket": ticket,
            "since": since,
        },
        "observations": sum(item["occurrences"] for item in candidates),
        "candidates": candidates,
        "parse_failures": parse_failures,
        "promotion_gates": [
            "separate normal ICODE development ticket",
            "RED baseline fixture before editing a Skill",
            "GREEN with-skill comparison and regression",
            "trigger precision and false-positive check",
            "human approval before manifest/routes update",
            "separate sync-to-global dry-run and apply decision",
        ],
        "read_only_sources": True,
        "side_effects": ["write learning report artifacts under the selected output directory"],
        "manifest_modified": False,
        "routes_modified": False,
    }


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:48]}-{digest}" if normalized else digest


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ICODE 学习报告",
        "",
        f"- 项目：`{report['source_scope']['project']}`",
        f"- 观测数：{report['observations']}",
        f"- 状态：`{report['status']}`（解析失败 {len(report['parse_failures'])} 项）",
        "- 边界：只读扫描源工单；本报告不修改 manifest/routes，也不执行全局同步。",
        "",
        "| Trigger | 次数 | 分类 | 既有 Skill | 理由 |",
        "|---|---:|---|---|---|",
    ]
    for item in report["candidates"]:
        skills = ", ".join(item["matched_skills"]) or "-"
        lines.append(
            f"| {item['trigger']} | {item['occurrences']} | {item['classification']} | "
            f"{skills} | {item['reason']} |"
        )
    lines.extend(
        [
            "",
            "## 晋升门",
            "",
            "创建或修改 Skill 必须另开正常 ICODE 开发工单：先记录 RED 基线，"
            "再完成 GREEN 对照和回归；只有人工批准后才能更新 manifest/routes。",
            "同步全局副本仍需单独执行 dry-run，并由用户决定是否 `--apply`。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_candidate(item: dict[str, Any]) -> str:
    skills = ", ".join(item["matched_skills"]) or "无"
    return "\n".join(
        [
            f"# 学习候选：{item['trigger']}",
            "",
            "> 这是候选说明，不是 SKILL.md，不会被自动安装或路由。",
            "",
            f"- 分类：`{item['classification']}`",
            f"- 独立实例：{item['occurrences']}",
            f"- 匹配既有 Skill：{skills}",
            f"- 理由：{item['reason']}",
            f"- 证据引用：{', '.join(item['evidence_refs']) or '无'}",
            "",
            "## 后续门禁",
            "",
            "1. 人工确认该模式是否跨项目复用且值得处理。",
            "2. 另开正常 ICODE 工单，并在修改 Skill 前保存 RED 失败夹具。",
            "3. 修改后运行同场景 GREEN 对照、误触发检查和完整回归。",
            "4. 人工批准后才更新 manifest/routes；全局同步另行确认。",
            "",
        ]
    )


def write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        output_dir / "learning_report.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write(output_dir / "learning_report.md", _render_markdown(report))
    for item in report["candidates"]:
        if item["classification"] == "no_action":
            continue
        _atomic_write(
            output_dir / f"skill_candidate_{_slug(item['trigger'])}.md",
            _render_candidate(item),
        )


def _default_output(project: Path) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return project / ".icode_output" / "learn" / run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读聚合 ICODE 使用观测并生成 Skill/工具化学习建议"
    )
    parser.add_argument("--project", required=True, help="只扫描该项目根目录")
    parser.add_argument("--ticket", help="仅分析 ticket_id 或工单目录名")
    parser.add_argument("--since", help="ISO 8601 日期或时间下界")
    parser.add_argument("--output-dir", help="报告目录；缺省写入项目 .icode_output/learn/<run-id>")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project = Path(args.project).resolve()
        report = build_learning_report(project, ticket=args.ticket, since=args.since)
        output = Path(args.output_dir).resolve() if args.output_dir else _default_output(project)
        learning_root = (project / ".icode_output" / "learn").resolve()
        try:
            output.relative_to(learning_root)
        except ValueError as exc:
            raise LearnError(
                f"output-dir 必须位于 project/.icode_output/learn 内: {output}"
            ) from exc
        write_report(output, report)
    except LearnError as exc:
        print(f"learn: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"learn: 文件系统错误: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(output),
                "observations": report["observations"],
                "candidates": len(report["candidates"]),
                "manifest_modified": False,
                "routes_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
