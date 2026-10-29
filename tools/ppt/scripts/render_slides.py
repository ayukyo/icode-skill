#!/usr/bin/env python3
"""Render every slide of a .pptx to PNG files using LibreOffice + pdftoppm.

Usage:
    python3 render_slides.py <input.pptx> <out_dir> [--dpi 144] [--managed]

--managed resolves the sibling DOCX owned open-source toolchain. It never
falls back to executables on PATH; bootstrap the managed renderer first.

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


def _resolve_managed_renderer() -> dict:
    component_dir = Path(__file__).resolve().parents[2] / 'docx'
    hint = 'run tools/docx/bootstrap_renderer.py first (managed mode has no PATH fallback)'
    if not (component_dir / 'resolve_renderer.py').is_file():
        raise RuntimeError(f'missing sibling tools/docx/resolve_renderer.py; {hint}')
    # Import only for an explicit managed request, from this toolset's sibling.
    sys.path.insert(0, str(component_dir))
    try:
        from resolve_renderer import resolve
        result = resolve()
    except ImportError as exc:
        raise RuntimeError(f'cannot load tools/docx/resolve_renderer.py: {exc}; {hint}') from exc
    finally:
        sys.path.remove(str(component_dir))
    if not isinstance(result, dict) or result.get('status') != 'ready':
        raise RuntimeError(f'managed renderer is not ready: {result}; {hint}')
    try:
        owned = result['renderer']
        bundle = Path(owned['bundle']).resolve()
        if not bundle.is_dir() or not isinstance(owned['id'], str) or not owned['id']:
            raise ValueError('invalid owned bundle or renderer id')
        for name in ('soffice', 'pdftoppm'):
            binary = Path(owned[name])
            if (not binary.is_absolute() or not binary.is_file() or
                    not binary.resolve().is_relative_to(bundle)):
                raise ValueError(f'{name} must be an absolute file inside the owned bundle')
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f'invalid owned renderer: {exc}; {hint}') from exc
    return owned


def render(pptx: Path, out_dir: Path, dpi: int = 144, *, renderer: dict | None = None) -> list[Path]:
    if type(dpi) is not int or dpi <= 0:
        raise ValueError('DPI must be a positive integer')
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pptrender_") as td:
        td_path = Path(td)
        soffice = renderer['soffice'] if renderer is not None else 'soffice'
        pdftoppm = renderer['pdftoppm'] if renderer is not None else 'pdftoppm'
        # Owned launchers may change directory inside their managed bundle.
        input_path = pptx.resolve() if renderer is not None else pptx
        run_options = {'timeout': 300} if renderer is not None else {}
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--nologo",
                "--nofirststartwizard",
                "--convert-to",
                "pdf",
                "--outdir",
                str(td_path),
                str(input_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            **run_options,
        )
        pdfs = list(td_path.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError("soffice did not produce a PDF")
        if renderer is not None and len(pdfs) != 1:
            raise RuntimeError('owned soffice did not produce exactly one PDF')
        pdf = pdfs[0]
        prefix = (td_path if renderer is not None else out_dir) / 'slide'
        subprocess.run(
            [
                pdftoppm,
                "-png",
                "-r",
                str(dpi),
                str(pdf),
                str(prefix),
            ],
            check=True,
            **({'stdout': subprocess.DEVNULL, 'stderr': subprocess.PIPE, **run_options}
               if renderer is not None else {}),
        )
        if renderer is not None:
            # Count only this invocation's pages; stale output cannot make an
            # unsuccessful managed render appear to have produced PNGs.
            generated = sorted(td_path.glob('slide-*.png'))
            if not generated:
                raise RuntimeError('owned renderer did not produce any slide PNGs')
            pages = []
            for path in generated:
                target = out_dir / path.name
                shutil.copyfile(path, target)
                pages.append(target)
            return pages
    pngs = sorted(out_dir.glob("slide-*.png"))
    return pngs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--dpi", type=int, default=144)
    ap.add_argument('--managed', action='store_true', help='use only the sibling DOCX owned renderer')
    args = ap.parse_args()
    try:
        if args.dpi <= 0:
            raise ValueError('DPI must be a positive integer')
        owned = _resolve_managed_renderer() if args.managed else None
        # Preserve the original three-argument call for existing integrations.
        if args.managed:
            pngs = render(args.pptx, args.out_dir, args.dpi, renderer=owned)
        else:
            pngs = render(args.pptx, args.out_dir, args.dpi)
        if not pngs:
            raise RuntimeError("renderer did not produce any slide PNGs")
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        # Host renderer incompatibility is a failed render, not a visual pass.
        # Preserve tool diagnostics instead of hiding them behind a traceback.
        detail = getattr(exc, "stderr", None)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        print(f"渲染失败：{exc}", file=sys.stderr)
        if detail:
            print(str(detail).strip(), file=sys.stderr)
        return 1
    if args.managed:
        print(f"Managed renderer: {owned['id']}")
    print(f"Rendered {len(pngs)} slides → {args.out_dir}")
    for p in pngs:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
