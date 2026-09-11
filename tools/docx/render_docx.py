#!/usr/bin/env python3
"""Visual-render a DOCX only with an ICODE-owned compatible renderer."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from resolve_renderer import TOOL_DIR, resolve


def update_manifest(path: Path, status: str, **extra: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["visual_qa"] = {"status": status, **extra}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="render DOCX with ICODE-owned renderer")
    parser.add_argument("docx", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--manifest", type=Path, default=TOOL_DIR / "renderer_manifest.json")
    parser.add_argument("--result-manifest", type=Path)
    parser.add_argument("--dpi", type=int, default=144)
    args = parser.parse_args()
    resolved = resolve(args.manifest)
    if resolved["status"] != "ready":
        if args.result_manifest:
            update_manifest(args.result_manifest, "visual_qa_pending", resolver=resolved)
        print(json.dumps({"status": "visual_qa_pending", "resolver": resolved}, ensure_ascii=False))
        return 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    renderer = resolved["renderer"]
    try:
        with tempfile.TemporaryDirectory(prefix="icode-docx-render-") as temporary:
            temp = Path(temporary)
            subprocess.run([renderer["soffice"], "--headless", "--norestore", "--nologo", "--nofirststartwizard", "--convert-to", "pdf", "--outdir", str(temp), str(args.docx)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            pdfs = list(temp.glob("*.pdf"))
            if len(pdfs) != 1:
                raise RuntimeError("renderer did not produce exactly one PDF")
            subprocess.run([renderer["pdftoppm"], "-png", "-r", str(args.dpi), str(pdfs[0]), str(args.out_dir / "page")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        pages = sorted(str(path) for path in args.out_dir.glob("page-*.png"))
        if not pages:
            raise RuntimeError("renderer did not produce PNG pages")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        if args.result_manifest:
            update_manifest(args.result_manifest, "visual_qa_failed", resolver=resolved, error=str(exc))
        print(f"ERROR: DOCX visual render failed: {exc}", file=sys.stderr)
        return 1
    if args.result_manifest:
        update_manifest(args.result_manifest, "passed", renderer=renderer, pages=pages)
    print(json.dumps({"status": "passed", "pages": pages, "renderer": renderer}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
