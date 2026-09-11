#!/usr/bin/env python3
"""Install the isolated, content-addressed ICODE DOCX Python runtime.

The source tree intentionally contains the lock file and bootstrapper, not a
copy of packages from the invoking user's Python environment.  A normal ICODE
install invokes this bootstrapper; a later `/icode docx` invocation performs
the same idempotent check before using the per-user venv below
``~/.local/share/icode/runtime/docx``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


TOOL_VERSION = "1.0.0"
TOOL_DIR = Path(__file__).resolve().parent
LOCK_FILE = TOOL_DIR / "requirements.lock"

# Keep the package index policy in the bootstrapper instead of install.sh: the
# same runtime may be created later by `/icode docx`, without the public
# installer being involved.  Mirrors are only a recovery path for the default
# index and never override an administrator's or user's pip configuration.
DEFAULT_INDEX = ("pypi", "https://pypi.org/simple")
FALLBACK_INDEXES = (
    ("tsinghua", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("aliyun", "https://mirrors.aliyun.com/pypi/simple"),
)
NETWORK_FAILURE_MARKERS = (
    "connection timed out",
    "connecttimeout",
    "read timed out",
    "readtimeout",
    "network is unreachable",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "connection reset",
    "connection aborted",
    "remote end closed connection",
    "too many 5xx error responses",
    "status code 500",
    "status code 502",
    "status code 503",
    "status code 504",
)


@dataclass(frozen=True)
class PackageIndexAttempt:
    """One deterministic package-index installation attempt.

    ``url=None`` deliberately delegates to the user's existing pip
    configuration; it must not be replaced or logged because it can contain
    private package-host credentials.
    """

    label: str
    url: str | None
    strategy: str


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_runtime_root() -> Path:
    configured = os.environ.get("ICODE_DOCX_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "icode" / "runtime" / "docx"


def venv_python(runtime: Path) -> Path:
    return runtime / ("Scripts/python.exe" if os.name == "nt" else "venv/bin/python")


def locked_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "==" in line:
            name, version = line.split("==", 1)
            result[name.strip().lower().replace("-", "_")] = version.strip()
    return result


def runtime_metadata(runtime: Path) -> dict | None:
    try:
        return json.loads((runtime / "runtime_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def validate_runtime(runtime: Path, lock_hash: str) -> tuple[bool, str]:
    metadata = runtime_metadata(runtime)
    python = venv_python(runtime)
    if not metadata or metadata.get("lock_sha256") != lock_hash:
        return False, "runtime manifest 缺失或与当前 requirements.lock 不一致"
    if not python.is_file():
        return False, f"runtime Python 不存在: {python}"
    probe = subprocess.run(
        [str(python), "-c", "import json, docx, PIL; print(json.dumps({'python_docx': docx.__version__, 'pillow': PIL.__version__}))"],
        text=True,
        capture_output=True,
    )
    if probe.returncode:
        return False, probe.stderr.strip() or "python-docx/Pillow 导入失败"
    try:
        versions = json.loads(probe.stdout)
    except json.JSONDecodeError:
        return False, "runtime 版本探针输出无效"
    required = locked_versions()
    if versions.get("python_docx") != required.get("python_docx") or versions.get("pillow") != required.get("pillow"):
        return False, f"runtime 版本不符合 lock: current={versions}, required={required}"
    return True, json.dumps(versions, ensure_ascii=False, sort_keys=True)


def user_configured_package_index() -> bool:
    """Whether the caller explicitly controls pip's index resolution."""
    return any(
        os.environ.get(name)
        for name in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_NO_INDEX")
    )


def package_index_attempts() -> tuple[PackageIndexAttempt, ...]:
    """Return the safe index order without observing or changing pip config."""
    if user_configured_package_index():
        return (PackageIndexAttempt("user-configured", None, "user_configured"),)
    return tuple(
        PackageIndexAttempt(label, url, "default" if index == 0 else "fallback")
        for index, (label, url) in enumerate((DEFAULT_INDEX, *FALLBACK_INDEXES))
    )


def is_retryable_network_failure(output: str) -> bool:
    """Only change index after a transport/download failure, never a lock error."""
    normalized = output.lower()
    return any(marker in normalized for marker in NETWORK_FAILURE_MARKERS)


def install_locked_dependencies(python: Path) -> tuple[bool, str, str]:
    """Install the fixed lock file with a conservative, observable fallback.

    The returned strategy is safe to persist in the runtime manifest.  It is
    intentionally a label rather than the raw index URL to avoid recording a
    caller-provided private package URL or credentials.
    """
    bundled_wheels = TOOL_DIR / "wheels"
    attempts = package_index_attempts()
    last_output = ""
    for number, attempt in enumerate(attempts, start=1):
        command = [
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "--only-binary=:all:", "-r", str(LOCK_FILE),
        ]
        if bundled_wheels.is_dir():
            command[5:5] = ["--find-links", str(bundled_wheels)]
        if attempt.url:
            command.extend(["--index-url", attempt.url])
        print(
            f"INFO: DOCX runtime dependency install attempt {number}/{len(attempts)} "
            f"via {attempt.label}",
            file=sys.stderr,
        )
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            return True, attempt.strategy, ""
        last_output = (result.stderr.strip() or result.stdout.strip() or "pip 安装失败")
        can_fallback = number < len(attempts) and is_retryable_network_failure(last_output)
        if not can_fallback:
            return False, attempt.strategy, last_output
        next_attempt = attempts[number]
        print(
            f"WARNING: {attempt.label} 下载失败（网络/超时）；"
            f"ICODE 将按固定依赖通过可信镜像 {next_attempt.label} 重试。",
            file=sys.stderr,
        )
    return False, attempts[-1].strategy, last_output


def emit(runtime: Path, lock_hash: str, state: str, detail: str) -> None:
    print(json.dumps({
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "state": state,
        "runtime": str(runtime),
        "python": str(venv_python(runtime)),
        "lock_sha256": lock_hash,
        "detail": detail,
    }, ensure_ascii=False, sort_keys=True))


def install(runtime: Path, host_python: Path, lock_hash: str) -> int:
    runtime.parent.mkdir(parents=True, exist_ok=True)
    valid, detail = validate_runtime(runtime, lock_hash)
    if valid:
        emit(runtime, lock_hash, "ready", detail)
        return 0
    if runtime.exists():
        print(f"ERROR: existing DOCX runtime is invalid; remove only this exact runtime then retry: {runtime}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix=".icode-docx-stage-", dir=runtime.parent) as tmp:
        stage = Path(tmp) / "runtime"
        venv_command = [str(host_python), "-m", "venv"]
        # Kept exclusively for hermetic CI fixtures whose supplied Python is a
        # prebuilt ICODE distribution.  Normal installations never set it and
        # always install from requirements.lock into an isolated venv.
        if os.environ.get("ICODE_DOCX_RUNTIME_SYSTEM_SITE_PACKAGES") == "1":
            venv_command.append("--system-site-packages")
        venv_command.append(str(stage / "venv"))
        create = subprocess.run(venv_command, text=True, capture_output=True)
        if create.returncode:
            print(create.stderr.strip() or "ERROR: Python venv 创建失败", file=sys.stderr)
            return create.returncode or 1
        python = venv_python(stage)
        installed, package_index_strategy, install_error = install_locked_dependencies(python)
        if not installed:
            print(install_error or "ERROR: DOCX runtime 依赖安装失败", file=sys.stderr)
            return 1
        metadata = {
            "schema_version": 1,
            "tool_version": TOOL_VERSION,
            "lock_sha256": lock_hash,
            "host_python": str(host_python),
            "runtime_python": str(venv_python(runtime)),
            "system_site_packages": os.environ.get("ICODE_DOCX_RUNTIME_SYSTEM_SITE_PACKAGES") == "1",
            "package_index_strategy": package_index_strategy,
        }
        (stage / "runtime_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        valid, detail = validate_runtime(stage, lock_hash)
        if not valid:
            print(f"ERROR: DOCX runtime 安装后自检失败: {detail}", file=sys.stderr)
            return 1
        try:
            stage.replace(runtime)
        except OSError as exc:
            print(f"ERROR: DOCX runtime 原子提交失败: {exc}", file=sys.stderr)
            return 1
    emit(runtime, lock_hash, "installed", detail)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="bootstrap the ICODE DOCX runtime")
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--check", action="store_true", help="only inspect an existing runtime")
    args = parser.parse_args()
    if not LOCK_FILE.is_file():
        print(f"ERROR: requirements lock is missing: {LOCK_FILE}", file=sys.stderr)
        return 1
    if not args.python.is_file():
        print(f"ERROR: host Python 不存在: {args.python}", file=sys.stderr)
        return 1
    lock_hash = sha256_file(LOCK_FILE)
    runtime = args.runtime_root.expanduser().resolve() / lock_hash[:16]
    if args.check:
        valid, detail = validate_runtime(runtime, lock_hash)
        emit(runtime, lock_hash, "ready" if valid else "missing_or_invalid", detail)
        return 0 if valid else 3
    return install(runtime, args.python.resolve(), lock_hash)


if __name__ == "__main__":
    raise SystemExit(main())
