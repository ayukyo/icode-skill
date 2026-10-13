"""ICODE 本地 MCP 的共享安全与输出合同。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, TypedDict

from mcp.types import ToolAnnotations


class ToolResponse(TypedDict, total=False):
    answer: Any
    error_code: str
    error: str
    confidence: float | None
    truncation: dict[str, Any]


READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
REMOTE_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
MANAGED_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
REMOTE_MANAGED_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def ok(answer: Any, *, confidence: float | None = 1.0, **extra: Any) -> ToolResponse:
    result: ToolResponse = {"answer": answer, "confidence": confidence}
    result.update(extra)
    return result


def fail(code: str, message: str) -> ToolResponse:
    return {"error_code": code, "error": message, "confidence": None}


def load_config(env_name: str, server_dir: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    """读取配置并做浅合并；配置损坏时明确失败，不静默退回。"""
    raw = os.environ.get(env_name)
    path = expand_path(raw) if raw else server_dir / "config.json"
    config = dict(defaults)
    if not path.exists():
        return config
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置顶层必须是 JSON object: {path}")
    config.update(data)
    return config


def expand_path(value: str | Path, *, base_dir: Path | None = None, resolve_symlinks: bool = True) -> Path:
    raw = str(value)
    if not os.environ.get("HOME"):
        # Windows 通常仅设置 USERPROFILE；使用系统用户目录，不将 $HOME 当相对路径。
        def system_home(_match: re.Match[str] | None) -> str:
            try:
                if os.name == "posix" and os.environ.get("HOME") == "":
                    # POSIX 的 Path.home() 会将空 HOME 解析成 /，须直接查询系统用户目录。
                    import pwd
                    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
                else:
                    home = Path.home()
                if not home.is_absolute():
                    raise ValueError("系统用户目录不是绝对路径，请在配置中指定绝对路径")
                return str(home)
            except (KeyError, RuntimeError) as exc:
                raise ValueError("无法确定系统用户目录，请在配置中指定绝对路径") from exc

        raw = re.sub(r"\$(?:HOME\b|\{HOME\})|%HOME%", system_home, raw)
        if os.name == "posix" and os.environ.get("HOME") == "" and (raw == "~" or raw.startswith("~/")):
            raw = system_home(None) + raw[1:]
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    # 附件与凭据路径须保留符号链接本身，让既有写入/凭据检查继续拒绝它们。
    return path.resolve() if resolve_symlinks else Path(os.path.abspath(path))


def allowed_roots(config: dict[str, Any]) -> list[Path]:
    roots = config.get("allowed_roots", ["$HOME"])
    if not isinstance(roots, list) or not roots:
        raise ValueError("allowed_roots 必须是非空数组")
    expanded = [expand_path(item) for item in roots if isinstance(item, str) and item.strip()]
    if not expanded:
        raise ValueError("allowed_roots 必须至少包含一个非空字符串路径")
    return expanded


def resolve_allowed_path(
    value: str | Path,
    config: dict[str, Any],
    *,
    must_exist: bool = True,
) -> Path:
    path = expand_path(value)
    roots = allowed_roots(config)
    if not any(path == root or root in path.parents for root in roots):
        raise PermissionError(f"路径不在 allowed_roots 内: {path}")
    if must_exist and not path.exists():
        raise FileNotFoundError(str(path))
    return path


def sha256_file(path: Path, *, max_bytes: int | None = None) -> tuple[str, int, bool]:
    digest = hashlib.sha256()
    total = 0
    truncated = False
    with path.open("rb") as handle:
        while True:
            remaining = None if max_bytes is None else max_bytes - total
            if remaining is not None and remaining <= 0:
                truncated = handle.read(1) != b""
                break
            chunk = handle.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total, truncated


def read_text_lines(path: Path, *, start_line: int = 1, max_lines: int = 200, max_bytes: int = 262144) -> dict[str, Any]:
    if start_line < 1 or max_lines < 1 or max_lines > 5000:
        raise ValueError("start_line >= 1 且 1 <= max_lines <= 5000")
    data = path.read_bytes()[: max_bytes + 1]
    byte_truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[start_line - 1 : start_line - 1 + max_lines]
    return {
        "text": "\n".join(selected),
        "start_line": start_line,
        "end_line": start_line + len(selected) - 1 if selected else start_line - 1,
        "total_lines_in_window": len(lines),
        "truncated": byte_truncated or start_line - 1 + max_lines < len(lines),
    }


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 10,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """无 shell 执行固定参数命令，并对输出设界。"""
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    stdout = completed.stdout
    stderr = completed.stderr
    return {
        "returncode": completed.returncode,
        "stdout": stdout[:max_chars],
        "stderr": stderr[:max_chars],
        "truncated": len(stdout) > max_chars or len(stderr) > max_chars,
    }


def describe(server: str, tools: list[str], purpose: str, *, managed_writes: list[str] | None = None) -> ToolResponse:
    return ok(
        {
            "server": server,
            "purpose": purpose,
            "tools": tools,
            "api_key_required": False,
            "network": server == "icode-device-observe",
            "source_read_only": True,
            "managed_writes": managed_writes or [],
            "decision_authority": "none",
        }
    )
