"""SVG safety, real rendering and evidence-cache contracts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import zlib

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools/docx/diagram_svg.py"


def api():
    assert MODULE.is_file(), "the shared SVG preparation API must exist"
    spec = importlib.util.spec_from_file_location("diagram_svg", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def svg(tmp_path, content="<rect width='100' height='50' fill='#123456'/>", attrs=""):
    source = tmp_path / "diagram.svg"
    source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" {attrs}>{content}</svg>', encoding="utf-8")
    return source


def test_validate_viewbox_without_render_runtime(tmp_path):
    report = api().validate_svg(svg(tmp_path))
    assert report["view_box"] == [0, 0, 100, 50]
    assert report["validation"]["svg_structure"] == "passed"


@pytest.mark.parametrize("body,attrs", [
    ("<script>alert(1)</script>", ""),
    ("<rect onclick='bad()'/>", ""),
    ("<style>rect { fill: red; }</style>", ""),
    ("<rect style='fill:url(file:///secret)'/>", ""),
    ("<image href='https://example.org/x.png'/>", ""),
    ("<foreignObject/>", ""),
    ("<use href='#a'/>", ""),
    ("<rect fill='url(data:image/png;base64,AA)'/>", ""),
    ("<rect fill='url(https://example.org/x)'/>", ""),
    ("<rect marker-end='url(#missing)'/>", ""),
    ("<defs><marker id='loop'><path d='M0 0 L1 1' marker-end='url(#loop)'/></marker></defs>", ""),
    ("<rect/>", "width='100' height='20'"),
    ("<rect x='NaN'/>", ""),
    ("<rect fill='notarealcolor'/>", ""),
    ("<rect fill='#12345'/>", ""),
    ("<path d='M0 0 L1 1' stroke='unknownpaint'/>", ""),
    ("<path d='M0 0 L1e999 1'/>", ""),
    ("<rect/>", "onload='bad()'"),
    ("<g xmlns='https://evil.invalid'/>", ""),
])
def test_rejects_unsafe_or_invalid_structure(tmp_path, body, attrs):
    with pytest.raises(ValueError):
        api().validate_svg(svg(tmp_path, body, attrs))


@pytest.mark.parametrize("prefix", [
    '<!DOCTYPE svg [<!ENTITY x "boom">]>',
    '<!DOCTYPE svg SYSTEM "https://example.org/evil">',
    '<?xml-stylesheet href="https://example.org/evil"?>',
])
def test_rejects_dtd_entities_and_processing_instructions(tmp_path, prefix):
    source = svg(tmp_path)
    source.write_text(prefix + source.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError):
        api().validate_svg(source)


def test_supports_markers_groups_and_tspans(tmp_path):
    source = svg(tmp_path, "<defs><marker id='arrow' markerWidth='10' markerHeight='10' refX='9' refY='3' orient='auto'><path d='M0 0 L9 3 L0 6 Z'/></marker></defs><g stroke='#123456'><line x1='0' y1='10' x2='80' y2='10' marker-end='url(#arrow)'/><text x='1' y='30'>中文<tspan dx='1'>ABC</tspan></text></g>")
    assert api().validate_svg(source)["validation"]["svg_structure"] == "passed"


def test_limits_bytes_nodes_depth_and_paths(tmp_path, monkeypatch):
    module = api()
    source = svg(tmp_path)
    monkeypatch.setattr(module, "MAX_SVG_BYTES", 20)
    with pytest.raises(ValueError, match="byte"):
        module.validate_svg(source)
    monkeypatch.setattr(module, "MAX_SVG_BYTES", 2_000_000)
    monkeypatch.setattr(module, "MAX_NODES", 2)
    with pytest.raises(ValueError, match="node"):
        module.validate_svg(svg(tmp_path, "<rect/><rect/>"))
    monkeypatch.setattr(module, "MAX_NODES", 100)
    monkeypatch.setattr(module, "MAX_DEPTH", 2)
    with pytest.raises(ValueError, match="depth"):
        module.validate_svg(svg(tmp_path, "<g><rect/></g>"))
    monkeypatch.setattr(module, "MAX_DEPTH", 64)
    monkeypatch.setattr(module, "MAX_PATH_CHARS", 4)
    with pytest.raises(ValueError, match="path"):
        module.validate_svg(svg(tmp_path, "<path d='M0 0 L1 1'/>"))


def test_render_real_png_cache_and_invalidation(tmp_path):
    module = api()
    source = svg(tmp_path)
    report = module.prepare_svg(source, tmp_path / "cache", width_inches=2, dpi=100, font_files=[])
    assert report["source"] == str(source.resolve())
    assert report["source_format"] == "svg"
    assert report["embedded_format"] == "png_fallback"
    assert report["vector_preserved"] is False
    assert report["renderer"]["name"] == "resvg-py"
    assert report["renderer"]["version"] == "0.5.0"
    assert report["validation"] == {"svg_structure": "passed", "fallback_render": "passed", "visual_review": "pending"}
    target = Path(report["fallback_png"])
    with Image.open(target) as image:
        assert image.size == (200, 100)
        assert image.convert("RGB").getpixel((50, 50)) == (0x12, 0x34, 0x56)
    assert report["fallback_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    stamp = target.stat().st_mtime_ns
    assert module.prepare_svg(source, tmp_path / "cache", width_inches=2, dpi=100, font_files=[]) == report
    assert target.stat().st_mtime_ns == stamp
    target.write_bytes(b"corrupted")
    repaired = module.prepare_svg(source, tmp_path / "cache", width_inches=2, dpi=100, font_files=[])
    assert hashlib.sha256(target.read_bytes()).hexdigest() == repaired["fallback_sha256"]
    source.write_text(source.read_text().replace("123456", "654321"))
    changed = module.prepare_svg(source, tmp_path / "cache", width_inches=2, dpi=100, font_files=[])
    assert changed["fallback_png"] != report["fallback_png"]
    assert module.prepare_svg(source, tmp_path / "cache", width_inches=3, dpi=100, font_files=[])["fallback_png"] != changed["fallback_png"]


@pytest.mark.parametrize("view_width,view_height,width_inches,dpi,expected", [
    (1400, 680, 6.25, 192, (1200, 583)),
    (1400, 680, 12.3, 192, (2362, 1147)),
    (100, 51, 2, 97, (194, 99)),
    (100, 49, 2, 97, (194, 95)),
])
def test_noninteger_aspect_ratio_uses_exact_canvas_without_stretch(tmp_path, monkeypatch, view_width, view_height, width_inches, dpi, expected):
    import resvg_py
    original = resvg_py.svg_to_bytes
    arguments = []
    def render(**kwargs):
        arguments.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(resvg_py, "svg_to_bytes", render)
    source = tmp_path / "rounded.svg"
    radius = min(view_width, view_height) / 4
    source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="10 20 {view_width} {view_height}" preserveAspectRatio="none"><circle cx="{10 + view_width / 2}" cy="{20 + view_height / 2}" r="{radius}" fill="red"/></svg>', encoding="utf-8")
    report = api().prepare_svg(source, tmp_path / "cache", width_inches=width_inches, dpi=dpi, font_files=[])
    with Image.open(report["fallback_png"]) as image:
        assert image.size == expected
        bounds = image.getbbox()
        assert abs((bounds[2] - bounds[0]) - (bounds[3] - bounds[1])) <= 1
    # One integer viewport performs the rounding; preserveAspectRatio keeps
    # source geometry intact instead of resizing a raster to the requested size.
    import xml.etree.ElementTree as ET
    rendered_root = ET.fromstring(arguments[0]["svg_string"])
    assert rendered_root.get("width") == str(expected[0])
    assert rendered_root.get("height") == str(expected[1])
    assert rendered_root.get("preserveAspectRatio") == "xMidYMid meet"
    assert "width" not in arguments[0] and "height" not in arguments[0]


@pytest.mark.parametrize("width,dpi", [(0, 192), (float("nan"), 192), (2, 0), (1000, 1000), (2, True)])
def test_rejects_invalid_or_excessive_output_size(tmp_path, width, dpi):
    with pytest.raises(ValueError):
        api().prepare_svg(svg(tmp_path), tmp_path / "cache", width_inches=width, dpi=dpi, font_files=[])


def test_text_without_font_and_missing_glyph_fail(tmp_path):
    module = api()
    source = svg(tmp_path, "<text x='1' y='20'>中文</text>")
    with pytest.raises(ValueError, match="font"):
        module.prepare_svg(source, tmp_path / "cache", font_files=[])
    latin = Path("C:/Windows/Fonts/arial.ttf")
    if not latin.is_file():
        pytest.skip("Latin font fixture unavailable")
    with pytest.raises(ValueError, match="glyph"):
        module.prepare_svg(source, tmp_path / "cache", font_files=[latin])


def test_explicit_chinese_font_real_render_and_env(tmp_path, monkeypatch):
    module = api()
    font = next((p for p in module.default_font_candidates() if p.is_file()), None)
    if not font:
        pytest.skip("CJK font fixture unavailable")
    source = svg(tmp_path, "<rect width='100' height='50' fill='white'/><text x='2' y='30' font-size='24'>中文ABC</text>")
    monkeypatch.setenv("ICODE_SVG_FONT_FILES", str(font))
    report = module.prepare_svg(source, tmp_path / "cache", width_inches=2, dpi=100)
    assert report["font_files"][0]["path"] == str(font.resolve())
    assert report["font_files"][0]["sha256"] == hashlib.sha256(font.read_bytes()).hexdigest()
    with Image.open(report["fallback_png"]) as image:
        assert len(image.convert("RGB").getcolors(maxcolors=10_000) or []) > 2


def test_rejects_reference_depth_even_when_targets_were_already_visited(tmp_path):
    markers = ["<marker id='m0'><path d='M0 0 L1 1'/></marker>"]
    markers += [f"<marker id='m{i}'><path d='M0 0 L1 1' marker-end='url(#m{i-1})'/></marker>" for i in range(1, 40)]
    with pytest.raises(ValueError, match="depth"):
        api().validate_svg(svg(tmp_path, "<defs>" + "".join(markers) + "</defs>"))


def test_marker_placement_cost_includes_path_vertices(tmp_path):
    marker_path = "M0 0 " + "L1 1 " * 200
    drawing_path = "M0 0 " + "L2 2 " * 1000
    source = svg(tmp_path, f"<defs><marker id='m'><path d='{marker_path}'/></marker></defs><path d='{drawing_path}' marker-mid='url(#m)'/>")
    with pytest.raises(ValueError, match="expansion"):
        api().validate_svg(source)


def test_marker_references_must_be_on_shapes_not_inherited_groups(tmp_path):
    source = svg(tmp_path, "<defs><marker id='m'><path d='M0 0 L1 1'/></marker></defs><g marker-end='url(#m)'><path d='M0 0 L1 1'/></g>")
    with pytest.raises(ValueError, match="marker"):
        api().validate_svg(source)


def test_limits_text(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setattr(module, "MAX_TEXT_CHARS", 10, raising=False)
    with pytest.raises(ValueError, match="text"):
        module.validate_svg(svg(tmp_path, "<text>" + "A" * 11 + "</text>"))


@pytest.mark.parametrize("width,dpi", [(10**1000, 192), (1, 10**1000), (1e-300, 10**300)])
def test_rejects_huge_integer_parameters(tmp_path, width, dpi):
    with pytest.raises(ValueError):
        api().prepare_svg(svg(tmp_path), tmp_path / "cache", width_inches=width, dpi=dpi, font_files=[])


def test_font_glyph_zero_is_not_accepted_as_coverage(tmp_path):
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    if not font_path.is_file():
        pytest.skip("Latin font fixture unavailable")
    from fontTools.ttLib import TTFont
    destination = tmp_path / "missing.ttf"
    with TTFont(font_path) as font:
        for table in font["cmap"].tables:
            if table.isUnicode():
                table.cmap[ord("A")] = ".notdef"
        font.save(destination)
    with pytest.raises(ValueError, match="glyph"):
        api().prepare_svg(svg(tmp_path, "<text x='1' y='20'>A</text>"), tmp_path / "cache", font_files=[destination])


def test_renderer_cannot_discover_system_fonts(tmp_path, monkeypatch):
    import resvg_py
    original = resvg_py.svg_to_bytes
    arguments = []
    def render(**kwargs):
        arguments.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(resvg_py, "svg_to_bytes", render)
    api().prepare_svg(svg(tmp_path), tmp_path / "cache", font_files=[])
    assert arguments[0]["skip_system_fonts"] is True
    assert arguments[0]["font_files"] == []
    assert "font_dirs" not in arguments[0] and "resources_dir" not in arguments[0]


def test_font_change_invalidates_cache(tmp_path):
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    if not font_path.is_file():
        pytest.skip("Latin font fixture unavailable")
    from fontTools.ttLib import TTFont
    destination = tmp_path / "local.ttf"
    destination.write_bytes(font_path.read_bytes())
    module = api()
    source = svg(tmp_path, "<text x='1' y='20'>ABC</text>")
    first = module.prepare_svg(source, tmp_path / "cache", font_files=[destination])
    with TTFont(destination) as font:
        font["head"].fontRevision += 0.01
        font.save(destination)
    second = module.prepare_svg(source, tmp_path / "cache", font_files=[destination])
    assert first["fallback_png"] != second["fallback_png"]
    assert first["font_files"][0]["sha256"] != second["font_files"][0]["sha256"]


def test_cache_requires_decodable_pixels_not_only_png_crc_and_hash(tmp_path):
    module = api()
    source = svg(tmp_path)
    first = module.prepare_svg(source, tmp_path / "cache", font_files=[])
    target = Path(first["fallback_png"])
    payload = target.read_bytes()
    output, offset = payload[:8], 8
    while offset < len(payload):
        size = struct.unpack(">I", payload[offset:offset + 4])[0]
        kind = payload[offset + 4:offset + 8]
        content = payload[offset + 8:offset + 8 + size]
        if kind == b"IDAT":
            content = b"\0" * size
        output += struct.pack(">I", size) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
        offset += size + 12
    target.write_bytes(output)
    metadata = target.with_suffix(".json")
    forged = json.loads(metadata.read_text(encoding="utf-8"))
    forged["fallback_sha256"] = hashlib.sha256(output).hexdigest()
    metadata.write_text(json.dumps(forged), encoding="utf-8")
    repaired = module.prepare_svg(source, tmp_path / "cache", font_files=[])
    assert repaired["fallback_sha256"] == first["fallback_sha256"]


def test_excessive_cached_png_dimensions_are_repaired_before_image_decode(tmp_path):
    module = api()
    source = svg(tmp_path)
    first = module.prepare_svg(source, tmp_path / "cache", font_files=[])
    target = Path(first["fallback_png"])
    payload = target.read_bytes()
    header = struct.pack(">II", 1_000_000, 1_000_000) + payload[24:29]
    forged = payload[:16] + header + struct.pack(">I", zlib.crc32(b"IHDR" + header)) + payload[33:]
    target.write_bytes(forged)
    repaired = module.prepare_svg(source, tmp_path / "cache", font_files=[])
    assert repaired["fallback_sha256"] == first["fallback_sha256"]


def test_missing_runtime_gives_bootstrap_instruction(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setitem(sys.modules, "resvg_py", None)
    with pytest.raises(RuntimeError, match="bootstrap_runtime.py"):
        module.prepare_svg(svg(tmp_path), tmp_path / "cache", font_files=[])


def test_cli_validate_and_convert(tmp_path):
    source = svg(tmp_path)
    for args in (["--validate-only"], ["--cache-dir", str(tmp_path / "cache"), "--width-inches", "2", "--dpi", "100"]):
        result = subprocess.run([sys.executable, str(MODULE), str(source), *args], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["validation"]["svg_structure"] == "passed"


def test_runtime_lock_and_probe_include_svg_dependencies(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("svg_bootstrap", ROOT / "tools/docx/bootstrap_runtime.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.locked_versions()["resvg_py"] == "0.5.0"
    assert module.locked_versions()["fonttools"] == "4.60.1"
    assert module.locked_versions().get("python_pptx") == "1.0.2"
    monkeypatch.setattr(module, "runtime_metadata", lambda _: {"lock_sha256": "abc"})
    monkeypatch.setattr(module, "venv_python", lambda _: Path(sys.executable))
    versions = module.locked_versions()
    captured = []
    def probe(command, **kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(versions), "")
    monkeypatch.setattr(module.subprocess, "run", probe)
    assert module.validate_runtime(tmp_path, "abc")[0]
    assert "resvg_py" in captured[0][2] and "fontTools" in captured[0][2]
    assert "pptx" in captured[0][2]
    versions["resvg_py"] = "wrong"
    assert not module.validate_runtime(tmp_path, "abc")[0]
    versions["resvg_py"] = "0.5.0"
    versions["python_pptx"] = "wrong"
    assert not module.validate_runtime(tmp_path, "abc")[0]
