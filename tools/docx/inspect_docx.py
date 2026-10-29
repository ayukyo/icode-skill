#!/usr/bin/env python3
"""Inspect a DOCX package and compare its basic structure with Markdown."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
HEADING_RE = re.compile(r"^(#{1,6})\s+", re.M)
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)
IMAGE_RE = re.compile(r"^!\[[^]]*\]\([^)]+\)\s*$", re.M)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paragraph_content(paragraph: ElementTree.Element) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag in {W_NS + "t", "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"}:
            parts.append(node.text or "")
        elif node.tag in {W_NS + "br", W_NS + "cr"}:
            parts.append("\n")
        elif node.tag == W_NS + "tab":
            parts.append("\t")
    return "".join(parts)


def markdown_expectations(source: Path) -> dict:
    lines = source.read_text(encoding="utf-8").splitlines()
    headings, mermaid = [], []
    tables = images = 0
    title_added = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(("```", "~~~")):
            fence, language = line[:3], line[3:].strip().lower()
            code = []
            index += 1
            while index < len(lines) and not lines[index].startswith(fence):
                code.append(lines[index])
                index += 1
            if language == "mermaid":
                mermaid.append("\n".join(code))
        else:
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if heading:
                level, text = len(heading[1]), heading[2]
                text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
                text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
                text = text.replace("**", "").replace("__", "").replace("`", "")
                style = "Title" if level == 1 and not title_added else f"Heading{level}"
                title_added = title_added or level == 1
                headings.append({"style": style, "text": text})
            images += bool(IMAGE_RE.fullmatch(line))
            tables += bool(line.strip().startswith("|") and index + 1 < len(lines)
                           and TABLE_SEPARATOR_RE.match(lines[index + 1]))
        index += 1
    return {"headings": len(headings), "heading_content": headings, "tables": tables,
            "images": images, "mermaid": mermaid}


def inspect(docx: Path) -> dict:
    with zipfile.ZipFile(docx) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        relationships = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
        targets = {rel.get("Id"): posixpath.normpath("word/" + rel.get("Target", ""))
                   for rel in relationships.findall(REL_NS + "Relationship")
                   if rel.get("TargetMode") != "External"}
        blips = document.findall(".//" + A_NS + "blip")
        embedded = [targets.get(blip.get(R_NS + "embed"), "") for blip in blips]
        drawing_links_valid = all(target in media for target in embedded)
        media_hashes = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in media}
    paragraphs = document.findall(".//" + W_NS + "p")
    tables = document.findall(".//" + W_NS + "tbl")
    drawings = document.findall(".//" + W_NS + "drawing")
    headings = []
    paragraph_text = []
    for paragraph in paragraphs:
        text = paragraph_content(paragraph)
        # A code paragraph can contain literal newlines in a single w:t.
        paragraph_text.append(text)
        style = paragraph.find(W_NS + "pPr/" + W_NS + "pStyle")
        name = style.get(W_NS + "val", "") if style is not None else ""
        if name == "Title" or re.fullmatch(r"Heading[1-6]", name):
            headings.append({"style": name, "text": text})
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
        "headings": headings,
        "paragraph_text": paragraph_text,
        "drawing_links_valid": drawing_links_valid,
        "embedded_images": len(blips),
        "media_sha256": media_hashes,
        "sha256": sha256_file(docx),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="inspect a generated ICODE DOCX")
    parser.add_argument("docx", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--require-diagrams", action="store_true", help="reject Mermaid code fallback for authored deliveries")
    args = parser.parse_args()
    try:
        actual = inspect(args.docx)
        expected = markdown_expectations(args.source) if args.source else None
        data = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
        if data is not None:
            if not isinstance(data, dict) or not all(isinstance(data.get(key), dict) for key in ("input", "output")):
                raise ValueError("manifest requires input/output objects")
            if not isinstance(data.get("source_map", []), list) or not isinstance(data.get("source_images", []), list):
                raise ValueError("manifest source_map/source_images must be lists")
        checks = {"package": actual["paragraphs"] > 0,
                  "media_matches_drawings": actual["drawing_links_valid"] and actual["embedded_images"] >= actual["drawings"]}
        warnings = []
        fallbacks = [item for item in (data or {}).get("source_map", [])
                     if isinstance(item, dict) and item.get("kind") == "mermaid" and item.get("rendered_as") == "code_fallback"]
        if fallbacks:
            warnings.append("Mermaid retained as code; diagram authoring/review is still required")
        if expected:
            available_codes = Counter(actual["paragraph_text"])
            fallback_codes = []
            for code in expected["mermaid"]:
                if available_codes[code]:
                    fallback_codes.append(code)
                    available_codes[code] -= 1
            # Count each source occurrence, not the number of unique embedded media files.
            checks.update({"tables_preserved": actual["tables"] >= expected["tables"],
                           "images_preserved": actual["drawings"] >= expected["images"] + len(expected["mermaid"]) - len(fallback_codes),
                           "headings_preserved": actual["headings"] == expected["heading_content"]})
            if fallback_codes and not fallbacks:
                warnings.append("Mermaid source is visible as code instead of a diagram")
            if args.require_diagrams:
                checks["diagrams_rendered"] = not fallback_codes and not fallbacks
        elif args.require_diagrams:
            if data is None:
                raise ValueError("--require-diagrams needs --source or --manifest")
            checks["diagrams_rendered"] = not fallbacks
        if data is not None:
            source = args.source or Path(data["input"]["path"])
            checks.update({"input_hash_matches": sha256_file(source) == data["input"]["sha256"],
                           "output_hash_matches": actual["sha256"] == data["output"]["sha256"],
                           "output_path_matches": args.docx.resolve() == Path(data["output"]["path"]).resolve(),
                           "input_path_matches": source.resolve() == Path(data["input"]["path"]).resolve()})
            checks["source_images_unchanged"] = all(sha256_file(Path(item["source"])) == item["sha256"]
                                                   for item in data.get("source_images", []))
            checks["source_images_embedded"] = all(item.get('fallback_sha256', item["sha256"]) in actual["media_sha256"].values()
                                                  for item in data.get("source_images", []))
            diagrams = [item for item in data.get('source_images', []) if item.get('source_format') == 'svg']
            if diagrams:
                from diagram_ooxml import inspect_diagram_package
                checks.update(inspect_diagram_package(args.docx, diagrams))
            qa = data.get("visual_qa", {})
            if qa.get("status") == "passed":
                if qa.get("docx_sha256"):
                    checks["render_matches_word"] = qa["docx_sha256"] == actual["sha256"]
                else:
                    warnings.append("Legacy render record has no DOCX hash; render current Word again before delivery")
                if qa.get("page_sha256"):
                    checks["render_pages_unchanged"] = all(sha256_file(Path(path)) == value for path, value in qa["page_sha256"].items())
        report = {"schema_version": 1, "docx": str(args.docx.resolve()), "actual": actual,
                  "expected": expected, "checks": checks, "warnings": warnings, "passed": all(checks.values()),
                  "scope": "structure and artifact integrity; content correctness and page readability require review"}
        if data is not None:
            data["structural_qa"] = report
            temporary = args.manifest.with_suffix(args.manifest.suffix + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(args.manifest)
    except (OSError, zipfile.BadZipFile, UnicodeError, ElementTree.ParseError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"ERROR: DOCX inspect failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
