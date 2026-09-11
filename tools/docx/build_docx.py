#!/usr/bin/env python3
"""Deterministically convert a Markdown delivery document to DOCX.

This is deliberately a small, audited Markdown subset rather than a general
HTML converter: it supports the engineering documents ICODE emits and produces
a source map for every input block.  Unsupported Markdown remains visible as
plain Word text instead of being silently lost.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


TOOL_VERSION = "1.0.0"
IMAGE_RE = re.compile(r"^!\[([^]]*)\]\(([^)]+)\)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.+?)\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
MERMAID_NODE_RE = re.compile(r"([A-Za-z][\w.-]*)(?:\[([^]]+)]|\(([^)]+)\)|\{([^}]+)\})?")
MERMAID_EDGE_RE = re.compile(r"([A-Za-z][\w.-]*(?:\[[^]]+\]|\([^)]*\)|\{[^}]*\})?)\s*(?:-->|-.->|==>)\s*([A-Za-z][\w.-]*(?:\[[^]]+\]|\([^)]*\)|\{[^}]*\})?)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_baseline(source: Path) -> dict[str, Any] | None:
    directory = source.parent
    try:
        root = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"], check=True, text=True, capture_output=True).stdout.strip()
        head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", root, "status", "--porcelain"], check=True, text=True, capture_output=True).stdout.strip())
        return {"root": root, "head": head, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return None


def strip_inline_markdown(text: str) -> str:
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return text


def markdown_cell(cell: str) -> str:
    return strip_inline_markdown(cell.strip().strip("| "))


def add_code_paragraph(document: Document, content: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.style = document.styles["Normal"]
    paragraph.paragraph_format.left_indent = Inches(0.22)
    paragraph.paragraph_format.space_before = Pt(3)
    paragraph.paragraph_format.space_after = Pt(3)
    run = paragraph.add_run(content)
    run.font.name = "DejaVu Sans Mono"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans Mono CJK SC")
    run.font.size = Pt(8.5)


def font_for_mermaid():
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for item in candidates:
        if Path(item).is_file():
            return ImageFont.truetype(item, 28)
    return ImageFont.load_default()


def node_id_and_label(token: str) -> tuple[str, str]:
    match = MERMAID_NODE_RE.fullmatch(token.strip())
    if not match:
        return token.strip(), token.strip()
    identifier = match.group(1)
    return identifier, next((value for value in match.groups()[1:] if value), identifier)


def render_mermaid(source: str, destination: Path) -> tuple[bool, str]:
    """Render common flowchart syntax to a local PNG without Node or browser.

    It purposely supports node/edge flowcharts only.  Unsupported Mermaid is
    retained as code in the DOCX, which is safer than producing a misleading
    pseudo-diagram.
    """
    from PIL import Image, ImageDraw

    direction = "TB"
    first = source.splitlines()[0].strip() if source.splitlines() else ""
    direction_match = re.search(r"(?:flowchart|graph)\s+(LR|RL|TB|BT)\b", first, re.I)
    if direction_match:
        direction = direction_match.group(1).upper()
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []
    for match in MERMAID_EDGE_RE.finditer(source):
        left, right = node_id_and_label(match.group(1)), node_id_and_label(match.group(2))
        nodes[left[0]] = left[1]
        nodes[right[0]] = right[1]
        edges.append((left[0], right[0]))
    if not edges:
        return False, "仅支持包含有向边的 Mermaid flowchart/graph"
    node_keys = list(nodes)
    horizontal = direction in {"LR", "RL"}
    columns = len(node_keys) if horizontal else min(3, len(node_keys))
    rows = 1 if horizontal else (len(node_keys) + columns - 1) // columns
    cell_w, cell_h, margin = 260, 110, 56
    width = max(640, columns * cell_w + margin * 2)
    height = max(280, rows * cell_h + margin * 2)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = font_for_mermaid()
    positions: dict[str, tuple[int, int, int, int]] = {}
    for index, key in enumerate(node_keys):
        column = index if horizontal else index % columns
        row = 0 if horizontal else index // columns
        x0, y0 = margin + column * cell_w + 8, margin + row * cell_h + 16
        x1, y1 = x0 + cell_w - 32, y0 + 64
        positions[key] = (x0, y0, x1, y1)
    for left, right in edges:
        x0, y0, x1, y1 = positions[left]
        rx0, ry0, rx1, ry1 = positions[right]
        if horizontal:
            start, end = (x1, (y0 + y1) // 2), (rx0, (ry0 + ry1) // 2)
        else:
            start, end = ((x0 + x1) // 2, y1), ((rx0 + rx1) // 2, ry0)
        draw.line([start, end], fill="#2b6cb0", width=4)
        ex, ey = end
        if horizontal:
            points = [(ex, ey), (ex - 12, ey - 7), (ex - 12, ey + 7)]
        else:
            points = [(ex, ey), (ex - 7, ey - 12), (ex + 7, ey - 12)]
        draw.polygon(points, fill="#2b6cb0")
    for key, label in nodes.items():
        x0, y0, x1, y1 = positions[key]
        draw.rounded_rectangle((x0, y0, x1, y1), radius=12, fill="#edf6ff", outline="#2b6cb0", width=3)
        bbox = draw.multiline_textbbox((0, 0), label, font=font, spacing=4)
        tx = x0 + max(8, ((x1 - x0) - (bbox[2] - bbox[0])) // 2)
        ty = y0 + max(4, ((y1 - y0) - (bbox[3] - bbox[1])) // 2)
        draw.multiline_text((tx, ty), label, font=font, fill="#17324d", spacing=4)
    image.save(destination, format="PNG")
    return True, "built_in_flowchart"


def configure_styles(document: Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = "Aptos"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    for level, size in enumerate((20, 16, 14, 12, 11, 10), 1):
        style = document.styles[f"Heading {level}"]
        style.font.name = "Aptos Display"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
    if "ICODE Code" not in [style.name for style in document.styles]:
        code = document.styles.add_style("ICODE Code", WD_STYLE_TYPE.PARAGRAPH)
        code.font.name = "DejaVu Sans Mono"
        code._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans Mono CJK SC")
        code.font.size = Pt(8.5)


def add_table(document: Document, rows: list[list[str]]) -> None:
    width = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=width)
    table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for index in range(width):
            value = row[index] if index < len(row) else ""
            cells[index].text = markdown_cell(value)
            if row_index == 0:
                for run in cells[index].paragraphs[0].runs:
                    run.bold = True


def build(source: Path, output: Path, manifest_path: Path) -> dict[str, Any]:
    if source.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("输入必须是 .md 或 .markdown 文件")
    content = source.read_text(encoding="utf-8")
    lines = content.splitlines()
    document = Document()
    configure_styles(document)
    document.core_properties.title = source.stem
    source_map: list[dict[str, Any]] = []
    source_images: list[dict[str, str]] = []
    title_added = False
    index = 0
    with tempfile.TemporaryDirectory(prefix="icode-docx-assets-") as temp_dir:
        assets = Path(temp_dir)
        while index < len(lines):
            line = lines[index]
            line_number = index + 1
            if line.startswith("```") or line.startswith("~~~"):
                fence = line[:3]
                language = line[3:].strip().lower()
                code: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].startswith(fence):
                    code.append(lines[index])
                    index += 1
                raw_code = "\n".join(code)
                if language == "mermaid":
                    image = assets / f"mermaid-{line_number}.png"
                    rendered, detail = render_mermaid(raw_code, image)
                    if rendered:
                        paragraph = document.add_paragraph()
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        paragraph.add_run().add_picture(str(image), width=Inches(6.25))
                        source_map.append({"lines": [line_number, index + 1], "kind": "mermaid", "rendered_as": "image", "detail": detail})
                    else:
                        add_code_paragraph(document, raw_code)
                        source_map.append({"lines": [line_number, index + 1], "kind": "mermaid", "rendered_as": "code_fallback", "detail": detail})
                else:
                    add_code_paragraph(document, raw_code)
                    source_map.append({"lines": [line_number, index + 1], "kind": "code", "language": language})
                index += 1
                continue
            heading = HEADING_RE.match(line)
            if heading:
                level, value = len(heading.group(1)), strip_inline_markdown(heading.group(2))
                if level == 1 and not title_added:
                    paragraph = document.add_paragraph(value, style="Title")
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    document.core_properties.title = value
                    title_added = True
                else:
                    document.add_heading(value, level=level)
                source_map.append({"lines": [line_number, line_number], "kind": "heading", "level": level, "text": value})
                index += 1
                continue
            image_match = IMAGE_RE.match(line)
            if image_match:
                alt, target = image_match.groups()
                candidate = (source.parent / target).resolve()
                if candidate.is_file():
                    paragraph = document.add_paragraph()
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    paragraph.add_run().add_picture(str(candidate), width=Inches(6.25))
                    if alt:
                        caption = document.add_paragraph(alt)
                        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    source_images.append({"source": str(candidate), "sha256": sha256_file(candidate)})
                    source_map.append({"lines": [line_number, line_number], "kind": "image", "status": "embedded", "source": str(candidate)})
                else:
                    document.add_paragraph(f"[未找到图片：{target}] {alt}")
                    source_map.append({"lines": [line_number, line_number], "kind": "image", "status": "missing", "source": target})
                index += 1
                continue
            if line.strip().startswith("|") and index + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[index + 1]):
                rows = [line.strip().strip("|").split("|")]
                index += 2
                while index < len(lines) and lines[index].strip().startswith("|"):
                    rows.append(lines[index].strip().strip("|").split("|"))
                    index += 1
                add_table(document, rows)
                source_map.append({"lines": [line_number, index], "kind": "table", "rows": len(rows), "columns": max(len(row) for row in rows)})
                continue
            list_match = LIST_RE.match(line)
            if list_match:
                _, marker, value = list_match.groups()
                style = "List Number" if marker[0].isdigit() else "List Bullet"
                document.add_paragraph(strip_inline_markdown(value), style=style)
                source_map.append({"lines": [line_number, line_number], "kind": "list", "ordered": marker[0].isdigit()})
                index += 1
                continue
            if line.startswith(">"):
                document.add_paragraph(strip_inline_markdown(line.lstrip("> ")), style="Intense Quote")
                source_map.append({"lines": [line_number, line_number], "kind": "quote"})
                index += 1
                continue
            if not line.strip():
                index += 1
                continue
            paragraph_lines = [line.strip()]
            index += 1
            while index < len(lines) and lines[index].strip() and not HEADING_RE.match(lines[index]) and not IMAGE_RE.match(lines[index]) and not LIST_RE.match(lines[index]) and not lines[index].startswith(("```", "~~~", ">")):
                if lines[index].strip().startswith("|"):
                    break
                paragraph_lines.append(lines[index].strip())
                index += 1
            document.add_paragraph(strip_inline_markdown(" ".join(paragraph_lines)))
            source_map.append({"lines": [line_number, index], "kind": "paragraph"})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    document.save(temporary_output)
    temporary_output.replace(output)
    result = {
        "schema_version": 1,
        "generator": {"name": "icode-docx", "version": TOOL_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(source.resolve()), "sha256": sha256_file(source)},
        "output": {"path": str(output.resolve()), "sha256": sha256_file(output)},
        "git_baseline": git_baseline(source),
        "source_images": source_images,
        "source_map": source_map,
        "visual_qa": {"status": "not_requested"},
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="build a DOCX from ICODE-supported Markdown")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--manifest", type=Path, help="default: sibling manifest.json")
    args = parser.parse_args()
    manifest = args.manifest or args.output.with_name(args.output.stem + ".manifest.json")
    try:
        data = build(args.source.resolve(), args.output.resolve(), manifest.resolve())
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"ERROR: DOCX build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": data["output"], "manifest": str(manifest.resolve()), "source_blocks": len(data["source_map"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
