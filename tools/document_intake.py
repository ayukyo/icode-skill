#!/usr/bin/env python3
"""Build a read-only identity, integrity, and coverage manifest for a document corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
import zlib
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


class IntakeError(ValueError):
    """Raised when input or output boundaries are unsafe or invalid."""


TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".log", ".rst",
}
ARCHIVE_SUFFIXES = {".7z", ".zip", ".tar", ".gz", ".bz2", ".xz", ".rar"}
EXECUTABLE_SUFFIXES = {
    ".exe", ".dll", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1",
    ".sh", ".so", ".dylib", ".appimage",
}
KIND_SUFFIXES = {
    "pdf": {".pdf"},
    "ooxml_presentation": {".pptx", ".ppsx", ".potx"},
    "ooxml_document": {".docx", ".dotx"},
    "ooxml_spreadsheet": {".xlsx", ".xltx"},
    "seven_zip_container": {".7z"},
    "truncated_container": {".7z"},
    "zip_container": {".zip"},
    "png_image": {".png"},
    "jpeg_image": {".jpg", ".jpeg"},
    "tiff_image": {".tif", ".tiff"},
    "text": TEXT_SUFFIXES,
}
READY_STATUSES = {"readable", "readable_container"}
SEVEN_ZIP_MAGIC = bytes.fromhex("377abcaf271c")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def looks_like_text(sample: bytes) -> bool:
    if not sample or b"\0" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _unsafe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if not normalized or "\0" in normalized or normalized.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:/", normalized):
        return True
    return ".." in PurePosixPath(normalized).parts


def _member_flags(names: list[str]) -> dict:
    suspicious = [name for name in names if _unsafe_member(name)]
    executables = [name for name in names if Path(name).suffix.lower() in EXECUTABLE_SUFFIXES]
    nested = [name for name in names if Path(name).suffix.lower() in ARCHIVE_SUFFIXES]
    macro_members = [
        name for name in names
        if Path(name).name.lower() in {"vbaproject.bin", "vbadata.xml"}
        or "/macros/" in f"/{name.replace(chr(92), '/').lower()}"
    ]
    embedded_members = [
        name for name in names
        if any(marker in f"/{name.replace(chr(92), '/').lower()}" for marker in (
            "/embeddings/", "/activex/", "/oleobjects/",
        ))
    ]
    checksums = [name for name in names if Path(name).name.lower() in {
        "sha256.txt", "sha256sum.txt", "checksums.txt", "checksum.txt",
    } or Path(name).suffix.lower() in {".sha256", ".sha512"}]
    return {
        "suspicious_paths": suspicious[:50],
        "executable_members": executables[:50],
        "nested_archives": nested[:50],
        "macro_members": macro_members[:50],
        "embedded_members": embedded_members[:50],
        "checksum_sidecars": checksums[:50],
    }


def _archive_risks(summary: dict, policy: dict) -> list[str]:
    risks = []
    if summary.get("suspicious_paths"):
        risks.append("path_traversal_or_absolute_member")
    if summary.get("symlink_count", 0):
        risks.append("symlink_member")
    if summary.get("member_count", 0) > policy["archive_max_members"]:
        risks.append("member_count_limit_exceeded")
    if summary.get("total_uncompressed_bytes", 0) > policy["archive_max_unpacked"]:
        risks.append("declared_unpacked_size_limit_exceeded")
    ratio = summary.get("compression_ratio")
    if ratio is not None and ratio > policy["archive_max_ratio"]:
        risks.append("compression_ratio_limit_exceeded")
    return risks


def _ooxml_probe(archive: zipfile.ZipFile, kind: str, policy: dict) -> dict:
    prefixes = {
        "ooxml_presentation": ("ppt/presentation.xml", "ppt/slides/slide"),
        "ooxml_document": ("word/document.xml",),
        "ooxml_spreadsheet": ("xl/workbook.xml", "xl/worksheets/sheet"),
    }[kind]
    selected = [
        item for item in archive.infolist()
        if item.filename.endswith(".xml")
        and any(item.filename == prefix or item.filename.startswith(prefix) for prefix in prefixes)
    ]
    result = {
        "attempted": True,
        "selected_xml_parts": len(selected),
        "parsed_xml_parts": 0,
        "text_nodes": 0,
        "status": "failed",
        "gaps": [],
    }
    if not selected:
        result["gaps"].append("primary_ooxml_content_parts_missing")
        return result
    total = sum(item.file_size for item in selected)
    if total > policy["ooxml_max_xml_bytes"]:
        result["gaps"].append("ooxml_content_probe_limit_exceeded")
        return result
    for item in selected:
        if item.file_size > policy["ooxml_max_xml_bytes"]:
            result["gaps"].append(f"oversized_xml_part:{item.filename}")
            continue
        try:
            root = ET.fromstring(archive.read(item))
        except (ET.ParseError, KeyError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            result["gaps"].append(
                f"unreadable_xml_part:{item.filename}:{type(exc).__name__}")
            continue
        result["parsed_xml_parts"] += 1
        result["text_nodes"] += sum(bool((node.text or "").strip()) for node in root.iter())
    result["status"] = (
        "success" if result["parsed_xml_parts"] == len(selected) else "partial")
    return result


def inspect_zip(path: Path, policy: dict) -> tuple[str, str, dict]:
    inspection = {
        "recognized": True,
        "structurally_valid": False,
        "metadata_readable": False,
        "archive": None,
    }
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        inspection["error"] = f"{type(exc).__name__}: {exc}"
        return "invalid_zip_container", "corrupt", inspection

    total_uncompressed = sum(item.file_size for item in infos)
    total_compressed = sum(item.compress_size for item in infos)
    flags = _member_flags(names)
    archive_summary = {
        "format": "zip",
        "listing_status": "complete",
        "member_count": len(infos),
        "file_count": sum(not item.is_dir() for item in infos),
        "total_uncompressed_bytes": total_uncompressed,
        "total_compressed_bytes": total_compressed,
        "compression_ratio": (
            round(total_uncompressed / max(total_compressed, 1), 3)
            if total_uncompressed else 0.0
        ),
        "encrypted": any(item.flag_bits & 0x1 for item in infos),
        "symlink_count": sum(
            ((item.external_attr >> 16) & 0o170000) == 0o120000 for item in infos
        ),
        "member_sample": names[:25],
        **flags,
    }
    archive_summary["risks"] = _archive_risks(archive_summary, policy)
    inspection.update({
        "structurally_valid": True,
        "metadata_readable": True,
        "archive": archive_summary,
    })
    name_set = set(names)
    if "[Content_Types].xml" in name_set and "ppt/presentation.xml" in name_set:
        kind = "ooxml_presentation"
    elif "[Content_Types].xml" in name_set and "word/document.xml" in name_set:
        kind = "ooxml_document"
    elif "[Content_Types].xml" in name_set and "xl/workbook.xml" in name_set:
        kind = "ooxml_spreadsheet"
    else:
        kind = "zip_container"
    if archive_summary["encrypted"]:
        return kind, "requires_authorized_export", inspection
    if archive_summary["risks"]:
        return kind, "suspicious_container", inspection
    if not kind.startswith("ooxml_"):
        return kind, "readable_container", inspection
    if not policy["probe_content"]:
        inspection["content_probe"] = {
            "attempted": False,
            "status": "not_requested",
            "gaps": ["primary_ooxml_content_not_probed"],
        }
        return kind, "metadata_only", inspection
    try:
        with zipfile.ZipFile(path) as archive:
            content_probe = _ooxml_probe(archive, kind, policy)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        content_probe = {
            "attempted": True,
            "status": "failed",
            "gaps": [f"ooxml_reopen_failed:{type(exc).__name__}"],
        }
    inspection["content_probe"] = content_probe
    return kind, ("readable" if content_probe["status"] == "success" else "partial"), inspection


def _crc32_range(path: Path, offset: int, length: int) -> int:
    crc = 0
    remaining = length
    with path.open("rb") as stream:
        stream.seek(offset)
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise EOFError("unexpected end of file while reading 7z next header")
            crc = zlib.crc32(chunk, crc)
            remaining -= len(chunk)
    return crc & 0xFFFFFFFF


def _list_7z(path: Path, policy: dict) -> tuple[dict | None, dict]:
    adapter = {"name": "py7zr", "available": False, "version": None, "status": "not_installed"}
    try:
        py7zr = importlib.import_module("py7zr")
    except ImportError:
        return None, adapter
    adapter.update({
        "available": True,
        "version": getattr(py7zr, "__version__", "unknown"),
        "status": "failed",
    })
    try:
        with py7zr.SevenZipFile(path, mode="r") as archive:
            encrypted = bool(archive.needs_password())
            infos = archive.list()
    except Exception as exc:
        adapter["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return None, adapter
    adapter["status"] = "success"
    names = [str(getattr(item, "filename", "")) for item in infos]
    total_uncompressed = sum(int(getattr(item, "uncompressed", 0) or 0) for item in infos)
    total_compressed = sum(int(getattr(item, "compressed", 0) or 0) for item in infos)
    flags = _member_flags(names)
    summary = {
        "format": "7z",
        "listing_status": "complete",
        "member_count": len(infos),
        "file_count": sum(bool(getattr(item, "is_file", False)) for item in infos),
        "total_uncompressed_bytes": total_uncompressed,
        "total_compressed_bytes": total_compressed,
        "compression_ratio": (
            round(total_uncompressed / max(total_compressed, 1), 3)
            if total_uncompressed else 0.0
        ),
        "encrypted": encrypted,
        "symlink_count": sum(bool(getattr(item, "is_symlink", False)) for item in infos),
        "member_sample": names[:25],
        **flags,
    }
    summary["risks"] = _archive_risks(summary, policy)
    return summary, adapter


def inspect_7z(path: Path, size: int, policy: dict) -> tuple[str, str, dict]:
    inspection = {
        "recognized": True,
        "structurally_valid": False,
        "metadata_readable": False,
        "archive": {
            "format": "7z",
            "listing_status": "not_attempted",
        },
        "adapter_attempts": [],
    }
    if size < 32:
        inspection["error"] = "7z file is shorter than its 32-byte start header"
        return "truncated_container", "corrupt", inspection
    with path.open("rb") as stream:
        header = stream.read(32)
    version = [header[6], header[7]]
    expected_start_crc = struct.unpack("<I", header[8:12])[0]
    actual_start_crc = zlib.crc32(header[12:32]) & 0xFFFFFFFF
    next_offset, next_size, next_crc = struct.unpack("<QQI", header[12:32])
    declared_end = 32 + next_offset + next_size
    inspection["archive"].update({
        "version": version,
        "start_header_crc_valid": expected_start_crc == actual_start_crc,
        "next_header_offset": next_offset,
        "next_header_size": next_size,
        "declared_end": declared_end,
        "actual_size": size,
    })
    if expected_start_crc != actual_start_crc:
        inspection["error"] = "7z start header CRC mismatch"
        return "seven_zip_container", "corrupt", inspection
    if declared_end > size:
        inspection["error"] = "7z next header extends beyond the actual file"
        return "truncated_container", "corrupt", inspection
    if next_size <= policy["archive_max_header_bytes"]:
        try:
            actual_next_crc = _crc32_range(path, 32 + next_offset, next_size)
            inspection["archive"]["next_header_crc_valid"] = actual_next_crc == next_crc
        except (OSError, EOFError) as exc:
            inspection["error"] = f"{type(exc).__name__}: {exc}"
            return "truncated_container", "corrupt", inspection
        if actual_next_crc != next_crc:
            inspection["error"] = "7z next header CRC mismatch"
            return "seven_zip_container", "corrupt", inspection
    else:
        inspection["archive"]["next_header_crc_valid"] = None
        inspection["archive"]["next_header_crc_gap"] = "header exceeds inspection limit"

    inspection["structurally_valid"] = True
    summary, adapter = _list_7z(path, policy)
    inspection["adapter_attempts"].append(adapter)
    if summary is None:
        inspection["archive"]["listing_status"] = "parser_required"
        return "seven_zip_container", "parser_required", inspection
    inspection["archive"] = {**inspection["archive"], **summary}
    inspection["metadata_readable"] = True
    if summary["encrypted"]:
        return "seven_zip_container", "requires_authorized_export", inspection
    if summary["risks"]:
        return "seven_zip_container", "suspicious_container", inspection
    return "seven_zip_container", "readable_container", inspection


def _adapter_candidates(command: str) -> list[Path]:
    roots = []
    override = os.environ.get("DOCUMENT_INTAKE_ADAPTER_PATHS")
    if override:
        roots.extend(item for item in override.split(os.pathsep) if item)
    use_defaults = os.environ.get("DOCUMENT_INTAKE_SKIP_DEFAULT_PATHS") != "1"
    if use_defaults:
        roots.extend(item for item in os.environ.get("PATH", "").split(os.pathsep) if item)
        roots.extend(["/usr/bin", "/usr/local/bin"])
    candidates = []
    seen = set()
    for root in roots:
        candidate = (Path(root).expanduser() / command).resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            candidates.append(candidate)
    if use_defaults:
        fallback = shutil.which(command)
        if fallback:
            candidate = Path(fallback).resolve()
            if candidate not in seen:
                candidates.append(candidate)
    return candidates


def _run_adapter(command: str, arguments: list[str], timeout: int) -> tuple[bytes | None, list[dict]]:
    attempts = []
    for candidate in _adapter_candidates(command):
        try:
            completed = subprocess.run(
                [str(candidate), *arguments], capture_output=True,
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            attempts.append({
                "tool": command,
                "path": str(candidate),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
            continue
        attempt = {
            "tool": command,
            "path": str(candidate),
            "version": "unqueried",
            "binary_sha256": sha256_file(candidate),
            "status": "success" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
        }
        if completed.returncode != 0:
            attempt["error"] = completed.stderr.decode("utf-8", errors="replace")[:500]
        attempts.append(attempt)
        if completed.returncode == 0:
            return completed.stdout, attempts
    return None, attempts


def inspect_pdf(path: Path, policy: dict) -> tuple[str, str, dict]:
    inspection = {
        "recognized": True,
        "structurally_valid": None,
        "metadata_readable": False,
        "encrypted": None,
        "page_count": None,
        "text_extractable": None,
        "text_pages": None,
        "renderable": None,
        "render_pages": [],
        "adapter_attempts": [],
    }
    info_bytes, attempts = _run_adapter("pdfinfo", [str(path)], policy["adapter_timeout"])
    inspection["adapter_attempts"].extend(attempts)
    if info_bytes is None:
        with path.open("rb") as stream:
            sample = stream.read(65536)
            stream.seek(max(0, path.stat().st_size - 65536))
            tail = stream.read()
        if not sample.startswith(b"%PDF-") or b"%%EOF" not in tail or b"startxref" not in tail:
            inspection["structurally_valid"] = False
            inspection["error"] = "PDF header exists but trailer/startxref validation failed"
            return "pdf", "corrupt", inspection
        inspection["fallback_validation"] = "header_trailer_only"
        return "pdf", "parser_required", inspection

    info = info_bytes.decode("utf-8", errors="replace")
    pages = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE | re.IGNORECASE)
    encrypted = re.search(r"^Encrypted:\s+(yes|no)\b", info, re.MULTILINE | re.IGNORECASE)
    inspection.update({
        "structurally_valid": True,
        "metadata_readable": True,
        "page_count": int(pages.group(1)) if pages else None,
        "encrypted": encrypted.group(1).lower() == "yes" if encrypted else None,
    })
    if inspection["encrypted"]:
        return "pdf", "requires_authorized_export", inspection
    if not policy["probe_content"]:
        return "pdf", "metadata_only", inspection

    text_bytes, attempts = _run_adapter(
        "pdftotext", ["-layout", str(path), "-"], policy["adapter_timeout"])
    inspection["adapter_attempts"].extend(attempts)
    if text_bytes is not None:
        pages_text = text_bytes.split(b"\f")
        if pages_text and not pages_text[-1].strip():
            pages_text.pop()
        nonempty = sum(bool(page.strip()) for page in pages_text)
        inspection["text_extractable"] = nonempty > 0
        inspection["text_pages"] = nonempty

    with tempfile.TemporaryDirectory(prefix="icode-pdf-render-") as raw:
        prefix = str(Path(raw) / "page")
        _render, attempts = _run_adapter(
            "pdftoppm",
            ["-f", "1", "-l", "1", "-singlefile", "-png", str(path), prefix],
            policy["adapter_timeout"],
        )
        inspection["adapter_attempts"].extend(attempts)
        rendered = Path(prefix + ".png")
        if any(item["tool"] == "pdftoppm" for item in attempts):
            inspection["renderable"] = rendered.is_file() and rendered.stat().st_size > 0
            if inspection["renderable"]:
                inspection["render_pages"] = [1]
    if inspection["renderable"]:
        return "pdf", "readable", inspection
    if inspection["text_extractable"]:
        return "pdf", "partial", inspection
    return "pdf", "parser_required", inspection


def detect_kind(path: Path, size: int, policy: dict) -> tuple[str, str, dict]:
    if size == 0:
        return "empty", "empty", {"recognized": True, "structurally_valid": None}
    with path.open("rb") as stream:
        sample = stream.read(65536)
    if sample.startswith(b"%TSD-Header-###%"):
        return "protected_tsd_container", "requires_authorized_export", {
            "recognized": True,
            "structurally_valid": None,
            "protection": "TSD",
        }
    if sample.startswith(b"%PDF-"):
        return inspect_pdf(path, policy)
    if sample.startswith(SEVEN_ZIP_MAGIC):
        return inspect_7z(path, size, policy)
    if sample.startswith(b"PK\x03\x04") or sample.startswith(b"PK\x05\x06"):
        return inspect_zip(path, policy)
    if sample.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "ole_compound_document", "parser_required", {
            "recognized": True, "structurally_valid": None,
        }
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png_image", "readable", {"recognized": True, "structurally_valid": None}
    if sample.startswith(b"\xff\xd8\xff"):
        return "jpeg_image", "readable", {"recognized": True, "structurally_valid": None}
    if sample.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff_image", "readable", {"recognized": True, "structurally_valid": None}
    if looks_like_text(sample):
        return "text", "readable", {"recognized": True, "structurally_valid": None}
    return "unknown_binary", "unsupported", {"recognized": False, "structurally_valid": None}


def extension_matches(kind: str, suffix: str) -> bool | None:
    if kind == "protected_tsd_container":
        return False
    expected = KIND_SUFFIXES.get(kind)
    if expected is None:
        return None
    return suffix.lower() in expected


def revision_hints(name: str) -> list[str]:
    patterns = [
        r"(?<![a-z0-9])rev(?:ision)?[._ -]*[0-9]+(?:\.[0-9]+)*\b",
        r"(?<![a-z0-9])v[0-9]+(?:\.[0-9]+)+(?:\.[0-9]+)*\b",
        r"\b20[0-9]{2}[-_.][01]?[0-9][-_.][0-3]?[0-9]\b",
    ]
    lower = unicodedata.normalize("NFKC", name).lower()
    return sorted({match.group(0) for pattern in patterns for match in re.finditer(pattern, lower)})


def variant_key(name: str) -> str:
    value = unicodedata.normalize("NFKC", Path(name).stem).lower()
    value = re.sub(r"(?<![a-z0-9])rev(?:ision)?[._ -]*[0-9]+(?:\.[0-9]+)*\b", " ", value)
    value = re.sub(r"(?<![a-z0-9])v[0-9]+(?:\.[0-9]+)+(?:\.[0-9]+)*\b", " ", value)
    value = re.sub(r"\b20[0-9]{2}[-_.][01]?[0-9][-_.][0-3]?[0-9]\b", " ", value)
    value = re.sub(r"(?:copy|副本|final|最终版|最新版)", " ", value)
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value)
    anchor = next((index for index, token in enumerate(tokens)
                   if len(token) >= 4 and re.search(r"[a-z]", token)
                   and re.search(r"[0-9]", token)), None)
    if anchor is not None:
        tokens = tokens[anchor:]
    return "-".join(tokens)


def regular_file_row(path: Path, root: Path, policy: dict) -> dict:
    relative = path.relative_to(root).as_posix()
    try:
        stat = path.stat()
        kind, status, inspection = detect_kind(path, stat.st_size, policy)
        digest = sha256_file(path)
        error = inspection.get("error")
        size = stat.st_size
    except (OSError, ValueError, struct.error) as exc:
        kind, status, digest, size = "unreadable", "unreadable", None, None
        inspection = {"recognized": False, "structurally_valid": None}
        error = f"{type(exc).__name__}: {exc}"
    return {
        "relative_path": relative,
        "suffix": path.suffix.lower(),
        "size_bytes": size,
        "sha256": digest,
        "detected_kind": kind,
        "status": status,
        "extension_matches_kind": extension_matches(kind, path.suffix),
        "revision_hints": revision_hints(path.name),
        "variant_key": variant_key(path.name),
        "inspection": inspection,
        "error": error,
    }


def symlink_row(path: Path, root: Path) -> dict:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "suffix": path.suffix.lower(),
        "size_bytes": None,
        "sha256": None,
        "detected_kind": "symlink",
        "status": "skipped",
        "extension_matches_kind": None,
        "revision_hints": [],
        "variant_key": variant_key(path.name),
        "inspection": {"recognized": True, "structurally_valid": None},
        "error": "symlink_not_followed",
    }


def inventory(root: Path, excluded: set[Path], policy: dict) -> list[dict]:
    rows: list[dict] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        retained_dirs = []
        for name in sorted(dirs):
            path = current_path / name
            if path.is_symlink():
                rows.append(symlink_row(path, root))
            else:
                retained_dirs.append(name)
        dirs[:] = retained_dirs
        for name in sorted(files):
            path = current_path / name
            if path.absolute() in excluded:
                continue
            if path.is_symlink():
                rows.append(symlink_row(path, root))
            elif path.is_file():
                rows.append(regular_file_row(path, root, policy))
    return sorted(rows, key=lambda row: row["relative_path"])


def extraction_action(row: dict) -> str:
    status = row["status"]
    kind = row["detected_kind"]
    if status == "requires_authorized_export":
        return "request_authorized_export"
    if status == "corrupt":
        return "reacquire_and_verify_source_hash"
    if status == "suspicious_container":
        return "review_archive_risks_before_isolated_bounded_extraction"
    if status in {"unsupported", "unreadable", "parser_required"}:
        return "obtain_supported_export_or_parser"
    if status in {"skipped", "empty"}:
        return "no_extraction"
    if kind == "pdf":
        return "extract_text_then_render_only_visual_regions_with_provenance"
    if kind.startswith("ooxml_"):
        return "parse_standard_ooxml_without_macros_or_embedded_execution"
    if kind.endswith("_image"):
        return "route_visual_region_by_media_capability_with_provenance"
    if kind == "text":
        return "read_as_utf8_text"
    return "list_container_members_without_extraction_or_execution"


def corpus_groups(rows: list[dict]) -> dict:
    by_hash: dict[str, list[str]] = defaultdict(list)
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["sha256"]:
            by_hash[row["sha256"]].append(row["relative_path"])
        if row["variant_key"]:
            by_variant[row["variant_key"]].append(row)
    duplicate_groups = [
        {"sha256": digest, "paths": sorted(paths), "canonical_path": sorted(paths)[0]}
        for digest, paths in sorted(by_hash.items()) if len(paths) > 1
    ]
    variant_groups = []
    for key, members in sorted(by_variant.items()):
        hashes = {row["sha256"] for row in members if row["sha256"]}
        if len(members) < 2 or len(hashes) < 2:
            continue
        variant_groups.append({
            "variant_key": key,
            "members": [
                {
                    "path": row["relative_path"],
                    "sha256": row["sha256"],
                    "revision_hints": row["revision_hints"],
                }
                for row in members
            ],
            "canonical_path": None,
            "selection_required": True,
            "selection_rule": "choose per product/variant/revision/claim scope; latest filename is not authority",
        })
    return {"duplicate_groups": duplicate_groups, "variant_groups": variant_groups}


def archive_risk_register(rows: list[dict]) -> list[dict]:
    register = []
    for row in rows:
        archive = row["inspection"].get("archive")
        if not archive:
            continue
        register.append({
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "detected_kind": row["detected_kind"],
            "status": row["status"],
            "structurally_valid": row["inspection"].get("structurally_valid"),
            "archive": archive,
            "adapter_attempts": row["inspection"].get("adapter_attempts", []),
            "allowed_next_action": extraction_action(row),
        })
    return register


def build_manifest(root: Path, output_paths: set[Path], policy: dict) -> dict:
    rows = inventory(root, output_paths, policy)
    status_counts = Counter(row["status"] for row in rows)
    gaps = [row["relative_path"] for row in rows if row["status"] not in READY_STATUSES]
    return {
        "schema_version": 2,
        "source_root": str(root),
        "security": {
            "document_content_trusted": False,
            "embedded_content_executed": False,
            "macros_executed": False,
            "archives_extracted": False,
            "source_files_modified": False,
            "symlinks_followed": False,
        },
        "policy": policy,
        "summary": dict(sorted(status_counts.items())),
        "files": rows,
        "corpus_groups": corpus_groups(rows),
        "archive_risk_register": archive_risk_register(rows),
        "readability_gaps": gaps,
        "extraction_plan": [
            {"relative_path": row["relative_path"], "action": extraction_action(row)}
            for row in rows
        ],
        "verdict": "ready" if not gaps else "partial",
    }


def render_markdown(manifest: dict) -> str:
    lines = [
        "# Document Intake Manifest", "",
        f"- Source root: `{manifest['source_root']}`",
        f"- Verdict: `{manifest['verdict']}`",
        "- Security: content is untrusted; archives, macros, and embedded programs were not executed.",
        "",
        "| Path | Detected kind | Status | Structure | SHA-256 |",
        "|---|---|---|---|---|",
    ]
    for row in manifest["files"]:
        structural = row["inspection"].get("structurally_valid")
        lines.append(
            f"| `{row['relative_path']}` | `{row['detected_kind']}` | "
            f"`{row['status']}` | `{structural}` | `{row['sha256'] or '-'}` |"
        )
    duplicates = manifest["corpus_groups"]["duplicate_groups"]
    variants = manifest["corpus_groups"]["variant_groups"]
    if duplicates:
        lines.extend(["", "## Exact duplicate groups", ""])
        for group in duplicates:
            lines.append(f"- `{group['sha256']}`: " + ", ".join(
                f"`{path}`" for path in group["paths"]))
    if variants:
        lines.extend(["", "## Variant groups requiring scoped selection", ""])
        for group in variants:
            lines.append(f"- `{group['variant_key']}`: " + ", ".join(
                f"`{item['path']}`" for item in group["members"]))
    if manifest["readability_gaps"]:
        lines.extend(["", "## Readability gaps", ""])
        lines.extend(f"- `{path}`" for path in manifest["readability_gaps"])
    return "\n".join(lines) + "\n"


def validate_output(path: Path, output_root: Path) -> Path:
    if not path.is_absolute():
        raise IntakeError("output paths must be absolute")
    if path.is_symlink():
        raise IntakeError(f"output cannot be a symlink: {path}")
    try:
        parent = path.parent.resolve(strict=True)
        parent.relative_to(output_root)
    except (OSError, ValueError) as exc:
        raise IntakeError(f"output escapes --output-root: {path}") from exc
    return parent / path.name


def write_pair(json_path: Path, json_text: str, markdown_path: Path,
               markdown_text: str) -> None:
    previous = {
        json_path: json_path.read_bytes() if json_path.exists() else None,
        markdown_path: markdown_path.read_bytes() if markdown_path.exists() else None,
    }
    temporary: list[Path] = []
    replaced: list[Path] = []
    try:
        staged = []
        for target, content in ((json_path, json_text), (markdown_path, markdown_text)):
            handle, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp",
                                           dir=target.parent)
            temp_path = Path(raw)
            temporary.append(temp_path)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temp_path, target))
        for source, target in staged:
            os.replace(source, target)
            replaced.append(target)
    except OSError:
        for target in reversed(replaced):
            old = previous[target]
            if old is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(old)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def scan(args: argparse.Namespace) -> int:
    root_input = Path(args.root).expanduser()
    output_root_input = Path(args.output_root).expanduser()
    if root_input.is_symlink() or output_root_input.is_symlink():
        raise IntakeError("root paths cannot be symlinks")
    root = root_input.resolve(strict=True)
    output_root = output_root_input.resolve(strict=True)
    if not root.is_dir() or not output_root.is_dir():
        raise IntakeError("--root and --output-root must be directories")
    json_path = validate_output(Path(args.output).expanduser(), output_root)
    markdown_path = validate_output(Path(args.markdown).expanduser(), output_root)
    if json_path == markdown_path:
        raise IntakeError("--output and --markdown must be different files")
    excluded = {json_path.absolute(), markdown_path.absolute()}
    policy = {
        "probe_content": args.probe_content,
        "adapter_timeout": args.adapter_timeout,
        "archive_max_members": args.archive_max_members,
        "archive_max_unpacked": args.archive_max_unpacked,
        "archive_max_ratio": args.archive_max_ratio,
        "archive_max_header_bytes": args.archive_max_header_bytes,
        "ooxml_max_xml_bytes": args.ooxml_max_xml_bytes,
    }
    manifest = build_manifest(root, excluded, policy)
    json_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    write_pair(json_path, json_text, markdown_path, render_markdown(manifest))
    print(json.dumps({"ok": True, "files": len(manifest["files"]),
                      "verdict": manifest["verdict"]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="inventory a document corpus")
    scan_parser.add_argument("--root", required=True)
    scan_parser.add_argument("--output-root", required=True)
    scan_parser.add_argument("--output", required=True)
    scan_parser.add_argument("--markdown", required=True)
    scan_parser.add_argument("--probe-content", action="store_true",
                             help="probe PDF text extraction and first-page rendering")
    scan_parser.add_argument("--adapter-timeout", type=int, default=120)
    scan_parser.add_argument("--archive-max-members", type=int, default=100000)
    scan_parser.add_argument("--archive-max-unpacked", type=int, default=50 * 1024**3)
    scan_parser.add_argument("--archive-max-ratio", type=float, default=1000.0)
    scan_parser.add_argument("--archive-max-header-bytes", type=int, default=64 * 1024**2)
    scan_parser.add_argument("--ooxml-max-xml-bytes", type=int, default=64 * 1024**2)
    args = parser.parse_args()
    try:
        return scan(args)
    except (IntakeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
