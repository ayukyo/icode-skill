#!/usr/bin/env python3
"""Deterministic, untrusted-by-default email evidence intake.

This module parses RFC 822/MIME messages without fetching remote resources or
executing content.  Attachment bytes are written only through the explicit,
bounded ``save_attachment`` API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


DEFAULT_MAX_MESSAGE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_PARTS = 1000
EXECUTABLE_SUFFIXES = {
    ".appimage", ".bat", ".cmd", ".com", ".dll", ".dylib", ".exe",
    ".msi", ".ps1", ".scr", ".sh", ".so",
}
SCHEMATIC_SUFFIXES = {".brd", ".dsn", ".edf", ".edif", ".kicad_pcb", ".kicad_sch", ".pcb", ".sch"}
SPREADSHEET_SUFFIXES = {".csv", ".ods", ".tsv", ".xls", ".xlsb", ".xlsm", ".xlsx"}
DOCUMENT_SUFFIXES = {".doc", ".docx", ".eml", ".msg", ".odp", ".odt", ".pdf", ".ppt", ".pptx"}
ARCHIVE_SUFFIXES = {".7z", ".bz2", ".gz", ".rar", ".tar", ".tgz", ".xz", ".zip"}
HTML_VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
HTML_SUPPRESSED_ELEMENTS = {"form", "head", "iframe", "object", "script", "style", "template"}
QUOTE_DELIMITER_RE = re.compile(
    r"^(?:-+\s*(?:forwarded message|original message|原始邮件|转发邮件)\s*-+|[-—_]{5,})$",
    re.IGNORECASE,
)
QUOTE_HEADER_PATTERNS = {
    "from": re.compile(r"^(?:from|发\s*件\s*人)\s*[:：]\s*(.+)$", re.IGNORECASE),
    "date": re.compile(r"^(?:sent|date|发\s*送\s*时\s*间|日\s*期)\s*[:：]\s*(.+)$", re.IGNORECASE),
    "to": re.compile(r"^(?:to|收\s*件\s*人)\s*[:：]\s*(.+)$", re.IGNORECASE),
    "cc": re.compile(r"^(?:cc|抄\s*送)\s*[:：]\s*(.+)$", re.IGNORECASE),
    "subject": re.compile(r"^(?:subject|主\s*题)\s*[:：]\s*(.+)$", re.IGNORECASE),
}
EVIDENCE_SUFFIXES = {
    ".bag", ".conf", ".json", ".jsonl", ".log", ".mcap", ".toml",
    ".trace", ".txt", ".yaml", ".yml",
}


def _decoded_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (LookupError, UnicodeDecodeError):
        return str(value).strip()


def _clean_text(value: str) -> str:
    """Remove bidi/zero-width control characters while preserving line breaks."""
    cleaned: list[str] = []
    for char in value:
        if char in "\n\r\t":
            cleaned.append(char)
            continue
        category = unicodedata.category(char)
        if category in {"Cf", "Cc"}:
            continue
        cleaned.append(char)
    return "".join(cleaned).replace("\r\n", "\n").replace("\r", "\n").strip()


def _quote_header(line: str) -> tuple[str, str] | None:
    normalized = " ".join(line.split())
    for name, pattern in QUOTE_HEADER_PATTERNS.items():
        match = pattern.match(normalized)
        if match:
            return name, match.group(1).strip()
    return None


def _quote_header_block(lines: list[str], start: int, window: int = 64) -> dict[str, str]:
    headers: dict[str, str] = {}
    for offset, line in enumerate(lines[start : min(len(lines), start + window)]):
        if offset and QUOTE_DELIMITER_RE.match(" ".join(line.split())):
            break
        parsed = _quote_header(line)
        if parsed:
            key, value = parsed
            headers.setdefault(key, value)
    return headers


def _segment_quoted_text(text: str, representation: str) -> list[dict[str, Any]]:
    """Conservatively segment visible forwarded/replied header blocks.

    These blocks are presentation evidence only.  They intentionally do not
    receive an RFC Message-ID because inline forwarding does not preserve one.
    """
    lines = text.splitlines()
    starts: list[int] = []
    for index, line in enumerate(lines):
        normalized = " ".join(line.split())
        delimiter = bool(QUOTE_DELIMITER_RE.match(normalized))
        header = _quote_header(normalized)
        header_start = index + 1 if delimiter else index
        headers = _quote_header_block(lines, header_start)
        enough_headers = "from" in headers and "subject" in headers and (
            "date" in headers or "to" in headers
        )
        if not enough_headers or (not delimiter and (not header or header[0] != "from")):
            continue
        if starts and index - starts[-1] <= 2:
            continue
        starts.append(index)

    sections: list[dict[str, Any]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        header_start = start + 1 if QUOTE_DELIMITER_RE.match(" ".join(lines[start].split())) else start
        section_text = "\n".join(lines[start:end]).strip()
        sections.append({
            "representation": representation,
            "kind": "forwarded_or_replied",
            "start_line": start + 1,
            "end_line": end,
            "headers": _quote_header_block(lines, header_start),
            "text": section_text,
            "rfc_identity": "unavailable_inline_quote",
        })
    return sections


def _safe_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.encode("idna").decode("ascii")
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", parsed.path or "/", "", ""))


def _hidden(attrs: dict[str, str]) -> bool:
    style = re.sub(r"\s+", "", attrs.get("style", "").lower())
    return (
        "hidden" in attrs
        or attrs.get("aria-hidden", "").lower() == "true"
        or "display:none" in style
        or "visibility:hidden" in style
        or "font-size:0" in style
    )


class _SafeHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.text_chunks: list[str] = []
        self.tables: list[dict[str, Any]] = []
        self._table_rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_chunks: list[str] | None = None
        self.remote_images: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key.lower(): value or "" for key, value in attrs_list}
        tag = tag.lower()
        if self.hidden_depth or tag in HTML_SUPPRESSED_ELEMENTS or _hidden(attrs):
            if tag not in HTML_VOID_ELEMENTS:
                self.hidden_depth += 1
            return
        if tag == "table":
            self._table_rows = []
        elif tag == "tr" and self._table_rows is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_chunks = []
        elif tag == "img":
            url = _safe_url(attrs.get("src", ""))
            if url and url not in self.remote_images:
                self.remote_images.append(url)
        elif tag == "a":
            url = _safe_url(attrs.get("href", ""))
            if url and url not in self.links:
                self.links.append(url)
        if tag in {"br", "p", "div", "li", "tr"}:
            self.text_chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in HTML_VOID_ELEMENTS:
            return
        if self.hidden_depth:
            self.hidden_depth -= 1
            return
        if tag in {"td", "th"} and self._cell_chunks is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell_chunks).split()))
            self._cell_chunks = None
        elif tag == "tr" and self._row is not None and self._table_rows is not None:
            if any(self._row):
                self._table_rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_rows is not None:
            if self._table_rows:
                self.tables.append({"rows": self._table_rows})
            self._table_rows = None
        if tag in {"p", "div", "li", "tr"}:
            self.text_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth:
            return
        self.text_chunks.append(data)
        if self._cell_chunks is not None:
            self._cell_chunks.append(data)

    def result(self) -> dict[str, Any]:
        lines = [" ".join(line.split()) for line in "".join(self.text_chunks).splitlines()]
        return {
            "text": "\n".join(line for line in lines if line),
            "tables": self.tables,
            "remote_images": self.remote_images,
            "links": self.links,
        }


def _safe_filename(value: str | None, fallback: str) -> str:
    decoded = _decoded_header(value)
    normalized = decoded.replace("\\", "/").split("/")[-1]
    normalized = "".join(
        char for char in normalized
        if char not in "\x00\r\n" and unicodedata.category(char) not in {"Cc", "Cf"}
    )
    normalized = normalized.strip(" .")
    if normalized in {"", ".", ".."}:
        normalized = fallback
    return normalized[:240]


def _part_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        charset = part.get_content_charset() or "utf-8"
        return payload.encode(charset, errors="replace")
    if part.get_content_type().lower() == "message/rfc822" and isinstance(part.get_payload(), list):
        return b"\n".join(
            child.as_bytes(policy=policy.default)
            for child in part.get_payload()
            if isinstance(child, Message)
        )
    return b""


def _part_text(part: Message) -> str:
    data = _part_bytes(part)
    charset = part.get_content_charset() or "utf-8"
    try:
        return _clean_text(data.decode(charset, errors="replace"))
    except LookupError:
        return _clean_text(data.decode("utf-8", errors="replace"))


def _leaf_parts(message: Message) -> Iterable[tuple[str, Message]]:
    index = 0
    stack = [message]
    while stack:
        part = stack.pop()
        payload = part.get_payload()
        if part.get_content_type().lower() != "message/rfc822" and part.is_multipart() and isinstance(payload, list):
            stack.extend(reversed([child for child in payload if isinstance(child, Message)]))
            continue
        index += 1
        yield f"part-{index}", part


def _blocked_reason(filename: str, payload: bytes) -> str | None:
    if Path(filename).suffix.lower() in EXECUTABLE_SUFFIXES:
        return "executable_extension"
    if payload.startswith(b"MZ") or payload.startswith(b"\x7fELF") or payload.startswith(b"#!"):
        return "executable_magic"
    if payload[:4] in {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}:
        return "executable_magic"
    return None


def _magic_kind(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload.startswith(b"%PDF-"):
        return "pdf"
    if payload.startswith(b"PK\x03\x04"):
        return "zip_container"
    if payload.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "ole_compound_file"
    if _blocked_reason("attachment", payload) == "executable_magic":
        return "executable"
    if payload and b"\x00" not in payload[:4096]:
        return "text_or_unknown"
    return "binary_or_unknown"


def _route_attachment(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if content_type.startswith("image/") or content_type.startswith("audio/") or content_type.startswith("video/"):
        return "media"
    if suffix in SCHEMATIC_SUFFIXES:
        return "schematic"
    if suffix in SPREADSHEET_SUFFIXES or "spreadsheet" in content_type or "excel" in content_type:
        return "spreadsheet"
    if suffix in DOCUMENT_SUFFIXES:
        return "technical_document"
    if suffix in ARCHIVE_SUFFIXES:
        return "archive"
    if suffix in EVIDENCE_SUFFIXES or content_type.startswith("text/"):
        return "evidence"
    return "binary_attachment"


def _address_list(message: Message, names: list[str]) -> list[dict[str, str]]:
    values: list[str] = []
    for name in names:
        values.extend(message.get_all(name, []))
    return [{"name": _clean_text(name), "address": _clean_text(address)} for name, address in getaddresses(values)]


def _date_utc(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse_email_bytes(
    raw: bytes,
    *,
    source_name: str = "message.eml",
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    max_parts: int = DEFAULT_MAX_PARTS,
) -> dict[str, Any]:
    """Parse one MIME message into a deterministic evidence manifest."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if max_message_bytes < 1 or len(raw) > max_message_bytes:
        raise ValueError(f"message exceeds max_message_bytes ({max_message_bytes})")
    if max_parts < 1:
        raise ValueError("max_parts must be positive")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    tables: list[dict[str, Any]] = []
    remote_images: list[str] = []
    links: list[str] = []
    attachments: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = [
        {"part_id": "message", "reason": f"mime_defect:{type(defect).__name__}"}
        for defect in message.defects
    ]

    for part_position, (part_id, part) in enumerate(_leaf_parts(message), 1):
        if part_position > max_parts:
            gaps.append({"part_id": part_id, "reason": "mime_part_limit_exceeded"})
            break
        content_type = part.get_content_type().lower()
        disposition = part.get_content_disposition()
        raw_filename = part.get_filename()
        content_id = (part.get("Content-ID") or "").strip().strip("<>")
        gaps.extend(
            {"part_id": part_id, "reason": f"mime_defect:{type(defect).__name__}"}
            for defect in part.defects
        )
        is_body = raw_filename is None and disposition != "attachment" and content_type in {"text/plain", "text/html"}
        try:
            payload = _part_bytes(part)
            if is_body and content_type == "text/plain":
                text = _part_text(part)
                if text:
                    plain_parts.append(text)
                continue
            if is_body and content_type == "text/html":
                parser = _SafeHTMLParser()
                parser.feed(_part_text(part))
                parsed_html = parser.result()
                if parsed_html["text"]:
                    html_parts.append(parsed_html["text"])
                tables.extend(parsed_html["tables"])
                for value in parsed_html["remote_images"]:
                    if value not in remote_images:
                        remote_images.append(value)
                for value in parsed_html["links"]:
                    if value not in links:
                        links.append(value)
                continue
        except (LookupError, UnicodeError, ValueError) as exc:
            gaps.append({"part_id": part_id, "reason": f"decode_failed:{type(exc).__name__}"})
            payload = b""

        fallback = f"attachment-{part_id}"
        filename = _safe_filename(raw_filename, fallback)
        blocked = _blocked_reason(filename, payload)
        item: dict[str, Any] = {
            "part_id": part_id,
            "filename": filename,
            "content_type": content_type,
            "detected_kind": _magic_kind(payload),
            "byte_representation": "normalized_rfc822" if content_type == "message/rfc822" else "decoded_mime_payload",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "inline": disposition == "inline" or bool(content_id),
            "content_id": content_id,
            "route": _route_attachment(filename, content_type),
            "saved": False,
            "analysis_status": "blocked" if blocked else "pending",
        }
        if blocked:
            item["blocked_reason"] = blocked
        attachments.append(item)

    original_date = _decoded_header(message.get("Date"))
    normalized_date = _date_utc(message.get("Date"))
    message_id = _decoded_header(message.get("Message-ID"))
    if not original_date or not normalized_date:
        gaps.append({"part_id": "headers", "reason": "missing_or_unparseable_timezone_aware_date"})
    if not message_id:
        gaps.append({"part_id": "headers", "reason": "missing_message_id"})
    references = re.findall(r"<[^<>\s]+>", _decoded_header(message.get("References")))
    quoted_sections: list[dict[str, Any]] = []
    for representation, text in (
        ("plain_text", "\n\n".join(plain_parts)),
        ("html_text", "\n\n".join(html_parts)),
    ):
        if text:
            quoted_sections.extend(_segment_quoted_text(text, representation))
    intake_status = "ready" if not gaps else "partial"
    unresolved = ["semantic_analysis_pending"]
    for item in attachments:
        if item["analysis_status"] == "blocked":
            unresolved.append(f"attachment_blocked:{item['part_id']}")
        else:
            unresolved.append(f"attachment_analysis_pending:{item['part_id']}")
    unresolved.extend(f"parse_gap:{gap['part_id']}:{gap['reason']}" for gap in gaps)
    result = {
        "schema_version": 1,
        "untrusted": True,
        "source": {
            "name": _safe_filename(source_name, "message.eml"),
            "detected_kind": "rfc822_message",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "headers": {
            "subject": _decoded_header(message.get("Subject")),
            "from": _address_list(message, ["From"]),
            "to": _address_list(message, ["To"]),
            "cc": _address_list(message, ["Cc"]),
            "date": original_date,
            "date_utc": normalized_date,
            "message_id": message_id,
            "in_reply_to": _decoded_header(message.get("In-Reply-To")),
            "references": references,
        },
        "body": {
            "plain_text": "\n\n".join(plain_parts),
            "html_text": "\n\n".join(html_parts),
            "tables": tables,
            "remote_images": remote_images,
            "links": links,
            "quoted_sections": quoted_sections,
        },
        "attachments": attachments,
        "content_coverage": {
            "plain_text": bool(plain_parts),
            "html_text": bool(html_parts),
            "tables": len(tables),
            "attachments": len(attachments),
            "inline_media": sum(1 for item in attachments if item["inline"]),
            "quoted_history": {
                "sections": len(quoted_sections),
                "status": "segmented" if quoted_sections else "not_detected",
            },
            "parse_gaps": gaps,
            "semantic_analysis_complete": False,
        },
        "attachment_routes": [
            {"part_id": item["part_id"], "filename": item["filename"], "route": item["route"]}
            for item in attachments
        ],
        "security": {
            "remote_resources_fetched": False,
            "message_instructions_executed": False,
            "links_opened": False,
            "active_content_executed": False,
        },
        "verdict": {
            "status": "partial",
            "intake_status": intake_status,
            "reason": (
                "MIME intake complete; semantic and routed attachment analysis remain pending"
                if not gaps else
                "MIME intake completed with parse gaps; semantic analysis remains pending"
            ),
            "unresolved": unresolved,
        },
    }
    return result


def _resolve_under(path: str | Path, root: str | Path, *, must_exist: bool) -> Path:
    root_path = Path(root).expanduser().resolve()
    target = Path(path).expanduser().resolve(strict=must_exist)
    if target != root_path and root_path not in target.parents:
        raise PermissionError(f"path is outside allowed root: {target}")
    return target


def _absolute_unresolved(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(str(value)))))


def _reject_symlinks_below(path: str | Path, root: str | Path) -> None:
    lexical_root = _absolute_unresolved(root)
    lexical_path = _absolute_unresolved(path)
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError as exc:
        raise PermissionError(f"path is outside allowed root: {lexical_path}") from exc
    cursor = lexical_root
    if cursor.is_symlink():
        raise PermissionError(f"symlink is not allowed in managed path: {cursor}")
    for component in relative.parts:
        cursor /= component
        if cursor.is_symlink():
            raise PermissionError(f"symlink is not allowed in managed path: {cursor}")


def save_attachment(
    raw: bytes,
    *,
    part_id: str,
    output_root: str | Path,
    allowed_root: str | Path,
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    max_parts: int = DEFAULT_MAX_PARTS,
) -> dict[str, Any]:
    """Save exactly one non-executable MIME leaf under an approved root."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if max_message_bytes < 1 or len(raw) > max_message_bytes:
        raise ValueError(f"message exceeds max_message_bytes ({max_message_bytes})")
    if max_attachment_bytes < 1:
        raise ValueError("max_attachment_bytes must be positive")
    if max_parts < 1:
        raise ValueError("max_parts must be positive")
    _reject_symlinks_below(output_root, allowed_root)
    allowed = Path(allowed_root).expanduser().resolve()
    output = _resolve_under(output_root, allowed, must_exist=False)
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise PermissionError(f"output_root is not a safe directory: {output}")
    missing_dirs: list[Path] = []
    cursor = output
    while not cursor.exists() and (cursor == allowed or allowed in cursor.parents):
        missing_dirs.append(cursor)
        if cursor == allowed:
            break
        cursor = cursor.parent
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    for created in missing_dirs:
        os.chmod(created, 0o700)
    output = _resolve_under(output, allowed, must_exist=True)

    message = BytesParser(policy=policy.default).parsebytes(raw)
    selected: Message | None = None
    for part_position, (current_id, part) in enumerate(_leaf_parts(message), 1):
        if part_position > max_parts:
            raise ValueError(f"part_id is outside max_parts ({max_parts})")
        if current_id == part_id:
            selected = part
            break
    if selected is None:
        raise ValueError(f"unknown part_id: {part_id}")
    if (
        selected.get_filename() is None
        and selected.get_content_disposition() not in {"attachment", "inline"}
        and not selected.get("Content-ID")
    ):
        raise ValueError(f"part_id is not an attachment: {part_id}")
    payload = _part_bytes(selected)
    filename = _safe_filename(selected.get_filename(), f"attachment-{part_id}")
    if len(payload) > max_attachment_bytes:
        raise ValueError(f"attachment exceeds max_attachment_bytes ({max_attachment_bytes})")
    blocked = _blocked_reason(filename, payload)
    if blocked:
        raise ValueError(f"attachment blocked: {blocked}")
    target = _resolve_under(output / filename, output, must_exist=False)
    digest = hashlib.sha256(payload).hexdigest()
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise PermissionError(f"unsafe existing target: {target}")
        existing = hashlib.sha256(target.read_bytes()).hexdigest()
        if existing == digest:
            return {"status": "already_exists_same_hash", "path": str(target), "sha256": digest, "size": len(payload)}
        raise FileExistsError(f"refusing to overwrite different attachment: {target}")

    descriptor, temporary = tempfile.mkstemp(prefix=".icode-mail-", dir=str(output))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            if target.is_symlink() or not target.is_file():
                raise PermissionError(f"unsafe existing target: {target}")
            existing = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing == digest:
                return {"status": "already_exists_same_hash", "path": str(target), "sha256": digest, "size": len(payload)}
            raise FileExistsError(f"refusing to overwrite different attachment: {target}")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"status": "saved", "path": str(target), "sha256": digest, "size": len(payload)}


def _inspect_msg(path: Path, raw: bytes, *, max_message_bytes: int, max_parts: int) -> dict[str, Any]:
    source = {
        "name": _safe_filename(path.name, "message.msg"),
        "detected_kind": "outlook_msg",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    try:
        import extract_msg  # type: ignore[import-not-found]
    except ImportError:
        return {
            "schema_version": 1,
            "untrusted": True,
            "source": source,
            "content_coverage": {"semantic_analysis_complete": False, "parse_gaps": ["outlook_msg_parser_unavailable"]},
            "security": {"remote_resources_fetched": False, "message_instructions_executed": False},
            "verdict": {"status": "requires_optional_dependency", "required_dependency": "extract-msg"},
        }
    parsed = None
    try:
        parsed = extract_msg.openMsg(str(path))
        synthetic = EmailMessage()
        synthetic["Subject"] = str(parsed.subject or "")
        synthetic["From"] = str(parsed.sender or "")
        synthetic["To"] = str(parsed.to or "")
        synthetic["Cc"] = str(parsed.cc or "")
        synthetic["Date"] = str(parsed.date or "")
        synthetic.set_content(parsed.body or "")
        if getattr(parsed, "htmlBody", None):
            html_body = parsed.htmlBody
            if isinstance(html_body, bytes):
                html_body = html_body.decode("utf-8", errors="replace")
            synthetic.add_alternative(str(html_body), subtype="html")
        for index, attachment in enumerate(parsed.attachments, 1):
            data = attachment.data
            if not isinstance(data, bytes):
                continue
            name = attachment.longFilename or attachment.shortFilename or f"attachment-{index}"
            mime = getattr(attachment, "mimetype", None) or "application/octet-stream"
            maintype, subtype = mime.split("/", 1) if "/" in mime else ("application", "octet-stream")
            synthetic.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        result = parse_email_bytes(
            synthetic.as_bytes(),
            source_name=path.name,
            max_message_bytes=max_message_bytes,
            max_parts=max_parts,
        )
        result["source"] = source
        result["source"]["parser"] = "extract-msg"
        return result
    except Exception as exc:  # extract-msg raises several format-specific exception classes
        return {
            "schema_version": 1,
            "untrusted": True,
            "source": source,
            "content_coverage": {"semantic_analysis_complete": False, "parse_gaps": [type(exc).__name__]},
            "security": {"remote_resources_fetched": False, "message_instructions_executed": False},
            "verdict": {"status": "corrupt", "reason": f"Outlook MSG parse failed: {type(exc).__name__}"},
        }
    finally:
        if parsed is not None:
            try:
                parsed.close()
            except Exception:
                pass


def inspect_email_file(
    path: str | Path,
    *,
    root: str | Path,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    max_parts: int = DEFAULT_MAX_PARTS,
) -> dict[str, Any]:
    _reject_symlinks_below(path, root)
    target = _resolve_under(path, root, must_exist=True)
    if not target.is_file():
        raise ValueError(f"not a regular email file: {target}")
    size = target.stat().st_size
    if size > max_message_bytes:
        raise ValueError(f"message exceeds max_message_bytes ({max_message_bytes})")
    raw = target.read_bytes()
    if raw.startswith(bytes.fromhex("d0cf11e0a1b11ae1")) or target.suffix.lower() == ".msg":
        return _inspect_msg(target, raw, max_message_bytes=max_message_bytes, max_parts=max_parts)
    return parse_email_bytes(
        raw,
        source_name=target.name,
        max_message_bytes=max_message_bytes,
        max_parts=max_parts,
    )


def _write_manifest(payload: dict[str, Any], output: Path, output_root: Path) -> None:
    root = output_root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"output_root must be an existing safe directory: {root}")
    _reject_symlinks_below(output, output_root)
    target = _resolve_under(output, root, must_exist=False)
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise PermissionError(f"unsafe output target: {target}")
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".icode-mail-manifest-", dir=str(root))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect untrusted email evidence without executing content")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--root", required=True)
    inspect_parser.add_argument("--path", required=True)
    inspect_parser.add_argument("--output-root", required=True)
    inspect_parser.add_argument("--output", required=True)
    inspect_parser.add_argument("--max-message-bytes", type=int, default=DEFAULT_MAX_MESSAGE_BYTES)
    inspect_parser.add_argument("--max-parts", type=int, default=DEFAULT_MAX_PARTS)
    args = parser.parse_args(argv)
    try:
        payload = inspect_email_file(
            args.path,
            root=args.root,
            max_message_bytes=args.max_message_bytes,
            max_parts=args.max_parts,
        )
        _write_manifest(payload, Path(args.output), Path(args.output_root))
        return 0
    except (OSError, TypeError, ValueError, PermissionError) as exc:
        parser.exit(2, f"email_intake: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
