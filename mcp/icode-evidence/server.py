"""ICODE 确定性证据 MCP：只读、可回指、带摘要哈希。"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import (  # noqa: E402
    READ_ONLY_ANNOTATIONS,
    describe,
    fail,
    load_config,
    ok,
    read_text_lines,
    resolve_allowed_path,
    sha256_file,
)
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-evidence"
TOOLS = ["describe_capabilities", "inspect_file", "read_evidence", "verify_digest", "build_timeline", "inspect_corpus"]
DEFAULTS = {"allowed_roots": ["$HOME"], "max_read_bytes": 1048576, "max_corpus_files": 2000}
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_EVIDENCE_CONFIG", SERVER_DIR, DEFAULTS)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return describe(SERVER_NAME, TOOLS, "文件取证、摘要校验、日志时间线与文档语料清单")


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_file(path: str) -> dict[str, Any]:
    try:
        target = resolve_allowed_path(path, _config())
        stat = target.stat()
        answer: dict[str, Any] = {
            "path": str(target),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "mode": oct(stat.st_mode & 0o7777),
            "is_file": target.is_file(),
            "is_dir": target.is_dir(),
        }
        if target.is_file():
            answer["sha256"] = sha256_file(target)[0]
        return ok(answer)
    except (OSError, ValueError, PermissionError) as exc:
        return fail("inspect_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def read_evidence(path: str, start_line: int = 1, max_lines: int = 200) -> dict[str, Any]:
    try:
        config = _config()
        target = resolve_allowed_path(path, config)
        if not target.is_file():
            return fail("not_file", f"不是普通文件: {target}")
        window = read_text_lines(
            target,
            start_line=start_line,
            max_lines=max_lines,
            max_bytes=int(config["max_read_bytes"]),
        )
        digest, _, _ = sha256_file(target)
        window.update({"path": str(target), "sha256": digest, "source_ref": f"{target}:{start_line}"})
        return ok(window)
    except (OSError, ValueError, PermissionError) as exc:
        return fail("read_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def verify_digest(path: str, sha256: str) -> dict[str, Any]:
    try:
        target = resolve_allowed_path(path, _config())
        actual, _, _ = sha256_file(target)
        expected = sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            return fail("invalid_digest", "sha256 必须是 64 位十六进制字符串")
        return ok({"path": str(target), "expected": expected, "actual": actual, "matches": actual == expected})
    except (OSError, ValueError, PermissionError) as exc:
        return fail("digest_failed", str(exc))


def _timestamp_key(value: str) -> tuple[int, str]:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return (0, parsed.astimezone(timezone.utc).isoformat())
        return (1, parsed.isoformat())
    except ValueError:
        return (2, value)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def build_timeline(paths: list[str], timestamp_regex: str = r"(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+)", max_events: int = 1000) -> dict[str, Any]:
    try:
        if not paths or len(paths) > 50:
            return fail("invalid_paths", "paths 数量必须为 1..50")
        if max_events < 1 or max_events > 10000:
            return fail("invalid_limit", "max_events 必须为 1..10000")
        pattern = re.compile(timestamp_regex)
        if "timestamp" not in pattern.groupindex:
            return fail("invalid_regex", "timestamp_regex 必须包含命名组 (?P<timestamp>...)")
        config = _config()
        events: list[dict[str, Any]] = []
        digests: dict[str, str] = {}
        source_truncated: dict[str, bool] = {}
        for raw in paths:
            target = resolve_allowed_path(raw, config)
            digests[str(target)] = sha256_file(target)[0]
            max_read_bytes = int(config["max_read_bytes"])
            window = target.read_bytes()[: max_read_bytes + 1]
            source_truncated[str(target)] = len(window) > max_read_bytes
            data = window[:max_read_bytes]
            for line_no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                match = pattern.search(line)
                if match:
                    stamp = match.group("timestamp")
                    events.append({"timestamp": stamp, "path": str(target), "line": line_no, "source_ref": f"{target}:{line_no}", "text": line[:2000]})
        events.sort(key=lambda item: (_timestamp_key(item["timestamp"]), item["path"], item["line"]))
        truncated = len(events) > max_events or any(source_truncated.values())
        return ok({
            "events": events[:max_events],
            "source_sha256": digests,
            "source_truncated": source_truncated,
            "truncated": truncated,
        })
    except (OSError, ValueError, PermissionError, re.error) as exc:
        return fail("timeline_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_corpus(root: str, extensions: list[str] | None = None, max_files: int = 500) -> dict[str, Any]:
    try:
        config = _config()
        base = resolve_allowed_path(root, config)
        if not base.is_dir():
            return fail("not_directory", f"不是目录: {base}")
        limit = min(max(1, max_files), int(config["max_corpus_files"]))
        suffixes = {item.lower() if item.startswith(".") else f".{item.lower()}" for item in (extensions or ["md", "txt", "pdf", "docx", "pptx", "xlsx", "csv", "log"])}
        files: list[dict[str, Any]] = []
        truncated = False
        for candidate in sorted(base.rglob("*")):
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() not in suffixes:
                continue
            resolved = candidate.resolve()
            if resolved != base and base not in resolved.parents:
                continue
            if len(files) >= limit:
                truncated = True
                break
            digest, _, _ = sha256_file(resolved)
            files.append({"path": str(resolved), "relative_path": str(candidate.relative_to(base)), "size": resolved.stat().st_size, "sha256": digest})
        manifest_digest = __import__("hashlib").sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return ok({"root": str(base), "files": files, "file_count": len(files), "manifest_sha256": manifest_digest, "truncated": truncated})
    except (OSError, ValueError, PermissionError) as exc:
        return fail("corpus_failed", str(exc))


if __name__ == "__main__":
    mcp.run()
