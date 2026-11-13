"""Run the documented readers against actual inline and encoded ticket files."""
import copy
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("doc_contract_control", ROOT / "tools/icode_control.py")
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


def blocks(name):
    return re.findall(r"^```[^\n]*\n(.*?)^```", (ROOT / name).read_text(encoding="utf-8"), re.M | re.S)


CASES = [
    ("status_word", "SKILL.md", "print('status:'"),
    ("code_gate", "SKILL.md", "d.get('status')=='plan_finalized'"),
    ("plan_limit", "steps/01_plan.md", "refs={int(r['redline_no'])"),
    ("log_limit", "steps/log.md", "refs={int(r['redline_no'])"),
    ("runtime", "steps/log.md", "runs=m.get('runtime_code_baselines'"),
    ("validate", "steps/status.md", "json_cnt=len(glob.glob"),
    ("backup", "steps/bak.md", "by_id ="),
    ("active", "references/dir_and_metadata.md", 'p["control_root"]'),
    ("reuse", "references/dir_and_metadata.md", "STATUS=$("),
]


def python_fragment(name, marker):
    block = next(body for body in blocks(name) if marker in body)
    if "<<'PY'" in block:
        return block.split("<<'PY'", 1)[1].split("\n", 1)[1].split("\nPY", 1)[0]
    command = block[block.index("python3 -c "):]
    # Parse the shell quoting too: removing quotes by regex would conceal broken examples.
    return shlex.split(command)[2]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def snapshot(directory):
    candidate = ctl.candidate_module()
    value = {
        "version": 1,
        "identity": {"common_git_dir": str(directory / ".git"),
                     "checkout_realpath": str(directory), "object_format": "sha1",
                     "common_git_device": 0, "common_git_inode": 0,
                     "checkout_device": 0, "checkout_inode": 0},
        "base": "1" * 40, "head": "1" * 40, "head_tree": "2" * 40,
        "code_files": ["a.py"], "scope_contract": None, "inspection_scope": {},
        "excluded_side_effects": [],
        "control_artifact_exclusions": list(candidate.CONTROL_DIRS),
        "manifest": [{"path": "a.py", "kind": "file", "mode": "100644",
                      "sha256": "3" * 64, "size": 1, "git_oid": "4" * 40}],
        "index_manifest": [{"path": "a.py", "mode": "100644", "oid": "4" * 40}],
        "untracked_manifest": [],
    }
    keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
            "excluded_side_effects", "control_artifact_exclusions", "manifest")
    value["content_id"] = candidate._digest({key: value[key] for key in keys})
    value["candidate_id"] = candidate._digest(value)
    assert candidate.snapshot_identity_ok(value)
    return value


def make_ticket(directory, mode, status="plan_finalized"):
    directory.mkdir(parents=True)
    meta = {"schema_version": 3, "ticket_id": "doc-1", "status": status,
            "artifact_root": str(directory), "code_files": ["a.py"],
            "limit_refs": [{"redline_no": 7}], "runtime_code_baselines": [],
            "candidate_tracking": {"current": snapshot(directory)}}
    event = {"schema_version": 1, "event_id": "11111111-1111-4111-8111-111111111111",
             "ticket_id": "doc-1", "timestamp": "2026-11-14T00:00:00",
             "actor": "icode", "event_type": "external_note",
             "payload": {"snapshot": copy.deepcopy(meta["candidate_tracking"]["current"])},
             "previous_event_hash": ctl.GENESIS_HASH}
    event["event_hash"] = ctl.canonical_event_hash(event)
    storage = ctl.storage_module()
    raw_event, registry = storage.encode_event(event, committed_registry=storage.Registry())
    raw_meta = storage.encode_document(meta, kind="metadata", committed_registry=registry)
    assert isinstance(raw_meta["value"]["candidate_tracking"]["current"]["manifest"], dict)
    if mode == "bad_ref":
        raw_meta["value"]["candidate_tracking"]["current"]["manifest"]["$icode_manifest_ref"]["sha256"] = "f" * 64
    dump(directory / ctl.METADATA_NAME, meta if mode in {"inline", "pending_inline"} else raw_meta)
    (directory / ctl.EVENTS_NAME).write_text(json.dumps(raw_event) + "\n", encoding="utf-8")
    if mode in {"pending", "pending_inline"}:
        dump(directory / ctl.TXN_NAME, {"untrusted_pending": True})
    for name in ["00_init.md", "01_plan.md", "02_review.md", "03_plan_final.md",
                 "04_code_review_fix.md", "05_deepcheck.md", "06_audit.md"]:
        (directory / name).write_text("红线 7\n", encoding="utf-8")
    (directory / "limit_checkpoint.md").write_text(
        "阶段块：plan前置硬基线\n阶段块：log前置检查点\n", encoding="utf-8")
    (directory / "log_analysis.md").write_text(
        "## 2. 现场\n现场运行版本未确定\nlimit 红线对照\n红线 7\n## 6. 建议\n红线 7\n", encoding="utf-8")


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("case,name,marker", CASES, ids=[row[0] for row in CASES])
@pytest.mark.parametrize("mode", ["inline", "v2", "bad_ref", "pending", "pending_inline"])
def test_documented_ticket_reader(case, name, marker, mode, tmp_path):
    ticket = tmp_path / "project/.icode_output/.icode_output_1"
    make_ticket(ticket, mode)
    home = tmp_path / "home"
    index = home / ".claude/icode_data/index.json"
    dump(index, {"tickets": [{"ticket_id": "doc-1", "backup_path": None}]})
    pointer = tmp_path / "pointer.json"
    dump(pointer, {"ticket_id": "doc-1", "control_root": str(ticket)})
    code = python_fragment(name, marker).replace("{ICODE_OUT_DIR}", ticket.as_posix())
    code = code.replace("{ICODE_SKILL_ROOT}", ROOT.as_posix())
    args = [str(ticket.parent)] if case == "backup" else [str(pointer)] if case == "active" else [str(ticket)] if case == "reuse" else ["1"]
    before = hashes(ticket)
    index_before = index.read_bytes()
    # A business project's tools module must never shadow the installed skill's reader.
    poison = tmp_path / "project/tools"
    poison.mkdir()
    (poison / "icode_control.py").write_text("raise RuntimeError('WRONG PROJECT MODULE')", encoding="utf-8")
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="1",
               PYTHONPATH=str(poison))
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, *args],
                            cwd=ticket.parents[1], env=env, capture_output=True, text=True, encoding="utf-8")
    assert "WRONG PROJECT MODULE" not in result.stderr
    expected_ok = mode in {"inline", "v2"}
    assert (result.returncode == 0) == expected_ok, result.stdout + result.stderr
    if expected_ok:
        output = {"status_word": "status:", "plan_limit": "limit_checkpoint:",
                  "log_limit": "limit_checkpoint:", "runtime": "unknown",
                  "validate": "产物:", "backup": "更新 backup_path:",
                  "active": str(ticket), "reuse": "plan_finalized"}.get(case)
        if output:
            assert output in result.stdout, "Shell quoting skipped the reader: " + result.stdout
    if expected_ok and case == "backup":
        assert json.loads(index.read_text(encoding="utf-8"))["tickets"][0]["backup_path"] == str(ticket)
    else:
        assert index.read_bytes() == index_before
    assert hashes(ticket) == before, "A documented readonly reader changed ticket files"


@pytest.mark.skipif(os.name == "nt", reason="Run the complete Bash examples on the Linux validation host")
@pytest.mark.parametrize("mode", ["inline", "v2", "bad_ref", "pending", "pending_inline"])
@pytest.mark.parametrize("case", ["code_gate", "reuse"])
def test_complete_shell_example_fails_closed(case, mode, tmp_path):
    workspace = tmp_path / "project"
    ticket = workspace / ".icode_output/.icode_output_1"
    make_ticket(ticket, mode, status="log_done" if case == "reuse" else "plan_finalized")
    if case == "reuse":
        (ticket / "01_plan.md").unlink()
    name = "SKILL.md" if case == "code_gate" else "references/dir_and_metadata.md"
    marker = "d.get('status')=='plan_finalized'" if case == "code_gate" else "STATUS=$("
    command = next(body for body in blocks(name) if marker in body)
    command = command.replace("{ICODE_SKILL_ROOT}", ROOT.as_posix()).replace("{ICODE_OUT_DIR}", ticket.as_posix())
    command += "\nprintf 'AFTER_SENTINEL reuse=%s\\n' \"${REUSE:-unused}\"\n"
    before = hashes(ticket)
    result = subprocess.run(["bash", "-c", command], cwd=workspace,
                            capture_output=True, text=True, encoding="utf-8")
    expected_ok = mode in {"inline", "v2"}
    assert (result.returncode == 0) == expected_ok, result.stdout + result.stderr
    assert ("AFTER_SENTINEL" in result.stdout) == expected_ok
    if expected_ok and case == "reuse":
        assert "reuse=2" in result.stdout
        assert not (ticket.parent / ".icode_output_2").exists()
    assert hashes(ticket) == before


def test_no_raw_ticket_readers_in_executable_docs():
    violations = []
    for path in [ROOT / "SKILL.md", *sorted((ROOT / "steps").glob("*.md")),
                 *sorted((ROOT / "references").glob("*.md"))]:
        for body in blocks(path.relative_to(ROOT)):
            for line in body.splitlines():
                if ((".ico_metadata.json" in line and re.search(r"json\.load|grep|\bjq\b|\bsed\b", line))
                        or re.search(r"json\.load\(f\)\.get\([\"']ticket_id", line)):
                    violations.append(f"{path.relative_to(ROOT)}: {line}")
    assert not violations, "Raw ticket readers bypass storage/pending checks:\n" + "\n".join(violations)


def test_debug_example_uses_the_same_readonly_protocol():
    body = next(body for body in blocks("references/debug_mode.md") if "metadata =" in body)
    tree = ast.parse(body)
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(isinstance(func, ast.Attribute) and func.attr == "read_metadata_readonly" for func in calls)
    assert not any(isinstance(func, ast.Name) and func.id == "read_metadata" for func in calls)


@pytest.mark.parametrize("mode", ["inline", "v2", "bad_ref", "pending", "pending_inline"])
def test_audit_inline_reader_example(mode, tmp_path):
    ticket = tmp_path / "ticket"
    make_ticket(ticket, mode)
    line = next(line for line in (ROOT / "steps/06_audit.md").read_text(encoding="utf-8").splitlines()
                if line.startswith("**自动化要求**"))
    command = re.search(r"`(python3 -c .*?)`", line).group(1)
    code = shlex.split(command)[2].replace("{ICODE_SKILL_ROOT}", ROOT.as_posix())
    code += "; assert d['ticket_id'] == 'doc-1'"
    before = hashes(ticket)
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(ticket / ctl.METADATA_NAME)],
                            capture_output=True, text=True, encoding="utf-8")
    assert (result.returncode == 0) == (mode in {"inline", "v2"}), result.stdout + result.stderr
    assert hashes(ticket) == before
