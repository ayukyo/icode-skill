#!/usr/bin/env python3
"""只读汇总 ICODE 分层验证债务，并生成单工单验证计划。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
METADATA_NAME = ".ico_metadata.json"
INDEX_PATH = Path.home() / ".claude" / "icode_data" / "index.json"
VERIFICATION_LAYERS = {
    "static",
    "unit",
    "build",
    "host",
    "deploy",
    "delivery",
    "consumption",
    "settings_preview",
    "operation_view",
    "closed_client",
    "physical",
}


class VerificationDebtError(RuntimeError):
    """可向命令行安全展示的输入/文件错误。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationDebtError(f"{label} 不存在: {path}") from exc
    except OSError as exc:
        raise VerificationDebtError(f"无法读取 {label}: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationDebtError(f"{label} JSON 解析失败: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationDebtError(f"{label} 顶层必须是对象: {path}")
    return value


def _stage_text(path: Path, content: str) -> Path:
    """在目标目录完整落盘，尚不替换可见目标。"""
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(temporary)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _backup_file(path: Path) -> Path:
    """复制现有目标用于跨两个 replace 的失败回滚。"""
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".rollback", dir=str(path.parent)
    )
    try:
        with path.open("rb") as source, os.fdopen(fd, "wb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, path.stat().st_mode)
        return Path(temporary)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def atomic_write_pair(items: Sequence[Tuple[Path, str]]) -> None:
    """先暂存两份报告；任一替换失败时恢复所有旧目标。"""
    staged: Dict[Path, Path] = {}
    backups: Dict[Path, Optional[Path]] = {}
    committed: List[Path] = []
    try:
        for path, content in items:
            staged[path] = _stage_text(path, content)
        for path, _content in items:
            backups[path] = _backup_file(path) if path.exists() else None
        try:
            for path, _content in items:
                os.replace(staged[path], path)
                committed.append(path)
        except BaseException as write_error:
            rollback_errors: List[str] = []
            for path in reversed(committed):
                backup = backups[path]
                try:
                    if backup is None:
                        _unlink_if_present(path)
                    else:
                        os.replace(backup, path)
                        backups[path] = None
                except OSError as exc:
                    rollback_errors.append(f"{path}: {exc}")
            if rollback_errors:
                raise VerificationDebtError(
                    "报告替换失败且回滚不完整: " + "; ".join(rollback_errors)
                ) from write_error
            raise
    finally:
        for temporary in staged.values():
            _unlink_if_present(temporary)
        for backup in backups.values():
            if backup is not None:
                _unlink_if_present(backup)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_project_child(project: Path, relative_path: str) -> Optional[Path]:
    """解析 index out_dir；越出项目根的条目不参与扫描。"""
    candidate = (project / relative_path).resolve()
    try:
        candidate.relative_to(project)
    except ValueError:
        return None
    return candidate


def _metadata_from_index_pointer(entry: Dict[str, Any], pointer_name: str) -> Optional[Path]:
    """安全解析归档/备份指针，并要求指针内 metadata 身份与 index 一致。"""
    raw = entry.get(pointer_name)
    if not nonempty_string(raw):
        return None
    raw_pointer = Path(raw).expanduser()
    # 控制面指针可指向目录或 metadata 文件，但不能借符号链接跳到未声明位置。
    if raw_pointer.is_symlink():
        return None
    try:
        pointer = raw_pointer.resolve()
    except (OSError, RuntimeError):
        return None
    if pointer == Path(pointer.anchor):
        return None
    out_dir = entry.get("out_dir") if nonempty_string(entry.get("out_dir")) else ""
    candidates = [pointer] if pointer.name == METADATA_NAME else [pointer / METADATA_NAME]
    if pointer.is_dir() and out_dir:
        candidates.extend(
            [
                pointer / out_dir / METADATA_NAME,
                pointer / Path(out_dir).name / METADATA_NAME,
            ]
        )
    expected_ticket = entry.get("ticket_id")
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            resolved_candidate = candidate.resolve(strict=True)
            if pointer.name == METADATA_NAME:
                if resolved_candidate != pointer:
                    continue
            else:
                resolved_candidate.relative_to(pointer)
            value = json.loads(resolved_candidate.read_text(encoding="utf-8"))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        actual_ticket = value.get("ticket_id")
        if nonempty_string(expected_ticket) and actual_ticket != expected_ticket:
            continue
        return resolved_candidate
    return None


def discover_ticket_metadata(project: Path) -> Tuple[List[Path], Dict[str, Any], List[str]]:
    """合并只读全局 index 指针与项目内 metadata 扫描结果。"""
    candidates: Dict[Path, None] = {}
    errors: List[str] = []
    index_info: Dict[str, Any] = {
        "path": str(INDEX_PATH),
        "loaded": False,
        "matching_entries": 0,
    }

    if INDEX_PATH.is_file():
        try:
            index = load_json(INDEX_PATH, "ticket index")
            index_info["loaded"] = True
            tickets = index.get("tickets")
            if not isinstance(tickets, list):
                errors.append(f"ticket index.tickets 非数组: {INDEX_PATH}")
            else:
                for entry in tickets:
                    if not isinstance(entry, dict):
                        continue
                    project_path = entry.get("project_path")
                    out_dir = entry.get("out_dir")
                    if not nonempty_string(project_path) or not nonempty_string(out_dir):
                        continue
                    try:
                        indexed_project = Path(project_path).expanduser().resolve()
                    except (OSError, RuntimeError):
                        continue
                    if indexed_project != project:
                        continue
                    ticket_dir = safe_project_child(project, out_dir)
                    if ticket_dir is None:
                        errors.append(
                            f"index out_dir 越出 project，已忽略: ticket={entry.get('ticket_id')} "
                            f"out_dir={out_dir}"
                        )
                        continue
                    index_info["matching_entries"] += 1
                    metadata_path = ticket_dir / METADATA_NAME
                    if metadata_path.is_file():
                        candidates[metadata_path.resolve()] = None
                    else:
                        fallback = None
                        for pointer_name in ("archive_path", "artifact_root", "backup_path"):
                            fallback = _metadata_from_index_pointer(entry, pointer_name)
                            if fallback is not None:
                                break
                        if fallback is not None:
                            candidates[fallback] = None
                        else:
                            errors.append(
                                f"index 指向的 metadata 不存在且归档/备份不可解析: "
                                f"ticket={entry.get('ticket_id')} path={metadata_path}"
                            )
        except VerificationDebtError as exc:
            errors.append(str(exc))

    output_root = project / ".icode_output"
    if output_root.is_dir():
        for metadata_path in sorted(output_root.rglob(METADATA_NAME)):
            try:
                resolved = metadata_path.resolve()
                resolved.relative_to(project)
            except (OSError, RuntimeError, ValueError):
                errors.append(f"metadata 路径不可安全解析，已忽略: {metadata_path}")
                continue
            if metadata_path.is_file():
                candidates[resolved] = None

    return sorted(candidates), index_info, errors


def required_dimensions(contract: Dict[str, Any]) -> Optional[Tuple[List[str], List[str], List[str]]]:
    keys = ("required_layers", "required_consumers", "required_scenarios")
    dimensions: List[List[str]] = []
    for key in keys:
        values = contract.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not nonempty_string(value) for value in values)
        ):
            return None
        normalized = [value.strip() for value in values]
        # 与 lint_workflow_contract.py 保持一致：重复维度是合同错误，不能静默修复。
        if len(normalized) != len(set(normalized)):
            return None
        dimensions.append(normalized)
    if not set(dimensions[0]).issubset(VERIFICATION_LAYERS):
        return None
    return dimensions[0], dimensions[1], dimensions[2]


def latest_matching_runs(metadata: Dict[str, Any]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """与 lint_workflow_contract.py 一致：追加数组中最后一条匹配记录最新。"""
    latest: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    runs = metadata.get("verification_runs") or []
    if not isinstance(runs, list):
        return latest
    for run in runs:
        if not isinstance(run, dict):
            continue
        key = (run.get("layer"), run.get("consumer"), run.get("scenario"))
        if all(nonempty_string(value) for value in key):
            latest[key] = run
    return latest


def classify_unit(run: Optional[Dict[str, Any]]) -> str:
    if run is None:
        return "run_missing"
    outcome = run.get("outcome")
    if outcome != "pass":
        normalized = outcome if outcome in {"fail", "inconclusive"} else "unknown"
        return f"latest_outcome_{normalized}"
    evidence_present = nonempty_string(run.get("evidence"))
    baseline_present = nonempty_string(run.get("baseline"))
    if not evidence_present and not baseline_present:
        return "evidence_and_baseline_missing"
    if not evidence_present:
        return "evidence_missing"
    if not baseline_present:
        return "baseline_missing"
    return "satisfied"


def build_unit(
    layer: str,
    consumer: str,
    scenario: str,
    run: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    state = classify_unit(run)
    latest_run = None
    if run is not None:
        latest_run = {
            "run_id": run.get("run_id"),
            "at": run.get("at"),
            "kind": run.get("kind"),
            "outcome": run.get("outcome"),
            "evidence_present": nonempty_string(run.get("evidence")),
            "baseline_present": nonempty_string(run.get("baseline")),
        }
    return {
        "layer": layer,
        "consumer": consumer,
        "scenario": scenario,
        "state": state,
        "blocking_verified": state != "satisfied",
        "latest_run": latest_run,
    }


def analyze_ticket(metadata: Dict[str, Any], ticket_dir: Path) -> Dict[str, Any]:
    ticket_id = metadata.get("ticket_id")
    if not nonempty_string(ticket_id):
        ticket_id = ticket_dir.name
    base = {
        "ticket_id": ticket_id,
        "ticket_dir": str(ticket_dir.resolve()),
        "status": metadata.get("status"),
        "delivery_verdict": metadata.get("delivery_verdict"),
        "ticket_blocked": metadata.get("delivery_verdict") == "blocked",
        "blocking_reason": (
            "delivery_verdict_blocked"
            if metadata.get("delivery_verdict") == "blocked"
            else None
        ),
        "tracking_status": "legacy_untracked",
        "required_units": 0,
        "satisfied_units": 0,
        "pending_units": 0,
        "units": [],
    }
    contract = metadata.get("verification_contract")
    schema_version = metadata.get("schema_version")
    is_vnext = schema_version == 3 or schema_version == "3"
    if contract is None:
        if is_vnext:
            base["tracking_status"] = "untracked"
        return base
    if not isinstance(contract, dict) or not isinstance(contract.get("required"), bool):
        base["tracking_status"] = "invalid_contract" if is_vnext else "legacy_untracked"
        return base
    if contract["required"] is False:
        base["tracking_status"] = "not_required"
        return base

    dimensions = required_dimensions(contract)
    if dimensions is None:
        base["tracking_status"] = "invalid_contract"
        return base

    latest = latest_matching_runs(metadata)
    units = [
        build_unit(layer, consumer, scenario, latest.get((layer, consumer, scenario)))
        for layer, consumer, scenario in product(*dimensions)
    ]
    pending = sum(unit["blocking_verified"] for unit in units)
    base.update(
        {
            "tracking_status": (
                "blocked"
                if base["ticket_blocked"]
                else ("verification_pending" if pending else "satisfied")
            ),
            "required_units": len(units),
            "satisfied_units": len(units) - pending,
            "pending_units": pending,
            "units": units,
        }
    )
    return base


def aggregate_summary(tickets: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "tickets": len(tickets),
        "tracked_required": sum(
            ticket["tracking_status"]
            in {"verification_pending", "satisfied", "invalid_contract", "blocked"}
            for ticket in tickets
        ),
        "not_required": sum(ticket["tracking_status"] == "not_required" for ticket in tickets),
        "legacy_untracked": sum(
            ticket["tracking_status"] == "legacy_untracked" for ticket in tickets
        ),
        "untracked": sum(ticket["tracking_status"] == "untracked" for ticket in tickets),
        "blocked_tickets": sum(ticket["ticket_blocked"] for ticket in tickets),
        "required_units": sum(ticket["required_units"] for ticket in tickets),
        "satisfied_units": sum(ticket["satisfied_units"] for ticket in tickets),
        "pending_units": sum(ticket["pending_units"] for ticket in tickets),
    }


def build_pending_report(project_value: str) -> Dict[str, Any]:
    project = Path(project_value).expanduser().resolve()
    if not project.is_dir():
        raise VerificationDebtError(f"project 目录不存在: {project}")
    if project == Path(project.anchor):
        raise VerificationDebtError(f"project 不得为文件系统根目录: {project}")
    paths, index_info, errors = discover_ticket_metadata(project)
    tickets: List[Dict[str, Any]] = []
    for metadata_path in paths:
        try:
            metadata = load_json(metadata_path, "ticket metadata")
        except VerificationDebtError as exc:
            errors.append(str(exc))
            continue
        tickets.append(analyze_ticket(metadata, metadata_path.parent))
    tickets.sort(key=lambda item: (str(item["ticket_id"]), item["ticket_dir"]))

    pending_units = [
        unit
        for ticket in tickets
        for unit in ticket["units"]
        if unit["blocking_verified"]
    ]
    by_layer = Counter(unit["layer"] for unit in pending_units)
    by_scenario = Counter(unit["scenario"] for unit in pending_units)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "pending",
        "generated_at": now_iso(),
        "project": str(project),
        "read_only": True,
        "status": "partial" if errors else "ok",
        "sources": {
            "metadata_root": str(project / ".icode_output"),
            "index": index_info,
        },
        "summary": aggregate_summary(tickets),
        "groups": {
            "by_layer": dict(sorted(by_layer.items())),
            "by_scenario": dict(sorted(by_scenario.items())),
        },
        "tickets": tickets,
        "errors": errors,
    }


def suggested_kind(layer: str) -> str:
    if layer == "deploy":
        return "deploy"
    if layer == "delivery":
        return "listen"
    return "device_test"


def build_record_command(ticket_dir: str, unit: Dict[str, Any]) -> List[str]:
    kind = suggested_kind(unit["layer"])
    return [
        "python3",
        "tools/icode_control.py",
        "record-verification",
        "--dir",
        ticket_dir,
        "--kind",
        kind,
        "--outcome",
        "<pass|fail|inconclusive>",
        "--layer",
        unit["layer"],
        "--consumer",
        unit["consumer"],
        "--scenario",
        unit["scenario"],
        "--baseline",
        "<device/artifact/source baseline>",
        "--evidence",
        "<observable evidence pointer>",
    ]


def build_plan(ticket_dir_value: str) -> Dict[str, Any]:
    ticket_dir = Path(ticket_dir_value).expanduser().resolve()
    if not ticket_dir.is_dir():
        raise VerificationDebtError(f"ticket-dir 目录不存在: {ticket_dir}")
    metadata = load_json(ticket_dir / METADATA_NAME, "ticket metadata")
    ticket = analyze_ticket(metadata, ticket_dir)
    pending_units = [unit for unit in ticket["units"] if unit["blocking_verified"]]
    actions = []
    for number, unit in enumerate(pending_units, start=1):
        action = {
            "order": number,
            "layer": unit["layer"],
            "consumer": unit["consumer"],
            "scenario": unit["scenario"],
            "current_state": unit["state"],
            "suggested_kind": suggested_kind(unit["layer"]),
            "required_evidence": (
                f"可回指的 {unit['layer']}/{unit['consumer']}/{unit['scenario']} 观测证据"
            ),
            "required_baseline": "设备身份、制品身份及验证所用源码/构建基线",
            "record_command": build_record_command(str(ticket_dir), unit),
        }
        actions.append(action)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "plan",
        "generated_at": now_iso(),
        "ticket_id": ticket["ticket_id"],
        "ticket_dir": str(ticket_dir),
        "read_only": True,
        "tracking_status": ticket["tracking_status"],
        "ticket_blocked": ticket["ticket_blocked"],
        "blocking_reason": ticket["blocking_reason"],
        "summary": {
            "required_units": ticket["required_units"],
            "satisfied_units": ticket["satisfied_units"],
            "pending_units": ticket["pending_units"],
        },
        "actions": actions,
    }


def markdown_pending(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# ICODE 验证债务",
        "",
        f"- Project: `{report['project']}`",
        f"- 生成时间: `{report['generated_at']}`",
        f"- 状态: `{report['status']}`",
        f"- 工单: {summary['tickets']}，required 单元: {summary['required_units']}，"
        f"已满足: {summary['satisfied_units']}，待验证: {summary['pending_units']}",
        "",
    ]
    for ticket in report["tickets"]:
        lines.extend(
            [
                f"## {ticket['ticket_id']}",
                "",
                f"- 目录: `{ticket['ticket_dir']}`",
                f"- 追踪状态: `{ticket['tracking_status']}`",
                f"- delivery_verdict: `{ticket['delivery_verdict']}`",
            ]
        )
        if ticket["tracking_status"] == "legacy_untracked":
            lines.append("- 原因: `legacy_untracked`（缺显式 verification_contract，不虚构要求）")
        elif ticket["tracking_status"] == "untracked":
            lines.append("- 原因: `untracked`（v3 工单未声明 verification_contract，不伪装成 legacy）")
        elif ticket["tracking_status"] == "not_required":
            lines.append("- 合同明确 `required=false`，无分层验证债务。")
        elif ticket["tracking_status"] == "invalid_contract":
            lines.append("- 原因: `invalid_contract`（required=true 但维度缺失或非法）")
        elif ticket["tracking_status"] == "blocked":
            lines.append("- 工单级阻塞: `delivery_verdict_blocked`；单元 outcome 仍遵循 pass/fail/inconclusive 三值 schema。")
        elif ticket["units"]:
            lines.extend(
                [
                    "",
                    "| layer | consumer | scenario | state |",
                    "|---|---|---|---|",
                ]
            )
            for unit in ticket["units"]:
                lines.append(
                    f"| {unit['layer']} | {unit['consumer']} | {unit['scenario']} | "
                    f"`{unit['state']}` |"
                )
        lines.append("")
    if report["errors"]:
        lines.extend(["## 读取降级", ""])
        lines.extend(f"- {error}" for error in report["errors"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown_plan(plan: Dict[str, Any]) -> str:
    summary = plan["summary"]
    lines = [
        f"# ICODE 验证计划：{plan['ticket_id']}",
        "",
        f"- 工单目录: `{plan['ticket_dir']}`",
        f"- 追踪状态: `{plan['tracking_status']}`",
        f"- required 单元: {summary['required_units']}，已满足: {summary['satisfied_units']}，"
        f"待验证: {summary['pending_units']}",
        "",
        "> 本计划只读生成，不追加 verification_runs，也不改变 verdict。",
        "",
    ]
    if plan["ticket_blocked"]:
        lines.extend(
            [
                f"- 工单级阻塞: `{plan['blocking_reason']}`",
                "",
            ]
        )
    if not plan["actions"]:
        lines.extend(["当前没有可执行的待验证单元。", ""])
    for action in plan["actions"]:
        label = f"{action['layer']} / {action['consumer']} / {action['scenario']}"
        lines.extend(
            [
                f"## {action['order']}. {label}",
                "",
                f"- 当前状态: `{action['current_state']}`",
                f"- 建议动作: `{action['suggested_kind']}`",
                f"- 基线要求: {action['required_baseline']}",
                f"- 证据要求: {action['required_evidence']}",
                "- 记录命令（执行前替换占位符）：",
                "",
                "```bash",
                shlex.join(action["record_command"]),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_parent_chain(path: Path) -> None:
    """在创建目录前确认最近的既存祖先确实是目录。"""
    ancestor = path.parent
    while not ancestor.exists() and not ancestor.is_symlink():
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    if ancestor.is_symlink():
        try:
            resolved = ancestor.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise VerificationDebtError(f"报告父目录符号链接不可解析: {ancestor}") from exc
        if not resolved.is_dir():
            raise VerificationDebtError(f"报告父路径不是目录: {ancestor}")
    elif not ancestor.is_dir():
        raise VerificationDebtError(f"报告父路径不是目录: {ancestor}")


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _control_sources(root: Path, explicit: Iterable[Path]) -> List[Path]:
    """收集本轮读取或同根可见的 metadata/index/manifest 真源。"""
    sources: Dict[Path, None] = {}
    for source in explicit:
        candidate = source.expanduser()
        if candidate.exists():
            sources[candidate] = None
    if root.is_dir():
        try:
            candidates = root.rglob("*")
            for candidate in candidates:
                name = candidate.name.lower()
                if candidate.is_file() and (
                    candidate.name == METADATA_NAME
                    or ("manifest" in name and candidate.suffix.lower() == ".json")
                ):
                    sources[candidate] = None
        except OSError as exc:
            raise VerificationDebtError(f"无法检查报告根内控制面真源: {root}: {exc}") from exc
    return list(sources)


def validate_report_paths(
    output: Path,
    markdown: Path,
    *,
    allowed_root: Optional[Path] = None,
    protected_sources: Iterable[Path] = (),
) -> Tuple[Path, Path]:
    raw_paths = (output.expanduser(), markdown.expanduser())
    if allowed_root is not None:
        raw_root = allowed_root.expanduser()
        if raw_root.is_symlink():
            raise VerificationDebtError(f"报告输出根不得是符号链接: {raw_root}")
        try:
            resolved_root = raw_root.resolve()
        except (OSError, RuntimeError) as exc:
            raise VerificationDebtError(f"报告输出根不可解析: {raw_root}") from exc
        if ".git" in raw_root.parts or ".git" in resolved_root.parts:
            raise VerificationDebtError(f"报告输出根不得位于 .git: {raw_root}")
    else:
        resolved_root = None

    protected_names = {
        METADATA_NAME,
        ".ico_events.jsonl",
        ".ico_txn.json",
        ".ico_lock",
        ".active_ticket.json",
        ".decision_anchors.json",
        "index.json",
        "ticket_snapshot.json",
        "archive_manifest.json",
        "evidence_manifest.json",
        "baseline_manifest.json",
    }
    resolved_paths: List[Path] = []
    for raw_path in raw_paths:
        if raw_path.is_symlink():
            raise VerificationDebtError(f"报告输出不得是符号链接: {raw_path}")
        try:
            path = raw_path.resolve()
        except (OSError, RuntimeError) as exc:
            raise VerificationDebtError(f"报告输出路径不可解析: {raw_path}") from exc
        name = path.name.lower()
        if (
            path.name in protected_names
            or path.name.startswith(".ico_")
            or ("manifest" in name and path.suffix.lower() == ".json")
        ):
            raise VerificationDebtError(f"报告输出不得覆盖控制面真源: {path}")
        if ".git" in raw_path.parts or ".git" in path.parts:
            raise VerificationDebtError(f"报告输出不得位于 .git: {raw_path}")
        if resolved_root is not None and not _is_within(path, resolved_root):
            raise VerificationDebtError(
                f"报告输出必须位于指定根目录: output={path} root={resolved_root}"
            )
        if path.exists() and path.is_dir():
            raise VerificationDebtError(f"报告输出不能是目录: {path}")
        _validate_parent_chain(raw_path)
        resolved_paths.append(path)

    resolved_output, resolved_markdown = resolved_paths
    if resolved_output == resolved_markdown:
        raise VerificationDebtError("--output 与 --markdown 必须是不同路径")
    if _same_existing_file(resolved_output, resolved_markdown):
        raise VerificationDebtError("--output 与 --markdown 不得是同一文件的别名")
    for path in (resolved_output, resolved_markdown):
        for source in protected_sources:
            if _same_existing_file(path, source):
                raise VerificationDebtError(f"报告输出不得覆盖控制面真源别名: {path}")
    return resolved_output, resolved_markdown


def write_reports(
    report: Dict[str, Any],
    output: str,
    markdown: str,
    renderer,
    *,
    allowed_root: Optional[Path] = None,
    protected_sources: Iterable[Path] = (),
) -> None:
    output_path, markdown_path = validate_report_paths(
        Path(output),
        Path(markdown),
        allowed_root=allowed_root,
        protected_sources=protected_sources,
    )
    # 两个父目录全部预检后才允许创建、暂存，避免第二目标错误时先改第一份报告。
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown_content = renderer(report)
    atomic_write_pair(
        ((output_path, json_content), (markdown_path, markdown_content))
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读汇总 ICODE 验证债务并生成执行计划"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pending = subparsers.add_parser("pending", help="按 project 汇总跨工单验证债务")
    pending.add_argument("--project", required=True, help="仅扫描该项目根")
    pending.add_argument("--output", required=True, help="JSON 报告路径")
    pending.add_argument("--markdown", required=True, help="Markdown 摘要路径")

    plan = subparsers.add_parser("plan", help="为单个 ticket 生成只读验证计划")
    plan.add_argument("--ticket-dir", required=True, help="包含 .ico_metadata.json 的工单目录")
    plan.add_argument("--output", required=True, help="JSON 计划路径")
    plan.add_argument("--markdown", required=True, help="Markdown 计划路径")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "pending":
            report = build_pending_report(args.project)
            allowed_root = Path(report["project"]) / ".icode_output"
            explicit_sources = [INDEX_PATH]
            explicit_sources.extend(
                Path(ticket["ticket_dir"]) / METADATA_NAME
                for ticket in report["tickets"]
            )
            protected_sources = _control_sources(allowed_root, explicit_sources)
            write_reports(
                report,
                args.output,
                args.markdown,
                markdown_pending,
                allowed_root=allowed_root,
                protected_sources=protected_sources,
            )
        else:
            report = build_plan(args.ticket_dir)
            allowed_root = Path(report["ticket_dir"])
            protected_sources = _control_sources(
                allowed_root, (allowed_root / METADATA_NAME, INDEX_PATH)
            )
            write_reports(
                report,
                args.output,
                args.markdown,
                markdown_plan,
                allowed_root=allowed_root,
                protected_sources=protected_sources,
            )
    except (VerificationDebtError, OSError) as exc:
        print(f"verification_debt: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
