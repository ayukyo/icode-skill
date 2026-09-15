#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ICODE 独立交叉复评控制器。

它只写 <project>/.icode_output/.crosscheck，绝不调用工单控制面的 writer，
也不创建 .ico_metadata.json。公开工作流见 steps/crosscheck.md。
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
CONTROL = SKILL_ROOT / "tools" / "icode_control.py"
METADATA_NAME = ".ico_metadata.json"
MANIFEST_NAME = "crosscheck_manifest.json"
MANIFEST_VERSION = 1
ROUND_VERSION = 1
CONTAINER_RE = re.compile(r"^\.icode_output_([1-9][0-9]*)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERDICTS = {"pass", "pass_with_suggestions", "changes_recommended", "blocked"}
SEVERITIES = {"blocker", "major", "minor", "suggestion"}
FINDING_STATES = {
    "new", "still_present", "resolved", "superseded", "regressed", "not_rechecked"
}
ROUND_STATES = {"in_progress", "completed", "blocked", "stale_input"}
ROUND_PHASES = {"fresh_review", "history_compare", "finalized"}
TRANSIENT_TARGET_FILES = {".icontrol.lock", ".icode_lock"}


class CrosscheckError(Exception):
    def __init__(self, message, exit_code=1, **extra):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.extra = extra

    def report(self):
        return {"ok": False, "error": self.message, **self.extra}


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def reject_nonfinite(value):
    raise ValueError(f"JSON 禁止非有限数 {value}")


def load_json(path, label):
    path = Path(path)
    if path.is_symlink():
        raise CrosscheckError(f"{label} 不得是符号链接: {path}", gate_id="crosscheck_symlink")
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, parse_constant=reject_nonfinite)
    except FileNotFoundError:
        raise CrosscheckError(f"{label} 不存在: {path}")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CrosscheckError(f"{label} 读取失败: {path}: {exc}")
    if not isinstance(value, dict):
        raise CrosscheckError(f"{label} 顶层必须是 JSON 对象: {path}")
    return value


def canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def object_digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def atomic_write(path, data):
    path = Path(path)
    if path.exists() and path.is_symlink():
        raise CrosscheckError(f"拒绝覆盖符号链接: {path}", gate_id="crosscheck_symlink")
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def atomic_write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n")


class DirectoryLock:
    def __init__(self, directory):
        self.path = Path(directory) / ".crosscheck.lock"
        self.stream = None

    def __enter__(self):
        self.stream = self.path.open("a+", encoding="utf-8")
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, traceback):
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def path_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def find_ticket_dir(raw_path):
    original = Path(raw_path).expanduser()
    if original.is_symlink():
        raise CrosscheckError(f"目标路径不得是符号链接: {original}", gate_id="target_symlink")
    current = original.resolve()
    if current.is_file():
        current = current.parent
    for _ in range(64):
        if (current / METADATA_NAME).is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    raise CrosscheckError(f"路径不属于可识别 ICODE 工单: {raw_path}", gate_id="target_resolution")


def workspace_from_cwd(raw_workspace=None):
    if raw_workspace:
        workspace = Path(raw_workspace).expanduser().resolve()
    else:
        try:
            value = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                text=True, capture_output=True, check=True, timeout=10,
            ).stdout.strip()
            workspace = Path(value).resolve()
        except (OSError, subprocess.SubprocessError):
            workspace = Path.cwd().resolve()
    if not workspace.is_dir():
        raise CrosscheckError(f"工程根不存在: {workspace}", exit_code=2)
    return workspace


def resolve_with_control(ticket_id, workspace):
    command = [
        sys.executable, str(CONTROL), "resolve-ticket", "--ticket", ticket_id,
        "--workspace", str(workspace), "--require-status", "completed",
    ]
    try:
        process = subprocess.run(
            command, text=True, capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CrosscheckError(f"工单解析器不可用: {exc}", gate_id="target_resolution")
    try:
        payload = json.loads(process.stdout, parse_constant=reject_nonfinite)
    except (ValueError, json.JSONDecodeError):
        payload = None
    if process.returncode != 0 or not isinstance(payload, dict) or not payload.get("resolved"):
        detail = payload.get("error") if isinstance(payload, dict) else (process.stderr or process.stdout).strip()
        raise CrosscheckError(
            f"无法唯一解析 ticket_id={ticket_id!r}（crosscheck 要求 status=completed）: {detail}",
            gate_id="target_resolution",
        )
    return Path(payload["out_dir"]).resolve()


def read_active_pointer(workspace):
    pointer_path = workspace / ".icode_output" / ".active_ticket.json"
    if not pointer_path.is_file():
        return None
    pointer = load_json(pointer_path, "当前工单指针")
    raw = pointer.get("control_root") or pointer.get("out_dir")
    if not isinstance(raw, str) or not raw:
        raise CrosscheckError("当前工单指针缺少 control_root/out_dir", gate_id="active_ticket_pointer")
    ticket_dir = Path(raw).expanduser().resolve()
    metadata = load_json(ticket_dir / METADATA_NAME, "当前工单 metadata")
    if metadata.get("ticket_id") != pointer.get("ticket_id"):
        raise CrosscheckError("当前工单指针与 metadata 身份不一致", gate_id="active_ticket_pointer")
    return ticket_dir


def derive_project_root(ticket_dir, metadata):
    candidates = []
    parts = ticket_dir.parts
    if ".icode_output" in parts:
        candidates.append(Path(*parts[:parts.index(".icode_output")]))
    raw_project = metadata.get("project_path")
    if isinstance(raw_project, str) and raw_project:
        candidates.append(Path(raw_project).expanduser())
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir():
            return resolved
    raise CrosscheckError(
        "目标工单所属项目根不可用，无法建立项目内 crosscheck 目录",
        gate_id="crosscheck_project_root",
    )


def validate_target(ticket_dir):
    ticket_dir = Path(ticket_dir).resolve()
    metadata = load_json(ticket_dir / METADATA_NAME, "目标工单 metadata")
    ticket_id = metadata.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id:
        raise CrosscheckError("目标工单缺少合法 ticket_id", gate_id="target_identity")
    if metadata.get("status") != "completed":
        raise CrosscheckError(
            f"crosscheck 只接受 status=completed 的正式工单，当前为 {metadata.get('status')!r}",
            gate_id="crosscheck_eligibility",
        )
    if metadata.get("debug") is True or ".debug" in ticket_dir.parts:
        raise CrosscheckError("crosscheck 不接受 debug 孪生工单", gate_id="crosscheck_eligibility")
    if ".crosscheck" in ticket_dir.parts:
        raise CrosscheckError("crosscheck 容器不是正式工单，不能作为目标", gate_id="target_identity")
    project_root = derive_project_root(ticket_dir, metadata)
    return {
        "ticket_id": ticket_id,
        "ticket_dir": str(ticket_dir),
        "project_root": str(project_root),
        "status": "completed",
        "metadata_schema_version": metadata.get("schema_version"),
    }, metadata


def resolve_target(args):
    workspace = workspace_from_cwd(args.workspace)
    by_path = find_ticket_dir(args.target) if args.target else None
    by_ticket = resolve_with_control(args.ticket, workspace) if args.ticket else None
    if by_path is not None and by_ticket is not None and by_path != by_ticket:
        raise CrosscheckError("路径与 --ticket 解析到不同工单", gate_id="target_identity")
    ticket_dir = by_path or by_ticket
    if ticket_dir is None:
        with contextlib.suppress(CrosscheckError):
            ticket_dir = find_ticket_dir(Path.cwd())
        if ticket_dir is None:
            ticket_dir = read_active_pointer(workspace)
    if ticket_dir is None:
        raise CrosscheckError(
            "无法确定当前工单；请在工单会话中运行，或提供 --ticket/工单路径（不会猜测 latest）",
            gate_id="target_resolution",
        )
    target, metadata = validate_target(ticket_dir)
    if args.ticket and target["ticket_id"] != args.ticket:
        raise CrosscheckError("--ticket 与目标 metadata 身份不一致", gate_id="target_identity")
    return target, metadata


def safe_crosscheck_root(project_root, override=None):
    project_root = Path(project_root).resolve()
    output_root = project_root / ".icode_output"
    expected = output_root / ".crosscheck"
    if override is not None and Path(override).expanduser().absolute() != expected.absolute():
        raise CrosscheckError("crosscheck 根固定为项目 .icode_output/.crosscheck，拒绝改写", gate_id="crosscheck_root")
    if output_root.is_symlink() or expected.is_symlink():
        raise CrosscheckError("crosscheck 路径不得经过符号链接", gate_id="crosscheck_symlink")
    output_root.mkdir(exist_ok=True)
    expected.mkdir(exist_ok=True)
    if not expected.is_dir() or expected.resolve().parent != output_root.resolve():
        raise CrosscheckError("crosscheck 根目录形状异常", gate_id="crosscheck_root")
    return expected


def snapshot_tree(root):
    root = Path(root).resolve()
    entries = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for name in sorted(dirs):
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries.append({"path": rel, "kind": "symlink", "target": os.readlink(path)})
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            if name in TRANSIENT_TARGET_FILES:
                continue
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries.append({"path": rel, "kind": "symlink", "target": os.readlink(path)})
            elif path.is_file():
                entries.append({
                    "path": rel, "kind": "file", "size": path.stat().st_size,
                    "sha256": file_digest(path),
                })
    return entries


def code_root_for(metadata, project_root):
    active = metadata.get("active_checkout")
    if isinstance(active, dict) and isinstance(active.get("path"), str):
        candidate = Path(active["path"]).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    if isinstance(metadata.get("worktree_path"), str):
        candidate = Path(metadata["worktree_path"]).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    return Path(project_root).resolve()


def normalize_code_files(metadata):
    result = []
    for item in metadata.get("code_files") or []:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            raw = item.get("path") or item.get("file")
            if isinstance(raw, str):
                result.append(raw)
    return sorted(set(result))


def git_snapshot(code_root):
    result = {"available": False, "head": None, "branch": None, "status": []}
    try:
        top = subprocess.run(
            ["git", "-C", str(code_root), "rev-parse", "--show-toplevel"],
            text=True, capture_output=True, check=True, timeout=15,
        ).stdout.strip()
        result["available"] = True
        result["root"] = str(Path(top).resolve())
        result["head"] = subprocess.run(
            ["git", "-C", str(code_root), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True, timeout=15,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(code_root), "branch", "--show-current"],
            text=True, capture_output=True, check=True, timeout=15,
        ).stdout.strip()
        result["branch"] = branch or None
        lines = subprocess.run(
            ["git", "-C", str(code_root), "status", "--porcelain=v1", "--untracked-files=all"],
            text=True, capture_output=True, check=True, timeout=30,
        ).stdout.splitlines()
        result["status"] = sorted(
            line for line in lines
            if not re.sub(r"^.. ", "", line).lstrip('"').startswith(".icode_output/")
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def capture_target_snapshot(target, metadata):
    ticket_dir = Path(target["ticket_dir"])
    project_root = Path(target["project_root"])
    code_root = code_root_for(metadata, project_root)
    code_entries = []
    for raw in normalize_code_files(metadata):
        candidate = Path(raw).expanduser()
        candidate = candidate.resolve() if candidate.is_absolute() else (code_root / candidate).resolve()
        entry = {"declared": raw}
        if not path_within(candidate, code_root):
            entry.update({"state": "outside_code_root", "path": str(candidate)})
        elif candidate.is_symlink():
            entry.update({"state": "symlink", "path": str(candidate), "target": os.readlink(candidate)})
        elif candidate.is_file():
            entry.update({"state": "present", "path": str(candidate), "size": candidate.stat().st_size,
                          "sha256": file_digest(candidate)})
        else:
            entry.update({"state": "missing", "path": str(candidate)})
        code_entries.append(entry)
    return {
        "ticket_artifacts": snapshot_tree(ticket_dir),
        "code_root": str(code_root),
        "code_files": code_entries,
        "git": git_snapshot(code_root),
        "patch_count": metadata.get("patch_count"),
    }


def validate_round_payload(payload, ticket_id, round_no, fresh=False):
    required = {
        "schema_version", "target_ticket_id", "round", "reviewed_at", "verdict",
        "evidence_boundary", "summary", "findings",
    }
    if set(payload) != required:
        raise CrosscheckError(
            f"round JSON 字段不符合 schema，缺失={sorted(required - set(payload))}，"
            f"多余={sorted(set(payload) - required)}",
            gate_id="crosscheck_round_schema",
        )
    if payload["schema_version"] != ROUND_VERSION or payload["target_ticket_id"] != ticket_id or payload["round"] != round_no:
        raise CrosscheckError("round JSON 版本或目标身份不匹配", gate_id="crosscheck_round_identity")
    if payload["verdict"] not in VERDICTS:
        raise CrosscheckError("round verdict 非法", gate_id="crosscheck_round_schema")
    for field in ("reviewed_at", "evidence_boundary", "summary"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise CrosscheckError(f"round {field} 必须是非空字符串", gate_id="crosscheck_round_schema")
    if not isinstance(payload["findings"], list):
        raise CrosscheckError("round findings 必须是数组", gate_id="crosscheck_round_schema")
    finding_required = {
        "finding_id", "title", "severity", "status", "category", "evidence",
        "analysis", "recommendation", "requires_change",
    }
    seen = set()
    for finding in payload["findings"]:
        if not isinstance(finding, dict) or set(finding) != finding_required:
            raise CrosscheckError("finding 字段不符合 schema", gate_id="crosscheck_round_schema")
        finding_id = finding["finding_id"]
        if not isinstance(finding_id, str) or not finding_id or finding_id in seen:
            raise CrosscheckError("finding_id 为空或重复", gate_id="crosscheck_round_schema")
        seen.add(finding_id)
        if finding["severity"] not in SEVERITIES or finding["status"] not in FINDING_STATES:
            raise CrosscheckError("finding severity/status 非法", gate_id="crosscheck_round_schema")
        if fresh and finding["status"] != "new":
            raise CrosscheckError("fresh 评审尚未读取历史，finding status 只能是 new", gate_id="fresh_review_bias")
        for field in ("title", "category", "analysis", "recommendation"):
            if not isinstance(finding[field], str) or not finding[field].strip():
                raise CrosscheckError(f"finding {field} 必须非空", gate_id="crosscheck_round_schema")
        if not isinstance(finding["evidence"], list) or not all(isinstance(x, str) and x for x in finding["evidence"]):
            raise CrosscheckError("finding evidence 必须是非空字符串数组", gate_id="crosscheck_round_schema")
        if not isinstance(finding["requires_change"], bool):
            raise CrosscheckError("finding requires_change 必须是布尔值", gate_id="crosscheck_round_schema")
    if payload["verdict"] == "pass" and payload["findings"]:
        raise CrosscheckError("verdict=pass 时 findings 必须为空", gate_id="crosscheck_round_logic")
    return payload


def validate_manifest(manifest, directory=None):
    required = {"schema_version", "crosscheck_id", "target", "created_at", "updated_at", "current_round", "rounds"}
    if set(manifest) != required or manifest.get("schema_version") != MANIFEST_VERSION:
        raise CrosscheckError("crosscheck manifest 顶层字段或版本非法", gate_id="crosscheck_manifest_schema")
    target = manifest.get("target")
    target_required = {"ticket_id", "ticket_dir", "project_root", "status", "metadata_schema_version"}
    if not isinstance(target, dict) or set(target) != target_required or target.get("status") != "completed":
        raise CrosscheckError("crosscheck manifest target 非法", gate_id="crosscheck_manifest_schema")
    if (not isinstance(target.get("ticket_id"), str) or not target["ticket_id"]
            or not isinstance(target.get("ticket_dir"), str) or not Path(target["ticket_dir"]).is_absolute()
            or not isinstance(target.get("project_root"), str) or not Path(target["project_root"]).is_absolute()
            or (target.get("metadata_schema_version") is not None
                and not isinstance(target.get("metadata_schema_version"), int))):
        raise CrosscheckError("crosscheck manifest target 类型或路径非法", gate_id="crosscheck_manifest_schema")
    expected_id = "crosscheck-" + hashlib.sha256(
        f"{target['project_root']}\0{target['ticket_id']}".encode("utf-8")
    ).hexdigest()[:16]
    if manifest.get("crosscheck_id") != expected_id:
        raise CrosscheckError("crosscheck_id 与目标身份不一致", gate_id="crosscheck_manifest_identity")
    rounds = manifest.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise CrosscheckError("crosscheck manifest rounds 为空", gate_id="crosscheck_manifest_schema")
    if manifest.get("current_round") != len(rounds):
        raise CrosscheckError("current_round 与 rounds 数量不一致", gate_id="crosscheck_manifest_schema")
    round_required = {
        "round", "state", "phase", "started_at", "start_snapshot",
        "start_snapshot_digest", "fresh_file", "final_file",
    }
    optional = {
        "finished_at", "fresh_sha256", "final_sha256", "round_markdown_sha256",
        "verdict", "stale_snapshot_digest",
    }
    for index, item in enumerate(rounds, 1):
        if not isinstance(item, dict) or not round_required.issubset(item) or set(item) - round_required - optional:
            raise CrosscheckError("crosscheck round manifest 字段非法", gate_id="crosscheck_manifest_schema")
        if item["round"] != index or item["state"] not in ROUND_STATES or item["phase"] not in ROUND_PHASES:
            raise CrosscheckError("crosscheck round 序号/状态/阶段非法", gate_id="crosscheck_manifest_schema")
        if (item["fresh_file"] != f"crosscheck_round_{index}.fresh.json"
                or item["final_file"] != f"crosscheck_round_{index}.json"):
            raise CrosscheckError("crosscheck round 文件名非法", gate_id="crosscheck_manifest_schema")
        for key in ("start_snapshot_digest", "fresh_sha256", "final_sha256",
                    "round_markdown_sha256", "stale_snapshot_digest"):
            if key in item and (not isinstance(item[key], str) or not SHA256_RE.fullmatch(item[key])):
                raise CrosscheckError(f"crosscheck round {key} 非法", gate_id="crosscheck_manifest_schema")
        if item["state"] == "in_progress" and item["phase"] == "history_compare" and "fresh_sha256" not in item:
            raise CrosscheckError("history_compare 缺 fresh_sha256", gate_id="crosscheck_manifest_schema")
        if item["state"] in {"completed", "blocked", "stale_input"}:
            if item["phase"] != "finalized" or "finished_at" not in item:
                raise CrosscheckError("终态 round 未 finalized", gate_id="crosscheck_manifest_schema")
        if item["state"] == "completed" and not {"fresh_sha256", "final_sha256", "round_markdown_sha256", "verdict"}.issubset(item):
            raise CrosscheckError("completed round 缺不可变哈希或 verdict", gate_id="crosscheck_manifest_schema")
        if object_digest(item["start_snapshot"]) != item["start_snapshot_digest"]:
            raise CrosscheckError("crosscheck start snapshot 摘要不一致", gate_id="crosscheck_snapshot")
    if directory is not None:
        directory = Path(directory).resolve()
        if directory.name == ".crosscheck" or not CONTAINER_RE.match(directory.name):
            raise CrosscheckError("crosscheck 容器目录形状非法", gate_id="crosscheck_dir_shape")
        if directory.parent.name != ".crosscheck":
            raise CrosscheckError("crosscheck 容器不在 .crosscheck 下", gate_id="crosscheck_dir_shape")
        expected_parent = Path(target["project_root"]).resolve() / ".icode_output" / ".crosscheck"
        if directory.parent != expected_parent:
            raise CrosscheckError("crosscheck 容器与 manifest project_root 不一致", gate_id="crosscheck_dir_shape")
    return manifest


def load_manifest(directory):
    raw_directory = Path(directory).expanduser()
    if raw_directory.is_symlink():
        raise CrosscheckError(f"crosscheck 容器不得是符号链接: {raw_directory}", gate_id="crosscheck_symlink")
    directory = raw_directory.resolve()
    manifest = load_json(directory / MANIFEST_NAME, "crosscheck manifest")
    return directory, validate_manifest(manifest, directory)


def matching_containers(root, target):
    matches = []
    numbers = []
    for child in sorted(root.iterdir()):
        match = CONTAINER_RE.match(child.name)
        if not match:
            continue
        numbers.append(int(match.group(1)))
        if child.is_symlink() or not child.is_dir():
            raise CrosscheckError(f"crosscheck 编号项不是安全目录: {child}", gate_id="crosscheck_dir_shape")
        manifest_path = child / MANIFEST_NAME
        if not manifest_path.is_file():
            raise CrosscheckError(f"crosscheck 编号目录缺 manifest，拒绝猜测恢复: {child}", gate_id="crosscheck_manifest_missing")
        manifest = validate_manifest(load_json(manifest_path, "crosscheck manifest"), child)
        identity = manifest["target"]
        if identity["ticket_id"] == target["ticket_id"] and identity["project_root"] == target["project_root"]:
            matches.append((child, manifest))
    return matches, numbers


def new_round(target, metadata, round_no):
    snapshot = capture_target_snapshot(target, metadata)
    return {
        "round": round_no,
        "state": "in_progress",
        "phase": "fresh_review",
        "started_at": now_iso(),
        "start_snapshot": snapshot,
        "start_snapshot_digest": object_digest(snapshot),
        "fresh_file": f"crosscheck_round_{round_no}.fresh.json",
        "final_file": f"crosscheck_round_{round_no}.json",
    }


def cmd_start(args):
    target, metadata = resolve_target(args)
    root = safe_crosscheck_root(target["project_root"], args.crosscheck_root)
    with DirectoryLock(root):
        matches, numbers = matching_containers(root, target)
        if len(matches) > 1:
            raise CrosscheckError(
                "同一目标存在多个 crosscheck 容器，拒绝猜测",
                gate_id="crosscheck_identity_ambiguous",
                candidates=[str(item[0]) for item in matches],
            )
        resumed = False
        if matches:
            directory, manifest = matches[0]
            current = manifest["rounds"][-1]
            if current["state"] == "in_progress":
                current_snapshot = capture_target_snapshot(target, metadata)
                if object_digest(current_snapshot) == current["start_snapshot_digest"]:
                    resumed = True
                else:
                    current.update({
                        "state": "stale_input", "phase": "finalized", "finished_at": now_iso(),
                        "stale_snapshot_digest": object_digest(current_snapshot),
                    })
            if not resumed:
                manifest["rounds"].append(new_round(target, metadata, len(manifest["rounds"]) + 1))
                manifest["current_round"] = len(manifest["rounds"])
                manifest["updated_at"] = now_iso()
                atomic_write_json(directory / MANIFEST_NAME, manifest)
        else:
            number = max(numbers, default=0) + 1
            directory = root / f".icode_output_{number}"
            directory.mkdir()
            created = now_iso()
            identity_material = f"{target['project_root']}\0{target['ticket_id']}"
            manifest = {
                "schema_version": MANIFEST_VERSION,
                "crosscheck_id": "crosscheck-" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:16],
                "target": target,
                "created_at": created,
                "updated_at": created,
                "current_round": 1,
                "rounds": [new_round(target, metadata, 1)],
            }
            atomic_write_json(directory / MANIFEST_NAME, manifest)
        current = manifest["rounds"][-1]
        return {
            "ok": True,
            "crosscheck_dir": str(directory),
            "target_ticket_id": target["ticket_id"],
            "target_ticket_dir": target["ticket_dir"],
            "round": current["round"],
            "phase": current["phase"],
            "resumed": resumed,
            "fresh_output": str(directory / current["fresh_file"]),
            "instruction": "先独立评审并写 fresh_output；此阶段不要读取既往 crosscheck 结果",
        }


def get_round(manifest, round_no):
    if round_no < 1 or round_no > len(manifest["rounds"]):
        raise CrosscheckError(f"crosscheck round 不存在: {round_no}", gate_id="crosscheck_round_identity")
    return manifest["rounds"][round_no - 1]


def previous_completed_round(directory, manifest, round_no):
    for item in reversed(manifest["rounds"][:round_no - 1]):
        if item["state"] == "completed" and item.get("final_sha256"):
            return item, directory / item["final_file"]
    return None, None


def cmd_freeze(args):
    directory, manifest = load_manifest(args.dir)
    with DirectoryLock(directory):
        manifest = validate_manifest(load_json(directory / MANIFEST_NAME, "crosscheck manifest"), directory)
        item = get_round(manifest, args.round)
        fresh_path = directory / item["fresh_file"]
        fresh = validate_round_payload(
            load_json(fresh_path, "fresh crosscheck round"),
            manifest["target"]["ticket_id"], args.round, fresh=True,
        )
        digest = file_digest(fresh_path)
        if item.get("fresh_sha256"):
            if item["fresh_sha256"] != digest:
                raise CrosscheckError("已冻结的 fresh 文件被修改", gate_id="fresh_immutable")
            already = True
        else:
            if item["state"] != "in_progress" or item["phase"] != "fresh_review":
                raise CrosscheckError("当前 round 不处于 fresh_review", gate_id="crosscheck_round_phase")
            item["fresh_sha256"] = digest
            item["phase"] = "history_compare"
            manifest["updated_at"] = now_iso()
            atomic_write_json(directory / MANIFEST_NAME, manifest)
            already = False
        _previous_item, previous = previous_completed_round(directory, manifest, args.round)
        return {
            "ok": True, "crosscheck_dir": str(directory), "round": args.round,
            "phase": item["phase"], "already_applied": already,
            "fresh_sha256": digest,
            "previous_round": str(previous) if previous else None,
            "final_output": str(directory / item["final_file"]),
            "fresh_verdict": fresh["verdict"],
        }


def validate_lifecycle(directory, manifest, round_no, fresh, final):
    fresh_ids = {item["finding_id"] for item in fresh["findings"]}
    final_by_id = {item["finding_id"]: item for item in final["findings"]}
    previous_ids = set()
    previous_item, previous_path = previous_completed_round(directory, manifest, round_no)
    if previous_path:
        previous = validate_round_payload(
            load_json(previous_path, "previous crosscheck round"),
            manifest["target"]["ticket_id"], previous_item["round"],
        )
        previous_ids = {item["finding_id"] for item in previous["findings"]}
    missing = (fresh_ids | previous_ids) - set(final_by_id)
    if missing:
        raise CrosscheckError(
            f"最终 findings 未覆盖 fresh/上一轮 finding: {sorted(missing)}",
            gate_id="crosscheck_finding_lifecycle",
        )
    for finding_id, finding in final_by_id.items():
        state = finding["status"]
        if round_no == 1 and state != "new":
            raise CrosscheckError("首轮 finding status 只能是 new", gate_id="crosscheck_finding_lifecycle")
        if round_no > 1 and finding_id in previous_ids and state == "new":
            raise CrosscheckError("既有 finding 不得重新标为 new", gate_id="crosscheck_finding_lifecycle")
        if finding_id not in previous_ids and finding_id not in fresh_ids:
            raise CrosscheckError("最终结果含既不在 fresh 也不在上一轮的 finding", gate_id="crosscheck_finding_lifecycle")


def render_round(payload):
    lines = [
        f"# Crosscheck Round {payload['round']}", "",
        f"- 目标工单：`{payload['target_ticket_id']}`",
        f"- 结论：`{payload['verdict']}`",
        f"- 评审时间：`{payload['reviewed_at']}`",
        f"- 证据边界：{payload['evidence_boundary']}", "",
        "## 摘要", "", payload["summary"], "", "## Findings", "",
    ]
    if not payload["findings"]:
        lines.append("无。")
    for finding in payload["findings"]:
        lines.extend([
            f"### {finding['finding_id']} · {finding['title']}", "",
            f"- 严重度：`{finding['severity']}`",
            f"- 生命周期：`{finding['status']}`",
            f"- 分类：`{finding['category']}`",
            f"- 是否建议修改：`{str(finding['requires_change']).lower()}`",
            f"- 证据：{'; '.join(finding['evidence']) or '无直接证据'}", "",
            finding["analysis"], "", f"建议：{finding['recommendation']}", "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def build_cumulative(directory, manifest):
    history = []
    latest = {}
    for item in manifest["rounds"]:
        if item["state"] != "completed":
            continue
        payload = load_json(directory / item["final_file"], "crosscheck round")
        for finding in payload["findings"]:
            record = dict(finding)
            record["round"] = item["round"]
            history.append(record)
            latest[finding["finding_id"]] = record
    return {
        "schema_version": 1,
        "target_ticket_id": manifest["target"]["ticket_id"],
        "updated_at": manifest["updated_at"],
        "latest_round": manifest["current_round"],
        "findings": [latest[key] for key in sorted(latest)],
        "history": history,
    }


def render_report(directory, manifest, cumulative):
    lines = [
        "# ICODE Crosscheck Report", "",
        f"- 目标工单：`{manifest['target']['ticket_id']}`",
        f"- 目标目录：`{manifest['target']['ticket_dir']}`",
        f"- 已完成轮次：{sum(item['state'] == 'completed' for item in manifest['rounds'])}", "",
        "## 轮次", "",
    ]
    for item in manifest["rounds"]:
        suffix = f"，结论 `{item.get('verdict')}`" if item.get("verdict") else ""
        lines.append(f"- Round {item['round']}：`{item['state']}`{suffix} — `{item['final_file']}`")
    lines.extend(["", "## 当前 Findings", ""])
    if not cumulative["findings"]:
        lines.append("无。")
    for finding in cumulative["findings"]:
        lines.append(
            f"- `{finding['finding_id']}` [{finding['severity']}/{finding['status']}] "
            f"{finding['title']}（Round {finding['round']}）"
        )
    lines.extend(["", "> Crosscheck 只提供复评建议；如需修改，由用户显式调用 `/icode patch`。", ""])
    return "\n".join(lines)


def write_if_changed(path, data):
    path = Path(path)
    if path.is_symlink():
        raise CrosscheckError(f"派生报告不得是符号链接: {path}", gate_id="crosscheck_symlink")
    if path.is_file() and path.read_bytes() == data:
        return
    atomic_write(path, data)


def ensure_round_markdown(directory, item, final):
    markdown_path = directory / f"crosscheck_round_{item['round']}.md"
    markdown = render_round(final).encode("utf-8")
    expected_digest = hashlib.sha256(markdown).hexdigest()
    recorded = item.get("round_markdown_sha256")
    if recorded and recorded != expected_digest:
        raise CrosscheckError("round Markdown 记录哈希与最终 JSON 不一致", gate_id="round_immutable")
    if markdown_path.exists() and (markdown_path.is_symlink() or markdown_path.read_bytes() != markdown):
        raise CrosscheckError("round Markdown 已存在且内容不一致，拒绝覆盖", gate_id="round_immutable")
    write_if_changed(markdown_path, markdown)
    return markdown_path, expected_digest


def write_derived_outputs(directory, manifest):
    cumulative = build_cumulative(directory, manifest)
    findings_data = json.dumps(
        cumulative, ensure_ascii=False, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    report_data = render_report(directory, manifest, cumulative).encode("utf-8")
    write_if_changed(directory / "findings.json", findings_data)
    write_if_changed(directory / "crosscheck_report.md", report_data)


def mark_stale(directory, manifest, item, round_no, final_path, reason, current_digest=None):
    updates = {
        "state": "stale_input", "phase": "finalized", "finished_at": now_iso(),
        "final_sha256": file_digest(final_path), "verdict": "blocked",
    }
    if current_digest:
        updates["stale_snapshot_digest"] = current_digest
    item.update(updates)
    manifest["updated_at"] = now_iso()
    atomic_write_json(directory / MANIFEST_NAME, manifest)
    raise CrosscheckError(
        reason, gate_id="crosscheck_stale_input", state="stale_input", round=round_no,
        crosscheck_dir=str(directory),
    )


def cmd_finish(args):
    directory, manifest = load_manifest(args.dir)
    with DirectoryLock(directory):
        manifest = validate_manifest(load_json(directory / MANIFEST_NAME, "crosscheck manifest"), directory)
        item = get_round(manifest, args.round)
        final_path = directory / item["final_file"]
        if item["state"] == "completed":
            if not final_path.is_file() or file_digest(final_path) != item.get("final_sha256"):
                raise CrosscheckError("已完成 round 的最终文件被修改", gate_id="final_immutable")
            final = validate_round_payload(
                load_json(final_path, "final crosscheck round"),
                manifest["target"]["ticket_id"], args.round,
            )
            ensure_round_markdown(directory, item, final)
            write_derived_outputs(directory, manifest)
            return {"ok": True, "already_applied": True, "state": "completed", "round": args.round,
                    "crosscheck_dir": str(directory)}
        if item["state"] != "in_progress" or item["phase"] != "history_compare" or not item.get("fresh_sha256"):
            raise CrosscheckError("finish 前必须先 freeze fresh 评审", gate_id="crosscheck_round_phase")
        fresh_path = directory / item["fresh_file"]
        if not fresh_path.is_file() or file_digest(fresh_path) != item["fresh_sha256"]:
            raise CrosscheckError("已冻结的 fresh 文件被修改", gate_id="fresh_immutable")
        fresh = validate_round_payload(
            load_json(fresh_path, "fresh crosscheck round"), manifest["target"]["ticket_id"], args.round, fresh=True,
        )
        final = validate_round_payload(
            load_json(final_path, "final crosscheck round"), manifest["target"]["ticket_id"], args.round,
        )
        validate_lifecycle(directory, manifest, args.round, fresh, final)
        try:
            target, metadata = validate_target(manifest["target"]["ticket_dir"])
        except CrosscheckError as exc:
            mark_stale(
                directory, manifest, item, args.round, final_path,
                f"目标工单在本轮期间变为不可复评状态，本轮标记 stale_input: {exc.message}",
            )
        if target["ticket_id"] != manifest["target"]["ticket_id"] or target["project_root"] != manifest["target"]["project_root"]:
            mark_stale(
                directory, manifest, item, args.round, final_path,
                "目标工单身份在评审期间变化，本轮标记 stale_input",
            )
        current_snapshot = capture_target_snapshot(target, metadata)
        current_digest = object_digest(current_snapshot)
        if current_digest != item["start_snapshot_digest"]:
            mark_stale(
                directory, manifest, item, args.round, final_path,
                "目标工单或代码在本轮期间发生变化，本轮标记 stale_input；请重新运行 crosscheck",
                current_digest,
            )
        markdown_path, markdown_digest = ensure_round_markdown(directory, item, final)
        item.update({
            "state": "completed", "phase": "finalized", "finished_at": now_iso(),
            "final_sha256": file_digest(final_path),
            "round_markdown_sha256": markdown_digest,
            "verdict": final["verdict"],
        })
        manifest["updated_at"] = now_iso()
        atomic_write_json(directory / MANIFEST_NAME, manifest)
        write_derived_outputs(directory, manifest)
        return {
            "ok": True, "crosscheck_dir": str(directory), "round": args.round,
            "state": "completed", "verdict": final["verdict"],
            "round_report": str(markdown_path),
            "report": str(directory / "crosscheck_report.md"),
        }


def cmd_validate(args):
    directory, manifest = load_manifest(args.dir)
    forbidden = list(directory.rglob(METADATA_NAME))
    if forbidden:
        raise CrosscheckError("crosscheck 容器不得含 .ico_metadata.json", gate_id="crosscheck_ticket_isolation")
    completed = 0
    for item in manifest["rounds"]:
        fresh_path = directory / item["fresh_file"]
        if item.get("fresh_sha256"):
            if not fresh_path.is_file() or file_digest(fresh_path) != item["fresh_sha256"]:
                raise CrosscheckError(f"Round {item['round']} fresh 哈希不一致", gate_id="fresh_immutable")
            validate_round_payload(
                load_json(fresh_path, "fresh crosscheck round"), manifest["target"]["ticket_id"], item["round"], fresh=True,
            )
        final = None
        final_path = directory / item["final_file"]
        if item.get("final_sha256"):
            if not final_path.is_file() or file_digest(final_path) != item["final_sha256"]:
                raise CrosscheckError(f"Round {item['round']} final 哈希不一致", gate_id="final_immutable")
            final = validate_round_payload(
                load_json(final_path, "final crosscheck round"), manifest["target"]["ticket_id"], item["round"],
            )
        if item["state"] == "completed":
            completed += 1
            markdown_path = directory / f"crosscheck_round_{item['round']}.md"
            if not markdown_path.is_file() or file_digest(markdown_path) != item.get("round_markdown_sha256"):
                raise CrosscheckError(f"Round {item['round']} Markdown 哈希不一致", gate_id="round_immutable")
            fresh = load_json(fresh_path, "fresh crosscheck round")
            validate_lifecycle(directory, manifest, item["round"], fresh, final)
    if completed:
        cumulative = build_cumulative(directory, manifest)
        expected_findings = json.dumps(
            cumulative, ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8") + b"\n"
        findings_path = directory / "findings.json"
        if findings_path.is_symlink() or not findings_path.is_file() or findings_path.read_bytes() != expected_findings:
            raise CrosscheckError("findings.json 与完成轮重建结果不一致", gate_id="crosscheck_report")
        expected_report = render_report(directory, manifest, cumulative).encode("utf-8")
        report_path = directory / "crosscheck_report.md"
        if report_path.is_symlink() or not report_path.is_file() or report_path.read_bytes() != expected_report:
            raise CrosscheckError("crosscheck report 与完成轮重建结果不一致", gate_id="crosscheck_report")
    return {
        "ok": True, "valid": True, "crosscheck_dir": str(directory),
        "target_ticket_id": manifest["target"]["ticket_id"],
        "rounds": len(manifest["rounds"]), "completed_rounds": completed,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="icode_crosscheck.py",
        description="ICODE 独立交叉复评：项目内隔离、多轮追加、原工单零回写",
    )
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="解析目标并开始或恢复一轮复评")
    start.add_argument("target", nargs="?", help="工单目录、metadata 或工单内产物路径")
    start.add_argument("--ticket", help="目标 ticket_id")
    start.add_argument("--workspace", help="当前工程根；用于 ticket/current 解析")
    start.add_argument("--crosscheck-root", help=argparse.SUPPRESS)
    start.set_defaults(function=cmd_start)

    freeze = sub.add_parser("freeze", help="冻结 fresh 评审，之后才允许读取上一轮")
    freeze.add_argument("--dir", required=True, help="crosscheck 容器")
    freeze.add_argument("--round", required=True, type=int)
    freeze.set_defaults(function=cmd_freeze)

    finish = sub.add_parser("finish", help="验证输入未漂移并完成当前轮次")
    finish.add_argument("--dir", required=True, help="crosscheck 容器")
    finish.add_argument("--round", required=True, type=int)
    finish.set_defaults(function=cmd_finish)

    validate = sub.add_parser("validate", help="只读校验 crosscheck 容器和不可变哈希")
    validate.add_argument("--dir", required=True, help="crosscheck 容器")
    validate.set_defaults(function=cmd_validate)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "function", None):
        parser.print_help()
        return 2
    try:
        result = args.function(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except CrosscheckError as exc:
        print(json.dumps(exc.report(), ensure_ascii=False, indent=2, allow_nan=False))
        return exc.exit_code
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"文件系统操作失败: {exc}", "gate_id": "filesystem_io"}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
