"""Offline CSS contracts; browser layout and keyboard checks remain separate."""
from pathlib import Path
import re
import unittest


CSS_PATH = Path(__file__).resolve().parents[1] / "site/style.css"
PALETTE = {
    "--bg": "#FAFAF7", "--panel": "#FFFFFF", "--text": "#182230",
    "--muted": "#516071", "--accent": "#4F46E5", "--green": "#087F72",
    "--pending": "#946200",
}
GRID_SELECTORS = (
    ".hero", ".flow", ".grid", ".stage-grid", ".optional-grid", ".scenes",
    ".delivery-grid", ".install-grid", ".examples", ".trace",
)


def css_rules(source, media=""):
    """Read this site's flat rules / single-level media blocks, not computed CSS.

    Fail on unsupported or malformed syntax instead of silently skipping it.
    This intentionally small reader keeps the contracts dependency-free.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    block = re.compile(r"\s*([^{}]+)\{((?:[^{}]|\{[^{}]*\})*)\}")
    position = 0
    while source[position:].strip():
        match = block.match(source, position)
        if not match:
            raise AssertionError("Unsupported CSS near: " + source[position:position + 80])
        head, body = match.groups()
        head = head.strip()
        if head.startswith("@media"):
            if media:
                raise AssertionError("Nested media rules are outside the site contract")
            yield from css_rules(body, head)
        else:
            if head.startswith("@"):
                raise AssertionError("Unexpected CSS at-rule: " + head)
            declarations = {}
            for declaration in body.split(";"):
                if not declaration.strip():
                    continue
                name, colon, value = declaration.partition(":")
                if not colon or not value.strip():
                    raise AssertionError("Invalid CSS declaration: " + declaration)
                declarations[name.strip()] = value.strip()
            for selector in head.split(","):
                yield media, selector.strip(), declarations
        position = match.end()


def contrast(first, second):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                  for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


class PublicSiteStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.rules = list(css_rules(cls.css))

    def declarations(self, selector, width=1440, reduced=False):
        """Merge exact-selector declarations at a viewport, without DOM inference."""
        result = {}
        for media, candidate, declarations in self.rules:
            maximum = re.search(r"max-width\s*:\s*(\d+)px", media)
            minimum = re.search(r"min-width\s*:\s*(\d+)px", media)
            if maximum and width > int(maximum[1]):
                continue
            if minimum and width < int(minimum[1]):
                continue
            if "prefers-reduced-motion" in media and not reduced:
                continue
            if candidate == selector:
                result.update(declarations)
        return result

    def test_approved_light_palette(self):
        root = self.declarations(":root")
        self.assertEqual(root.get("color-scheme"), "light")
        for token, value in PALETTE.items():
            with self.subTest(token=token):
                self.assertEqual(root.get(token, "").upper(), value)

    def test_headings_do_not_use_negative_letter_spacing(self):
        for width in (360, 768, 1440):
            for selector in ("h1", "h2"):
                with self.subTest(width=width, selector=selector):
                    self.assertIn(self.declarations(selector, width).get("letter-spacing"),
                                  ("normal", "0"))

    def test_text_tokens_meet_aa_on_both_light_surfaces(self):
        root = self.declarations(":root")
        for foreground in ("--text", "--muted", "--accent", "--green", "--pending"):
            for background in ("--bg", "--panel"):
                with self.subTest(foreground=foreground, background=background):
                    self.assertIn(foreground, root)
                    self.assertGreaterEqual(contrast(root[foreground], root[background]), 4.5)

    def test_text_and_surfaces_use_contrast_checked_tokens(self):
        text_tokens = {f"var({name})" for name in PALETTE}
        surfaces = {"var(--bg)", "var(--panel)", "var(--accent)", "transparent", "none"}
        for _, selector, declarations in self.rules:
            if "color" in declarations:
                self.assertIn(declarations["color"], text_tokens | {"inherit"}, selector)
            if "background" in declarations:
                self.assertIn(declarations["background"], surfaces, selector)
            self.assertNotIn("opacity", declarations, selector)
        for selector in (".button", ".button:hover"):
            style = self.declarations(selector)
            self.assertEqual(style.get("color"), "var(--panel)")
            self.assertEqual(style.get("background"), "var(--accent)")

    def test_existing_and_new_component_contracts_have_rules(self):
        selectors = {selector for _, selector, _ in self.rules}
        required = {
            "header", "nav", ".brand", ".brand span", ".skip", ".skip:focus",
            ".hero", ".eyebrow", ".lead", ".actions", ".button", ".terminal",
            ".terminal-head", ".terminal-body", ".terminal-body .prompt", ".trace",
            ".trace li", ".trace b", ".tag", ".section", ".flow", ".flow li",
            ".grid", ".card", ".card p", ".note", ".install-grid", "pre", "code",
            ".examples", ".examples article", ".examples code", ".boundary",
            ".update", ".update time", "footer", "footer a", ".version",
            ".hosts", ".hosts li", ".stage-grid", ".stage", ".stage summary",
            ".step-number", ".stage summary strong", ".stage summary code",
            ".stage-body", ".stage-body p", ".stage-body dl", ".stage-body dt",
            ".stage-body dd", ".stage-body a", ".optional-grid", ".scenes",
            ".example-command", ".scenes details", ".principles", ".principles .grid",
            ".delivery-grid", ".pending", ".more-examples", ".more-examples .examples",
        }
        self.assertEqual(required - selectors, set())

    def test_stage_and_card_grids_adapt_at_required_viewports(self):
        for width, columns in ((360, 1), (768, 2), (1440, 3)):
            for selector in (".stage-grid", ".grid", ".optional-grid", ".scenes"):
                with self.subTest(width=width, selector=selector):
                    value = self.declarations(selector, width).get("grid-template-columns", "")
                    self.assertRegex(value, rf"repeat\(\s*{columns}\s*,\s*minmax\(0,\s*1fr\)\)")
        for width, columns in ((360, 1), (768, 2), (1440, 4)):
            self.assertRegex(self.declarations(".delivery-grid", width).get("grid-template-columns", ""),
                             rf"repeat\(\s*{columns}\s*,\s*minmax\(0,\s*1fr\)\)")

    def test_flow_has_six_desktop_cells_and_a_mobile_timeline(self):
        self.assertRegex(self.declarations(".flow").get("grid-template-columns", ""),
                         r"repeat\(6,\s*minmax\(0,\s*1fr\)\)")
        self.assertRegex(self.declarations(".flow", 360).get("grid-template-columns", ""),
                         r"repeat\(1,\s*minmax\(0,\s*1fr\)\)")
        self.assertIn("border-inline-start", self.declarations(".flow li", 360))

    def test_grid_items_and_header_can_shrink_without_page_clipping(self):
        for selector in GRID_SELECTORS:
            self.assertEqual(self.declarations(selector + " > *").get("min-width"), "0", selector)
        self.assertEqual(self.declarations(".install-grid > div").get("min-width"), "0")
        for selector in ("header", "nav", ".hosts"):
            self.assertEqual(self.declarations(selector).get("flex-wrap"), "wrap", selector)
        for _, selector, declarations in self.rules:
            for property_name in ("overflow", "overflow-x", "overflow-inline"):
                self.assertNotIn(declarations.get(property_name), ("hidden", "clip"), selector)

    def test_expanded_groups_keep_space_for_the_real_dom_intro_and_next_heading(self):
        self.assertEqual(self.declarations(".stage-grid").get("margin-bottom"), "2rem")
        for selector in (".principles > h3", ".more-examples > .note"):
            self.assertEqual(self.declarations(selector).get("padding-inline"), "1.25rem", selector)

    def test_long_commands_wrap_or_scroll_only_inside_code_blocks(self):
        self.assertEqual(self.declarations("pre").get("overflow-x"), "auto")
        self.assertEqual(self.declarations("pre").get("max-width"), "100%")
        for selector in ("code", ".example-command", ".examples code", ".stage summary code"):
            self.assertEqual(self.declarations(selector).get("overflow-wrap"), "anywhere", selector)
        for _, selector, declarations in self.rules:
            if declarations.get("overflow-x") in ("auto", "scroll"):
                self.assertIn(selector, ("pre", "code", ".example-command", ".examples code"))

    def test_focus_and_native_disclosures_keep_accessible_targets(self):
        for selector in ("a", "summary"):
            self.assertEqual(self.declarations(selector).get("min-height"), "44px", selector)
            self.assertEqual(self.declarations(selector).get("min-width"), "44px", selector)
        for selector in ("a:focus-visible", "summary:focus-visible"):
            self.assertRegex(self.declarations(selector).get("outline", ""), r"[2-9]px solid")
        self.assertEqual(self.declarations("summary").get("display"), "list-item")
        for _, selector, declarations in self.rules:
            if "summary" in selector:
                self.assertNotEqual(declarations.get("list-style"), "none", selector)
                self.assertNotEqual(declarations.get("display"), "none", selector)
        self.assertNotIn("::-webkit-details-marker", self.css)

    def test_terminal_is_a_static_light_illustration(self):
        self.assertEqual(self.declarations(".terminal").get("background"), "var(--panel)")
        self.assertEqual(self.declarations(".terminal-head").get("background"), "var(--bg)")
        self.assertEqual(self.declarations(".tag").get("color"), "var(--muted)")
        for _, selector, declarations in self.rules:
            if "content" in declarations:
                self.assertIn(selector, (".flow li::before", ".flow li:before"))

    def test_system_fonts_no_remote_resources_or_motion(self):
        self.assertIn("system-ui", self.declarations("body").get("font", ""))
        self.assertNotRegex(self.css, r"(?i)@import|@font-face|url\s*\(|expression\s*\(|javascript:")
        for _, selector, declarations in self.rules:
            for name, value in declarations.items():
                if name.startswith(("animation", "transition")):
                    self.assertEqual(value, "none !important", selector)
        reduced = [(selector, values) for media, selector, values in self.rules
                   if "prefers-reduced-motion" in media]
        self.assertTrue(reduced)
        for selector in ("*", "*::before", "*::after"):
            self.assertTrue(any(candidate == selector
                                and values.get("animation") == "none !important"
                                and values.get("transition") == "none !important"
                                for candidate, values in reduced), selector)
        self.assertEqual(self.declarations("html", reduced=True).get("scroll-behavior"),
                         "auto !important")

    def test_css_is_readable_component_source(self):
        self.assertLessEqual(max(map(len, self.css.splitlines())), 120)
        self.assertGreaterEqual(len(re.findall(r"/\*", self.css)), 5)


if __name__ == "__main__":
    unittest.main()
