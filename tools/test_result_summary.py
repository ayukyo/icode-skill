"""Bounded, read-only JUnit evidence. A report never executes its contents.

Counts come from testcase elements; suite attributes only cross-check them.
Stored summaries authenticate the bytes observed then, not a live test run or
coverage beyond the report. No independent writer or public command is added.
"""
import hashlib
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import time
from xml.parsers import expat

DEFAULT_LIMITS = {"max_bytes": 8 * 1024 * 1024, "max_elements": 100000,
                  "max_depth": 64, "max_tests": 100000, "timeout_seconds": 5.0}
COUNT_FIELDS = ("total", "passed", "failed", "errors", "skipped")
INTERNAL_COUNTS = (*COUNT_FIELDS, "_runtime_skipped", "_disabled")
SUITES = {"testsuite", "testsuites"}
OUTCOMES = {"failure": "failed", "error": "errors", "skipped": "skipped"}


class ReportError(ValueError):
    pass


def report_outcome(counts):
    if counts["failed"] or counts["errors"]:
        return "fail"
    return "pass" if counts["passed"] else "inconclusive"


def validate_summary(summary, outcome=None):
    """Offline validation preserves history even after a report moves away."""
    required = {"version", "format", "report", "outcome", *COUNT_FIELDS}
    if not isinstance(summary, dict) or set(summary) != required or type(summary["version"]) is not int \
            or summary["version"] != 1 or summary["format"] != "junit":
        raise ReportError("invalid test_summary shape/version/format")
    if any(type(summary[key]) is not int or summary[key] < 0 for key in COUNT_FIELDS) \
            or summary["total"] != sum(summary[key] for key in COUNT_FIELDS[1:]):
        raise ReportError("test_summary count mismatch")
    if summary["outcome"] != report_outcome(summary):
        raise ReportError("test_summary outcome disagrees with case counts")
    report = summary["report"]
    if not isinstance(report, dict) or set(report) != {"path", "sha256", "bytes"} \
            or not isinstance(report["path"], str) or not report["path"] or "\0" in report["path"] \
            or not (PurePosixPath(report["path"]).is_absolute() or PureWindowsPath(report["path"]).is_absolute()) \
            or not isinstance(report["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", report["sha256"]) is None \
            or type(report["bytes"]) is not int or report["bytes"] <= 0:
        raise ReportError("test_summary requires original absolute report path/SHA256/byte count")
    if summary["outcome"] == "fail" and outcome is not None and outcome != "fail":
        raise ReportError("report failure cannot be downgraded or overridden")
    if summary["outcome"] == "inconclusive" and outcome == "pass":
        raise ReportError("zero/all-skipped report cannot be pass")


def report_path(raw, workspace):
    """Resolve an explicitly selected output file without requiring it to exist."""
    if not isinstance(raw, (str, Path)) or not str(raw) or "\0" in str(raw):
        raise ReportError("report path must name a local file")
    path = Path(raw)
    if ".." in path.parts:
        raise ReportError("report path traversal is prohibited")
    root = Path(workspace).resolve()
    if not path.is_absolute():
        path = root / path
    private = {".git", ".ssh", ".aws", ".gnupg", ".codex", ".claude", ".config"}
    if any(part.lower() in private for part in path.parts):
        raise ReportError("system/private paths cannot be test reports")
    for parent in (path, *path.parents):
        if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0)
                                   & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ReportError("report path symlinks/reparse points are prohibited")
    path = path.resolve()
    if path.parts[1:2] and path.parts[1].lower() in {
            "etc", "proc", "sys", "dev", "root", "boot", "usr", "bin", "sbin", "lib", "lib64", "windows"}:
        raise ReportError("system/private paths cannot be test reports")
    return path


def summarize_report(raw, workspace, *, limits=None):
    """Read one regular local report with bounded bytes, structure and time."""
    bounds = dict(DEFAULT_LIMITS)
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) - bounds.keys():
            raise ReportError("unknown report limit")
        bounds.update(limits)
    for key, value in bounds.items():
        if key == "timeout_seconds":
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        else:
            valid = type(value) is int and value > 0
        if not valid:
            raise ReportError("invalid report limit: " + key)
    deadline = time.monotonic() + bounds["timeout_seconds"]

    def check_time():
        if time.monotonic() >= deadline:
            raise ReportError("test report read/parse timeout exceeded")

    def forbidden(*args):
        raise ReportError("DTD/entity declarations are prohibited in test reports")

    stack, counts, elements = [], None, 0
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden

    def start(name, attrs):
        nonlocal elements
        check_time()
        name = name.rsplit("}", 1)[-1]
        elements += 1
        if elements > bounds["max_elements"]:
            raise ReportError("test report element limit exceeded")
        if len(stack) >= bounds["max_depth"]:
            raise ReportError("test report depth limit exceeded")
        parent = stack[-1]["name"] if stack else None
        if parent is None and name not in SUITES:
            raise ReportError("test report root must be testsuite/testsuites")
        if (name in SUITES and parent is not None and parent not in SUITES) \
                or (name == "testcase" and parent != "testsuite") \
                or (name in OUTCOMES and parent != "testcase"):
            raise ReportError("invalid JUnit report structure")
        declared = {}
        if name in SUITES:
            for attr, key in (("tests", "total"), ("failures", "failed"), ("errors", "errors"),
                              ("skipped", "_runtime_skipped"), ("disabled", "_disabled")):
                if attr not in attrs:
                    continue
                value = attrs[attr]
                if re.fullmatch(r"[0-9]{1,12}", value) is None:
                    raise ReportError("invalid declared test count")
                declared[key] = int(value)
                if declared[key] > bounds["max_tests"]:
                    raise ReportError("test count limit exceeded")
        if name in OUTCOMES:
            stack[-1]["outcomes"].add(OUTCOMES[name])
            if name == "skipped":
                stack[-1]["explicit_skip"] = True
        status, result = attrs.get("status"), attrs.get("result")
        if name == "testcase" and (status not in {None, "run", "notrun"}
                or result not in {None, "completed", "skipped", "suppressed"}
                or (status == "notrun" and result == "completed") or (status == "run" and result == "suppressed")):
            raise ReportError("unknown/conflicting testcase status/result")
        outcomes = {"skipped"} if name == "testcase" and (status == "notrun" or result in {"skipped", "suppressed"}) else set()
        stack.append({"name": name, "counts": dict.fromkeys(INTERNAL_COUNTS, 0), "declared": declared, "outcomes": outcomes,
                      "status": status, "result": result, "explicit_skip": False})

    def end(name):
        nonlocal counts
        check_time()
        frame = stack.pop()
        actual = frame["counts"]
        if frame["name"] == "testcase":
            if len(frame["outcomes"]) > 1:
                raise ReportError("conflicting testcase outcomes")
            if frame["result"] == "completed" and "skipped" in frame["outcomes"]:
                raise ReportError("conflicting testcase result/skipped marker")
            actual["total"] = 1
            actual[next(iter(frame["outcomes"]), "passed")] = 1
            # GoogleTest suppressed tests and runtime skips use distinct suite
            # counters; the public skipped count includes both unexecuted forms.
            actual["_disabled"] = int(frame["result"] == "suppressed" or
                                      (frame["status"] == "notrun" and not frame["explicit_skip"] and frame["result"] != "skipped"))
            actual["_runtime_skipped"] = actual["skipped"] - actual["_disabled"]
        for key, value in frame["declared"].items():
            # GoogleTest keeps its disabled-label count even when explicitly
            # running disabled tests. It bounds suppressed cases, not passes.
            matches = actual[key] <= value <= actual["total"] if key == "_disabled" else actual[key] == value
            if not matches:
                raise ReportError("declared report count mismatch: " + key)
        if stack:
            parent_counts = stack[-1]["counts"]
            for key in INTERNAL_COUNTS:
                parent_counts[key] += actual[key]
            if parent_counts["total"] > bounds["max_tests"]:
                raise ReportError("test count limit exceeded")
        else:
            counts = actual

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = lambda data: check_time()
    digest, size = hashlib.sha256(), 0
    try:
        check_time()
        path = report_path(raw, workspace)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ReportError("test report must be a regular file")
        if hasattr(os, "getuid") and before.st_uid != os.getuid():
            raise ReportError("test report must be owned by the current user")
        if before.st_size > bounds["max_bytes"]:
            raise ReportError("test report byte limit exceeded")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ReportError("test report replaced during read")
            while True:
                check_time()
                block = stream.read(min(65536, bounds["max_bytes"] - size + 1))
                size += len(block)
                if size > bounds["max_bytes"]:
                    raise ReportError("test report byte limit exceeded")
                digest.update(block)
                parser.Parse(block, not block)
                if not block:
                    break
        after = path.stat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or size != before.st_size:
            raise ReportError("test report changed during read")
        check_time()
    except ReportError:
        raise
    except (OSError, expat.ExpatError, LookupError, ValueError) as exc:
        # Expat's codec bridge may raise LookupError (unknown encoding) or
        # ValueError (unsupported multi-byte encoding), not only ExpatError.
        raise ReportError("unreadable/malformed test report: " + str(exc)) from exc
    summary = {"version": 1, "format": "junit", "report": {"path": str(path), "sha256": digest.hexdigest(), "bytes": size},
               **{key: counts[key] for key in COUNT_FIELDS}, "outcome": report_outcome(counts)}
    validate_summary(summary)
    return summary
