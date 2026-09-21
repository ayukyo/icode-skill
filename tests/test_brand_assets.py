"""Contracts for the static ICODE brand asset and its published consumers."""

from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SVG = ASSETS / "icode-ticket-hex.svg"
PNG_SMALL = ASSETS / "icode-ticket-hex-128.png"
PNG_LARGE = ASSETS / "icode-ticket-hex-512.png"
APPROVED_COLORS = {"#12AAA8", "#FFC857", "#4FCB86", "#FFFFFF"}
ALLOWED_SVG_ELEMENTS = {"svg", "path", "circle", "g"}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def png_header(path):
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[16:26])
    return width, height, bit_depth, color_type


def test_svg_is_static_and_uses_approved_palette():
    root = ET.parse(SVG).getroot()
    assert local_name(root.tag) == "svg"
    assert root.attrib["viewBox"] == "0 0 512 512"
    assert root.attrib.get("role") == "img"
    assert root.attrib.get("aria-label") == "ICODE workflow ticket"
    seen_roles = set()
    seen_colors = set()
    for element in root.iter():
        assert local_name(element.tag) in ALLOWED_SVG_ELEMENTS
        assert not (element.text or "").strip(), "Brand SVG must not embed text"
        role = element.attrib.get("data-role")
        if role:
            seen_roles.add(role)
        for key, value in element.attrib.items():
            assert not local_name(key).lower().startswith("on")
            assert local_name(key).lower() not in {"href", "style"}
            assert "http://" not in value and "https://" not in value
            if value.upper() in APPROVED_COLORS:
                seen_colors.add(value.upper())
    assert seen_roles == {
        "ticket",
        "workflow-path",
        "brand-i",
        "start-node",
        "done-node",
        "done-check",
    }
    assert seen_colors == APPROVED_COLORS


def test_png_derivatives_are_rgba_at_contract_sizes():
    assert png_header(PNG_SMALL) == (128, 128, 8, 6)
    assert png_header(PNG_LARGE) == (512, 512, 8, 6)


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
