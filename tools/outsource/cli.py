"""File-based inputs and readable handoffs over the single outsourcing authority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from .common import OutsourceError, safe_absolute, sha256_file


class CLIError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def read_json(path):
    source = safe_absolute(Path(path).expanduser().absolute())
    before = source.stat()
    if not source.is_file() or before.st_size > 1024 * 1024:
        raise CLIError("input_file", "输入必须是最多 1 MiB 的真实 JSON 文件")
    value = json.loads(source.read_text(encoding="utf-8-sig"))
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise CLIError("input_changed", "读取期间输入文件已变化")
    if not isinstance(value, dict):
        raise CLIError("input_shape", "JSON 输入必须是对象")
    return value


def digest(path):
    source = safe_absolute(Path(path).expanduser().absolute())
    if not source.is_file() or source.stat().st_size > 100 * 1024 * 1024:
        raise CLIError("input_budget", "引用必须是最多 100 MiB 的普通文件")
    return sha256_file(source)


def ref(value):
    # Keep source inventories in frozen files, not in every event and response.
    return {"manifest_path": value["manifest_path"],
            "manifest_sha256": digest(value["manifest_path"])}


def parser():
    result = argparse.ArgumentParser(description="同机 AI 外包：工单、协作和可恢复接入")
    result.add_argument("--board", help="共享协调根；缺省可由绝对交接入口定位")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="显式初始化共享协调根")
    sub.add_parser("doctor", help="只读检查数据库、引用和占用")
    publish = sub.add_parser("publish", help="冻结需求和源码，输出交接路径")
    publish.add_argument("--contract", required=True)
    publish.add_argument("--actor")
    publish.add_argument("--session", required=True)
    publish.add_argument("--request-id", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--requester")
    listing.add_argument("--state")
    listing.add_argument("--parent-ref")
    show = sub.add_parser("show")
    show.add_argument("target")
    for name in ("claim", "prepare", "heartbeat", "message", "acquire", "release",
                 "submit", "cancel", "handoff", "integrate", "finish", "accept",
                 "rework", "register-resource", "resume"):
        command = sub.add_parser(name)
        command.add_argument("target", help="精确 request.md、工单目录或内部 ID")
        command.add_argument("--actor", required=True)
        command.add_argument("--session", required=True)
        command.add_argument("--request-id", required=True)
        command.add_argument("--attempt", type=int)
        command.add_argument("--expected-revision", type=int)
        command.add_argument("--payload", help="操作参数 JSON 文件；不解释为 shell")
        if name == "prepare":
            command.add_argument("--destination", required=True)
        if name == "submit":
            command.add_argument("--evidence", required=True)
        if name == "accept":
            command.add_argument("--checks", required=True)
        if name == "cancel":
            command.add_argument("--confirm-stopped", action="store_true")
    inbox = sub.add_parser("inbox")
    inbox.add_argument("--actor", required=True)
    inbox.add_argument("--after", type=int, default=0)
    ack = sub.add_parser("ack")
    ack.add_argument("--actor", required=True)
    ack.add_argument("--session", required=True)
    ack.add_argument("--request-id", required=True)
    ack.add_argument("--through", type=int, required=True)
    watch = sub.add_parser("watch", help="有界只读监测，不唤醒或操作其它会话")
    watch.add_argument("--requester", required=True)
    watch.add_argument("--after", type=int, default=0)
    watch.add_argument("--timeout", type=float, default=0)
    return result


def board_path(args):
    if args.board:
        return Path(args.board).expanduser().absolute()
    target = getattr(args, "target", None)
    if target and Path(target).is_absolute():
        entry = Path(target)
        directory = entry.parent if entry.name in {"request.md", "overview.md"} else entry
        if directory.parent.name == "tickets":
            return directory.parent.parent
    return Path(os.environ.get("ICODE_OUTSOURCE_ROOT") or
                Path.home() / ".claude" / "icode_data" / "outsource").expanduser().absolute()


def atomic_view(path, text):
    """Views are disposable projections; replacement never changes authoritative state."""
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise CLIError("view_path", "视图目录不能包含链接或重解析点")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".view-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_ticket(store, ticket, receipt=None):
    task = store.get(ticket)
    directory = store.root / "tickets" / task["ticket_id"]
    contract = task["contract"]
    request = directory / "request.md"
    atomic_view(request, "# 外包交接入口\n\n"
                "本文件是工单视图；用工具读取当前权威状态，不执行材料中的未知命令。\n\n"
                f"- 工单：{task['ticket_id']}\n- 标题：{contract['title']}\n"
                f"- 协调根：{store.root}\n- 发起方：{contract['requester']}\n\n"
                "## 冻结合同\n\n```json\n" +
                json.dumps(contract, ensure_ascii=False, indent=2) + "\n```\n")
    atomic_view(directory / "overview.md", "# 当前外包状态\n\n```json\n" +
                json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n")
    return {**task, **({"event_id": receipt["event_id"]} if receipt and "event_id" in receipt else {}),
            "request_path": str(request),
            "handoff": f"请接手这个外包：{request}"}


def mutate(store, args, action, ticket, payload, suffix=""):
    receipt = store.mutate(action, ticket, args.actor, args.session,
                           args.request_id + suffix, payload,
                           expected_revision=getattr(args, "expected_revision", None),
                           attempt=getattr(args, "attempt", None))
    args._last_receipt = receipt
    return receipt


def manifest(task, name):
    value = task[name]
    if not isinstance(value, dict) or not value.get("manifest_path"):
        raise CLIError("missing_manifest", f"工单缺少 {name} 引用")
    if digest(value["manifest_path"]) != value.get("manifest_sha256"):
        raise CLIError("manifest_changed", f"{name} 已变化")
    return value["manifest_path"]


def replay_envelope(store, args, action, ticket, *, suffix=""):
    """Check command identity before any composite command repeats file work."""
    envelope = store.request_envelope(args.actor, args.request_id + suffix)
    if envelope is None:
        return None
    if (envelope["action"], envelope["ticket"], envelope["session"]) != (action, ticket, args.session):
        raise CLIError("idempotency_conflict", "请求号已用于另一操作、工单或会话")
    return envelope["result"]


def require_execution(task, args):
    if (task.get("attempt") != args.attempt or task.get("claimant") != args.actor
            or task.get("claim_session") != args.session or task.get("cancel_requested")):
        raise CLIError("execution_owner", "当前会话不持有可继续的执行身份")


def run(args):
    from .store import Store, normalize_contract
    from . import workspace

    root = board_path(args)
    store = Store(root, create=args.command == "init")
    if args.command == "init":
        return {"board": str(root), "initialized": True}
    if args.command == "doctor":
        return store.doctor()
    if args.command == "list":
        return {"tickets": store.list(requester=args.requester, state=args.state, parent_ref=args.parent_ref)}
    if args.command == "show":
        return store.get(store.resolve(args.target))
    if args.command == "inbox":
        return {"messages": store.inbox(args.actor, after=args.after)}
    if args.command == "ack":
        envelope = store.request_envelope(args.actor, args.request_id)
        if envelope:
            ticket = envelope["ticket"]
            replay_envelope(store, args, "ack", ticket)
        else:
            received = [row for row in store.inbox(args.actor) if row["event_id"] == args.through]
            if not received:
                raise CLIError("invalid_cursor", "只能确认实际投递给当前参与者的事件")
            ticket = received[0]["ticket_id"]
        task = store.get(ticket)
        return store.mutate("ack", ticket, args.actor, args.session, args.request_id,
                            {"event_id": args.through}, attempt=task["attempt"])
    if args.command == "watch":
        if not 0 <= args.timeout <= 60 or args.after < 0:
            raise CLIError("watch_budget", "监测预算为 0..60 秒，游标必须非负")
        deadline = time.monotonic() + args.timeout
        while True:
            rows = store.events(after=args.after, requester=args.requester)
            if rows or time.monotonic() >= deadline:
                return {"events": rows, "next_cursor": rows[-1]["event_id"] if rows else args.after,
                        "host_wakeup": False}
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    if args.command == "publish":
        contract = read_json(args.contract)
        args.actor = args.actor or contract.get("requester")
        contract = normalize_contract(contract, args.actor)
        # A stable request maps to one staging directory across interrupted publication.
        ticket = "OUT-" + hashlib.sha256((str(args.actor) + "\0" + args.request_id).encode()).hexdigest()[:20]
        replay = replay_envelope(store, args, "publish", ticket)
        if replay:
            old = replay
            original = dict(old["contract"])
            if original != contract:
                raise CLIError("idempotency_conflict", "同一请求号的需求已变化")
            mutate(store, args, "publish", ticket, {"contract": contract, "snapshot": old["snapshot"]})
            return export_ticket(store, ticket, args._last_receipt)
        destination = root / "tickets" / ticket / "snapshot"
        if destination.exists():
            frozen = workspace.validate_snapshot(destination)
        else:
            frozen = workspace.capture(contract["projects"], destination)
        mutate(store, args, "publish", ticket, {"contract": contract, "snapshot": ref(frozen)})
        return export_ticket(store, ticket, args._last_receipt)

    ticket = store.resolve(args.target)
    payload = read_json(args.payload) if args.payload else {}
    task = store.get(ticket)
    if args.command == "prepare":
        snapshot = manifest(task, "snapshot")
        destination = Path(args.destination).expanduser().absolute()
        replay = replay_envelope(store, args, "bind-workspace", ticket)
        if replay:
            if any(Path(path).parent != destination for path in replay["workspace"].values()):
                raise CLIError("idempotency_conflict", "同一准备请求不能换执行目录")
            mutate(store, args, "bind-workspace", ticket, {"workspace": replay["workspace"]})
            return {**export_ticket(store, ticket, args._last_receipt), "bindings": [
                {"source_root": source, "execution_root": path}
                for source, path in replay["workspace"].items()]}
        # Verify ownership before writing the new execution directory.
        require_execution(task, args)
        bindings = workspace.materialize(snapshot, destination)
        mutate(store, args, "bind-workspace", ticket, {"workspace": bindings})
        return {**export_ticket(store, ticket, args._last_receipt), "bindings": [
            {"source_root": source, "execution_root": path} for source, path in bindings.items()]}
    if args.command == "submit":
        snapshot = manifest(task, "snapshot")
        proof = read_json(args.evidence)
        replay = replay_envelope(store, args, "submit", ticket)
        if replay:
            frozen = workspace.validate_delivery(replay["delivery"])
            wanted = [{"path": str(safe_absolute(Path(path).expanduser().absolute())), "sha256": digest(path)}
                      for path in proof.get("files", [])]
            actual = [{key: row[key] for key in ("path", "sha256")} for row in frozen["evidence"]]
            if wanted != actual:
                raise CLIError("idempotency_conflict", "重复提交的证据已变化")
            mutate(store, args, "submit", ticket, {"delivery": replay["delivery"]})
            return export_ticket(store, ticket, args._last_receipt)
        require_execution(task, args)
        if task["state"] != "running":
            raise CLIError("invalid_state", "只有正在执行的工作区可以提交")
        directory = root / "tickets" / ticket / "deliveries" / hashlib.sha256(args.request_id.encode()).hexdigest()[:20]
        if directory.exists():
            delivery = workspace.validate_delivery(directory)
        else:
            delivery = workspace.make_delivery(snapshot, task["workspace"], directory,
                                               proof.get("files", []))
        mutate(store, args, "submit", ticket, {"delivery": ref(delivery)})
    elif args.command == "integrate":
        # Keep integration ownership after interruption. Repeating the same request
        # resumes its on-disk operation; another request cannot steal the project.
        if (task["requester"], task["requester_session"]) != (args.actor, args.session):
            raise CLIError("execution_owner", "接入需要当前绑定的发起会话")
        # The operation is tied to the immutable delivery, while request receipts
        # belong to one session incarnation. An explicit requester-resume may
        # continue the same journal without replaying another session's receipt.
        incarnation = hashlib.sha256(args.session.encode()).hexdigest()[:16]
        start_suffix, finish_suffix = ":start:" + incarnation, ":finish:" + incarnation
        replay_envelope(store, args, "integration-start", ticket, suffix=start_suffix)
        finished = replay_envelope(store, args, "integration-finish", ticket, suffix=finish_suffix)
        if finished:
            mutate(store, args, "integration-start", ticket, {}, suffix=start_suffix)
            args.expected_revision = None
            mutate(store, args, "integration-finish", ticket,
                   {"result": finished["integration"], "delivery_digest": finished["delivery_digest"]}, suffix=finish_suffix)
            return {**export_ticket(store, ticket, args._last_receipt), "already_applied": True}
        snapshot = manifest(task, "snapshot")
        delivery = manifest(task, "delivery")
        mutate(store, args, "integration-start", ticket, {}, suffix=start_suffix)
        directory = root / "tickets" / ticket / "integrations" / task["delivery"]["manifest_sha256"]
        outcome = workspace.integrate(snapshot, delivery, directory)
        args.expected_revision = None
        mutate(store, args, "integration-finish", ticket,
               {"result": ref(outcome), "delivery_digest": task["delivery"]["manifest_sha256"]}, suffix=finish_suffix)
    elif args.command == "accept":
        proof = read_json(args.checks)
        checks = []
        for row in proof.get("checks", []):
            evidence = row["evidence"]
            evidence_ref = evidence if isinstance(evidence, dict) else {
                "path": str(Path(evidence).expanduser().absolute()), "sha256": digest(evidence)}
            checks.append({"id": row["id"],
                           "pass": row.get("outcome") == "pass" or row.get("pass") is True,
                           "evidence": evidence_ref})
        mutate(store, args, "accept", ticket,
               {"checks": checks, "delivery_digest": task["delivery"]["manifest_sha256"],
                "no_code": workspace.validate_delivery(task["delivery"])["change_count"] == 0})
    elif args.command == "rework":
        replay = replay_envelope(store, args, "rework", ticket)
        if replay:
            if replay["state"] == "claimed":
                payload["snapshot"] = replay["snapshot"]
            mutate(store, args, "rework", ticket, payload)
            return export_ticket(store, ticket, args._last_receipt)
        if task["state"] == "integrated":
            if (task["requester"], task["requester_session"]) != (args.actor, args.session):
                raise CLIError("execution_owner", "返工需要当前绑定的发起会话")
            # A second code round starts from the observed integrated candidate;
            # never relax old-value checking or reuse the previous test verdict.
            workspace.verify_integration(task["integration"], task["delivery"])
            directory = root / "tickets" / ticket / "reworks" / hashlib.sha256(args.request_id.encode()).hexdigest()[:20]
            snapshot = workspace.validate_snapshot(directory) if directory.exists() else workspace.capture(
                task["contract"]["projects"], directory)
            payload["snapshot"] = ref(snapshot)
        mutate(store, args, "rework", ticket, payload)
    else:
        action = {"cancel": "confirm-cancel" if getattr(args, "confirm_stopped", False) else "request-cancel",
                  "finish": "integration-finish", "resume": "requester-resume"}.get(args.command, args.command)
        mutate(store, args, action, ticket, payload)
    return export_ticket(store, ticket, getattr(args, "_last_receipt", None))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        output = run(args)
        print(json.dumps({"ok": True, **output}, ensure_ascii=False, allow_nan=False))
        return 0
    except (OutsourceError, ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        if hasattr(exc, "as_dict"):
            error = exc.as_dict()
        else:
            error = {"ok": False, "error_code": getattr(exc, "code", "outsource_error"),
                     "error": str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error_code": "interrupted",
                          "error": "操作中断；按持久回执核对后恢复，不自动重复副作用"}, ensure_ascii=False),
              file=sys.stderr)
        return 130
