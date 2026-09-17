#!/usr/bin/env python3
"""Check a public knowledge chapter against its private source record."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath


SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ARTICLE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
GIT_HEAD = re.compile(r"[0-9a-f]{7,64}\Z")
MERMAID = re.compile(r"```mermaid\s*\n\s*(?:flowchart|graph|sequenceDiagram|stateDiagram)", re.I)
TABLE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*\|", re.M)
PRIVATE_MARKER = re.compile(
    r"(?:\.icode_output|\.ico_metadata\.json|/icode\b|"
    r"/(?:home|mnt|tmp|opt|var|etc|usr)/|[A-Za-z]:\\Users\\|"
    r"\b(?:ticket_id|project_root|code_files)\b|本项目|本工程)", re.I
)
SECTIONS = ("问题", "原理", "示例", "边界", "自测")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value: object) -> bool:
    if not _text(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def validate(article: Path, metadata: Path, source: Path, *, check_source_files: bool = True) -> list[str]:
    errors: list[str] = []
    try:
        body = article.read_text(encoding="utf-8")
        meta = load_json(metadata)
        private = load_json(source)
    except (OSError, ValueError) as exc:
        return [str(exc)]

    volume, chapter = meta.get("volume"), meta.get("chapter")
    if meta.get("schema_version") != 1:
        errors.append("metadata.schema_version must be 1")
    if not isinstance(volume, str) or not SLUG.fullmatch(volume):
        errors.append("metadata.volume must be a slug")
    if not isinstance(chapter, str) or not SLUG.fullmatch(chapter):
        errors.append("metadata.chapter must be a slug")
    article_id = f"{volume}/{chapter}"
    for key in ("title", "summary"):
        if not _text(meta.get(key)):
            errors.append(f"metadata.{key} is required")
    if isinstance(meta.get("title"), str) and "\n" in meta["title"]:
        errors.append("metadata.title must be one line")
    if not isinstance(meta.get("order"), int) or isinstance(meta.get("order"), bool) or meta["order"] < 0:
        errors.append("metadata.order must be a nonnegative integer")
    if not isinstance(meta.get("tags"), list) or not all(_text(item) for item in meta["tags"]):
        errors.append("metadata.tags must be a list of strings")
    if not isinstance(meta.get("related"), list) or not all(
        isinstance(item, str) and ARTICLE_ID.fullmatch(item) for item in meta["related"]
    ):
        errors.append("metadata.related must contain volume/chapter ids")
    for key in ("created_at", "updated_at"):
        if not _timestamp(meta.get(key)):
            errors.append(f"metadata.{key} must be an ISO-8601 timestamp with timezone")

    if not re.search(r"^#\s+" + re.escape(str(meta.get("title", ""))) + r"\s*$", body, re.M):
        errors.append("article H1 must equal metadata.title")
    for name in SECTIONS:
        if not re.search(rf"^##\s+[^\n]*{name}", body, re.M):
            errors.append(f"article needs a section containing {name}")
    if not (MERMAID.search(body) or TABLE.search(body)):
        errors.append("article needs a Mermaid diagram or Markdown table")
    if PRIVATE_MARKER.search(body):
        errors.append("article contains an internal marker or absolute path")
    public_meta_text = json.dumps(meta, ensure_ascii=False)
    if PRIVATE_MARKER.search(public_meta_text):
        errors.append("public metadata contains an internal marker or absolute path")
    if len(body) > 14000:
        errors.append("article exceeds 14000 characters; split the topic")
    for block in re.findall(r"```[^\n]*\n(.*?)\n```", body, re.S):
        if len(block.splitlines()) > 40:
            errors.append("article contains a code/diagram block longer than 40 lines")

    if private.get("schema_version") != 1 or private.get("article_id") != article_id:
        errors.append("source schema_version/article_id mismatch")
    if not _timestamp(private.get("generated_at")):
        errors.append("source.generated_at needs an ISO-8601 timestamp")
    project_root = private.get("project_root")
    if not _text(project_root) or not Path(project_root).is_absolute():
        errors.append("source.project_root must be absolute")
    if not _text(private.get("ticket_id")):
        errors.append("source.ticket_id is required")
    private_terms = private.get("private_terms")
    if not isinstance(private_terms, list) or not all(_text(term) for term in private_terms):
        errors.append("source.private_terms must be a string list")
    else:
        for term in private_terms:
            if len(term.strip()) >= 4 and term.casefold() in body.casefold():
                errors.append(f"article contains private term: {term}")
            if len(term.strip()) >= 4 and term.casefold() in public_meta_text.casefold():
                errors.append(f"public metadata contains private term: {term}")
    decision = private.get("decision_record")
    if not isinstance(decision, dict) or not all(
        isinstance(decision.get(key), list)
        for key in ("reasons", "excluded", "verified_facts", "uncertainties")
    ):
        errors.append("source.decision_record needs four lists")
    baselines = private.get("baselines")
    if not isinstance(baselines, list) or not baselines:
        errors.append("source.baselines must be a nonempty list")
    else:
        for baseline in baselines:
            if not isinstance(baseline, dict) or not all(_text(baseline.get(k)) for k in ("repo", "branch", "head")):
                errors.append("source baseline needs repo/branch/head")
            elif not Path(baseline["repo"]).is_absolute() or not GIT_HEAD.fullmatch(baseline["head"]) or not isinstance(baseline.get("dirty"), bool):
                errors.append("source baseline head/dirty is invalid")

    refs = private.get("source_refs")
    if not isinstance(refs, list) or not refs:
        errors.append("source.source_refs must be a nonempty list")
    else:
        for ref in refs:
            if not isinstance(ref, dict):
                errors.append("source_ref must be an object")
                continue
            repo, rel = ref.get("repo"), ref.get("path")
            if not _text(repo) or not Path(repo).is_absolute() or not _text(rel):
                errors.append("source_ref needs absolute repo and relative path")
                continue
            rel_path = PurePosixPath(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                errors.append("source_ref path escapes its repo")
                continue
            if isinstance(baselines, list) and repo not in {
                item.get("repo") for item in baselines if isinstance(item, dict)
            }:
                errors.append("source_ref repo is absent from baselines")
            start, end = ref.get("start_line"), ref.get("end_line")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                errors.append("source_ref line range is invalid")
                continue
            if not _text(ref.get("symbol")) or not isinstance(ref.get("sha256"), str) or not HEX.fullmatch(ref["sha256"]):
                errors.append("source_ref symbol/hash is invalid")
                continue
            if check_source_files:
                repo_root = Path(repo).resolve()
                target = (repo_root / rel).resolve()
                if not target.is_relative_to(repo_root) or not target.is_file():
                    errors.append("source_ref file is missing or outside repo")
                    continue
                lines = target.read_bytes().splitlines(keepends=True)
                if end > len(lines) or hashlib.sha256(b"".join(lines[start - 1:end])).hexdigest() != ref["sha256"]:
                    errors.append("source_ref line/hash drift")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("article", type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    errors = validate(args.article, args.metadata, args.source)
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
