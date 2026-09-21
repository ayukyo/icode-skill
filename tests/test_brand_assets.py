"""Contracts for the static ICODE brand asset and its published consumers."""

from pathlib import Path
import hashlib
import json
import struct
import xml.etree.ElementTree as ET
import zlib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SVG = ASSETS / "icode-ticket-hex.svg"
PNG_SMALL = ASSETS / "icode-ticket-hex-128.png"
PNG_LARGE = ASSETS / "icode-ticket-hex-512.png"
MANIFEST = ASSETS / "icode-ticket-hex.manifest.json"
RENDERER = ROOT / "tools/render_brand_assets.py"
APPROVED_COLORS = {"#12AAA8", "#FFC857", "#4FCB86", "#FFFFFF"}
ALLOWED_SVG_ELEMENTS = {"svg", "path", "circle", "g"}
ALLOWED_SVG_ATTRIBUTES = {
    "svg": {"viewBox", "role", "aria-label"},
    "path": {"data-role", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "d"},
    "circle": {"fill", "cx", "cy", "r"},
    "g": {"data-role"},
}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def png_header(path):
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[16:26])
    return width, height, bit_depth, color_type


def png_rgba(path):
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    compressed = bytearray()
    width = height = None
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        data = payload[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            assert (bit_depth, color_type, compression, filtering, interlace) == (8, 6, 0, 0, 0)
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
    assert width and height and compressed
    raw = zlib.decompress(bytes(compressed))
    stride = width * 4
    rows = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scanline = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        reconstructed = bytearray(stride)
        for index, value in enumerate(scanline):
            left = reconstructed[index - 4] if index >= 4 else 0
            up = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            else:
                raise AssertionError(f"Unsupported PNG filter: {filter_type}")
            reconstructed[index] = (value + predictor) & 0xFF
        rows.append(bytes(reconstructed))
        previous = reconstructed
    return width, height, b"".join(rows)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_svg(root):
    assert local_name(root.tag) == "svg"
    assert root.attrib["viewBox"] == "0 0 512 512"
    assert root.attrib.get("role") == "img"
    assert root.attrib.get("aria-label") == "ICODE workflow ticket"
    seen_roles = set()
    seen_colors = set()
    for element in root.iter():
        element_name = local_name(element.tag)
        assert element_name in ALLOWED_SVG_ELEMENTS
        assert not (element.text or "").strip(), "Brand SVG must not embed text"
        role = element.attrib.get("data-role")
        if role:
            seen_roles.add(role)
        for key, value in element.attrib.items():
            attribute = local_name(key)
            assert attribute in ALLOWED_SVG_ATTRIBUTES[element_name], (
                f"Unexpected {element_name} attribute: {attribute}"
            )
            lowered = value.lower()
            assert "url(" not in lowered and not lowered.lstrip().startswith("//")
            assert "://" not in lowered
            if value.upper() in APPROVED_COLORS:
                seen_colors.add(value.upper())
    return seen_roles, seen_colors


def test_svg_is_static_and_uses_approved_palette():
    root = ET.parse(SVG).getroot()
    seen_roles, seen_colors = validate_svg(root)
    assert seen_roles == {
        "ticket",
        "workflow-path",
        "brand-i",
        "start-node",
        "done-node",
        "done-check",
    }
    assert seen_colors == APPROVED_COLORS


@pytest.mark.parametrize(
    "attribute,value",
    (
        ("filter", "url(//attacker.invalid/filter.svg#x)"),
        ("style", "fill:url(data:image/svg+xml;base64,AAAA)"),
        ("href", "https://attacker.invalid/image.svg"),
        ("onclick", "alert(1)"),
    ),
)
def test_svg_contract_rejects_unapproved_or_external_attributes(attribute, value):
    root = ET.parse(SVG).getroot()
    next(iter(root)).set(attribute, value)
    with pytest.raises(AssertionError):
        validate_svg(root)


def test_png_derivatives_are_rgba_at_contract_sizes():
    assert png_header(PNG_SMALL) == (128, 128, 8, 6)
    assert png_header(PNG_LARGE) == (512, 512, 8, 6)
    for path in (PNG_SMALL, PNG_LARGE):
        width, height, pixels = png_rgba(path)
        alphas = pixels[3::4]
        assert min(alphas) == 0 and max(alphas) == 255
        corners = (0, width - 1, (height - 1) * width, height * width - 1)
        assert all(alphas[index] == 0 for index in corners)
        assert sum(alpha > 0 for alpha in alphas) > width * height // 4


def test_png_derivatives_are_bound_to_the_svg_source_by_manifest():
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["source"] == {
        "path": "assets/icode-ticket-hex.svg",
        "sha256": sha256(SVG),
    }
    assert document["renderer"]["name"] == "Google Chrome"
    assert document["renderer"]["version"]
    expected = {
        "assets/icode-ticket-hex-128.png": (PNG_SMALL, 128),
        "assets/icode-ticket-hex-512.png": (PNG_LARGE, 512),
    }
    assert set(document["outputs"]) == set(expected)
    for relative, (path, size) in expected.items():
        assert document["outputs"][relative] == {
            "sha256": sha256(path),
            "width": size,
            "height": size,
            "mode": "RGBA",
        }
    assert RENDERER.is_file()


def test_codex_metadata_uses_existing_relative_brand_assets():
    document = yaml.safe_load((ROOT / "agents/openai.yaml").read_text(encoding="utf-8"))
    interface = document["interface"]
    assert interface["brand_color"] == "#12AAA8"
    assert interface["icon_small"] == "./assets/icode-ticket-hex-128.png"
    assert interface["icon_large"] == "./assets/icode-ticket-hex-512.png"
    for field in ("icon_small", "icon_large"):
        value = Path(interface[field])
        assert not value.is_absolute() and ".." not in value.parts
        assert (ROOT / value).is_file()


def test_bilingual_readmes_show_the_workflow_icon_once_at_the_top():
    readmes = {
        "README.md": 'alt="ICODE workflow icon"',
        "README.zh-CN.md": 'alt="ICODE 工作流图标"',
    }
    for filename, alt in readmes.items():
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert text.count("assets/icode-ticket-hex.svg") == 1
        assert text.count(alt) == 1
        assert 'width="128"' in text
        assert text.index("assets/icode-ticket-hex.svg") < text.index("# ICode")


def test_tool_support_progress_records_brand_icon_sync_actions_and_boundaries():
    text = (ROOT / "docs/tool-support-progress.md").read_text(encoding="utf-8")
    section = text.split("## 品牌图标同步", 1)[1].split("\n## ", 1)[0]

    for action in (
        "自动部署",
        "关联仓库待刷新核验",
        "人工资料更新",
        "暂不重复提交",
    ):
        assert action in section

    for platform in (
        "GitHub Pages",
        "Context7",
        "SkillsMP",
        "agentskill.sh",
        "Smithery",
        "skills.sh",
        "SkillHub",
        "SkillKit.io",
    ):
        assert platform in section

    assert "`main` push" in section
    assert "固定提交" in section
    assert "不承诺实时" in section
    assert "载荷完整性" in section
    assert "不宣称完整安装通过" in section
