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
