"""Exercise the real local CLI against private boards and source directories."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "icode_outsource.py"


def put_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def run_cli(board, *args, ok=True):
    result = subprocess.run(
        [sys.executable, "-B", str(TOOL), "--board", str(board), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if ok:
        assert result.returncode == 0, result.stderr + result.stdout
    else:
        assert result.returncode != 0
    output = result.stdout if result.returncode == 0 else result.stderr
    return json.loads(output)


@pytest.fixture
def case(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "source.c").write_text("old\n", encoding="utf-8")
    contract = {
        "schema_version": 1,
        "title": "修复示例",
        "goal": "将 source.c 的 old 改为 new",
        "requester": "initiator",
        "projects": [{"root": str(source), "read_paths": ["."],
                      "write_paths": ["source.c"]}],
        "acceptance": [{"id": "R1", "check": "内容是 new"}],
        "constraints": ["不提交、不部署"],
        "resources": [], "depends_on": [], "parent_ref": "parent-local",
    }
    return tmp_path / "board", source, put_json(tmp_path / "contract.json", contract)


def test_help_and_queries_do_not_create_board(tmp_path):
    board = tmp_path / "missing"
    result = subprocess.run([sys.executable, str(TOOL), "--help"],
                            capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    result = run_cli(board, "list", ok=False)
    assert result["ok"] is False
    assert not board.exists()


def test_publish_path_handoff_claim_and_no_duplicate_request(case):
    board, source, contract = case
    run_cli(board, "init")
    published = run_cli(board, "publish", "--contract", contract,
                        "--session", "origin-1", "--request-id", "publish-1")
    entry = Path(published["request_path"])
    assert entry.is_absolute() and entry.is_file()
    assert "修复示例" in entry.read_text(encoding="utf-8")
    replay = run_cli(board, "publish", "--contract", contract,
                     "--session", "origin-1", "--request-id", "publish-1")
    assert replay["ticket_id"] == published["ticket_id"]
    claim = run_cli(board, "claim", entry, "--actor", "worker", "--session", "worker-1",
                    "--request-id", "claim-1")
    assert claim["attempt"]
    error = run_cli(board, "claim", entry, "--actor", "other", "--session", "other-1",
                    "--request-id", "claim-other", ok=False)
    assert error["ok"] is False
    assert (source / "source.c").read_text() == "old\n"
    items = run_cli(board, "list", "--requester", "initiator", "--parent-ref", "parent-local")
    assert len(items["tickets"]) == 1


def test_complete_isolated_delivery_integration_and_acceptance(case, tmp_path):
    board, source, contract = case
    run_cli(board, "init")
    task = run_cli(board, "publish", "--contract", contract,
                   "--session", "origin", "--request-id", "pub")
    target = task["request_path"]
    claim = run_cli(board, "claim", target, "--actor", "worker", "--session", "worker",
                    "--request-id", "claim")
    worker_args = ["--actor", "worker", "--session", "worker", "--attempt", claim["attempt"]]
    prepared = run_cli(board, "prepare", target, *worker_args,
                       "--destination", tmp_path / "execution", "--request-id", "prepare")
    binding = prepared["bindings"][0]
    execution = Path(binding["execution_root"])
    (execution / "source.c").write_text("new\n", encoding="utf-8")
    assert (source / "source.c").read_text() == "old\n"
    evidence = tmp_path / "test.txt"
    evidence.write_text("checked isolated source: new\n", encoding="utf-8")
    proof = put_json(tmp_path / "proof.json", {
        "summary": "已修改并检查临时源码", "files": [str(evidence)],
        "checks": [{"id": "R1", "outcome": "pass", "evidence": str(evidence)}],
    })
    submitted = run_cli(board, "submit", target, *worker_args,
                        "--evidence", proof, "--request-id", "submit")
    assert submitted["state"] == "submitted"
    changes = run_cli(board, "watch", "--requester", "initiator", "--timeout", "0")
    assert any(row["ticket_id"] == task["ticket_id"] for row in changes["events"])
    integrated = run_cli(board, "integrate", target, "--actor", "initiator",
                         "--session", "origin", "--request-id", "integrate")
    assert integrated["state"] != "accepted"
    assert (source / "source.c").read_text() == "new\n"
    replay = run_cli(board, "integrate", target, "--actor", "initiator",
                     "--session", "origin", "--request-id", "integrate")
    assert replay["ticket_id"] == integrated["ticket_id"]
    accepted = run_cli(board, "accept", target, "--actor", "initiator", "--session", "origin",
                       "--checks", proof, "--request-id", "accept")
    assert accepted["state"] == "accepted"


def test_explicit_foreign_entry_never_resolves_local_latest(case, tmp_path):
    board, _, contract = case
    run_cli(board, "init")
    run_cli(board, "publish", "--contract", contract, "--session", "origin", "--request-id", "pub")
    foreign = tmp_path / "foreign" / "request.md"
    foreign.parent.mkdir()
    foreign.write_text("OUT-001", encoding="utf-8")
    assert run_cli(board, "show", foreign, ok=False)["ok"] is False


def test_absolute_entry_locates_board_without_user_remembering_id(case):
    board, _, contract = case
    run_cli(board, "init")
    published = run_cli(board, "publish", "--contract", contract,
                        "--session", "origin", "--request-id", "pub")
    result = subprocess.run(
        [sys.executable, "-B", str(TOOL), "show", published["request_path"]],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ticket_id"] == published["ticket_id"]


def test_same_publication_key_with_changed_contract_is_rejected(case):
    board, _, contract = case
    run_cli(board, "init")
    run_cli(board, "publish", "--contract", contract, "--session", "origin", "--request-id", "pub")
    altered = json.loads(contract.read_text(encoding="utf-8"))
    altered["goal"] = "a different task"
    put_json(contract, altered)
    assert run_cli(board, "publish", "--contract", contract, "--session", "origin",
                   "--request-id", "pub", ok=False)["ok"] is False


def test_watch_budget_rejects_nan_and_unbounded_wait(case):
    board, _, _ = case
    run_cli(board, "init")
    for timeout in ("nan", "inf", "-1", "61"):
        assert run_cli(board, "watch", "--requester", "initiator",
                       "--timeout", timeout, ok=False)["ok"] is False


def test_preparation_replay_does_not_change_destination(case, tmp_path):
    board, _, contract = case
    run_cli(board, "init")
    published = run_cli(board, "publish", "--contract", contract,
                        "--session", "origin", "--request-id", "pub")
    target = published["request_path"]
    claim = run_cli(board, "claim", target, "--actor", "worker", "--session", "worker",
                    "--request-id", "claim")
    identity = ["--actor", "worker", "--session", "worker", "--attempt", claim["attempt"]]
    first = run_cli(board, "prepare", target, *identity,
                    "--destination", tmp_path / "execution", "--request-id", "prepare")
    replay = run_cli(board, "prepare", target, *identity,
                     "--destination", tmp_path / "execution", "--request-id", "prepare")
    assert first["bindings"] == replay["bindings"]
    error = run_cli(board, "prepare", target, *identity,
                    "--destination", tmp_path / "other", "--request-id", "prepare", ok=False)
    assert error["ok"] is False
    assert not (tmp_path / "other").exists()


def test_changed_session_cannot_reuse_publication_receipt(case):
    board, _, contract = case
    run_cli(board, "init")
    run_cli(board, "publish", "--contract", contract, "--session", "origin", "--request-id", "pub")
    error = run_cli(board, "publish", "--contract", contract, "--session", "another-session",
                    "--request-id", "pub", ok=False)
    assert error["error_code"] == "idempotency_conflict"


def test_message_ack_and_requester_session_recovery(case, tmp_path):
    board, _, contract = case
    run_cli(board, "init")
    published = run_cli(board, "publish", "--contract", contract,
                        "--session", "origin", "--request-id", "pub")
    target = published["request_path"]
    claimed = run_cli(board, "claim", target, "--actor", "worker", "--session", "worker",
                      "--request-id", "claim")
    body = put_json(tmp_path / "message.json", {"body": "输入已读，正在处理。"})
    message = run_cli(board, "message", target, "--actor", "worker", "--session", "worker",
                      "--attempt", claimed["attempt"], "--payload", body, "--request-id", "message")
    received = run_cli(board, "inbox", "--actor", "initiator")
    assert any(row["event_id"] == message["event_id"] for row in received["messages"])
    run_cli(board, "ack", "--actor", "initiator", "--session", "origin",
            "--through", message["event_id"], "--request-id", "ack")
    assert run_cli(board, "inbox", "--actor", "initiator")["messages"] == []
    resume = put_json(tmp_path / "resume.json", {
        "previous_session": "origin", "stopped": True, "reason": "已确认原发起会话停止",
    })
    rebound = run_cli(board, "resume", target, "--actor", "initiator", "--session", "origin-new",
                      "--payload", resume, "--request-id", "resume")
    assert rebound["requester_session"] == "origin-new"
    error = run_cli(board, "message", target, "--actor", "initiator", "--session", "origin",
                    "--payload", body, "--request-id", "old-session", ok=False)
    assert error["ok"] is False


def test_forward_slash_project_root_is_normalized(case):
    board, source, contract = case
    value = json.loads(contract.read_text(encoding="utf-8"))
    value["projects"][0]["root"] = source.as_posix()
    put_json(contract, value)
    run_cli(board, "init")
    published = run_cli(board, "publish", "--contract", contract,
                        "--session", "origin", "--request-id", "pub")
    assert Path(published["contract"]["projects"][0]["root"]) == source
