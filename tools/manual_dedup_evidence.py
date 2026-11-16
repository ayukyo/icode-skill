"""Bounded validation of human dedup evidence; hashes never prove understanding.

Python catalogs can be checked with AST. Other recognized source languages
require explicit manual reading; their semantic completeness cannot be proven
mechanically. Unavailable Git and protected providers remain evidence gaps.
"""
from __future__ import annotations

import ast
import hashlib
import os
import signal
import re
import subprocess
import threading
import time
from pathlib import Path


class EvidenceError(ValueError):
    pass


_SOURCE_SUFFIXES = {
    ".py", ".pyi", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".go", ".rs",
    ".java", ".kt", ".kts", ".swift", ".cs", ".sh", ".bash", ".zsh", ".ps1",
    ".bat", ".cmd", ".html", ".htm", ".lua", ".rb", ".php", ".sql",
}
_NON_SOURCE_SUFFIXES = {
    ".md", ".txt", ".json", ".jsonl", ".toml", ".yaml", ".yml", ".xml", ".ini",
    ".cfg", ".lock", ".csv", ".tsv", ".svg", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".webp", ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".css", ".scss",
    ".map", ".log", ".rst", ".po", ".pot", ".gitignore", ".gitattributes",
}
_NON_SOURCE_NAMES = {"LICENSE", "NOTICE", "COPYING", "README", ".gitignore", ".gitattributes"}
_GIT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 15
_DEFAULT_SOURCE_MAX_BYTES = 1048576
_LANGUAGES = {
    ".c": "c", ".h": "c-header", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp-header", ".hh": "cpp-header", ".hxx": "cpp-header",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".vue": "vue", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift", ".cs": "csharp",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell", ".bat": "batch",
    ".cmd": "batch", ".html": "html", ".htm": "html", ".lua": "lua", ".rb": "ruby",
    ".php": "php", ".sql": "sql",
}


def _windows_job(process):
    """Contain a suspended process and its descendants before allowing it to run."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError("cannot create process job")
    if not kernel.AssignProcessToJobObject(job, process._handle):
        kernel.CloseHandle(job)
        raise OSError("cannot contain process in job")
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    if ntdll.NtResumeProcess(process._handle) != 0:
        kernel.TerminateJobObject(job, 1)
        kernel.CloseHandle(job)
        raise OSError("cannot resume contained process")
    return kernel, job


def _git(root: Path, args: list[str]) -> bytes:
    windows = os.name == "nt"
    options = {"creationflags": 4} if windows else {"start_new_session": True}
    try:
        process = subprocess.Popen(["git", "-C", str(root), *args], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options)
    except OSError as exc:
        raise EvidenceError("git unavailable") from exc
    job = None
    if windows:
        try:
            job = _windows_job(process)
        except OSError as exc:
            process.kill()
            process.wait(timeout=1)
            process.stdout.close()
            raise EvidenceError("git process containment unavailable") from exc
    output = bytearray()
    truncated = threading.Event()
    failed = threading.Event()

    def read_output():
        try:
            while True:
                chunk = process.stdout.read(min(65536, _GIT_MAX_OUTPUT_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _GIT_MAX_OUTPUT_BYTES:
                    truncated.set()
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    break
        except OSError:
            failed.set()
            try:
                process.kill()
            except ProcessLookupError:
                pass

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    timed_out = False
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
        reader.join(timeout=max(0, deadline - time.monotonic()))
        timed_out = reader.is_alive()
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # 仅在异常时清理整个受控进程组，成功时不干扰已重定向输出的后台任务。
        cleanup = timed_out or truncated.is_set() or failed.is_set() or process.returncode not in (0, None)
        if windows:
            kernel, handle = job
            if cleanup:
                kernel.TerminateJobObject(handle, 1)
            kernel.CloseHandle(handle)
        elif cleanup:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            timed_out = True
        reader.join(timeout=1)
        if not reader.is_alive():
            process.stdout.close()
        else:
            # 读取线程仍持有流；等待其自然结束，避免跨线程关闭或复用文件描述符。
            timed_out = True
    if timed_out:
        raise EvidenceError("git enumeration timed out")
    if truncated.is_set():
        raise EvidenceError("git enumeration truncated")
    if failed.is_set() or process.returncode:
        raise EvidenceError("git enumeration failed")
    return bytes(output)


def _read_source(path: Path, max_bytes: int) -> bytes:
    try:
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except OSError as exc:
        raise EvidenceError(f"source unreadable: {path.name}") from exc
    if len(data) > max_bytes:
        raise EvidenceError(f"source exceeds byte budget: {path.name}")
    return data


def _scopes(root: Path, review_scope) -> list[str]:
    if not isinstance(review_scope, list) or not review_scope:
        raise EvidenceError("review scope required")
    result = []
    for item in review_scope:
        if not isinstance(item, str) or not item.strip():
            raise EvidenceError("invalid review scope")
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            raise EvidenceError("review scope escapes workspace")
        candidate = root / path
        if candidate.is_symlink() or not candidate.exists() or not candidate.resolve().is_relative_to(root):
            raise EvidenceError("review scope unavailable or unsafe")
        normalized = path.as_posix()
        if normalized not in result:
            result.append(normalized)
    return result


def collect_sources(workspace, review_scope, *, max_files=2000, max_bytes=1048576,
                    require_git_provider=False):
    """Enumerate current tracked/unignored recognized source files in scope.

    Unknown file types and nested Git roots fail closed. The function never
    bypasses a required protected provider. Git-ignored files are not enumerated
    and must not be claimed as reviewed by this mechanism.
    """
    if require_git_provider:
        raise EvidenceError("protected Git provider unsupported")
    if type(max_files) is not int or max_files <= 0 or type(max_bytes) is not int or max_bytes <= 0:
        raise EvidenceError("invalid source budget")
    root = Path(workspace).resolve()
    scopes = _scopes(root, review_scope)
    actual_root = _git(root, ["rev-parse", "--show-toplevel"]).decode("utf-8", "strict").strip()
    if Path(actual_root).resolve() != root:
        raise EvidenceError("workspace is not the Git root")
    specs = [f":(literal){scope}" for scope in scopes]
    stage = _git(root, ["ls-files", "--stage", "-z", "--", *specs])
    for entry in stage.split(b"\0"):
        if entry.startswith(b"160000 "):
            raise EvidenceError("nested Git repository requires separate enumeration")
    data = _git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *specs])
    paths = set()
    for raw in data.split(b"\0"):
        if not raw:
            continue
        try:
            relative = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceError("non-UTF8 path") from exc
        if ".icode_output" in Path(relative).parts:
            continue
        paths.add(relative)
    if len(paths) > max_files:
        raise EvidenceError("source enumeration exceeds file budget")
    sources = {}
    for relative in sorted(paths):
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise EvidenceError("unsafe source path")
        if path.is_dir():
            raise EvidenceError("nested directory or Git repository cannot be enumerated")
        if not path.is_file():
            continue  # deleted files are absent from the final candidate
        suffix = path.suffix.lower()
        if suffix not in _SOURCE_SUFFIXES:
            if suffix in _NON_SOURCE_SUFFIXES or path.name in _NON_SOURCE_NAMES:
                continue
            raise EvidenceError(f"unknown file type: {relative}")
        data = _read_source(path, max_bytes)
        sources[relative] = hashlib.sha256(data).hexdigest()
    return sources


def validate_report(report, workspace, ticket_id, attempt, review_scope, expected_sources, *,
                    max_bytes=_DEFAULT_SOURCE_MAX_BYTES):
    """Check identity, hashes, AST catalogs, manual claims and family coverage.

    For non-Python files, manual_read claims cannot prove the reader actually
    read a file or discovered all functions; source-line references are checked.
    """
    errors = []
    root = Path(workspace).resolve()
    if type(max_bytes) is not int or max_bytes <= 0:
        return ["report:invalid_source_budget"]
    if not isinstance(report, dict):
        return ["report:not_object"]
    if type(report.get("schema_version")) is not int or report.get("schema_version") != 1 or not isinstance(ticket_id, str) or not ticket_id or not isinstance(attempt, str) or not attempt or report.get("ticket_id") != ticket_id or report.get("attempt") != attempt:
        errors.append("report:identity")
    if report.get("review_scope") != review_scope:
        errors.append("report:scope")
    if report.get("unobserved") != []:
        errors.append("report:unobserved")
    if not isinstance(expected_sources, dict):
        return errors + ["report:expected_sources"]
    sources = report.get("sources")
    if not isinstance(sources, list):
        return errors + ["report:sources"]
    entries = {}
    groups = {}
    valid_locations = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            errors.append("source:invalid")
            continue
        path = source["path"]
        if path in entries or path not in expected_sources:
            errors.append(f"source:unexpected_or_duplicate:{path}")
            continue
        entries[path] = source
        source_path = root / path
        if Path(path).is_absolute() or ".." in Path(path).parts or source_path.is_symlink() or not source_path.resolve().is_relative_to(root):
            errors.append(f"source:unsafe:{path}")
            continue
        try:
            data = _read_source(source_path, max_bytes)
            digest = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            errors.append(f"source:unreadable_or_invalid:{path}")
            continue
        if digest != expected_sources[path] or source.get("sha256") != digest:
            errors.append(f"source:drift:{path}")
        suffix = source_path.suffix.lower()
        if suffix not in _SOURCE_SUFFIXES:
            errors.append(f"source:unsupported:{path}")
            continue
        if suffix in {".py", ".pyi"}:
            if source.get("catalog_method") != "python_ast":
                errors.append(f"source:catalog_method:{path}")
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError:
                errors.append(f"source:unreadable_or_invalid:{path}")
                continue
            actual = {(n.lineno, n.name) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        else:
            if (source.get("catalog_method") != "manual_read" or source.get("language") != _LANGUAGES[suffix]
                    or source.get("read_sha256") != digest or source.get("completeness") != "complete_within_scope"
                    or not isinstance(source.get("catalog_reason"), str) or not source["catalog_reason"].strip()):
                errors.append(f"source:manual_read_missing:{path}")
            actual = None
        functions = source.get("functions")
        if not isinstance(functions, list):
            errors.append(f"source:functions:{path}")
            continue
        if actual is None and not functions and (not isinstance(source.get("empty_reason"), str) or not source["empty_reason"].strip()):
            errors.append(f"source:empty_catalog_reason:{path}")
        declared = []
        lines = text.splitlines()
        for fn in functions:
            if not isinstance(fn, dict) or type(fn.get("line")) is not int or not isinstance(fn.get("name"), str) or not isinstance(fn.get("family"), str) or not fn["family"].strip():
                errors.append(f"function:invalid:{path}")
                continue
            key = (fn["line"], fn["name"])
            declared.append(key)
            if not fn["name"].strip() or fn["line"] < 1 or fn["line"] > len(lines) or fn["name"] not in lines[fn["line"] - 1]:
                errors.append(f"function:source_line:{path}")
            elif actual is None and fn.get("excerpt") != lines[fn["line"] - 1]:
                errors.append(f"function:source_excerpt:{path}")
            member = f"{path}:{fn['line']}:{fn['name']}"
            groups.setdefault(fn["family"], []).append(member)
        if len(set(declared)) != len(declared) or (actual is not None and set(declared) != actual):
            errors.append(f"source:catalog_mismatch:{path}")
        valid_locations.update(f"{path}:{i}" for i in range(1, len(text.splitlines()) + 1))
    if set(entries) != set(expected_sources):
        errors.append("source:enumeration_mismatch")
    families = report.get("families")
    if not isinstance(families, list):
        return errors + ["family:invalid"]
    seen = set()
    for family in families:
        if not isinstance(family, dict) or not isinstance(family.get("name"), str):
            errors.append("family:invalid")
            continue
        name = family["name"]
        if name in seen or name not in groups:
            errors.append(f"family:unexpected_or_duplicate:{name}")
            continue
        seen.add(name)
        members = family.get("members")
        if not isinstance(members, list) or any(not isinstance(item, str) for item in members) or len(members) != len(set(members)) or set(members) != set(groups[name]):
            errors.append(f"family:members:{name}")
        conclusion = family.get("conclusion")
        if not isinstance(conclusion, str) or conclusion not in {"single", "different", "duplicates"} or not isinstance(family.get("reason"), str) or not family["reason"].strip():
            errors.append(f"family:conclusion:{name}")
        elif conclusion == "single" and len(groups[name]) != 1:
            errors.append(f"family:conclusion:{name}")
        elif conclusion in {"different", "duplicates"} and len(groups[name]) < 2:
            errors.append(f"family:conclusion:{name}")
        refs = family.get("evidence")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in valid_locations for ref in refs):
            errors.append(f"family:evidence:{name}")
        elif any(member.rsplit(":", 1)[0] not in refs for member in groups[name]):
            errors.append(f"family:evidence_incomplete:{name}")
    if seen != set(groups):
        errors.append("family:missing")
    return errors
