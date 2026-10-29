#!/usr/bin/env python3
"""Validate a restricted local SVG and prepare a reproducible PNG fallback.

No browser, network resources, CSS, images, use-elements or system-font discovery
are involved. Only explicit font files are loaded. Selection is --font/API,
ICODE_SVG_FONT_FILES (os.pathsep separated), then the first existing named CJK
candidate below. Fonts are local prerequisites, never downloaded or bundled.
Unsupported SVG constructs fail rather than being silently removed.

Limits: 2 MiB UTF-8 input, 10,000 nodes, depth 64, 500,000 path/points characters,
20,000 text characters, finite coordinates <= 10 million, 16 million output
pixels, 8192 pixels/side, output DPI 1..2400.
Marker properties belong on path/line/polygon/polyline, not inherited groups;
the expanded reference budget includes path/points and marker placements.
Structure/render success is evidence only; visual review remains pending.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_NODES = 10_000
MAX_DEPTH = 64
MAX_PATH_CHARS = 500_000
MAX_TEXT_CHARS = 20_000
MAX_COORDINATE = 10_000_000
MAX_PIXELS = 16_000_000
MAX_SIDE = 8192
RENDERER_VERSION = "0.5.0"
FONTTOOLS_VERSION = "4.60.1"
SVG_NS = "http://www.w3.org/2000/svg"
NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
NUM_RE = re.compile(NUMBER)
ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")
URL_RE = re.compile(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.-]{0,127})\s*\)")
# Fixed CSS color keywords keep validation independent of third-party packages.
COLOR_NAMES = set("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond
blue blueviolet brown burlywood cadetblue chartreuse chocolate coral
cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
darkgrey darkgreen darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod
gray grey green greenyellow honeydew hotpink indianred indigo ivory khaki
lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
lightgoldenrodyellow lightgreen lightgray lightgrey lightpink lightsalmon
lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue
lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue
mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod
palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon
sandybrown seagreen seashell sienna silver skyblue slateblue slategray
slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
wheat white whitesmoke yellow yellowgreen none currentcolor transparent
""".split())
COMMON = {
    "id", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "fill-rule",
    "opacity", "fill-opacity", "stroke-opacity", "transform", "font-family",
    "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline",
    "marker-start", "marker-mid", "marker-end", "visibility", "display", "color",
}
ELEMENT_ATTRS = {
    "svg": {"viewBox", "width", "height", "preserveAspectRatio"},
    "g": set(), "defs": set(), "title": set(), "desc": set(),
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"}, "ellipse": {"cx", "cy", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2"}, "polygon": {"points"},
    "polyline": {"points"}, "path": {"d"},
    "text": {"x", "y", "dx", "dy", "rotate"},
    "tspan": {"x", "y", "dx", "dy", "rotate"},
    "marker": {"viewBox", "markerWidth", "markerHeight", "refX", "refY", "orient", "markerUnits", "preserveAspectRatio"},
}
NUMERIC = {
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "width", "height", "dx", "dy", "rotate", "stroke-width", "stroke-miterlimit",
    "stroke-dashoffset", "font-size", "markerWidth", "markerHeight", "refX", "refY",
    "opacity", "fill-opacity", "stroke-opacity",
}
ENUMS = {
    "stroke-linecap": {"butt", "round", "square"},
    "stroke-linejoin": {"miter", "round", "bevel"},
    "fill-rule": {"nonzero", "evenodd"},
    "font-style": {"normal", "italic", "oblique"},
    "font-weight": {"normal", "bold", *map(str, range(100, 1000, 100))},
    "text-anchor": {"start", "middle", "end"},
    "dominant-baseline": {"auto", "alphabetic", "middle", "central", "hanging", "text-before-edge", "text-after-edge"},
    "markerUnits": {"strokeWidth", "userSpaceOnUse"},
    "visibility": {"visible", "hidden", "collapse"},
    "display": {"inline", "none"},
}


def _numbers(value: str) -> list[float]:
    tokens = NUM_RE.findall(value)
    if not tokens or NUM_RE.sub("", value).strip(" ,\t\r\n"):
        raise ValueError(f"invalid finite numeric value: {value[:80]}")
    numbers = [float(token) for token in tokens]
    if any(not math.isfinite(number) or abs(number) > MAX_COORDINATE for number in numbers):
        raise ValueError("non-finite or excessive SVG coordinate")
    return numbers


def _scalar(value: str) -> float:
    values = _numbers(value)
    if len(values) != 1:
        raise ValueError("SVG scalar expected")
    return values[0]


def _path(value: str) -> None:
    token_re = re.compile(rf"[MmZzLlHhVvCcSsQqTtAa]|{NUMBER}")
    tokens = token_re.findall(value)
    if not tokens or token_re.sub("", value).strip(" ,\t\r\n") or tokens[0] not in ("M", "m"):
        raise ValueError("invalid SVG path syntax")
    arities = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    index = 0
    while index < len(tokens):
        command = tokens[index].upper()
        if command not in arities:
            raise ValueError("SVG path command expected")
        index += 1
        end = index
        while end < len(tokens) and tokens[end].upper() not in arities:
            end += 1
        values = _numbers(" ".join(tokens[index:end])) if end > index else []
        arity = arities[command]
        if (arity == 0 and values) or (arity and (not values or len(values) % arity)):
            raise ValueError("invalid SVG path argument count")
        if command == "A":
            for offset in range(0, len(values), arity):
                if values[offset] < 0 or values[offset + 1] < 0 or values[offset + 3] not in (0, 1) or values[offset + 4] not in (0, 1):
                    raise ValueError("invalid SVG arc arguments")
        index = end


class _SafeTreeBuilder(ET.TreeBuilder):
    """Enforce XML resource limits while parsing, before building a large tree."""

    def __init__(self):
        super().__init__()
        self.nodes = self.depth = 0

    def start(self, tag, attrs):
        self.nodes += 1
        self.depth += 1
        if self.nodes > MAX_NODES:
            raise ValueError("SVG node limit exceeded")
        if self.depth > MAX_DEPTH:
            raise ValueError("SVG depth limit exceeded")
        return super().start(tag, attrs)

    def end(self, tag):
        result = super().end(tag)
        self.depth -= 1
        return result

    def doctype(self, name, pubid, system):
        raise ValueError("SVG DTD/entities are forbidden")

    def pi(self, target, text):
        raise ValueError("SVG processing instructions are forbidden")


def _local(tag: str) -> str:
    if tag.startswith("{" + SVG_NS + "}"):
        return tag[len(SVG_NS) + 2:]
    if "{" not in tag:
        return tag
    raise ValueError("unsupported SVG namespace")


def _check_attribute(name: str, value: str, tag: str) -> str | None:
    if name not in COMMON | ELEMENT_ATTRS[tag]:
        raise ValueError(f"unsupported SVG attribute: {name}")
    if name.startswith("marker-") and tag not in ("path", "line", "polygon", "polyline"):
        raise ValueError("SVG marker properties must be on explicit path/line/polygon/polyline shapes")
    if name == "id":
        if not ID_RE.fullmatch(value):
            raise ValueError("invalid SVG id")
    elif name in ("fill", "stroke", "color") or name.startswith("marker-"):
        reference = URL_RE.fullmatch(value)
        if reference:
            if not name.startswith("marker-"):
                raise ValueError("paint servers are outside the supported SVG subset")
            return reference[1]
        if name.startswith("marker-"):
            if value != "none":
                raise ValueError("marker must be none or a local url(#id)")
        elif value.casefold() not in COLOR_NAMES and not re.fullmatch(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", value):
            raise ValueError("SVG paint must be a color name or hex color")
    elif name in NUMERIC:
        raw = value[:-2] if value.endswith("px") and name in ("width", "height", "font-size", "stroke-width") else value
        values = _numbers(raw)
        if (tag not in ("text", "tspan") or name not in ("x", "y", "dx", "dy", "rotate")) and len(values) != 1:
            raise ValueError("SVG attribute requires one number")
        if name in ("width", "height", "r", "rx", "ry", "stroke-width", "font-size", "markerWidth", "markerHeight") and min(values) < 0:
            raise ValueError("negative SVG length")
        if name.endswith("opacity") and not 0 <= values[0] <= 1:
            raise ValueError("SVG opacity must be between zero and one")
    elif name == "viewBox":
        values = _numbers(value)
        if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
            raise ValueError("viewBox requires four numbers with positive dimensions")
    elif name == "d":
        _path(value)
    elif name == "points":
        values = _numbers(value)
        if len(values) % 2 or len(values) < (6 if tag == "polygon" else 4):
            raise ValueError("invalid SVG points")
    elif name == "transform":
        pattern = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)")
        matches = list(pattern.finditer(value))
        if not matches or pattern.sub("", value).strip(" ,\t\r\n"):
            raise ValueError("invalid SVG transform")
        counts = {"matrix": (6,), "translate": (1, 2), "scale": (1, 2), "rotate": (1, 3), "skewX": (1,), "skewY": (1,)}
        for match in matches:
            if len(_numbers(match[2])) not in counts[match[1]]:
                raise ValueError("invalid SVG transform arguments")
    elif name == "stroke-dasharray":
        if value != "none" and min(_numbers(value)) < 0:
            raise ValueError("negative SVG dash length")
    elif name == "orient":
        if value not in ("auto", "auto-start-reverse"):
            _scalar(value)
    elif name == "font-family":
        if len(value) > 256 or not re.fullmatch(r"[\w ,.'\"-]+", value, re.UNICODE):
            raise ValueError("invalid SVG font family")
    elif name == "preserveAspectRatio":
        if not re.fullmatch(r"none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max)(?:\s+(?:meet|slice))?", value):
            raise ValueError("invalid SVG preserveAspectRatio")
    elif name in ENUMS and value not in ENUMS[name]:
        raise ValueError(f"unsupported SVG {name}")
    return None


def _load_svg(source: Path) -> tuple[bytes, ET.Element, dict]:
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() != ".svg":
        raise ValueError("source must be a local .svg file")
    with source.open("rb") as stream:
        raw = stream.read(MAX_SVG_BYTES + 1)
    if len(raw) > MAX_SVG_BYTES:
        raise ValueError("SVG byte limit exceeded")
    try:
        root = ET.fromstring(raw.decode("utf-8-sig"), parser=ET.XMLParser(target=_SafeTreeBuilder()))
    except (ET.ParseError, UnicodeError) as exc:
        raise ValueError(f"invalid UTF-8 SVG XML: {exc}") from exc
    if _local(root.tag) != "svg":
        raise ValueError("SVG root element required")
    nodes = list(root.iter())
    parents = {child: parent for parent in nodes for child in parent}
    ids, refs, graph = {}, [], {node: [(child, 1) for child in node] for node in nodes}
    weights = {}
    path_chars = text_chars = 0
    for node in nodes:
        tag = _local(node.tag)
        if tag not in ELEMENT_ATTRS or (tag == "svg" and node is not root):
            raise ValueError(f"unsupported SVG element: {tag}")
        if tag == "tspan" and (node not in parents or _local(parents[node].tag) not in ("text", "tspan")):
            raise ValueError("SVG tspan must be inside text/tspan")
        if tag not in ("text", "tspan", "title", "desc") and node.text and node.text.strip():
            raise ValueError("SVG text must be inside a text/tspan element")
        text_chars += len(node.text or "") + len(node.tail or "")
        if text_chars > MAX_TEXT_CHARS:
            raise ValueError("SVG text character limit exceeded")
        for name, value in node.attrib.items():
            target = _check_attribute(name, value.strip(), tag)
            if target:
                refs.append((node, target, name))
            if name in ("d", "points"):
                path_chars += len(value)
        if path_chars > MAX_PATH_CHARS:
            raise ValueError("SVG path/points character limit exceeded")
        weights[node] = 1 + len(NUM_RE.findall(node.get("d", node.get("points", ""))))
        if "id" in node.attrib:
            if node.attrib["id"] in ids:
                raise ValueError("duplicate SVG id")
            ids[node.attrib["id"]] = node
    for node, target, attribute in refs:
        if target not in ids or _local(ids[target].tag) != "marker":
            raise ValueError("SVG reference must target a declared marker")
        # Mid markers repeat at vertices; start/end markers repeat per subpath.
        # Conservative numeric-token counts also cover implicit path commands.
        placements = max(1, weights[node] - 1) if attribute == "marker-mid" else max(1, len(re.findall(r"[Mm]", node.get("d", ""))))
        graph[node].append((ids[target], placements))
    active, costs = set(), {}
    def visit(node, depth=0):
        if node in active:
            raise ValueError("cyclic SVG reference")
        if depth > MAX_DEPTH:
            raise ValueError("SVG reference depth limit exceeded")
        if node in costs:
            if depth + costs[node][1] > MAX_DEPTH:
                raise ValueError("SVG reference depth limit exceeded")
            return costs[node]
        active.add(node)
        cost, height = weights[node], 0
        for child, placements in graph[node]:
            child_cost, child_height = visit(child, depth + 1)
            cost += child_cost * placements
            height = max(height, child_height + 1)
            if cost > MAX_NODES * 8:
                raise ValueError("SVG reference expansion limit exceeded")
        active.remove(node)
        costs[node] = (cost, height)
        return costs[node]
    visit(root)
    if "viewBox" not in root.attrib:
        raise ValueError("SVG requires an explicit viewBox")
    view_box = _numbers(root.attrib["viewBox"])
    width, height = root.get("width"), root.get("height")
    if bool(width) != bool(height):
        raise ValueError("SVG width and height must be specified together")
    if width and height:
        w, h = _scalar(width.removesuffix("px")), _scalar(height.removesuffix("px"))
        if w <= 0 or h <= 0 or not math.isclose(w / h, view_box[2] / view_box[3], rel_tol=1e-5):
            raise ValueError("SVG width/height must match the viewBox aspect ratio")
    report = {"source": str(source), "sha256": hashlib.sha256(raw).hexdigest(), "source_format": "svg", "view_box": view_box,
              "validation": {"svg_structure": "passed"}}
    return raw, root, report


def validate_svg(source: Path) -> dict:
    """Check structure without importing renderer dependencies or loading fonts."""
    return _load_svg(source)[2]


def default_font_candidates() -> list[Path]:
    """Named local prerequisites only; no font-directory discovery or downloads."""
    return [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]


def _runtime():
    try:
        import resvg_py
        import fontTools
        from fontTools.ttLib import TTCollection, TTFont
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("SVG runtime unavailable; run tools/docx/bootstrap_runtime.py and use its managed Python") from exc
    if resvg_py.__version__ != RENDERER_VERSION or fontTools.__version__ != FONTTOOLS_VERSION:
        raise RuntimeError("SVG runtime version mismatch; run tools/docx/bootstrap_runtime.py and use its managed Python")
    return resvg_py, TTCollection, TTFont, Image


def _fonts(font_files, root, collection_type, font_type):
    if font_files is None:
        configured = os.environ.get("ICODE_SVG_FONT_FILES")
        font_files = [Path(value) for value in configured.split(os.pathsep) if value] if configured else next(([p] for p in default_font_candidates() if p.is_file()), [])
    records, faces = [], []
    for path in dict.fromkeys(Path(p).expanduser().resolve() for p in font_files):
        if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError(f"SVG font missing or excessive: {path}")
        try:
            with path.open("rb") as stream:
                collection = stream.read(4) == b"ttcf"
            loaded = collection_type(str(path), lazy=True) if collection else font_type(str(path), lazy=True)
            fonts = loaded.fonts if collection else [loaded]
            names = []
            try:
                for font in fonts:
                    name = font["name"].getDebugName(16) or font["name"].getDebugName(1)
                    if not name:
                        raise ValueError("font has no family name")
                    faces.append((name, set(font.getBestCmap() or {})))
                    names.append(name)
            finally:
                loaded.close()
        except Exception as exc:
            raise ValueError(f"cannot inspect SVG font: {path}: {exc}") from exc
        records.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "families": names})
    generic = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"}
    def check(node, inherited=None):
        family = node.get("font-family", inherited)
        text = (node.text or "") if _local(node.tag) in ("text", "tspan") else ""
        text += "".join(child.tail or "" for child in node) if _local(node.tag) in ("text", "tspan") else ""
        chars = {ord(char) for char in text if not char.isspace()}
        if chars:
            if not faces:
                raise ValueError("SVG text requires an explicit local font; set ICODE_SVG_FONT_FILES or --font")
            requested = [name.strip().strip("'\"") for name in family.split(",")] if family else [faces[0][0]]
            requested = [faces[0][0] if name in generic else name for name in requested]
            chosen = [(name, glyphs) for name, glyphs in faces if name.casefold() in {n.casefold() for n in requested}]
            if not chosen:
                raise ValueError(f"SVG font family is not present in configured files: {family}")
            # resvg may fall back only among explicitly loaded files. Check every
            # visible codepoint against those same files before rendering.
            missing = chars - set().union(*(glyphs for _, glyphs in faces))
            if missing:
                raise ValueError("SVG font glyph coverage missing: " + ", ".join(f"U+{code:04X}" for code in sorted(missing)[:20]))
            node.set("font-family", ", ".join(dict.fromkeys(requested)))
        for child in node:
            check(child, family)
    check(root)
    return records, faces[0][0] if faces else None


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".svg-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _verify_png(payload: bytes, size: tuple[int, int], image_type) -> None:
    if len(payload) > MAX_PIXELS * 5 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("renderer did not produce a bounded PNG")
    # Inspect IHDR before Pillow allocates or applies its own global bomb limits.
    if len(payload) < 33 or payload[8:16] != b"\0\0\0\rIHDR" or struct.unpack(">II", payload[16:24]) != size:
        raise ValueError("SVG fallback PNG dimensions differ from requested dimensions")
    with image_type.open(io.BytesIO(payload)) as image:
        if image.format != "PNG" or image.size != size:
            raise ValueError("SVG fallback PNG dimensions differ from requested dimensions")
        image.verify()
    with image_type.open(io.BytesIO(payload)) as image:
        image.load()


def prepare_svg(source: Path, cache_dir: Path, *, width_inches: float = 6.25, dpi: int = 192,
                font_files: list[Path] | None = None) -> dict:
    """Validate/render SVG; return provenance with visual_review always pending."""
    _, root, report = _load_svg(source)
    if isinstance(width_inches, bool) or not isinstance(width_inches, (int, float)) or not 0 < width_inches <= MAX_SIDE or type(dpi) is not int or not 1 <= dpi <= 2400:
        raise ValueError("width_inches must be positive/finite and integer dpi must be 1..2400")
    pixels = width_inches * dpi
    if not math.isfinite(pixels) or pixels > MAX_SIDE:
        raise ValueError("SVG output pixel limit exceeded")
    pixel_width = max(1, round(pixels))
    aspect_height = pixel_width * report["view_box"][3] / report["view_box"][2]
    if not math.isfinite(aspect_height) or aspect_height > MAX_SIDE:
        raise ValueError("SVG output pixel limit exceeded")
    pixel_height = max(1, round(aspect_height))
    if pixel_width * pixel_height > MAX_PIXELS:
        raise ValueError("SVG output pixel limit exceeded")
    renderer, collection_type, font_type, image_type = _runtime()
    fonts, default_family = _fonts(font_files, root, collection_type, font_type)
    engine = {"name": "resvg-py", "version": renderer.__version__, "resvg_version": renderer.__resvg_version__}
    key_input = {"schema_version": 2, "source_sha256": report["sha256"], "renderer": engine,
                 "fonttools_version": FONTTOOLS_VERSION, "font_files": fonts, "pixel_width": pixel_width,
                 "pixel_height": pixel_height, "width_inches": width_inches, "dpi": dpi}
    key = hashlib.sha256(json.dumps(key_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cache_dir = Path(cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    png_path, metadata_path = cache_dir / (key + ".png"), cache_dir / (key + ".json")
    report.update({"fallback_png": str(png_path), "embedded_format": "png_fallback", "vector_preserved": False,
                   "pixel_width": pixel_width, "pixel_height": pixel_height, "renderer": engine, "font_files": fonts,
                   "width_inches": width_inches, "dpi": dpi,
                   "validation": {"svg_structure": "passed", "fallback_render": "passed", "visual_review": "pending"}})
    try:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
        with png_path.open("rb") as stream:
            payload = stream.read(MAX_PIXELS * 5 + 1)
        _verify_png(payload, (pixel_width, pixel_height), image_type)
        digest = hashlib.sha256(payload).hexdigest()
        if cached == {**report, "fallback_sha256": digest}:
            return cached
    except (OSError, ValueError, TypeError, SyntaxError):
        pass
    # resvg's width+height options perform a second aspect-fit calculation and
    # can round an already rounded dimension one pixel away from our contract.
    # Give it the exact integer viewport instead. SVG's meet transform preserves
    # geometry and absorbs the rounding difference as a subpixel transparent
    # border, without stretching or resampling the generated raster.
    root.set("width", str(pixel_width))
    root.set("height", str(pixel_height))
    root.set("preserveAspectRatio", "xMidYMid meet")
    try:
        payload = renderer.svg_to_bytes(svg_string=ET.tostring(root, encoding="unicode"),
                                        dpi=float(dpi), skip_system_fonts=True,
                                        font_files=[font["path"] for font in fonts], font_family=default_family,
                                        sans_serif_family=default_family, serif_family=default_family,
                                        monospace_family=default_family, cursive_family=default_family,
                                        fantasy_family=default_family)
    except Exception as exc:
        raise ValueError(f"SVG fallback rendering failed: {exc}") from exc
    _verify_png(payload, (pixel_width, pixel_height), image_type)
    report["fallback_sha256"] = hashlib.sha256(payload).hexdigest()
    _atomic_write(png_path, payload)
    _atomic_write(metadata_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--width-inches", type=float, default=6.25)
    parser.add_argument("--dpi", type=int, default=192)
    parser.add_argument("--font", type=Path, action="append")
    args = parser.parse_args()
    if not args.validate_only and args.cache_dir is None:
        parser.error("--cache-dir is required for conversion")
    try:
        report = validate_svg(args.source) if args.validate_only else prepare_svg(args.source, args.cache_dir,
            width_inches=args.width_inches, dpi=args.dpi, font_files=args.font)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR: SVG preparation failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
