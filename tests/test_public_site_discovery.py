"""Offline discovery contracts against real rendered pages and public docs."""
from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOSTS = ("Claude Code", "Codex", "CodeBuddy")
DISCOVERY = "npx skills add ayukyo/icode-skill --list"


class PageText(HTMLParser):
    """Read semantic HTML blocks without depending on CSS or exact copy."""

    def __init__(self, html):
        super().__init__()
        self.blocks = {}
        self.active = {}
        self.description = ""
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "description":
            self.description = attrs["content"]
        if tag in {"title", "h1", "p", "article", "code"}:
            self.active[tag] = []

    def handle_data(self, data):
        for parts in self.active.values():
            parts.append(data)

    def handle_endtag(self, tag):
        if tag in self.active:
            self.blocks.setdefault(tag, []).append(" ".join(self.active.pop(tag)))


class PublicDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "discovery_site_builder", ROOT / "tools/build_public_site.py"
        )
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix="icode-discovery-test-") as temp:
            output = Path(temp) / "public"
            # Call the real builder without inherited notification credentials.
            builder.build(ROOT, output, builder.DEFAULT_BASE, indexnow_key=None)
            cls.pages = {
                lang: PageText((output / path).read_text(encoding="utf-8"))
                for lang, path in (("en", "en/index.html"), ("zh-CN", "index.html"))
            }
        cls.readmes = {
            lang: (ROOT / path).read_text(encoding="utf-8")
            for lang, path in (("en", "README.md"), ("zh-CN", "README.zh-CN.md"))
        }

    def test_search_metadata_identifies_workflow_and_supported_hosts(self):
        for lang, page in self.pages.items():
            with self.subTest(lang=lang):
                for heading in page.blocks["title"] + page.blocks["h1"]:
                    self.assertRegex(heading, r"(?i)AI.*(?:coding workflow|编码工作流)")
                for host in HOSTS:
                    self.assertIn(host, page.description)
                self.assertRegex(page.description, r"(?i)ticket|工单")
                self.assertRegex(page.description, r"(?i)verif|验证")

    def test_rendered_features_explain_the_discovery_use_cases(self):
        concepts = (
            r"(?i)ticket[- ]based development|工单驱动开发",
            r"(?i)multi[- ]model code review|多模型代码审查",
            r"(?i)evidence|证据",
            r"(?i)resum|断点续接|断点续跑",
            r"(?i)local.*(?:UI|workspace)|本地.*(?:UI|工作台)",
        )
        for lang, page in self.pages.items():
            text = " ".join(page.blocks["article"])
            for concept in concepts:
                with self.subTest(lang=lang, concept=concept):
                    self.assertRegex(text, concept)

    def test_invocation_boundary_is_explicit_in_pages_and_readmes(self):
        for lang in self.pages:
            sources = {
                "page": " ".join(self.pages[lang].blocks["p"]),
                "readme": self.readmes[lang],
            }
            for source, text in sources.items():
                with self.subTest(lang=lang, source=source):
                    self.assertRegex(text, r"(?i)only.{0,80}(?:name|invoke).*ICODE|仅.{0,40}点名\s*ICODE")
                    self.assertRegex(text, r"(?i)(?:bound|绑定).{0,30}(?:ticket|工单)")
                    self.assertRegex(text, r"(?i)(?:does not|do not|never).{0,50}(?:ordinary|unrelated) requests|不.{0,15}接管.{0,20}普通请求")
        router = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("\n---", 1)[0]
        self.assertIn("已绑定 ICODE 工单", router)
        self.assertIn("不接管未指定 ICODE 的普通请求", router)

    def test_multimodel_review_explains_user_switch_before_crosscheck(self):
        for lang, page in self.pages.items():
            # Review features must state the prerequisite, not just repeat a command.
            reviews = [block for block in page.blocks["article"]
                       if "/icode crosscheck" in block
                       and re.search(r"(?i)completed|已完成", block)]
            with self.subTest(lang=lang):
                self.assertTrue(reviews, "explain crosscheck for completed tickets")
                self.assertRegex(" ".join(reviews), r"(?i)(?:yourself|manually)|自行|手动")
                self.assertRegex(" ".join(reviews), r"(?i)model|模型")
                self.assertRegex(" ".join(reviews), r"(?i)(?:does not|no|never).{0,20}automatic|不.{0,8}自动.{0,8}切换")

    def test_examples_offer_resuming_an_explicit_icode_ticket(self):
        for lang, page in self.pages.items():
            with self.subTest(lang=lang):
                examples = " ".join(page.blocks["code"])
                self.assertRegex(examples, r"(?i)(?:resume|continue).{0,60}ICODE.{0,30}ticket|续接.{0,30}ICODE.{0,20}工单")
                for command in ("/icode start ", "/icode verify --listen",
                                "/icode crosscheck", "/icode status --pending", "/icode ui"):
                    self.assertIn(command, examples)

    def test_readme_introductions_cover_hosts_and_workflow_uses(self):
        for lang, readme in self.readmes.items():
            intro = readme.split("\n## ", 1)[0]
            with self.subTest(lang=lang):
                for host in HOSTS:
                    self.assertIn(host, intro)
                self.assertRegex(intro, r"(?i)AI coding workflow|AI 编码工作流")
                self.assertRegex(intro, r"(?i)ticket[- ]based|工单驱动")
                self.assertRegex(intro, r"(?i)evidence|证据")
                self.assertRegex(intro, r"(?i)resum|断点续")
                self.assertIn("/icode crosscheck", intro)
                self.assertIn("/icode ui", intro)

    def test_readme_discovery_is_read_only_and_not_the_full_installer(self):
        for lang, readme in self.readmes.items():
            sections = re.split(r"\n#{2,3} ", readme)
            discovery = [section for section in sections if DISCOVERY in section]
            with self.subTest(lang=lang):
                self.assertEqual(len(discovery), 1, "one clear discovery section")
                section = discovery[0]
                self.assertIn("DISABLE_TELEMETRY=1", section)
                self.assertRegex(section, r"(?i)read[- ]only|只读")
                self.assertRegex(section, r"(?i)(?:does not|without).{0,30}install|不.{0,15}安装")
                self.assertRegex(section, r"(?i)shared skills|共享技能")
                for dependency in ("MCP", "DOCX runtime", "CodeBuddy"):
                    self.assertIn(dependency, section)
                self.assertRegex(section, r"(?i)command (?:bridge|adapter)|命令桥")
                self.assertIn("./install.sh --client all", section)

    def test_discovery_report_distinguishes_snapshots_from_indexing(self):
        report = (ROOT / "docs/public-discovery.md").read_text(encoding="utf-8")
        self.assertRegex(report, r"\d{4}-\d{2}-\d{2}.*快照")
        self.assertIn("skills@1.7.0", report)
        self.assertIn(DISCOVERY, report)
        self.assertIn("DISABLE_TELEMETRY=1", report)
        self.assertIn("Chat2AnyLLM/awesome-claude-skills", report)
        self.assertIn("metadata_catalog.py", report)
        self.assertRegex(report, r"待上游修复")

    def test_indexnow_report_requires_actual_notification_evidence(self):
        report = (ROOT / "docs/public-discovery.md").read_text(encoding="utf-8")
        self.assertIn("INDEXNOW_ENABLED=true", report)
        self.assertIn("INDEXNOW_KEY", report)
        self.assertRegex(report, r"绿色.{0,30}不等于.{0,20}通知成功")
        for evidence in ("summary", "outcome", "JSON", "received", "pending", "failed"):
            self.assertIn(evidence, report)
        self.assertRegex(report, r"不表示已收录|不代表.{0,10}收录")

    def test_publication_docs_require_main_push_and_current_source(self):
        report = (ROOT / "docs/public-discovery.md").read_text(encoding="utf-8")
        sources = {"guide": report}
        for lang, readme in self.readmes.items():
            sources[lang] = next((paragraph for paragraph in readme.split("\n\n")
                                  if "docs/public-discovery.md" in paragraph), "")
        for source, text in sources.items():
            with self.subTest(source=source):
                self.assertRegex(text, r"(?i)push.{0,30}main")
                self.assertRegex(text, r"(?i)automatic|自动")
                self.assertRegex(text, r"(?i)release")
                self.assertRegex(text, r"(?i)manual|手动")
                self.assertRegex(text, r"(?i)latest commit|最新提交")
                self.assertRegex(text, r"(?i)(?:PRs?.{0,25}(?:do not|never) publish)|PR.{0,15}不发布")
        self.assertIn("github.sha", report)
        self.assertRegex(report, r"默认分支.{0,15}HEAD")
        self.assertRegex(report, r"过期.{0,30}拒绝发布|拒绝.{0,15}过期")

    def test_packages_documentation_keeps_source_installer_as_delivery_path(self):
        report = (ROOT / "docs/public-discovery.md").read_text(encoding="utf-8")
        sections = [section for section in report.split("\n## ")
                    if "Packages" in section.split("\n", 1)[0]]
        self.assertEqual(len(sections), 1, "explain Packages separately from site publishing")
        section = sections[0]
        for concept in ("Release", "Pages", "npm", "GHCR", "install.sh"):
            self.assertIn(concept, section)
        self.assertRegex(section, r"当前不启用|目前不启用")
        self.assertIn("https://docs.github.com/en/packages/learn-github-packages/introduction-to-github-packages", section)


if __name__ == "__main__":
    unittest.main()
