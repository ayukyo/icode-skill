"""ICODE 工作区与构建产物来源 MCP。所有工具均只读 Git/文件系统。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_mcp_common import READ_ONLY_ANNOTATIONS, describe, fail, load_config, ok, resolve_allowed_path, run_command, sha256_file  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-workspace"
TOOLS = [
    "describe_capabilities",
    "resolve_project",
    "inspect_project_profile",
    "inspect_workspace",
    "inspect_repo_matrix",
    "inspect_build_inputs",
    "inspect_artifact",
]
DEFAULTS = {
    "allowed_roots": ["$HOME"],
    "max_repo_depth": 5,
    "max_repos": 100,
    "max_scan_dirs": 5000,
    "max_command_chars": 30000,
    "max_profile_paths": 300000,
    "max_build_entrypoints": 32,
    "max_build_file_bytes": 262144,
    "large_repo_files": 50000,
    "huge_repo_files": 150000,
}
mcp = FastMCP(SERVER_NAME)

SKIP_DISCOVERY_DIRS = {
    ".venv", "node_modules", "build", "dist", "output", "__pycache__",
    ".cache", "target", "out",
}
PLATFORM_ROOT_SIGNALS = {
    "bootloader": {"boot", "u-boot", "uboot"},
    "kernel": {"kernel", "kernel-6.1", "linux"},
    "rootfs": {"buildroot", "yocto", "rootfs"},
    "rtos": {"rtos", "freertos", "rt-thread", "pm_rtos"},
    "trusted_runtime": {"optee", "tee"},
    "vendor_sdk": {"sdk", "external", "vendor", "hal"},
    "host_tools": {"tools", "toolchain"},
}
BUILD_ENTRYPOINT_NAMES = {
    "build.sh", "makefile", "cmakelists.txt", "meson.build",
    "build.gradle", "build.gradle.kts", "west.yml",
}
BUILD_RISK_PATTERNS = (
    ("destructive_cleanup", re.compile(r"\brm\s+-[^\n]*r|\bcleanall\b|\bmake\s+[^\n]*clean\b")),
    ("source_or_config_mutation", re.compile(r"\bsed\s+-i\b|\bcp\s+(?:-[^\s]+\s+)*|\bmv\s+|\bln\s+-s\b|(?:^|[;&|]\s*)mkdir\s+")),
    ("filesystem_or_image_mutation", re.compile(r"\bmkfs(?:\.|\s)|\bdd\s+if=|\bmount\s+|\bumount\s+")),
    ("device_or_flash_mutation", re.compile(r"\b(?:fastboot|rkdeveloptool|upgrade_tool|flashcp|dfu-util|nandwrite|eraseall)\b|\badb\s+(?:push|install|reboot|root)\b")),
    ("build_or_pack_write", re.compile(r"\b(?:make|cmake|ninja|bitbake)\b|\b(?:pack|image|firmware|ota)(?:[_-]|\b)")),
)


def _config() -> dict[str, Any]:
    return load_config("ICODE_WORKSPACE_CONFIG", SERVER_DIR, DEFAULTS)


def _git(path: Path, *args: str) -> dict[str, Any]:
    return run_command(["git", "-C", str(path), *args], timeout=15, max_chars=int(_config()["max_command_chars"]))


def _git_text(path: Path, *args: str) -> str | None:
    result = _git(path, *args)
    return result["stdout"].strip() if result["returncode"] == 0 else None


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return describe(
        SERVER_NAME,
        TOOLS,
        "工程根安全解析、超大仓静态画像、多 Git 根/worktree/构建输入与二进制来源观测",
    )


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


def _discover_project_roots(
    base: Path,
    max_depth: int,
    max_repos: int,
    max_scan_dirs: int,
) -> tuple[list[Path], int, bool]:
    """发现独立 Git 根；命中仓库后不继续遍历其巨型工作树。"""
    repos: list[Path] = []
    scanned_dirs = 0
    truncated = False
    for current, dirs, files in os.walk(base, followlinks=False):
        scanned_dirs += 1
        if scanned_dirs > max_scan_dirs:
            truncated = True
            break
        here = Path(current)
        depth = len(here.relative_to(base).parts)
        dirs[:] = sorted(item for item in dirs if item not in SKIP_DISCOVERY_DIRS)
        if ".git" in dirs or ".git" in files:
            repos.append(here)
            dirs[:] = []
            if len(repos) >= max_repos:
                truncated = True
                break
            continue
        if depth >= max_depth:
            dirs[:] = []
    return repos, scanned_dirs, truncated


def _resolve_project(target: Path, max_depth: int) -> dict[str, Any]:
    config = _config()
    root_text = _git_text(target, "rev-parse", "--show-toplevel")
    if root_text:
        root = resolve_allowed_path(root_text, config)
        return {
            "requested_path": str(target),
            "project_root": str(root),
            "mode": "current_git",
            "candidates": [str(root)],
            "scan": {"scanned_dirs": 0, "truncated": False},
        }

    repo_manifest = target / ".repo" / "manifest.xml"
    if repo_manifest.is_file():
        return {
            "requested_path": str(target),
            "project_root": str(target),
            "mode": "repo_manifest",
            "candidates": [],
            "scan": {"scanned_dirs": 0, "truncated": False},
        }

    depth = min(max(0, max_depth), int(config["max_repo_depth"]))
    repos, scanned_dirs, truncated = _discover_project_roots(
        target,
        depth,
        int(config["max_repos"]),
        int(config["max_scan_dirs"]),
    )
    checked = [resolve_allowed_path(item, config) for item in repos]
    if truncated:
        raise ValueError(
            f"工程根发现触达扫描预算，拒绝猜测: scanned_dirs={scanned_dirs}, candidates={[str(p) for p in checked]}"
        )
    if len(checked) > 1:
        raise ValueError(f"目录下存在多个 Git 根，必须显式选择: {[str(p) for p in checked]}")
    if len(checked) == 1:
        return {
            "requested_path": str(target),
            "project_root": str(checked[0]),
            "mode": "unique_nested_git",
            "candidates": [str(checked[0])],
            "scan": {"scanned_dirs": scanned_dirs, "truncated": False},
        }
    return {
        "requested_path": str(target),
        "project_root": str(target),
        "mode": "non_git",
        "candidates": [],
        "scan": {"scanned_dirs": scanned_dirs, "truncated": False},
    }


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def resolve_project(path: str, max_depth: int = 2) -> dict[str, Any]:
    """从当前目录或唯一嵌套仓解析工程根；歧义和预算耗尽均 fail-closed。"""
    try:
        target = resolve_allowed_path(path, _config())
        if not target.is_dir():
            return fail("project_resolution_failed", f"工程路径不是目录: {target}")
        return ok(_resolve_project(target, max_depth))
    except (OSError, ValueError, PermissionError) as exc:
        return fail("project_resolution_failed", str(exc))


def _tracked_paths(root: Path, max_paths: int) -> tuple[list[str], int, bool]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.decode("utf-8", errors="replace").strip())
    all_paths = [item.decode("utf-8", errors="replace") for item in completed.stdout.split(b"\0") if item]
    return all_paths[:max_paths], len(all_paths), len(all_paths) > max_paths


def _entrypoint_candidates(paths: list[str], limit: int) -> list[str]:
    candidates = []
    for value in paths:
        path = Path(value)
        if len(path.parts) > 2:
            continue
        if path.name.lower() in BUILD_ENTRYPOINT_NAMES or (
            path.suffix == ".sh" and "build" in path.name.lower()
        ):
            candidates.append(value)
    return sorted(candidates)[:limit]


def _inspect_entrypoint(root: Path, relative: str, max_bytes: int) -> dict[str, Any]:
    path = root / relative
    resolved_root = root.resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return {
            "path": relative,
            "readable": False,
            "reason": "symlink_escapes_project_root",
        }
    if not resolved.is_file():
        return {"path": relative, "readable": False, "reason": "not_regular_file"}
    raw = resolved.read_bytes()
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    evidence = []
    categories = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for category, pattern in BUILD_RISK_PATTERNS:
            if pattern.search(stripped):
                categories.add(category)
                if len(evidence) < 20:
                    evidence.append({"line": line_no, "category": category, "text": stripped[:240]})
    return {
        "path": relative,
        "resolved_path": str(resolved.relative_to(resolved_root)),
        "symlink": path.is_symlink(),
        "readable": True,
        "size": len(raw),
        "truncated": truncated,
        "risk_categories": sorted(categories),
        "evidence": evidence,
    }


def _project_profile(resolution: dict[str, Any]) -> dict[str, Any]:
    if resolution["mode"] not in {"current_git", "unique_nested_git"}:
        raise ValueError(f"静态工程画像要求 Git 根，当前模式: {resolution['mode']}")
    config = _config()
    root = Path(resolution["project_root"])
    paths, tracked_count, paths_truncated = _tracked_paths(root, int(config["max_profile_paths"]))
    top_level = Counter(Path(item).parts[0] for item in paths if Path(item).parts)
    extensions = Counter((Path(item).suffix.lower() or "[noext]") for item in paths)
    layer_hits: dict[str, set[str]] = {key: set() for key in PLATFORM_ROOT_SIGNALS}
    for value in paths:
        parts = Path(value).parts[:2]
        for index, part in enumerate(parts):
            lowered = part.lower()
            for layer, names in PLATFORM_ROOT_SIGNALS.items():
                if lowered in names:
                    layer_hits[layer].add("/".join(parts[: index + 1]))
    layers = {layer: sorted(hits) for layer, hits in layer_hits.items() if hits}
    entrypoints = [
        _inspect_entrypoint(root, item, int(config["max_build_file_bytes"]))
        for item in _entrypoint_candidates(paths, int(config["max_build_entrypoints"]))
    ]
    large_threshold = int(config["large_repo_files"])
    huge_threshold = int(config["huge_repo_files"])
    if tracked_count >= huge_threshold:
        scale = "huge"
        strategy = "tracked_paths_then_targeted_content"
        content_budget = 200
    elif tracked_count >= large_threshold:
        scale = "large"
        strategy = "targeted_content"
        content_budget = 500
    else:
        scale = "normal"
        strategy = "normal"
        content_budget = 2000
    bundled_toolchain = any(
        "toolchain" in {part.lower() for part in Path(value).parts[:4]}
        or any(part.lower().startswith("gcc-") for part in Path(value).parts[:4])
        for value in paths
    )
    firmware_candidates = sum(
        count for suffix, count in extensions.items()
        if suffix in {".bin", ".img", ".fw", ".elf", ".dtb", ".itb"}
    )
    return {
        "resolution": resolution,
        "repository": _repo_snapshot(root),
        "scale": {
            "class": scale,
            "tracked_files": tracked_count,
            "analyzed_paths": len(paths),
            "paths_truncated": paths_truncated,
        },
        "top_level_counts": dict(top_level.most_common(40)),
        "extension_counts": dict(extensions.most_common(30)),
        "platform_layer_hints": layers,
        "bundled_toolchain_hint": bundled_toolchain,
        "firmware_artifact_path_count": firmware_candidates,
        "build_entrypoints": entrypoints,
        "probe_policy": {
            "mode": "static_only",
            "execute_build_entrypoint_for_discovery": False,
            "reason": "构建入口可能在参数解析或 help 之前产生配置、源码、制品或设备副作用",
        },
        "scan_policy": {
            "strategy": strategy,
            "content_file_budget": content_budget,
            "use_git_tracked_paths": True,
            "full_tree_scan_allowed_by_default": scale == "normal",
            "platform_roots_are_hints_not_exclusions": True,
        },
    }


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_project_profile(path: str, max_depth: int = 2) -> dict[str, Any]:
    """静态生成仓库规模、平台层、构建入口风险和扫描策略；不运行仓内命令。"""
    try:
        target = resolve_allowed_path(path, _config())
        resolution = _resolve_project(target, max_depth)
        return ok(_project_profile(resolution))
    except (OSError, ValueError, PermissionError, subprocess.TimeoutExpired) as exc:
        return fail("project_profile_failed", str(exc))


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_repo_matrix(root: str, max_depth: int = 4) -> dict[str, Any]:
    try:
        config = _config()
        base = resolve_allowed_path(root, config)
        depth = min(max(0, max_depth), int(config["max_repo_depth"]))
        repos, scanned_dirs, truncated = _discover_project_roots(
            base,
            depth,
            int(config["max_repos"]),
            int(config["max_scan_dirs"]),
        )
        return ok({
            "root": str(base),
            "repositories": [_repo_snapshot(item) for item in repos],
            "count": len(repos),
            "scan": {"scanned_dirs": scanned_dirs, "truncated": truncated},
        })
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
