"""ICODE 工作区与构建产物来源 MCP。所有工具均只读 Git/文件系统。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import READ_ONLY_ANNOTATIONS, describe, fail, load_config, ok, resolve_allowed_path, run_command, sha256_file  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-workspace"
TOOLS = ["describe_capabilities", "inspect_workspace", "inspect_repo_matrix", "inspect_build_inputs", "inspect_artifact"]
DEFAULTS = {"allowed_roots": ["$HOME"], "max_repo_depth": 5, "max_repos": 100, "max_command_chars": 30000}
mcp = FastMCP(SERVER_NAME)


def _config() -> dict[str, Any]:
    return load_config("ICODE_WORKSPACE_CONFIG", SERVER_DIR, DEFAULTS)


def _git(path: Path, *args: str) -> dict[str, Any]:
    return run_command(["git", "-C", str(path), *args], timeout=15, max_chars=int(_config()["max_command_chars"]))


def _git_text(path: Path, *args: str) -> str | None:
    result = _git(path, *args)
    return result["stdout"].strip() if result["returncode"] == 0 else None


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return describe(SERVER_NAME, TOOLS, "多 Git 根、worktree、构建输入与二进制来源观测")


def _repo_snapshot(path: Path) -> dict[str, Any]:
    root_text = _git_text(path, "rev-parse", "--show-toplevel")
    if not root_text:
        return {"path": str(path), "is_git": False}
    # git 可从允许目录向上发现仓库；对发现出的顶层再次做边界校验，避免越权读取父仓库。
    root = resolve_allowed_path(root_text, _config())
    status = _git(root, "status", "--porcelain=v1", "--branch")
    upstream = _git_text(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    return {
        "path": str(root),
        "is_git": True,
        "head": _git_text(root, "rev-parse", "HEAD"),
        "branch": _git_text(root, "branch", "--show-current") or "(detached)",
        "upstream": upstream,
        "dirty": any(line and not line.startswith("##") for line in status["stdout"].splitlines()),
        "status": status["stdout"],
        "worktrees": _git(root, "worktree", "list", "--porcelain")["stdout"],
        "submodules": _git(root, "submodule", "status", "--recursive")["stdout"],
    }


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_workspace(path: str) -> dict[str, Any]:
    try:
        target = resolve_allowed_path(path, _config())
        return ok(_repo_snapshot(target))
    except (OSError, ValueError, PermissionError) as exc:
        return fail("workspace_failed", str(exc))


def _discover_repos(base: Path, max_depth: int, max_repos: int) -> list[Path]:
    repos: list[Path] = []
    for current, dirs, files in os.walk(base):
        here = Path(current)
        depth = len(here.relative_to(base).parts)
        dirs[:] = [item for item in dirs if item not in {".venv", "node_modules", "build", "dist", "__pycache__"}]
        if ".git" in dirs or ".git" in files:
            repos.append(here)
            if ".git" in dirs:
                dirs.remove(".git")
            if len(repos) >= max_repos:
                break
        if depth >= max_depth:
            dirs[:] = []
    return repos


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_repo_matrix(root: str, max_depth: int = 4) -> dict[str, Any]:
    try:
        config = _config()
        base = resolve_allowed_path(root, config)
        depth = min(max(0, max_depth), int(config["max_repo_depth"]))
        repos = _discover_repos(base, depth, int(config["max_repos"]))
        return ok({"root": str(base), "repositories": [_repo_snapshot(item) for item in repos], "count": len(repos)})
    except (OSError, ValueError, PermissionError) as exc:
        return fail("repo_matrix_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_build_inputs(path: str) -> dict[str, Any]:
    try:
        target = resolve_allowed_path(path, _config())
        compile_db = target if target.is_file() else target / "compile_commands.json"
        if not compile_db.is_file():
            return fail("compile_db_missing", f"未找到 compile_commands.json: {compile_db}")
        rows = json.loads(compile_db.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return fail("invalid_compile_db", "compile_commands.json 顶层必须是数组")
        files = sorted({str(item.get("file")) for item in rows if isinstance(item, dict) and item.get("file")})
        directories = sorted({str(item.get("directory")) for item in rows if isinstance(item, dict) and item.get("directory")})
        digest, _, _ = sha256_file(compile_db)
        return ok({"path": str(compile_db), "sha256": digest, "entry_count": len(rows), "source_count": len(files), "directories": directories[:200], "sources_sample": files[:500], "truncated": len(files) > 500})
    except (OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        return fail("build_inputs_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_artifact(path: str, include_symbols: bool = False, include_strings: bool = False) -> dict[str, Any]:
    try:
        target = resolve_allowed_path(path, _config())
        if not target.is_file():
            return fail("not_file", f"不是普通文件: {target}")
        digest, _, _ = sha256_file(target)
        answer: dict[str, Any] = {"path": str(target), "size": target.stat().st_size, "sha256": digest}
        for label, command in (
            ("file", ["file", "-b", str(target)]),
            ("elf_header", ["readelf", "-h", str(target)]),
            ("elf_notes", ["readelf", "-n", str(target)]),
        ):
            try:
                result = run_command(command, timeout=10, max_chars=12000)
                answer[label] = result if result["returncode"] != 127 else {"unavailable": True}
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                answer[label] = {"unavailable": True, "reason": str(exc)}
        if include_symbols:
            try:
                answer["symbols"] = run_command(["nm", "-an", str(target)], timeout=15, max_chars=20000)
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                answer["symbols"] = {"unavailable": True, "reason": str(exc)}
        if include_strings:
            try:
                answer["strings"] = run_command(["strings", "-a", str(target)], timeout=15, max_chars=20000)
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                answer["strings"] = {"unavailable": True, "reason": str(exc)}
        return ok(answer)
    except (OSError, ValueError, PermissionError) as exc:
        return fail("artifact_failed", str(exc))


if __name__ == "__main__":
    mcp.run()
