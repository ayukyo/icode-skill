"""ICODE 本地 MCP 的共享安全与输出合同。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any, TypedDict

from mcp.types import ToolAnnotations


class ToolResponse(TypedDict, total=False):
    answer: Any
    error_code: str
    error: str
    confidence: float | None
    truncation: dict[str, Any]
    diagnostic: dict[str, Any]


class LocalPathPolicyError(PermissionError):
    """可辨识的访问策略拒绝；普通文件权限错误不属于此类型。"""

    def __init__(self, message: str, path: Path, policy: dict[str, Any], kind: str) -> None:
        super().__init__(message)
        self.diagnostic = {**policy, "kind": kind, "resolved_path": str(path)}


class AllowedRootsError(LocalPathPolicyError):
    def __init__(self, path: Path, policy: dict[str, Any]) -> None:
        super().__init__(f"路径不在 allowed_roots 内: {path}", path, policy, "mcp_allowed_roots")


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


def fail(code: str, message: str, *, exc: Exception | None = None, config_env: str | None = None) -> ToolResponse:
    result: ToolResponse = {"error_code": code, "error": message, "confidence": None}
    if isinstance(exc, LocalPathPolicyError):
        result["diagnostic"] = dict(exc.diagnostic)
        if config_env is not None:
            result["diagnostic"]["config_env"] = config_env
    return result


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


def _windows_platform() -> bool:
    # 独立可替换的平台探针，测试无需修改 os.name 并破坏 pathlib 的路径类型。
    return os.name == "nt"


def _windows_local_roots() -> list[Path]:
    """枚举本地逻辑盘根，不扫描磁盘；排除网络盘和不可用类型。"""
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetLogicalDrives.restype = ctypes.c_uint32
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint32
    mask = kernel.GetLogicalDrives()
    if not mask:
        raise OSError(ctypes.get_last_error(), "无法枚举 Windows 本地磁盘")
    roots = []
    for index in range(26):
        if mask & (1 << index):
            root = f"{chr(ord('A') + index)}:\\"
            # DRIVE_REMOVABLE=2, DRIVE_FIXED=3, DRIVE_RAMDISK=6。
            if kernel.GetDriveTypeW(root) in {2, 3, 6}:
                roots.append(Path(root))
    return roots


def _wsl_unc_root(value: str | Path) -> str | None:
    drive = PureWindowsPath(str(value)).drive
    if not drive.startswith("\\\\"):
        return None
    parts = drive[2:].split("\\")
    if len(parts) == 2 and parts[0].casefold() in {"wsl.localhost", "wsl$"} and parts[1]:
        return drive + "\\"
    return None


def allowed_roots(config: dict[str, Any], *, requested_path: Path | None = None) -> list[Path]:
    roots = config.get("allowed_roots")
    if roots is None:
        if not _windows_platform():
            return [expand_path("$HOME")]
        local = _windows_local_roots()
        if requested_path is not None:
            wsl_root = _wsl_unc_root(requested_path)
            if wsl_root is not None:
                local = [*local, Path(wsl_root)]
        if not local:
            raise ValueError("未发现可用的 Windows 本地磁盘，请显式配置 allowed_roots")
        return local
    if not isinstance(roots, list) or not roots:
        raise ValueError("allowed_roots 必须是非空数组")
    expanded = [expand_path(item) for item in roots if isinstance(item, str) and item.strip()]
    if not expanded:
        raise ValueError("allowed_roots 必须至少包含一个非空字符串路径")
    if len(expanded) != len(roots):
        raise ValueError("allowed_roots 的每一项都必须是非空路径字符串")
    return expanded


def access_policy(config: dict[str, Any], *, config_env: str | None = None, requested_path: Path | None = None) -> dict[str, Any]:
    default = config.get("allowed_roots") is None
    policy = {
        "configured_allowed_roots": config.get("allowed_roots"),
        "allowed_roots": [str(root) for root in allowed_roots(config, requested_path=requested_path)],
        "scope": "platform_default" if default else "explicit",
        "auto_expand_allowed_roots": False,
        "protect_sensitive_paths": default and _windows_platform(),
        "wsl_local_unc": "on_request" if default and _windows_platform() else "explicit_only",
        "remediation": "可显式配置 allowed_roots 为所需目录列表；此拒绝不表示工程不存在。",
    }
    if config_env is not None:
        policy["config_env"] = config_env
    return policy


def _sensitive_path(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    if any(part in {".ssh", ".aws", ".azure", ".gnupg"} for part in parts):
        return True
    name = path.name.casefold()
    if name in {".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc", ".env"}:
        return True
    if name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template", ".env.dist"}:
        return True
    protected_sequences = (
        (".codex", "auth.json"), (".claude", ".credentials.json"),
        (".config", "gh", "hosts.yml"), (".config", "gcloud"), (".docker", "config.json"),
        ("microsoft", "credentials"), ("microsoft", "protect"), ("microsoft", "vault"),
    )
    if any(parts[index:index + len(sequence)] == sequence
           for sequence in protected_sequences for index in range(len(parts))):
        return True
    chromium_profile = any(parts[index:index + 3] in {
        ("google", "chrome", "user data"), ("microsoft", "edge", "user data"),
    } for index in range(len(parts)))
    firefox_profile = any(parts[index:index + 2] == ("firefox", "profiles") for index in range(len(parts)))
    return (chromium_profile and name in {"login data", "cookies", "web data"}) or (
        firefox_profile and name in {"cookies.sqlite", "key4.db", "logins.json"}
    )


def resolve_allowed_path(
    value: str | Path,
    config: dict[str, Any],
    *,
    must_exist: bool = True,
) -> Path:
    if config.get("allowed_roots") is None and _windows_platform():
        # 普通远端 UNC 在解析/访问文件系统前拒绝；WSL 的标准本地视图按请求处理。
        lexical = expand_path(value, resolve_symlinks=False)
        if PureWindowsPath(str(lexical)).drive.startswith("\\\\") and _wsl_unc_root(lexical) is None:
            raise AllowedRootsError(lexical, access_policy(config, requested_path=lexical))
    path = expand_path(value)
    roots = allowed_roots(config, requested_path=path)
    if not any(path == root or root in path.parents for root in roots):
        raise AllowedRootsError(path, access_policy(config, requested_path=path))
    if config.get("allowed_roots") is None and _windows_platform() and _sensitive_path(path):
        raise LocalPathPolicyError(f"默认本地读取策略拒绝敏感凭据路径: {path}", path, access_policy(config, requested_path=path), "sensitive_path")
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
        # Fixed probes must not consume or wait on the MCP protocol pipe.
        stdin=subprocess.DEVNULL,
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


def describe(server: str, tools: list[str], purpose: str, *, managed_writes: list[str] | None = None, config: dict[str, Any] | None = None, config_env: str | None = None) -> ToolResponse:
    result = ok(
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
    if config is not None:
        result["answer"]["access_policy"] = access_policy(config, config_env=config_env)
    return result
