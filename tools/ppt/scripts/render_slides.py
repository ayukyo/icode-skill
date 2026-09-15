#!/usr/bin/env python3
"""Render every slide of a .pptx to PNG files using LibreOffice + pdftoppm.

Usage:
    python3 render_slides.py <input.pptx> <out_dir> [--dpi 144]

Output:
    <out_dir>/slide-<N>.png  (1-indexed；pdftoppm 可能按总页数补零，如 slide-01.png)
"""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def render(pptx: Path, out_dir: Path, dpi: int = 144) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pptrender_") as td:
        td_path = Path(td)
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--norestore",
                "--nologo",
                "--nofirststartwizard",
                "--convert-to",
                "pdf",
                "--outdir",
                str(td_path),
                str(pptx),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        pdfs = list(td_path.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError("soffice did not produce a PDF")
        pdf = pdfs[0]
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(dpi),
                str(pdf),
                str(out_dir / "slide"),
            ],
            check=True,
        )
    pngs = sorted(out_dir.glob("slide-*.png"))
    return pngs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--dpi", type=int, default=144)
    args = ap.parse_args()
    try:
        pngs = render(args.pptx, args.out_dir, args.dpi)
        if not pngs:
            raise RuntimeError("renderer did not produce any slide PNGs")
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        # Host renderer incompatibility is a failed render, not a visual pass.
        # Preserve tool diagnostics instead of hiding them behind a traceback.
        detail = getattr(exc, "stderr", None)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        print(f"渲染失败：{exc}", file=sys.stderr)
        if detail:
            print(str(detail).strip(), file=sys.stderr)
        return 1
    print(f"Rendered {len(pngs)} slides → {args.out_dir}")
    for p in pngs:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
