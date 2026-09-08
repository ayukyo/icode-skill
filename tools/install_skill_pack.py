#!/usr/bin/env python3
"""Safely publish ICODE bundled skills into one or more host skill roots."""

import argparse
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from validate_skill_pack import (
    OWNER_MARKER,
    RUNTIME_FILES,
    RUNTIME_NAMES,
    ValidationError,
    compare_source_files,
    installed_digest,
    iter_source_publish_files,
    validate_skills,
)


OWNER = "icode-skill"


class InstallConflict(RuntimeError):
    """Raised when an existing target is not owned by this installer."""


def expected_marker(name: str) -> dict:
    return {"schema_version": 1, "owner": OWNER, "skill": name}


def read_marker(target: Path) -> dict | None:
    marker_path = target / OWNER_MARKER
    if not marker_path.exists():
        return None
    try:
        value = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallConflict(f"所有权标记损坏: {marker_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallConflict(f"所有权标记必须是 JSON 对象: {marker_path}")
    return value


def validate_target_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise InstallConflict(f"拒绝使用过宽的技能根目录: {root}")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise InstallConflict(f"技能根目录不是普通目录: {root}")
    return root


def classify_target(row: dict, target: Path, keep_extra: bool) -> str:
    if not target.exists():
        return "create"
    if not target.is_dir() or target.is_symlink():
        raise InstallConflict(f"目标技能不是普通目录: {target}")
    marker = read_marker(target)
    if marker is not None:
        if marker != expected_marker(row["name"]):
            raise InstallConflict(f"目标技能所有权冲突: {target}")
        if keep_extra:
            try:
                compare_source_files(
                    Path(row["path"]), row["entrypoint"], target)
            except ValidationError:
                return "update"
            return "current"
        if installed_digest(target) == row["digest"]:
            return "current"
        return "update"
    if installed_digest(target) == row["digest"]:
        return "adopt"
    raise InstallConflict(
        f"存在未托管的同名技能: {target}; 请先改名或移走后重试")


def is_runtime_path(rel: Path) -> bool:
    return (
        any(part in RUNTIME_NAMES for part in rel.parts)
        or rel.name in RUNTIME_FILES
        or rel.suffix in {".pyc", ".pyo"}
    )


def preserve_runtime_files(target: Path, stage: Path) -> None:
    if not target.is_dir():
        return
    for source in sorted(target.rglob("*")):
        rel = source.relative_to(target)
        if not is_runtime_path(rel):
            continue
        destination = stage / rel
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def prepare_stage(row: dict, root: Path, target: Path, keep_extra: bool) -> Path:
    stage = Path(tempfile.mkdtemp(
        prefix=f".{row['name']}.icode-stage-", dir=root))
    try:
        source_root = Path(row["path"])
        if keep_extra and target.is_dir():
            shutil.copytree(target, stage, dirs_exist_ok=True)
        for source, rel in iter_source_publish_files(
                source_root, row["entrypoint"]):
            destination = stage / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if not keep_extra:
            preserve_runtime_files(target, stage)
        marker_path = stage / OWNER_MARKER
        marker_path.write_text(
            json.dumps(expected_marker(row["name"]), ensure_ascii=False,
                       sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if keep_extra:
            compare_source_files(source_root, row["entrypoint"], stage)
        else:
            actual = installed_digest(stage)
            if actual != row["digest"]:
                raise ValidationError(
                    f"暂存技能 hash 不一致: {target} "
                    f"expected={row['digest']} actual={actual}")
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def remove_generated_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def commit_stages(actions: list[dict]) -> None:
    committed = []
    try:
        for action in actions:
            target = action["target"]
            stage = action["stage"]
            backup = target.parent / (
                f".{target.name}.icode-backup-{uuid.uuid4().hex}")
            if target.exists():
                target.rename(backup)
            else:
                backup = None
            try:
                stage.rename(target)
            except Exception:
                if backup is not None and backup.exists():
                    backup.rename(target)
                raise
            committed.append({"target": target, "backup": backup})
    except Exception:
        for item in reversed(committed):
            remove_generated_tree(item["target"])
            backup = item["backup"]
            if backup is not None and backup.exists():
                backup.rename(item["target"])
        raise
    else:
        for item in committed:
            backup = item["backup"]
            if backup is not None:
                remove_generated_tree(backup)
    finally:
        for action in actions:
            remove_generated_tree(action["stage"])


def install(manifest: Path, raw_roots: list[str], dry_run: bool,
            keep_extra: bool) -> list[dict]:
    rows = validate_skills(manifest)
    roots = []
    for raw in raw_roots:
        root = validate_target_root(raw)
        if root in roots:
            raise InstallConflict(f"目标技能根目录重复: {root}")
        roots.append(root)

    actions = []
    for root in roots:
        for row in rows:
            target = root / row["name"]
            actions.append({
                "row": row,
                "root": root,
                "target": target,
                "action": classify_target(row, target, keep_extra),
            })

    if dry_run:
        return actions

    try:
        pending = [action for action in actions
                   if action["action"] != "current"]
        for root in {action["root"] for action in pending}:
            root.mkdir(parents=True, exist_ok=True)
        for action in pending:
            action["stage"] = prepare_stage(
                action["row"], action["root"], action["target"], keep_extra)
        commit_stages(pending)
    except Exception:
        for action in actions:
            stage = action.get("stage")
            if stage is not None:
                remove_generated_tree(stage)
        raise
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安装 ICODE manifest 声明的共享技能")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--target-root", action="append", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-extra", action="store_true",
                        help="更新已托管技能时保留目标独有文件")
    args = parser.parse_args()
    try:
        actions = install(
            Path(args.manifest).expanduser().resolve(),
            args.target_root,
            args.dry_run,
            args.keep_extra,
        )
    except (InstallConflict, OSError, UnicodeError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    for action in actions:
        prefix = "would " if args.dry_run else ""
        print(f"{prefix}{action['action']}: {action['target']}")
    print(json.dumps({
        "ok": True,
        "dry_run": args.dry_run,
        "targets": len(actions),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
