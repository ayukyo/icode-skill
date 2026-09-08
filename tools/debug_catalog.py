#!/usr/bin/env python3
"""重建和查询项目本地 debug 工单目录；绝不读写全局 ICODE 索引。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
CATALOG_NAME = "debug_catalog.json"


class CatalogError(RuntimeError):
    """目录范围、输入格式或文件系统错误。"""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _project_root(value: str | Path) -> Path:
    project = Path(value).resolve()
    if not project.is_dir() or project == Path(project.anchor):
        raise CatalogError(f"project 必须是已存在的非根目录: {project}")
    return project


def _debug_root(project: Path) -> Path:
    output_root = project / ".icode_output"
    if output_root.is_symlink():
        raise CatalogError(f".icode_output 不得是符号链接: {output_root}")
    if output_root.exists() and not output_root.is_dir():
        raise CatalogError(f".icode_output 必须是目录: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    debug_root = output_root / ".debug"
    if debug_root.is_symlink():
        raise CatalogError(f".debug 不得是符号链接: {debug_root}")
    if debug_root.exists() and not debug_root.is_dir():
        raise CatalogError(f".debug 必须是目录: {debug_root}")
    debug_root.mkdir(exist_ok=True)
    resolved = debug_root.resolve()
    if not _inside(resolved, project):
        raise CatalogError(f"debug catalog 目录越出 project: {resolved}")
    return resolved


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"JSON 顶层必须是 object: {path}")
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_manifest_hash(manifest: dict[str, Any]) -> str:
    """Hash evidence meaning while ignoring run time and representation-only fields."""
    identity = manifest.get("source_identity") or {}
    activities = []
    for activity in manifest.get("activities") or []:
        if not isinstance(activity, dict):
            continue
        activities.append(
            {
                "activity_id": activity.get("activity_id") or activity.get("stable_id"),
                "summary_fingerprint": activity.get("summary_fingerprint"),
            }
        )
    artifacts = []
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        artifacts.append(
            {
                "semantic_id": artifact.get("semantic_id") or artifact.get("artifact_id"),
                "sha256": artifact.get("sha256"),
                "content_hash_pending": artifact.get("content_hash_pending"),
                "pending_size": (
                    artifact.get("size") if not artifact.get("sha256") else None
                ),
                "source_activity": artifact.get("source_activity"),
            }
        )
    derivations = [
        item
        for item in manifest.get("derivations") or []
        if isinstance(item, dict)
    ]
    canonical = {
        "source_identity": {
            key: identity.get(key)
            for key in ("source_label", "pid", "lib", "num", "task_id")
        },
        "activities": sorted(
            activities,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
        "artifacts": sorted(
            artifacts,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
        "derivations": sorted(
            derivations,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_for_twin(twin: Path) -> Path | None:
    direct = twin / "evidence_manifest.json"
    if direct.is_file() and _inside(direct.resolve(), twin.resolve()):
        return direct
    for candidate in sorted(twin.rglob("evidence_manifest.json")):
        resolved = candidate.resolve()
        if candidate.is_file() and _inside(resolved, twin.resolve()):
            return candidate
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _evidence_fingerprint(manifest_path: Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {
            "manifest_sha256": None,
            "activities": 0,
            "artifacts": 0,
            "latest_time": None,
        }
    try:
        manifest = _load_object(manifest_path)
    except CatalogError:
        return {
            "manifest_sha256": None,
            "activities": 0,
            "artifacts": 0,
            "latest_time": None,
            "manifest_error": "invalid_json",
        }
    timestamps = []
    for activity in manifest.get("activities") or []:
        if isinstance(activity, dict):
            value = activity.get("created_at") or activity.get("created")
            if isinstance(value, str) and value:
                timestamps.append(value)
    return {
        "manifest_sha256": _semantic_manifest_hash(manifest),
        "raw_manifest_sha256": _hash_file(manifest_path),
        "activities": len(manifest.get("activities") or []),
        "artifacts": len(manifest.get("artifacts") or []),
        "latest_time": max(timestamps) if timestamps else None,
    }


def _ticket_record(twin: Path, metadata_path: Path, metadata: dict[str, Any]) -> dict[str, Any] | None:
    if metadata.get("debug") is not True:
        return None
    source = metadata.get("tb_source") or {}
    if not isinstance(source, dict):
        source = {}
    manifest_path = _manifest_for_twin(twin)
    modules = _string_list(metadata.get("code_modules") or metadata.get("modules"))
    error_codes = _string_list(metadata.get("error_codes"))
    log_tags = _string_list(metadata.get("log_tags"))
    state_keywords = _string_list(metadata.get("state_keywords"))
    action_keywords = _string_list(metadata.get("action_keywords"))
    bounded_verdict = metadata.get("bounded_verdict") or metadata.get("root_cause")
    if not isinstance(bounded_verdict, str):
        bounded_verdict = None
    return {
        "identity": {
            "pid": str(source.get("pid") or ""),
            "lib": str(source.get("lib") or ""),
            "num": str(source.get("num") or ""),
            "ticket_id": metadata.get("ticket_id"),
            "debug_dir": str(twin),
            "metadata_path": str(metadata_path),
        },
        "state": metadata.get("status") or "halfdone",
        "evidence_fingerprint": _evidence_fingerprint(manifest_path),
        "analysis_fingerprint": {
            "modules": modules,
            "error_codes": error_codes,
            "log_tags": log_tags,
            "state_keywords": state_keywords,
            "action_keywords": action_keywords,
            "runtime_candidates": _string_list(metadata.get("runtime_version_candidates")),
        },
        "conclusion": {
            "bounded_verdict": bounded_verdict,
            "unobserved_boundaries": _string_list(metadata.get("unobserved_boundaries")),
            "updated_at": metadata.get("updated_at"),
        },
        "staleness": {
            "evidence_missing": manifest_path is None,
            "analysis_artifact_missing": not any(
                (twin / name).is_file()
                for name in ("00_log_analysis.md", "06_audit.md", "limit_checkpoint.md")
            ),
        },
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def rebuild_catalog(project: str | Path) -> dict[str, Any]:
    """只扫描 `<project>/.icode_output/.debug` 并返回可重建目录。"""
    project_root = _project_root(project)
    debug_root = _debug_root(project_root)
    records = []
    skipped = []
    for twin in sorted(debug_root.iterdir()):
        if not twin.is_dir() or twin.is_symlink():
            continue
        metadata_path = twin / ".ico_metadata.json"
        if not metadata_path.is_file():
            skipped.append({"debug_dir": str(twin), "reason": "metadata_missing"})
            continue
        if metadata_path.is_symlink() or not _inside(metadata_path.resolve(), twin.resolve()):
            skipped.append({"debug_dir": str(twin), "reason": "metadata_out_of_scope"})
            continue
        try:
            metadata = _load_object(metadata_path)
        except CatalogError:
            skipped.append({"debug_dir": str(twin), "reason": "metadata_invalid"})
            continue
        record = _ticket_record(twin.resolve(), metadata_path.resolve(), metadata)
        if record is not None:
            records.append(record)
    records.sort(
        key=lambda item: (
            item["identity"]["pid"],
            item["identity"]["lib"],
            item["identity"]["num"],
        )
    )
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": str(project_root),
        "scope": "project_debug_only",
        "source_root": str(debug_root),
        "tickets": records,
        "skipped": skipped,
        "read_only_sources": True,
        "side_effects": [str(debug_root / CATALOG_NAME)],
        "global_index_written": False,
    }
    _atomic_json(debug_root / CATALOG_NAME, catalog)
    return catalog


def _catalog(project: Path) -> dict[str, Any]:
    path = _debug_root(project) / CATALOG_NAME
    try:
        if path.is_symlink():
            raise CatalogError("catalog 不得是符号链接")
        value = _load_object(path)
        if value.get("schema_version") != SCHEMA_VERSION:
            raise CatalogError("unsupported catalog schema")
        if value.get("project") != str(project) or value.get("scope") != "project_debug_only":
            raise CatalogError("catalog project/scope mismatch")
        return value
    except CatalogError:
        return rebuild_catalog(project)


def _keyword_match(record: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return False
    values = []
    fingerprint = record.get("analysis_fingerprint") or {}
    for field in (
        "modules",
        "error_codes",
        "log_tags",
        "state_keywords",
        "action_keywords",
        "runtime_candidates",
    ):
        values.extend(_string_list(fingerprint.get(field)))
    haystack = " ".join(values).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def query_catalog(
    project: str | Path,
    pid: str,
    lib: str,
    num: str,
    manifest: str | Path | None = None,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    """返回精确孪生的复用动作；相似命中只作为重新校验候选。"""
    project_root = _project_root(project)
    catalog = _catalog(project_root)
    exact = None
    for record in catalog.get("tickets") or []:
        identity = record.get("identity") or {}
        if (
            str(identity.get("pid") or "") == str(pid)
            and str(identity.get("lib") or "") == str(lib)
            and str(identity.get("num") or "") == str(num)
        ):
            exact = record
            break
    incoming_hash = None
    if manifest is not None:
        manifest_path = Path(manifest).resolve()
        if not manifest_path.is_file():
            raise CatalogError(f"manifest 不存在: {manifest_path}")
        if not _inside(manifest_path, project_root):
            raise CatalogError(f"manifest 必须位于 project 内: {manifest_path}")
        incoming_hash = _semantic_manifest_hash(_load_object(manifest_path))
    if exact is not None:
        previous_hash = (exact.get("evidence_fingerprint") or {}).get("manifest_sha256")
        if incoming_hash is None:
            action = "reuse_existing_debug"
        elif previous_hash == incoming_hash:
            action = "no_reanalysis"
        else:
            action = "incremental_analysis"
        return {
            "schema_version": SCHEMA_VERSION,
            "match": "exact",
            "action": action,
            "ticket": exact,
            "incoming_manifest_sha256": incoming_hash,
            "global_index_written": False,
        }
    candidates = [
        record
        for record in catalog.get("tickets") or []
        if _keyword_match(record, keywords or [])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "match": "similar_candidates" if candidates else "none",
        "action": "revalidate_before_reuse" if candidates else "create_or_locate_debug_twin",
        "candidates": candidates,
        "global_index_written": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="项目本地 ICODE debug 目录工具")
    sub = parser.add_subparsers(dest="command", required=True)
    rebuild = sub.add_parser("rebuild", help="从 .icode_output/.debug 原子重建目录")
    rebuild.add_argument("--project", required=True)
    query = sub.add_parser("query", help="按 TB 身份查询精确孪生或相似候选")
    query.add_argument("--project", required=True)
    query.add_argument("--pid", required=True)
    query.add_argument("--lib", required=True)
    query.add_argument("--num", required=True)
    query.add_argument("--manifest")
    query.add_argument("--keyword", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "rebuild":
            result = rebuild_catalog(args.project)
        else:
            result = query_catalog(
                args.project,
                args.pid,
                args.lib,
                args.num,
                manifest=args.manifest,
                keywords=args.keyword,
            )
    except CatalogError as exc:
        print(f"debug_catalog: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"debug_catalog: 文件系统错误: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
