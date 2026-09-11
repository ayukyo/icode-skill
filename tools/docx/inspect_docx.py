#!/usr/bin/env python3
"""Inspect a DOCX package and compare its basic structure with Markdown."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_RE = re.compile(r"^(#{1,6})\s+", re.M)
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)
IMAGE_RE = re.compile(r"^!\[[^]]*\]\([^)]+\)\s*$", re.M)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def markdown_expectations(source: Path) -> dict[str, int]:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()
    tables = sum(1 for index in range(len(lines) - 1) if lines[index].strip().startswith("|") and TABLE_SEPARATOR_RE.match(lines[index + 1]))
    mermaid = len(re.findall(r"^```mermaid\s*$", text, flags=re.M))
    return {"headings": len(HEADING_RE.findall(text)), "tables": tables, "images": len(IMAGE_RE.findall(text)) + mermaid}


def inspect(docx: Path) -> dict:
    with zipfile.ZipFile(docx) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
    paragraphs = document.findall(".//" + W_NS + "p")
    tables = document.findall(".//" + W_NS + "tbl")
    drawings = document.findall(".//" + DRAWING_NS + "drawing")
    heading_styles = {
        style.get(W_NS + "styleId") for style in styles.findall(W_NS + "style")
        if style.get(W_NS + "type") == "paragraph" and (style.get(W_NS + "styleId") or "").startswith("Heading")
    }
    return {
        "paragraphs": len(paragraphs),
        "tables": len(tables),
        "drawings": len(drawings),
        "media": len(media),
        "heading_styles": sorted(heading_styles),
        "sha256": sha256_file(docx),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="inspect a generated ICODE DOCX")
    parser.add_argument("docx", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        actual = inspect(args.docx)
        expected = markdown_expectations(args.source) if args.source else None
    except (OSError, zipfile.BadZipFile, UnicodeError, ElementTree.ParseError) as exc:
        print(f"ERROR: DOCX inspect failed: {exc}", file=sys.stderr)
        return 1
    checks: dict[str, bool] = {"package": actual["paragraphs"] > 0, "media_matches_drawings": actual["media"] >= actual["drawings"]}
    if expected:
        checks.update({
            "tables_preserved": actual["tables"] >= expected["tables"],
            "images_preserved": actual["drawings"] >= expected["images"],
            "headings_supported": bool(actual["heading_styles"]),
        })
    report = {"schema_version": 1, "docx": str(args.docx.resolve()), "actual": actual, "expected": expected, "checks": checks, "passed": all(checks.values())}
    if args.manifest:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        data["structural_qa"] = report
        temporary = args.manifest.with_suffix(args.manifest.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.manifest)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
