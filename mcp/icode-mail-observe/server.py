"""ICODE read-only IMAP evidence MCP.

Mailbox state is never changed: folders are selected read-only and complete
messages are fetched with BODY.PEEK.  The only write operation saves one
explicitly selected attachment under the configured managed evidence root.
"""

from __future__ import annotations

import imaplib
import os
import re
import stat
import sys
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterator

SERVER_DIR = Path(__file__).resolve().parent
LIB_DIR = SERVER_DIR.parent / "_lib"
TOOLS_DIR = SERVER_DIR.parents[1] / "tools"
for import_dir in (LIB_DIR, TOOLS_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

import email_intake  # noqa: E402
from local_mcp_common import (  # noqa: E402
    REMOTE_MANAGED_WRITE_ANNOTATIONS,
    REMOTE_READ_ONLY_ANNOTATIONS,
    fail,
    load_config,
    ok,
)
from mcp.server.fastmcp import FastMCP  # noqa: E402

SERVER_NAME = "icode-mail-observe"
TOOLS = ["describe_capabilities", "list_mailboxes", "search_messages", "get_message", "get_thread", "save_attachment"]
DEFAULTS = {
    "host": "",
    "port": 993,
    "username": "",
    "credential_file": "",
    "mailbox_allowlist": ["INBOX"],
    "download_root": "$HOME/.claude/icode_data/mail_attachments",
    "max_messages": 50,
    "max_parts": 1000,
    "max_message_bytes": 25 * 1024 * 1024,
    "max_attachment_bytes": 50 * 1024 * 1024,
    "timeout_seconds": 15,
}
mcp = FastMCP(SERVER_NAME)
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailObserveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _config() -> dict[str, Any]:
    config = load_config("ICODE_MAIL_OBSERVE_CONFIG", SERVER_DIR, DEFAULTS)
    if not isinstance(config.get("host"), str) or not config["host"].strip():
        raise MailObserveError("config_invalid", "host 未配置")
    if not isinstance(config.get("username"), str) or not config["username"].strip():
        raise MailObserveError("config_invalid", "username 未配置")
    port = config.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise MailObserveError("config_invalid", "port 必须是 1..65535")
    allowlist = config.get("mailbox_allowlist")
    if not isinstance(allowlist, list) or not allowlist or not all(isinstance(item, str) and item for item in allowlist):
        raise MailObserveError("config_invalid", "mailbox_allowlist 必须是非空字符串数组")
    limits = {
        "max_messages": (1, 1000),
        "max_parts": (1, 10000),
        "max_message_bytes": (1, 1024 * 1024 * 1024),
        "max_attachment_bytes": (1, 1024 * 1024 * 1024),
        "timeout_seconds": (1, 120),
    }
    for key, (minimum, maximum) in limits.items():
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not (minimum <= value <= maximum):
            raise MailObserveError("config_invalid", f"{key} 必须是 {minimum}..{maximum} 的整数")
    if not isinstance(config.get("download_root"), str) or not config["download_root"].strip():
        raise MailObserveError("config_invalid", "download_root 必须是非空路径")
    return config


def _credential(config: dict[str, Any]) -> str:
    value = os.environ.get("ICODE_MAIL_OBSERVE_SECRET", "")
    if value:
        return value
    raw_path = config.get("credential_file", "")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise MailObserveError("credentials_unavailable", "未设置 ICODE_MAIL_OBSERVE_SECRET 或 credential_file")
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    try:
        if path.is_symlink() or not path.is_file():
            raise MailObserveError("credentials_unavailable", "credential_file 必须是普通文件且不能是符号链接")
        mode = stat.S_IMODE(path.stat().st_mode)
        if os.name != "nt" and mode & 0o077:
            raise MailObserveError("credentials_unavailable", "credential_file 权限必须为 0600 或更严格")
        secret = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise MailObserveError("credentials_unavailable", f"无法读取 credential_file: {type(exc).__name__}") from exc
    if not secret:
        raise MailObserveError("credentials_unavailable", "credential_file 为空")
    return secret


@contextmanager
def _connection(config: dict[str, Any]) -> Iterator[imaplib.IMAP4_SSL]:
    secret = _credential(config)
    client: imaplib.IMAP4_SSL | None = None
    try:
        client = imaplib.IMAP4_SSL(
            config["host"].strip(),
            int(config["port"]),
            timeout=max(1, min(int(config.get("timeout_seconds", 15)), 120)),
        )
        status, _ = client.login(config["username"].strip(), secret)
        if status != "OK":
            raise MailObserveError("authentication_failed", "IMAP 登录失败")
        yield client
    except MailObserveError:
        raise
    except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
        raise MailObserveError("imap_failed", f"IMAP 访问失败: {type(exc).__name__}") from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def _allowed_mailbox(mailbox: str, config: dict[str, Any]) -> str:
    if not isinstance(mailbox, str) or not mailbox or any(char in mailbox for char in "\x00\r\n"):
        raise MailObserveError("mailbox_not_allowed", "邮箱目录名无效")
    allowlist = config["mailbox_allowlist"]
    if "*" not in allowlist and mailbox not in allowlist:
        raise MailObserveError("mailbox_not_allowed", f"邮箱目录不在 allowlist: {mailbox}")
    return mailbox


def _uid(value: str) -> str:
    normalized = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", normalized):
        raise MailObserveError("invalid_uid", "uid 必须是正整数")
    return normalized


def _select(client: imaplib.IMAP4_SSL, mailbox: str) -> None:
    status, _ = client.select(mailbox, readonly=True)
    if status != "OK":
        raise MailObserveError("mailbox_unavailable", f"无法只读打开邮箱目录: {mailbox}")


def _literal(data: Any) -> bytes:
    payloads: list[bytes] = []
    candidates: list[bytes] = []
    if isinstance(data, bytes):
        candidates.append(data)
    elif isinstance(data, (list, tuple)):
        for item in data:
            if isinstance(item, tuple):
                if len(item) > 1 and isinstance(item[1], bytes) and item[1]:
                    payloads.append(item[1])
                candidates.extend(value for value in item if isinstance(value, bytes))
            elif isinstance(item, bytes):
                candidates.append(item)
    # Metadata and payload are both bytes.  The payload is normally the largest
    # item and, unlike metadata, is not prefixed with an IMAP sequence number.
    return max(payloads or candidates, key=len, default=b"")


def _message_size(client: imaplib.IMAP4_SSL, uid: str) -> int:
    status, data = client.uid("fetch", uid, "(RFC822.SIZE)")
    if status != "OK":
        raise MailObserveError("fetch_failed", f"无法读取邮件大小: {uid}")
    metadata: list[bytes] = []
    for item in data or []:
        if isinstance(item, bytes):
            metadata.append(item)
        elif isinstance(item, tuple) and item and isinstance(item[0], bytes):
            metadata.append(item[0])
    joined = b" ".join(metadata)
    match = re.search(rb"RFC822\.SIZE\s+(\d+)", joined, re.IGNORECASE)
    if not match:
        raise MailObserveError("fetch_failed", f"邮件大小响应无效: {uid}")
    return int(match.group(1))


def _fetch_raw(client: imaplib.IMAP4_SSL, uid: str, config: dict[str, Any]) -> bytes:
    size = _message_size(client, uid)
    maximum = int(config["max_message_bytes"])
    if size > maximum:
        raise MailObserveError("message_too_large", f"邮件大小 {size} 超过上限 {maximum}")
    status, data = client.uid("fetch", uid, "(BODY.PEEK[])")
    if status != "OK":
        raise MailObserveError("fetch_failed", f"无法读取邮件: {uid}")
    raw = _literal(data)
    if not raw or len(raw) > maximum:
        raise MailObserveError("fetch_failed", f"邮件正文为空或超出上限: {uid}")
    return raw


def _envelope(raw: bytes, uid: str, size: int) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    return {
        "untrusted": True,
        "uid": uid,
        "size": size,
        "subject": str(message.get("Subject", "")),
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "date": str(message.get("Date", "")),
        "message_id": str(message.get("Message-ID", "")),
        "in_reply_to": str(message.get("In-Reply-To", "")),
        "references": str(message.get("References", "")),
    }


def _handle_error(exc: Exception, fallback_code: str) -> dict[str, Any]:
    if isinstance(exc, MailObserveError):
        return fail(exc.code, str(exc))
    return fail(fallback_code, f"{type(exc).__name__}: {exc}")


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def describe_capabilities() -> dict[str, Any]:
    return ok({
        "server": SERVER_NAME,
        "purpose": "以只读 IMAP 获取邮件、线程和附件证据",
        "tools": TOOLS,
        "api_key_required": False,
        "network": True,
        "mailbox_read_only": True,
        "source_read_only": True,
        "managed_writes": ["save_attachment writes one selected attachment under download_root"],
        "credential_sources": ["ICODE_MAIL_OBSERVE_SECRET", "credential_file"],
        "forbidden_operations": ["SMTP", "STORE", "MOVE", "COPY", "EXPUNGE", "APPEND", "raw IMAP command"],
        "message_fetch": "BODY.PEEK[]",
        "decision_authority": "none",
    })


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def list_mailboxes() -> dict[str, Any]:
    try:
        config = _config()
        with _connection(config) as client:
            status, data = client.list()
            if status != "OK":
                raise MailObserveError("list_failed", "无法列出邮箱目录")
        available: set[str] = set()
        for raw in data or []:
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            matches = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', line)
            name = matches[-1].replace('\\"', '"') if matches else line.rsplit(" ", 1)[-1].strip('"')
            available.add(name)
        allowlist = config["mailbox_allowlist"]
        selected = sorted(available if "*" in allowlist else available.intersection(allowlist))
        return ok({"untrusted": True, "mailboxes": [{"path": name} for name in selected], "returned": len(selected)})
    except (OSError, ValueError, MailObserveError, imaplib.IMAP4.error) as exc:
        return _handle_error(exc, "list_failed")


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def search_messages(
    mailbox: str,
    subject_contains: str = "",
    since: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        config = _config()
        mailbox = _allowed_mailbox(mailbox, config)
        requested_limit = min(max(1, int(limit)), int(config["max_messages"]))
        offset = int(offset)
        if offset < 0:
            raise MailObserveError("invalid_query", "offset 不能小于 0")
        criteria: list[str] = ["ALL"]
        if subject_contains:
            if len(subject_contains) > 200 or any(char in subject_contains for char in "\x00\r\n"):
                raise MailObserveError("invalid_query", "subject_contains 无效或过长")
        if since:
            try:
                date = datetime.strptime(since, "%Y-%m-%d")
            except ValueError as exc:
                raise MailObserveError("invalid_query", "since 必须是 YYYY-MM-DD") from exc
            criteria.extend(["SINCE", f"{date.day:02d}-{MONTH_ABBR[date.month - 1]}-{date.year}"])
        with _connection(config) as client:
            _select(client, mailbox)
            status, data = client.uid("search", None, *criteria)
            if status != "OK":
                raise MailObserveError("search_failed", "IMAP 搜索失败")
            uids = (data[0] if data and isinstance(data[0], bytes) else b"").split()
            scan_limit = int(config["max_messages"])
            scan_window = list(reversed(uids[-scan_limit:]))
            matched = []
            subject_filter = subject_contains.casefold()
            for raw_uid in scan_window:
                uid = _uid(raw_uid.decode("ascii"))
                status, header_data = client.uid(
                    "fetch",
                    uid,
                    "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])",
                )
                if status != "OK":
                    continue
                header = _literal(header_data)
                size_match = re.search(rb"RFC822\.SIZE\s+(\d+)", b" ".join(
                    item[0] for item in header_data or [] if isinstance(item, tuple) and item and isinstance(item[0], bytes)
                ), re.IGNORECASE)
                envelope = _envelope(header, uid, int(size_match.group(1)) if size_match else 0)
                if not subject_filter or subject_filter in envelope["subject"].casefold():
                    matched.append(envelope)
            messages = matched[offset : offset + requested_limit]
        return ok({
            "mailbox": mailbox,
            "messages": messages,
            "returned": len(messages),
            "offset": offset,
            "limit": requested_limit,
            "scanned": len(scan_window),
            "truncated": len(uids) > len(scan_window) or offset + len(messages) < len(matched),
        })
    except (OSError, TypeError, ValueError, MailObserveError, imaplib.IMAP4.error) as exc:
        return _handle_error(exc, "search_failed")


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def get_message(mailbox: str, uid: str) -> dict[str, Any]:
    try:
        config = _config()
        mailbox = _allowed_mailbox(mailbox, config)
        uid = _uid(uid)
        with _connection(config) as client:
            _select(client, mailbox)
            raw = _fetch_raw(client, uid, config)
        mail = email_intake.parse_email_bytes(
            raw,
            source_name=f"{mailbox}-{uid}.eml",
            max_message_bytes=int(config["max_message_bytes"]),
            max_parts=int(config["max_parts"]),
        )
        return ok({"mailbox": mailbox, "uid": uid, "mail": mail})
    except (OSError, TypeError, ValueError, MailObserveError, imaplib.IMAP4.error) as exc:
        return _handle_error(exc, "message_failed")


@mcp.tool(annotations=REMOTE_READ_ONLY_ANNOTATIONS)
def get_thread(mailbox: str, uid: str, limit: int = 20) -> dict[str, Any]:
    """Return a bounded thread view using the selected message's RFC IDs."""
    try:
        config = _config()
        mailbox = _allowed_mailbox(mailbox, config)
        uid = _uid(uid)
        maximum = min(max(1, int(limit)), int(config["max_messages"]))
        with _connection(config) as client:
            _select(client, mailbox)
            seed_raw = _fetch_raw(client, uid, config)
            seed = email_intake.parse_email_bytes(
                seed_raw,
                source_name=f"{mailbox}-{uid}.eml",
                max_message_bytes=int(config["max_message_bytes"]),
                max_parts=int(config["max_parts"]),
            )
            ids = [seed["headers"]["message_id"], seed["headers"]["in_reply_to"], *seed["headers"]["references"]]
            candidates = {uid}
            resolution_gaps: list[dict[str, str]] = []
            if not any(ids):
                resolution_gaps.append({"uid": uid, "reason": "missing_rfc_thread_identifiers"})
            for message_id in [value for value in ids if value][:20]:
                safe_id = re.sub(r'["\\\x00\r\n]', "", message_id).encode("ascii", errors="ignore").decode("ascii")[:500]
                if not safe_id:
                    continue
                for field in ("MESSAGE-ID", "IN-REPLY-TO", "REFERENCES"):
                    status, data = client.uid("search", None, "HEADER", field, f'"{safe_id}"')
                    if status == "OK" and data and isinstance(data[0], bytes):
                        candidates.update(value.decode("ascii") for value in data[0].split())
                    elif status != "OK":
                        resolution_gaps.append({"uid": uid, "reason": f"thread_search_failed:{field}"})
                if len(candidates) >= maximum:
                    break
            messages = []
            for candidate in sorted(candidates, key=int)[:maximum]:
                try:
                    raw = seed_raw if candidate == uid else _fetch_raw(client, _uid(candidate), config)
                except MailObserveError as exc:
                    resolution_gaps.append({"uid": candidate, "reason": exc.code})
                    continue
                messages.append({
                    "uid": candidate,
                    "mail": email_intake.parse_email_bytes(
                        raw,
                        source_name=f"{mailbox}-{candidate}.eml",
                        max_message_bytes=int(config["max_message_bytes"]),
                        max_parts=int(config["max_parts"]),
                    ),
                })
        messages.sort(key=lambda item: item["mail"]["headers"].get("date_utc", ""))
        return ok({
            "mailbox": mailbox,
            "seed_uid": uid,
            "messages": messages,
            "returned": len(messages),
            "truncated": len(candidates) > maximum,
            "resolution_gaps": resolution_gaps,
            "resolution_status": "partial" if resolution_gaps or len(candidates) > maximum else "bounded",
        })
    except (OSError, TypeError, ValueError, MailObserveError, imaplib.IMAP4.error) as exc:
        return _handle_error(exc, "thread_failed")


@mcp.tool(annotations=REMOTE_MANAGED_WRITE_ANNOTATIONS)
def save_attachment(mailbox: str, uid: str, part_id: str) -> dict[str, Any]:
    try:
        config = _config()
        mailbox = _allowed_mailbox(mailbox, config)
        uid = _uid(uid)
        if not re.fullmatch(r"part-[1-9][0-9]*", part_id):
            raise MailObserveError("invalid_part_id", "part_id 格式无效")
        download_root = Path(os.path.expandvars(os.path.expanduser(config["download_root"])))
        if not download_root.is_absolute():
            download_root = SERVER_DIR / download_root
        download_root = Path(os.path.abspath(download_root))
        folder_component = re.sub(r"[^A-Za-z0-9._-]+", "_", mailbox).strip("._") or "mailbox"
        output = download_root / folder_component / uid
        with _connection(config) as client:
            _select(client, mailbox)
            raw = _fetch_raw(client, uid, config)
        answer = email_intake.save_attachment(
            raw,
            part_id=part_id,
            output_root=output,
            allowed_root=download_root,
            max_attachment_bytes=int(config["max_attachment_bytes"]),
            max_message_bytes=int(config["max_message_bytes"]),
            max_parts=int(config["max_parts"]),
        )
        answer.update({"mailbox": mailbox, "uid": uid, "part_id": part_id})
        return ok(answer)
    except (OSError, TypeError, ValueError, PermissionError, MailObserveError, imaplib.IMAP4.error) as exc:
        return _handle_error(exc, "attachment_failed")


if __name__ == "__main__":
    mcp.run()
