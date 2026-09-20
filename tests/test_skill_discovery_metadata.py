"""Discovery metadata contracts, not a host/model intent-classification test.

The prompt cases guard the instructions exposed during skill selection. Actual
host selection and marketplace ranking still require separate live evaluation.
Synchronization uses the real script with all destinations under tmp_path.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def frontmatter():
    payload = (ROOT / "SKILL.md").read_bytes()
    match = re.match(rb"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", payload, re.S)
    assert match, "SKILL.md must expose YAML frontmatter"
    metadata = yaml.safe_load(match.group(1).decode("utf-8"))
    assert isinstance(metadata, dict)
    return metadata


def test_portable_metadata_has_valid_identity_and_size(frontmatter):
    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == "icode"
    description = frontmatter["description"]
    assert isinstance(description, str) and description.strip()
    assert len(description) <= 1024
    assert (ROOT / "SKILL.md").stat().st_size <= 50 * 1024


@pytest.mark.parametrize("query", [
    "AI coding workflow",
    "ticket-based development",
    "code review",
    "evidence verification",
    "resume",
])
def test_english_use_cases_are_exposed_before_loading_body(frontmatter, query):
    # These are real advertised uses, not host names or a new search engine.
    assert query.casefold() in frontmatter["description"].casefold()


@pytest.mark.parametrize("prompt,context,contract", [
    ("使用 ICODE 审查当前方案，审完就停。", "", "使用 ICODE"),
    ("Use ICODE for code review; do not edit files.", "", "Use ICODE"),
    ("继续。", "已绑定 ICODE 工单，当前授权步骤尚未完成", "已绑定 ICODE 工单的续接"),
    ("Review this code.", "没有 ICODE 工单上下文", "不接管未指定 ICODE 的普通请求"),
    ("ICODE 支持哪些能力？", "", "能力咨询只解释不执行"),
])
def test_selection_contracts_cover_prompt_boundaries(
    frontmatter, prompt, context, contract
):
    # The host interprets these prompts. Assert the published contract only;
    # do not introduce a test-only intent router and claim host behavior passed.
    assert contract in frontmatter["description"], (prompt, context, contract)


def test_codex_interface_is_valid_and_keeps_default_discovery():
    path = ROOT / "agents/openai.yaml"
    assert path.is_file(), "Codex UI metadata is missing"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert set(document) == {"interface"}, "No new invocation or dependency policy"
    interface = document["interface"]
    assert set(interface) == {"display_name", "short_description", "default_prompt"}
    assert all(isinstance(value, str) and value.strip() for value in interface.values())
    assert "ICODE" in interface["display_name"]
    assert 25 <= len(interface["short_description"]) <= 64
    assert re.search(r"\$icode\b", interface["default_prompt"])
    nodes = yaml.compose(path.read_text(encoding="utf-8"))
    for _, value in nodes.value[0][1].value:
        assert value.style == '"', "UI string values must be quoted"


@pytest.mark.parametrize("engine", ["rsync", "cp"])
def test_sync_carries_discovery_files_to_isolated_targets(tmp_path, engine):
    if engine == "rsync" and not shutil.which("rsync"):
        pytest.skip("rsync is unavailable; cp fallback is tested separately")
    source_metadata = ROOT / "agents/openai.yaml"
    assert source_metadata.is_file(), "Cannot publish missing Codex UI metadata"
    claude_root = tmp_path / "claude-skills"
    agents_root = tmp_path / "agents-skills"
    commands = tmp_path / "codebuddy-commands"
    # Every writable destination is explicit; inherited global target overrides
    # cannot redirect this test to the user's installed skills or command bridge.
    env = {
        **os.environ,
        "CLAUDE_SKILLS_ROOT": str(claude_root),
        "GLOBAL_DIR": str(claude_root / "icode"),
        "AGENTS_SKILLS_ROOT": str(agents_root),
        "AGENTS_DIR": str(agents_root / "icode"),
        "CODEBUDDY_COMMANDS_DIR": str(commands),
        "SKILL_PACK_MANIFEST": str(ROOT / "skill-packs/manifest.json"),
        "SKILL_ROUTES": str(ROOT / "mcp/workflow-gate/skill-routes.json"),
        "ICODE_SYNC_ENGINE": engine,
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def sync(mode):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/sync-to-global.sh"), mode, "--client", "all"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    sync("--dry-run")
    assert not list(tmp_path.iterdir()), "Initial dry-run must write nothing"
    sync("--apply")
    for target_root in (claude_root, agents_root):
        installed = target_root / "icode"
        for relative in ("SKILL.md", "agents/openai.yaml"):
            assert (installed / relative).read_bytes() == (ROOT / relative).read_bytes()
        assert not (installed / ".git").exists()
        assert not (installed / "tests").exists()
    assert (commands / "icode.md").read_bytes() == (
        ROOT / "integrations/codebuddy/commands/icode.md"
    ).read_bytes()
    # A second preview and apply must preserve the published metadata bytes.
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    sync("--dry-run")
    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert after == before, "Repeat dry-run must not modify the installed tree"
    sync("--apply")
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == {
        path: value[0] for path, value in before.items()
    }
