#!/usr/bin/env python3
"""Build a deterministic, read-only evidence manifest.

The tool normalizes Teambition probe/meta JSON and local evidence files.  It
does not infer a root cause, contact Teambition, or mutate any input.  The only
side effect is the explicitly requested output manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".7z")
_LOG_SUFFIXES = {".log", ".txt", ".mcap", ".bag", ".json", ".jsonl", ".trace"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8", errors="replace"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _under_root(root: Path, path: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(path))) == str(root)
    except ValueError:
        return False


def _trusted_root(value: str | os.PathLike[str]) -> Path:
    root = Path(value).resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError(f"--root must be an existing non-root directory: {root}")
    return root


def _resolve_input(root: Path, value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if not _under_root(root, resolved):
        raise ValueError(f"input path escapes --root: {value}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _normalize_ext(name: str, ext: object) -> str:
    value = str(ext or "").strip().lower().lstrip(".")
    if value:
        return value
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix


def _safe_filename(name: str, ext: str) -> str:
    base = (name or "file").strip()
    if ext and not base.lower().endswith("." + ext.lower()):
        return f"{base}.{ext}"
    return base


def url_fingerprint(url: object) -> str | None:
    """Hash a URL without query/fragment credentials or expiring tokens."""
    if not url:
        return None
    parsed = urlsplit(str(url))
    stable = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    return _sha256_text(stable)


def canonical_remote_identity(raw: dict, source_activity: str | None = None) -> tuple[str, str]:
    """Return ``(dedupe_reason, stable_key)`` for one remote representation."""
    return remote_identity_aliases(raw, source_activity)[0]


def remote_identity_aliases(raw: dict, source_activity: str | None = None) -> list[tuple[str, str]]:
    """Return every usable identity alias, ordered from strongest to weakest.

    A representation carrying both a remote ID and URL is the bridge that lets
    an ID-only and URL-only representation converge on one logical artifact.
    Metadata fallback is registered only when neither strong alias exists, so
    two distinct remote IDs with the same name/size are never collapsed.
    """
    aliases = []
    remote_id = (raw.get("remote_id") or raw.get("_id") or raw.get("fileId")
                 or raw.get("file_id"))
    if remote_id:
        aliases.append(("same_remote_id", f"remote_id:{remote_id}"))
    fingerprint = raw.get("url_fingerprint") or url_fingerprint(raw.get("url") or raw.get("downloadUrl"))
    if fingerprint:
        aliases.append(("same_url_fingerprint", f"url:{fingerprint}"))
    if aliases:
        return aliases
    name = str(raw.get("name") or raw.get("fileName") or "file").strip()
    ext = _normalize_ext(name, raw.get("ext") or raw.get("fileType"))
    size = raw.get("size") if raw.get("size") is not None else raw.get("fileSize")
    fallback = json.dumps([name.casefold(), ext, size, source_activity or ""],
                          ensure_ascii=False, separators=(",", ":"))
    return [("same_metadata_fallback", f"metadata:{_sha256_text(fallback)}")]


def _representation_fingerprint(raw: dict, kind: str, source_activity: str | None) -> str:
    remote_id = (raw.get("remote_id") or raw.get("_id") or raw.get("fileId")
                 or raw.get("file_id"))
    name = str(raw.get("name") or raw.get("fileName") or "file").strip()
    ext = _normalize_ext(name, raw.get("ext") or raw.get("fileType"))
    size = raw.get("size") if raw.get("size") is not None else raw.get("fileSize")
    data = [kind, remote_id, name, ext, size,
            raw.get("url_fingerprint") or url_fingerprint(raw.get("url") or raw.get("downloadUrl")),
            source_activity]
    return _sha256_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _activity_text(raw: dict) -> str:
    content = raw.get("content")
    if isinstance(content, dict):
        value = content.get("comment") or content.get("text") or ""
    else:
        value = raw.get("comment") or raw.get("text") or ""
    return str(value).strip()


def _activity_id(raw: dict) -> str:
    explicit = raw.get("activity_id") or raw.get("_id") or raw.get("id")
    if explicit:
        return str(explicit)
    payload = json.dumps([raw.get("created") or "", _activity_text(raw)],
                         ensure_ascii=False, separators=(",", ":"))
    return f"activity:{_sha256_text(payload)[:24]}"


def _source_objects(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return [item for item in payload["items"] if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError(f"source JSON must be an object or list, got {type(payload).__name__}")


def _identity_from_sources(sources: list[dict], source_label: str | None) -> dict:
    def first(*keys: str):
        for source in sources:
            for key in keys:
                value = source.get(key)
                if value not in (None, ""):
                    return value
        return None

    label = first("id")
    lib = first("lib")
    num = first("num", "uniqueId")
    if (not lib or num is None) and isinstance(label, str):
        match = re.match(r"^([A-Za-z0-9]+)-(\d+)$", label)
        if match:
            lib = lib or match.group(1)
            num = num if num is not None else int(match.group(2))
    statuses = []
    for source in sources:
        status = source.get("status")
        if status not in (None, "") and status not in statuses:
            statuses.append(status)
    return {
        "source_label": source_label,
        "pid": first("pid", "project_id"),
        "lib": lib,
        "num": num,
        "task_id": first("task_id", "_id"),
        "status": statuses[0] if len(statuses) == 1 else statuses,
    }


def _iter_remote_representations(source: dict):
    activities = source.get("activities")
    if not isinstance(activities, list):
        activities = source.get("comments") or []
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        activity_id = _activity_id(activity)
        content = activity.get("content")
        containers = []
        if isinstance(content, dict):
            containers.append(content)
        containers.append(activity)
        seen_container = set()
        for container in containers:
            if id(container) in seen_container:
                continue
            seen_container.add(id(container))
            for key in ("files", "attachments"):
                for raw in container.get(key) or []:
                    if isinstance(raw, dict):
                        yield raw, activity_id, key
    content = source.get("content")
    containers = [source]
    if isinstance(content, dict):
        containers.append(content)
    for container in containers:
        for key in ("files", "attachments"):
            for raw in container.get(key) or []:
                if isinstance(raw, dict):
                    yield raw, None, f"top_{key}"


def _local_files(root: Path, values: list[str], parse_failures: list[dict],
                 exclude_paths=None) -> list[dict]:
    files: dict[str, dict] = {}
    excluded = set()
    for value in exclude_paths or []:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        excluded.add(str(candidate.absolute()))
        excluded.add(str(candidate.resolve(strict=False)))

    def add_file(candidate: Path) -> None:
        lexical = Path(os.path.abspath(candidate))
        try:
            lexical.relative_to(root)
            resolved = candidate.resolve(strict=True)
            if not _under_root(root, resolved):
                raise ValueError("symlink target escapes --root")
            if str(lexical) in excluded or str(resolved) in excluded:
                return
            if not resolved.is_file():
                return
            local_path = lexical.relative_to(root).as_posix()
            stat = resolved.stat()
            files[local_path] = {
                "path": resolved,
                "local_path": local_path,
                "original_name": lexical.name,
                "normalized_ext": lexical.suffix.lower().lstrip("."),
                "size": stat.st_size,
                "sha256": _file_sha256(resolved),
                "mtime_ns": stat.st_mtime_ns,
                "is_symlink": candidate.is_symlink(),
                "symlink_target": (_relative(root, resolved) if candidate.is_symlink() else None),
            }
        except (OSError, RuntimeError, ValueError) as exc:
            parse_failures.append({"input": str(candidate), "error": str(exc)})

    for value in values:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        lexical = Path(os.path.abspath(candidate))
        try:
            lexical.relative_to(root)
            path = candidate.resolve(strict=True)
            if not _under_root(root, path):
                raise ValueError(f"input path escapes --root: {value}")
        except (OSError, RuntimeError, ValueError) as exc:
            parse_failures.append({"input": str(value), "error": str(exc)})
            continue
        if path.is_file():
            add_file(candidate)
            continue
        if not path.is_dir():
            parse_failures.append({"input": str(value), "error": "not a regular file or directory"})
            continue
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            current = Path(dirpath)
            # Never descend through symlinked directories, even when they point in-root.
            dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
            for name in sorted(filenames):
                candidate = current / name
                add_file(candidate)
    return [files[key] for key in sorted(files)]


def _semantic_ids(manifest: dict) -> set[str]:
    ids = set()
    for activity in manifest.get("activities", []):
        semantic = activity.get("semantic_id")
        if not semantic:
            payload = [activity.get("activity_id"), activity.get("summary_fingerprint")]
            semantic = f"activity:{_sha256_text(json.dumps(payload, separators=(',', ':')))[:24]}"
        ids.add(str(semantic))
    for artifact in manifest.get("artifacts", []):
        digest = artifact.get("sha256")
        if digest:
            ids.add(f"artifact:sha256:{digest}")
            continue
        semantic = artifact.get("semantic_id") or artifact.get("artifact_id")
        ids.add(f"artifact:{semantic}:size={artifact.get('size')}")
    return ids


def _representation_ids(manifest: dict) -> set[str]:
    result = set()
    for artifact in manifest.get("artifacts", []):
        result.update(str(value) for value in artifact.get("representations", []) if value)
    return result


def build_manifest_from_objects(root, source_objects, local_paths, previous=None, source_label=None,
                                input_scope=None, initial_failures=None, exclude_paths=None):
    """Build a manifest from already loaded JSON objects (used by read-only callers)."""
    root_path = _trusted_root(root)
    sources = []
    for payload in source_objects:
        sources.extend(_source_objects(payload))
    parse_failures: list[dict] = list(initial_failures or [])

    activities = []
    activity_seen = set()
    timestamps = []
    for source in sources:
        raw_activities = source.get("activities")
        if not isinstance(raw_activities, list):
            raw_activities = source.get("comments") or []
        for raw in raw_activities:
            if not isinstance(raw, dict):
                parse_failures.append({"input": "activity", "error": "non-object activity skipped"})
                continue
            activity_id = _activity_id(raw)
            text = _activity_text(raw)
            fingerprint = _sha256_text(text)
            key = (activity_id, fingerprint)
            if key in activity_seen:
                continue
            activity_seen.add(key)
            created = raw.get("created") or raw.get("updated")
            if created:
                timestamps.append(str(created))
            activities.append({
                "activity_id": activity_id,
                "semantic_id": f"activity:{_sha256_text(json.dumps([activity_id, fingerprint], separators=(',', ':')))[:24]}",
                "created": created,
                "summary_fingerprint": fingerprint,
                "text_length": len(text),
            })

    artifacts = []
    remote_by_alias = {}
    duplicate_records = []
    artifact_order = 0
    for source in sources:
        for raw, activity_id, kind in _iter_remote_representations(source):
            aliases = remote_identity_aliases(raw, activity_id)
            representation = _representation_fingerprint(raw, kind, activity_id)
            name = str(raw.get("name") or raw.get("fileName") or "file").strip()
            ext = _normalize_ext(name, raw.get("ext") or raw.get("fileType"))
            remote_id = (raw.get("remote_id") or raw.get("_id") or raw.get("fileId")
                         or raw.get("file_id"))
            size = raw.get("size") if raw.get("size") is not None else raw.get("fileSize")
            fingerprint = (raw.get("url_fingerprint")
                           or url_fingerprint(raw.get("url") or raw.get("downloadUrl")))
            matches = []
            matched_reason = None
            for reason, alias in aliases:
                existing = remote_by_alias.get(alias)
                if (existing is not None and alias.startswith("url:") and remote_id
                        and existing.get("remote_id")
                        and str(existing["remote_id"]) != str(remote_id)):
                    # A stable remote ID outranks a reused URL path.  Do not
                    # collapse two explicit, conflicting IDs via URL alone.
                    continue
                if existing is not None and existing not in matches:
                    matches.append(existing)
                    matched_reason = matched_reason or reason
            if matches:
                artifact = min(matches, key=lambda item: item["_order"])
                for other in matches:
                    if other is artifact:
                        continue
                    artifact["_aliases"].update(other["_aliases"])
                    artifact["representations"].extend(
                        value for value in other["representations"]
                        if value not in artifact["representations"])
                    for field in ("remote_id", "url_fingerprint", "source_activity"):
                        if not artifact.get(field) and other.get(field):
                            artifact[field] = other[field]
                    if other in artifacts:
                        artifacts.remove(other)
                    duplicate_records = [
                        (old_reason, artifact if owner is other else owner, old_representation)
                        for old_reason, owner, old_representation in duplicate_records
                    ]
                    for alias, owner in list(remote_by_alias.items()):
                        if owner is other:
                            remote_by_alias[alias] = artifact
                if representation not in artifact["representations"]:
                    artifact["representations"].append(representation)
                duplicate_records.append((matched_reason or aliases[0][0], artifact, representation))
                if not artifact.get("remote_id") and remote_id:
                    artifact["remote_id"] = str(remote_id)
                if not artifact.get("url_fingerprint") and fingerprint:
                    artifact["url_fingerprint"] = fingerprint
            else:
                artifact = {
                    "artifact_id": None,
                    "semantic_id": None,
                    "remote_id": str(remote_id) if remote_id else None,
                    "original_name": name,
                    "normalized_ext": ext,
                    "size": size,
                    "url_fingerprint": fingerprint,
                    "local_path": None,
                    "sha256": None,
                    "content_hash_pending": True,
                    "source_activity": activity_id,
                    "representations": [representation],
                    "_aliases": set(),
                    "_order": artifact_order,
                }
                artifact_order += 1
                artifacts.append(artifact)
            artifact["_aliases"].update(alias for _reason, alias in aliases)
            for _reason, alias in aliases:
                owner = remote_by_alias.get(alias)
                if (owner is not None and owner is not artifact and alias.startswith("url:")
                        and owner.get("remote_id") and artifact.get("remote_id")
                        and str(owner["remote_id"]) != str(artifact["remote_id"])):
                    remote_by_alias[alias] = None
                elif alias not in remote_by_alias or owner is not None:
                    remote_by_alias[alias] = artifact

    # Normalize aliases only after all bridge representations are seen, making
    # the final artifact identity independent of files[]/attachments[] order.
    previous_aliases = {}
    if isinstance(previous, dict):
        for prior in previous.get("artifacts", []):
            if not isinstance(prior, dict):
                continue
            prior_semantic = prior.get("semantic_id")
            if not prior_semantic:
                continue
            aliases = prior.get("identity_aliases") or [prior_semantic]
            for alias in aliases:
                if isinstance(alias, str) and alias:
                    previous_aliases.setdefault(alias, str(prior_semantic))
    for artifact in artifacts:
        aliases = sorted(artifact.pop("_aliases"),
                         key=lambda value: ({"remote_id": 0, "url": 1, "metadata": 2}.get(
                             value.split(":", 1)[0], 9), value))
        artifact.pop("_order", None)
        prior_semantics = [previous_aliases[alias] for alias in aliases if alias in previous_aliases]
        # Identity enrichment (URL-only -> remote ID+URL) is not new evidence.
        # Keep the prior canonical semantic ID whenever an alias bridges rounds.
        canonical = prior_semantics[0] if prior_semantics else aliases[0]
        artifact["identity_aliases"] = aliases
        artifact["semantic_id"] = canonical
        artifact["artifact_id"] = f"artifact:{_sha256_text(canonical)[:24]}"

    duplicates = [
        {
            "reason": reason,
            "primary_artifact_id": artifact["artifact_id"],
            "duplicate_representation": representation,
        }
        for reason, artifact, representation in duplicate_records
    ]

    # downloaded[] is an explicit remote-to-local bridge.  Include those files
    # even when the caller did not repeat them as --path arguments.
    requested_local = [str(value) for value in local_paths]
    downloaded = []
    for source in sources:
        for item in source.get("downloaded") or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            downloaded.append(item)
            requested_local.append(str(item["path"]))
    local_infos = _local_files(root_path, requested_local, parse_failures, exclude_paths=exclude_paths)
    local_by_rel = {item["local_path"]: item for item in local_infos}
    consumed_local = set()
    download_queues: dict[str, list[dict]] = {}
    download_by_alias: dict[str, list[dict]] = {}
    for item in downloaded:
        try:
            resolved = _resolve_input(root_path, str(item["path"]))
            rel = _relative(root_path, resolved)
        except (OSError, ValueError) as exc:
            parse_failures.append({"input": str(item.get("path")), "error": str(exc)})
            continue
        info = local_by_rel.get(rel)
        if info:
            for _reason, alias in remote_identity_aliases(item, None):
                download_by_alias.setdefault(alias, []).append(info)
            download_queues.setdefault(str(item.get("name") or resolved.name).casefold(), []).append(info)

    for artifact in artifacts:
        names = [artifact["original_name"].casefold(),
                 _safe_filename(artifact["original_name"], artifact["normalized_ext"]).casefold()]
        matched = None
        for alias in artifact.get("identity_aliases", []):
            queue = download_by_alias.get(alias) or []
            while queue and queue[0]["local_path"] in consumed_local:
                queue.pop(0)
            if queue:
                matched = queue.pop(0)
                break
        for name in names:
            if matched:
                break
            queue = download_queues.get(name) or []
            while queue and queue[0]["local_path"] in consumed_local:
                queue.pop(0)
            if queue:
                matched = queue.pop(0)
                break
        if matched:
            artifact["local_path"] = matched["local_path"]
            artifact["sha256"] = matched["sha256"]
            artifact["content_hash_pending"] = False
            artifact["is_symlink"] = matched["is_symlink"]
            artifact["symlink_target"] = matched["symlink_target"]
            artifact["representations"].append(
                _sha256_text(json.dumps(["local", matched["local_path"], matched["size"],
                                         matched["is_symlink"], matched["symlink_target"]],
                                        separators=(",", ":"))))
            consumed_local.add(matched["local_path"])

    for info in local_infos:
        if info["local_path"] in consumed_local:
            continue
        semantic = f"sha256:{info['sha256']}"
        artifacts.append({
            "artifact_id": f"artifact:{_sha256_text('local:' + info['local_path'])[:24]}",
            "semantic_id": semantic,
            "remote_id": None,
            "original_name": info["original_name"],
            "normalized_ext": info["normalized_ext"],
            "size": info["size"],
            "url_fingerprint": None,
            "local_path": info["local_path"],
            "sha256": info["sha256"],
            "content_hash_pending": False,
            "source_activity": None,
            "identity_aliases": [semantic],
            "is_symlink": info["is_symlink"],
            "symlink_target": info["symlink_target"],
            "representations": [
                _sha256_text(json.dumps(["local", info["local_path"], info["size"],
                                         info["is_symlink"], info["symlink_target"]],
                                        separators=(",", ":")))
            ],
        })

    by_sha = {}
    seen_sha_pairs = set()
    for artifact in artifacts:
        digest = artifact.get("sha256")
        if not digest:
            continue
        primary = by_sha.setdefault(digest, artifact)
        if primary is artifact:
            continue
        pair = (primary["artifact_id"], artifact["artifact_id"])
        if pair not in seen_sha_pairs:
            duplicates.append({
                "reason": "same_sha256",
                "primary_artifact_id": primary["artifact_id"],
                "duplicate_artifact_id": artifact["artifact_id"],
                "sha256": digest,
            })
            seen_sha_pairs.add(pair)

    derivations = []
    archive_by_stem = {}
    for artifact in artifacts:
        name = artifact["original_name"].casefold()
        for suffix in _ARCHIVE_SUFFIXES:
            if name.endswith(suffix):
                archive_by_stem[name[:-len(suffix)]] = artifact["artifact_id"]
                break
    for artifact in artifacts:
        local_path = artifact.get("local_path")
        if not local_path:
            continue
        parts = Path(local_path).parts
        for stem, parent_id in archive_by_stem.items():
            if any(part.casefold() == stem for part in parts[:-1]):
                derivations.append({"parent_artifact_id": parent_id,
                                    "child_artifact_id": artifact["artifact_id"],
                                    "relation": "archive_extraction_candidate"})
                break

    artifacts.sort(key=lambda item: (item.get("local_path") or "", item["artifact_id"]))
    activities.sort(key=lambda item: (str(item.get("created") or ""), item["activity_id"]))
    previous_manifest = previous or {}
    current_semantic = _semantic_ids({"activities": activities, "artifacts": artifacts})
    previous_semantic = _semantic_ids(previous_manifest) if previous is not None else set()
    new_ids = sorted(current_semantic - previous_semantic)
    removed_ids = sorted(previous_semantic - current_semantic)
    current_representations = _representation_ids({"artifacts": artifacts})
    previous_representations = _representation_ids(previous_manifest) if previous is not None else set()
    added_representations = sorted(current_representations - previous_representations)

    identity = _identity_from_sources(sources, source_label)
    old_status = (previous_manifest.get("source_identity") or {}).get("status")
    status_changed = previous is not None and identity.get("status") != old_status
    duplicate_representation = bool(added_representations and not new_ids)
    status_only = bool(status_changed and not new_ids and not removed_ids and not added_representations)

    log_candidates = sorted({a["local_path"] for a in artifacts
                             if a.get("local_path") and Path(a["original_name"]).suffix.lower() in _LOG_SUFFIXES})
    node_candidates = sorted({Path(value).stem for value in log_candidates})
    timestamp_values = sorted(set(timestamps))
    semantic_payload = {
        "source_identity": {
            key: (None if identity.get(key) is None else str(identity.get(key)))
            for key in ("pid", "lib", "num", "task_id")
        },
        "semantic_ids": sorted(current_semantic),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _generated_at(),
        "status": "partial" if parse_failures else "ok",
        "semantic_fingerprint": _sha256_text(
            json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        "read_only": True,
        "side_effects": ["write_output_manifest"],
        "input_scope": input_scope or {"root": str(root_path), "source_jsons": [], "local_paths": []},
        "source_identity": identity,
        "activities": activities,
        "artifacts": artifacts,
        "derivations": derivations,
        "duplicates": duplicates,
        "delta": {
            "baseline": previous is None,
            "new_semantic_evidence": bool(new_ids),
            "new_semantic_ids": new_ids,
            "duplicate_representation": duplicate_representation,
            "new_representation_ids": added_representations,
            "status_only": status_only,
            "removed_or_unavailable": bool(removed_ids),
            "removed_semantic_ids": removed_ids,
        },
        "coverage_hints": {
            "timestamp_range": {
                "start": timestamp_values[0] if timestamp_values else None,
                "end": timestamp_values[-1] if timestamp_values else None,
            },
            "node_candidates": node_candidates,
            "log_file_candidates": log_candidates,
            "parse_failures": parse_failures,
        },
    }
    return manifest


def build_manifest(root, source_jsons, local_paths, previous=None, source_label=None,
                   exclude_paths=None):
    """Return normalized manifest; never modify an input source."""
    root_path = _trusted_root(root)
    payloads = []
    source_scope = []
    input_failures = []
    for value in source_jsons:
        scope_value = str(value)
        try:
            source_path = _resolve_input(root_path, value)
            scope_value = _relative(root_path, source_path)
            with source_path.open(encoding="utf-8") as stream:
                payloads.append(json.load(stream))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            input_failures.append({"input": str(value), "error": f"{type(exc).__name__}: {exc}"})
        source_scope.append(scope_value)
    previous_manifest = previous
    previous_scope = None
    if isinstance(previous, (str, os.PathLike)):
        try:
            previous_path = _resolve_input(root_path, previous)
            previous_scope = _relative(root_path, previous_path)
            with previous_path.open(encoding="utf-8") as stream:
                previous_manifest = json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            input_failures.append({"input": str(previous), "error": f"{type(exc).__name__}: {exc}"})
            previous_manifest = None
            previous_scope = str(previous)
    local_scope = [str(value) for value in local_paths]
    excluded = list(exclude_paths or []) + list(source_jsons)
    if isinstance(previous, (str, os.PathLike)):
        excluded.append(str(previous))
    return build_manifest_from_objects(
        root_path,
        payloads,
        local_paths,
        previous=previous_manifest,
        source_label=source_label,
        initial_failures=input_failures,
        exclude_paths=excluded,
        input_scope={
            "root": str(root_path),
            "source_jsons": source_scope,
            "local_paths": local_scope,
            "previous": previous_scope,
        },
    )


def validate_output_not_input(root, output, source_jsons, local_paths, previous=None,
                              explicit_excludes=None):
    """Reject an output resolving to an explicitly supplied input file."""
    root_path = _trusted_root(root)
    output_path = Path(output).expanduser().resolve(strict=False)
    if not _under_root(root_path, output_path):
        raise ValueError(f"output path escapes --root: {output}")
    allowed_existing_outputs = set()
    for value in explicit_excludes or []:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = root_path / candidate
        allowed_existing_outputs.add(candidate.resolve(strict=False))

    def refreshable_manifest(path: Path) -> bool:
        if path.name != "evidence_manifest.json" or not path.is_file():
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            isinstance(value, dict)
            and value.get("schema_version") == SCHEMA_VERSION
            and value.get("read_only") is True
            and isinstance(value.get("activities"), list)
            and isinstance(value.get("artifacts"), list)
        )
    values = list(source_jsons)
    if previous:
        values.append(previous)
    for value in values:
        try:
            if _resolve_input(root_path, value) == output_path:
                raise ValueError(f"output must not overwrite input: {value}")
        except FileNotFoundError:
            continue
    for value in local_paths:
        try:
            candidate = _resolve_input(root_path, value)
        except (OSError, ValueError):
            continue
        if candidate.is_file() and candidate == output_path:
            raise ValueError(f"output must not overwrite local evidence: {value}")
        if candidate.is_dir() and output_path.exists():
            try:
                output_path.relative_to(candidate)
            except ValueError:
                continue
            if not (
                output_path in allowed_existing_outputs
                and refreshable_manifest(output_path)
            ):
                raise ValueError(f"output must not overwrite evidence reached through directory: {output_path}")
    return output_path


def write_manifest(path, manifest):
    """Write UTF-8 JSON atomically with os.replace()."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=False)
            stream.write("\n")
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv=None):
    """Parse --root/--source-json/--path/--previous/--source-label/--output."""
    parser = argparse.ArgumentParser(description="Build a deterministic evidence manifest")
    parser.add_argument("--root", default=os.getcwd(), help="trusted evidence root (default: cwd)")
    parser.add_argument("--source-json", action="append", default=[], help="TB probe/meta JSON under root")
    parser.add_argument("--path", action="append", default=[], help="local evidence file/directory under root")
    parser.add_argument("--previous", help="previous evidence manifest under root")
    parser.add_argument("--exclude-path", action="append", default=[],
                        help="derived/control file to exclude from a scanned directory")
    parser.add_argument("--source-label", help="optional source label")
    parser.add_argument("--output", required=True, help="manifest output path")
    args = parser.parse_args(argv)
    try:
        validate_output_not_input(
            args.root, args.output, args.source_json, args.path, args.previous,
            explicit_excludes=args.exclude_path,
        )
        manifest = build_manifest(
            args.root, args.source_json, args.path,
            previous=args.previous, source_label=args.source_label,
            exclude_paths=[*args.exclude_path, args.output],
        )
        write_manifest(args.output, manifest)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"evidence_intake: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
