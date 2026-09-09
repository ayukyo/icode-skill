"""ICODE 本地 SQLite 全文索引。源文件只读，只写自身受管数据库。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import MANAGED_WRITE_ANNOTATIONS, READ_ONLY_ANNOTATIONS, describe, fail, load_config, ok, resolve_allowed_path  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-local-index"
TOOLS = ["describe_capabilities", "build_index", "query_index", "index_status"]
DEFAULTS = {
    "allowed_roots": ["$HOME"],
    "data_dir": "$HOME/.claude/icode_data/local-index",
    "max_files": 5000,
    "max_file_bytes": 524288,
    "max_total_bytes": 104857600,
    "extensions": [".c", ".cc", ".cpp", ".h", ".hpp", ".py", ".rs", ".go", ".java", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cmake", ".sh", ".log"],
    "exclude_dirs": [".git", ".venv", "node_modules", "build", "dist", "__pycache__", ".icode_output"]
}
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_LOCAL_INDEX_CONFIG", SERVER_DIR, DEFAULTS)


def _db_path(root: Path, config: dict[str, Any], *, create_dir: bool = False) -> Path:
    data_dir = Path(os.path.expandvars(os.path.expanduser(str(config["data_dir"])))).resolve()
    if create_dir:
        data_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(root).encode()).hexdigest()[:20]
    return data_dir / f"{key}.sqlite3"


def _iter_files(root: Path, config: dict[str, Any]) -> Iterable[Path]:
    extensions = {str(item).lower() for item in config["extensions"]}
    excluded = {str(item) for item in config["exclude_dirs"]}
    for current, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in excluded]
        dirs.sort()
        base = Path(current)
        for name in sorted(files):
            path = base / name
            if path.is_symlink():
                continue
            if path.suffix.lower() in extensions or name in {"CMakeLists.txt", "Makefile"}:
                yield path


def _schema(connection: sqlite3.Connection) -> str:
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("CREATE TABLE files (path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, content TEXT NOT NULL)")
    try:
        connection.execute("CREATE VIRTUAL TABLE search USING fts5(path UNINDEXED, content)")
        return "fts5"
    except sqlite3.OperationalError:
        connection.execute("CREATE TABLE search (path TEXT PRIMARY KEY, content TEXT NOT NULL)")
        return "like"


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return describe(SERVER_NAME, TOOLS, "大型本地源码/日志/文档的可重建 SQLite 全文索引", managed_writes=["configured data_dir/*.sqlite3"])


@mcp.tool(annotations=MANAGED_WRITE_ANNOTATIONS)
def build_index(root: str, max_files: int = 2000) -> dict[str, Any]:
    try:
        config = _config()
        base = resolve_allowed_path(root, config)
        if not base.is_dir():
            return fail("not_directory", f"不是目录: {base}")
        limit = min(max(1, max_files), int(config["max_files"]))
        destination = _db_path(base, config, create_dir=True)
        fd, temp_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
        os.close(fd)
        temp = Path(temp_name)
        count = 0
        total_bytes = 0
        skipped_large = 0
        truncated = False
        try:
            connection = sqlite3.connect(temp)
            mode = _schema(connection)
            for path in _iter_files(base, config):
                if count >= limit:
                    truncated = True
                    break
                stat = path.stat()
                if stat.st_size > int(config["max_file_bytes"]):
                    skipped_large += 1
                    continue
                if total_bytes + stat.st_size > int(config["max_total_bytes"]):
                    truncated = True
                    break
                content = path.read_text(encoding="utf-8", errors="replace")
                relative = str(path.relative_to(base))
                connection.execute("INSERT INTO files VALUES (?, ?, ?, ?)", (relative, stat.st_mtime_ns, stat.st_size, content))
                connection.execute("INSERT INTO search VALUES (?, ?)", (relative, content))
                count += 1
                total_bytes += stat.st_size
            meta = {
                "root": str(base),
                "built_at": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "file_count": count,
                "total_bytes": total_bytes,
                "skipped_large": skipped_large,
                "truncated": truncated,
            }
            connection.executemany("INSERT INTO meta VALUES (?, ?)", [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()])
            connection.commit()
            connection.close()
            os.replace(temp, destination)
        finally:
            if temp.exists():
                temp.unlink()
        return ok({"root": str(base), "database": str(destination), "file_count": count, "total_bytes": total_bytes, "skipped_large": skipped_large, "truncated": truncated})
    except (OSError, ValueError, PermissionError, sqlite3.Error) as exc:
        return fail("index_build_failed", str(exc))


def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    return {key: json.loads(value) for key, value in connection.execute("SELECT key, value FROM meta")}


def _stale_summary(base: Path, connection: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    stale = 0
    missing = 0
    checked = 0
    indexed: set[str] = set()
    for relative, mtime_ns, size in connection.execute("SELECT path, mtime_ns, size FROM files"):
        indexed.add(relative)
        path = base / relative
        checked += 1
        if not path.exists():
            missing += 1
            continue
        stat = path.stat()
        if stat.st_mtime_ns != mtime_ns or stat.st_size != size:
            stale += 1
    metadata = _metadata(connection)
    incomplete = bool(metadata.get("truncated", False))
    new = 0
    if not incomplete:
        for path in _iter_files(base, config):
            try:
                if path.stat().st_size > int(config["max_file_bytes"]):
                    continue
                if str(path.relative_to(base)) not in indexed:
                    new += 1
            except OSError:
                continue
    return {
        "checked": checked,
        "changed": stale,
        "missing": missing,
        "new": new,
        "incomplete": incomplete,
        "stale": stale > 0 or missing > 0 or new > 0,
    }


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def index_status(root: str) -> dict[str, Any]:
    try:
        config = _config()
        base = resolve_allowed_path(root, config)
        database = _db_path(base, config)
        if not database.exists():
            return ok({"root": str(base), "exists": False, "stale": True})
        with sqlite3.connect(database) as connection:
            return ok({"root": str(base), "database": str(database), "exists": True, "metadata": _metadata(connection), **_stale_summary(base, connection, config)})
    except (OSError, ValueError, PermissionError, sqlite3.Error) as exc:
        return fail("index_status_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def query_index(root: str, query: str, limit: int = 20) -> dict[str, Any]:
    try:
        if not query.strip():
            return fail("empty_query", "query 不能为空")
        if limit < 1 or limit > 100:
            return fail("invalid_limit", "limit 必须为 1..100")
        config = _config()
        base = resolve_allowed_path(root, config)
        database = _db_path(base, config)
        if not database.exists():
            return fail("index_missing", "索引不存在，请先 build_index")
        with sqlite3.connect(database) as connection:
            meta = _metadata(connection)
            if meta.get("mode") == "fts5":
                try:
                    rows = connection.execute("SELECT path, snippet(search, 1, '[', ']', ' … ', 24) FROM search WHERE search MATCH ? LIMIT ?", (query, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = connection.execute("SELECT path, substr(content, 1, 500) FROM search WHERE content LIKE ? LIMIT ?", (f"%{query}%", limit)).fetchall()
            else:
                rows = connection.execute("SELECT path, substr(content, 1, 500) FROM search WHERE content LIKE ? LIMIT ?", (f"%{query}%", limit)).fetchall()
            stale = _stale_summary(base, connection, config)
        terms = [item.lower() for item in query.split() if item]
        results = []
        for relative, snippet in rows:
            source = base / relative
            line = 1
            try:
                content = source.read_text(encoding="utf-8", errors="replace")
                positions = [content.lower().find(term) for term in terms]
                positions = [pos for pos in positions if pos >= 0]
                if positions:
                    line = content.count("\n", 0, min(positions)) + 1
            except OSError:
                pass
            results.append({"path": str(source), "relative_path": relative, "line": line, "source_ref": f"{source}:{line}", "snippet": snippet})
        return ok({"root": str(base), "query": query, "results": results, "count": len(results), **stale})
    except (OSError, ValueError, PermissionError, sqlite3.Error) as exc:
        return fail("index_query_failed", str(exc))


if __name__ == "__main__":
    mcp.run()
