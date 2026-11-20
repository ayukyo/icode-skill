import copy
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("manual_dedup", ROOT / "tools/manual_dedup_evidence.py")
manual = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manual)


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def first():\n    return 1\n\nasync def second():\n    return 2\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = {
        "schema_version": 1, "ticket_id": "ticket-1", "attempt": "attempt-1",
        "review_scope": ["."], "unobserved": [],
        "sources": [{"path": "app.py", "sha256": digest, "catalog_method": "python_ast",
                     "functions": [{"name": "first", "line": 1, "family": "values"},
                                   {"name": "second", "line": 4, "family": "values"}]}],
        "families": [{"name": "values", "members": ["app.py:1:first", "app.py:4:second"],
                      "conclusion": "different", "reason": "返回值不同", "evidence": ["app.py:1", "app.py:4"]}],
    }
    return tmp_path, report, {"app.py": digest}


def validate(evidence, report=None, expected=None):
    root, original, files = evidence
    return manual.validate_report(report if report is not None else original, root, "ticket-1",
                                  "attempt-1", ["."], files if expected is None else expected)


def test_complete_real_python_family_and_async_function_pass(evidence):
    assert validate(evidence) == []


@pytest.mark.parametrize("change", ["missing_async", "missing_family", "wrong_members", "missing_evidence",
                                    "stale_hash", "unobserved", "wrong_attempt", "wrong_scope", "duplicate"])
def test_incomplete_or_unbound_evidence_does_not_pass(evidence, change):
    _, report, _ = evidence
    report = copy.deepcopy(report)
    if change == "missing_async":
        report["sources"][0]["functions"].pop()
    elif change == "missing_family":
        report["families"] = []
    elif change == "wrong_members":
        report["families"][0]["members"].pop()
    elif change == "missing_evidence":
        report["families"][0]["evidence"] = []
    elif change == "stale_hash":
        report["sources"][0]["sha256"] = "0" * 64
    elif change == "unobserved":
        report["unobserved"] = ["未审文件"]
    elif change == "wrong_attempt":
        report["attempt"] = "older"
    elif change == "wrong_scope":
        report["review_scope"] = ["other"]
    else:
        report["sources"].append(copy.deepcopy(report["sources"][0]))
    assert validate(evidence, report)


def test_unlisted_source_and_source_drift_rejected(evidence):
    root, report, expected = evidence
    assert validate(evidence, expected={**expected, "missing.py": "0" * 64})
    (root / "app.py").write_text("def changed(): pass\n", encoding="utf-8")
    assert validate(evidence, report)


def test_collect_sources_enumerates_scope_and_rejects_truncation_and_unknown(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("async def run(): pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    result = manual.collect_sources(tmp_path, ["src"])
    assert set(result) == {"src/app.py"}
    with pytest.raises(manual.EvidenceError, match="budget"):
        manual.collect_sources(tmp_path, ["src"], max_bytes=1)
    (tmp_path / "src" / "ui.ts").write_text("export function f() {}\n", encoding="utf-8")
    assert set(manual.collect_sources(tmp_path, ["src"])) == {"src/app.py", "src/ui.ts"}
    (tmp_path / "src" / "file.unknown").write_text("unknown", encoding="utf-8")
    with pytest.raises(manual.EvidenceError, match="unknown file type"):
        manual.collect_sources(tmp_path, ["src"])
    with pytest.raises(manual.EvidenceError, match="protected Git provider"):
        manual.collect_sources(tmp_path, ["src"], require_git_provider=True)


def test_manual_typescript_read_claim_is_bound_to_version_and_exact_excerpt(tmp_path):
    src = tmp_path / "ui.ts"
    src.write_text("export function render() {}\n", encoding="utf-8")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    report = {"schema_version": 1, "ticket_id": "t", "attempt": "a", "review_scope": ["."], "unobserved": [],
              "sources": [{"path": "ui.ts", "sha256": digest, "read_sha256": digest,
                           "language": "typescript", "catalog_method": "manual_read",
                           "completeness": "complete_within_scope", "catalog_reason": "通读函数声明",
                           "functions": [{"name": "render", "line": 1, "family": "ui",
                                          "excerpt": "export function render() {}"}]}],
              "families": [{"name": "ui", "members": ["ui.ts:1:render"], "conclusion": "single",
                            "reason": "该组仅一个函数", "evidence": ["ui.ts:1"]}]}
    def check(item):
        return manual.validate_report(item, tmp_path, "t", "a", ["."], {"ui.ts": digest})
    assert not check(report)
    for key, value in (("completeness", None), ("language", "javascript"), ("read_sha256", "0" * 64),
                       ("catalog_reason", "")):
        changed = copy.deepcopy(report)
        changed["sources"][0][key] = value
        assert check(changed)
    changed = copy.deepcopy(report)
    changed["sources"][0]["functions"][0]["excerpt"] = "different"
    assert check(changed)
    empty = copy.deepcopy(report)
    empty["sources"][0]["functions"] = []
    empty["families"] = []
    assert check(empty)
    empty["sources"][0]["empty_reason"] = "完整通读后该文件无函数声明"
    assert not check(empty)


def test_enumeration_and_validation_do_not_read_whole_source_unbounded(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    source = tmp_path / "large.py"
    source.write_bytes(b"x" * 2048)
    def forbid_read_bytes(self):
        raise AssertionError("source read_bytes is unbounded")
    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)
    with pytest.raises(manual.EvidenceError, match="budget"):
        manual.collect_sources(tmp_path, ["."], max_bytes=128)
    report = {"schema_version": 1, "ticket_id": "t", "attempt": "a", "review_scope": ["."],
              "unobserved": [], "sources": [{"path": "large.py", "sha256": "0" * 64,
              "catalog_method": "python_ast", "functions": []}], "families": []}
    assert manual.validate_report(report, tmp_path, "t", "a", ["."], {"large.py": "0" * 64}, max_bytes=128)


def test_git_output_limit_stops_and_reaps_child(tmp_path, monkeypatch):
    original = subprocess.Popen
    started = []
    def large_stdout(*args, **kwargs):
        process = original([sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()"],
                           **kwargs)
        started.append(process)
        return process
    monkeypatch.setattr(manual, "_GIT_MAX_OUTPUT_BYTES", 128, raising=False)
    monkeypatch.setattr(manual.subprocess, "Popen", large_stdout)
    with pytest.raises(manual.EvidenceError, match="truncated"):
        manual._git(tmp_path, ["ls-files"])
    assert started and started[0].poll() is not None


def test_git_timeout_stops_and_reaps_child(tmp_path, monkeypatch):
    original = subprocess.Popen
    started = []
    def sleeping(*args, **kwargs):
        process = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        started.append(process)
        return process
    monkeypatch.setattr(manual, "_GIT_TIMEOUT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(manual.subprocess, "Popen", sleeping)
    with pytest.raises(manual.EvidenceError, match="timed out"):
        manual._git(tmp_path, ["ls-files"])
    assert started and started[0].poll() is not None


@pytest.mark.parametrize("parent_waits", [False, True])
def test_git_timeout_when_descendant_inherits_stdout(tmp_path, parent_waits):
    # 子进程退出后孙进程仍持有 stdout；用隔离解释器限制旧实现的无限等待。
    script = r'''
import importlib.util, subprocess, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("manual", sys.argv[1])
manual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manual)
original = subprocess.Popen
def spawn(*args, **kwargs):
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)']); time.sleep(" + sys.argv[3] + ")"
    return original([sys.executable, "-c", code], **kwargs)
manual.subprocess.Popen = spawn
manual._GIT_TIMEOUT_SECONDS = 0.15
try:
    manual._git(Path(sys.argv[2]), ["ls-files"])
except manual.EvidenceError as exc:
    print(str(exc))
else:
    print("unexpected success")
'''
    result = subprocess.run([sys.executable, "-c", script, str(ROOT / "tools/manual_dedup_evidence.py"), str(tmp_path),
                             "4" if parent_waits else "0"],
                            capture_output=True, text=True, timeout=2, check=False)
    assert result.returncode == 0
    assert "timed out" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_git_success_does_not_kill_redirected_background_process(tmp_path, monkeypatch):
    import time
    marker = tmp_path / "background-finished"
    original = subprocess.Popen
    def spawn(*args, **kwargs):
        child = "import pathlib,sys,time; time.sleep(0.2); pathlib.Path(sys.argv[1]).touch()"
        parent = ("import subprocess,sys; subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], "
                  "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")
        return original([sys.executable, "-c", parent, child, str(marker)], **kwargs)
    monkeypatch.setattr(manual.subprocess, "Popen", spawn)
    assert manual._git(tmp_path, ["ls-files"]) == b""
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX escaped process behavior")
def test_git_escaped_stdout_holder_does_not_raw_close_fd(tmp_path):
    script = r'''
import importlib.util, os, subprocess, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("manual", sys.argv[1])
manual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manual)
original = subprocess.Popen
def spawn(*args, **kwargs):
    code = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], start_new_session=True)"
    process = original([sys.executable, "-c", code], **kwargs)
    def forbidden_close(fd):
        raise AssertionError("raw close while stream reader is active")
    manual.os.close = forbidden_close
    return process
manual.subprocess.Popen = spawn
manual._GIT_TIMEOUT_SECONDS = 0.1
try:
    manual._git(Path(sys.argv[2]), ["ls-files"])
except manual.EvidenceError as exc:
    print(str(exc))
else:
    print("unexpected success")
'''
    result = subprocess.run([sys.executable, "-c", script, str(ROOT / "tools/manual_dedup_evidence.py"), str(tmp_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
    assert result.returncode == 0
    assert "timed out" in result.stdout
