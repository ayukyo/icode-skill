"""Reinstallation exercises real shell entrypoints with offline MCP fixtures."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def bash_binary():
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin/bash.exe" if git else Path("missing-bash")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def deployment(tmp_path):
    source, home = tmp_path / "source", tmp_path / "home"
    source.mkdir()
    home.mkdir()
    for rel in ("install.sh", "scripts/sync-to-global.sh", "tools/install_skill_pack.py",
                "tools/validate_skill_pack.py", "mcp/install.sh"):
        target = source / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    shutil.copytree(ROOT / "mcp/_lib", source / "mcp/_lib", ignore=shutil.ignore_patterns("__pycache__"))
    put(source / ".gitignore", "mcp/**/config.json\nmcp/**/.venv/\n**/__pycache__/\n")
    put(source / "SKILL.md", "---\nname: icode\ndescription: Use when testing offline installation.\n---\n# ICODE\n")
    put(source / "skill-packs/manifest.json", '{"schema_version":1,"skills":[]}\n')
    put(source / "mcp/workflow-gate/skill-routes.json", '{"schema_version":1,"routes":[]}\n')
    for name in ("bootstrap_runtime.py", "bootstrap_renderer.py"):
        put(source / "tools/docx" / name,
            "import os,sys\nsys.exit(7 if os.environ.get('FIXTURE_DOCX_FAIL') else 0)\n")
    for name in ("fixture-old", "fixture-current"):
        put(source / "mcp" / name / "server.py", "# Offline fixture; never launched by installation.\n")
        put(source / "mcp" / name / "install.sh", """#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$ICODE_PYTHON_BIN" - "$HERE" <<'PY'
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path.insert(0,str(root.parent/'_lib'))
import claude_registry
cfg=root/'config.json'
if not cfg.exists(): cfg.write_text(json.dumps({'local_data':root.name}),encoding='utf-8')
claude_registry.register(root.name,{'command':sys.executable,'args':[str(root/'server.py')]})
with Path(os.environ['FIXTURE_INSTALL_LOG']).open('a',encoding='utf-8') as log: log.write(root.name+'\\n')
PY
""")
    subprocess.run(["git", "init", "-q", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("ICODE_", "CLAUDE_", "AGENTS_", "WORKBUDDY_", "CODEBUDDY_")):
            environment.pop(key)
    environment.update(HOME=str(home), USERPROFILE=str(home),
        ICODE_PYTHON_BIN=sys.executable, ICODE_SYNC_ENGINE="cp",
        CLAUDE_SKILLS_ROOT=str(home / ".claude/skills"),
        AGENTS_SKILLS_ROOT=str(home / ".agents/skills"),
        FIXTURE_INSTALL_LOG=str(tmp_path / "installed.log"), PYTHONUTF8="1")
    if os.name == "nt":
        git_root = Path(shutil.which("git")).parents[1]
        system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
        tools = [Path(sys.executable).parent, git_root / "cmd", git_root / "usr/bin",
                 system_root / "System32", system_root]
        environment["PATH"] = os.pathsep.join(map(str, tools))
    else:
        environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.defpath
    environment["CODEX_HOME"] = str(home / ".codex")
    return source, home, environment


def run_install(deployment, *args):
    source, _, environment = deployment
    binary = bash_binary()
    if not binary:
        pytest.skip("Bash required for real installer entrypoint")
    return subprocess.run([binary, str(source / "install.sh"), "--client", "claude", *args],
        cwd=source, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)


def remove_old_source(deployment):
    source, _, _ = deployment
    names = ["mcp/fixture-old/install.sh", "mcp/fixture-old/server.py"]
    subprocess.run(["git", "-C", str(source), "rm", "--cached", "--", *names],
                   check=True, capture_output=True)
    for name in names:
        (source / name).unlink()


def servers(home):
    return json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]


def first_install(deployment):
    result = run_install(deployment)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reinstall_retires_old_mcp_and_preserves_config_archive(deployment):
    _, home, environment = deployment
    first_install(deployment)
    config = home / ".claude.json"
    value = json.loads(config.read_text(encoding="utf-8"))
    value.update(theme="dark")
    value["mcpServers"]["user-service"] = {"command": "user-owned"}
    config.write_text(json.dumps(value), encoding="utf-8")
    remove_old_source(deployment)
    result = run_install(deployment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture-old" not in servers(home)
    assert servers(home)["user-service"] == {"command": "user-owned"}
    assert json.loads(config.read_text(encoding="utf-8"))["theme"] == "dark"
    assert not (home / ".claude/skills/icode/mcp/fixture-old").exists()
    archives = [path for path in home.rglob("config.json")
                if "/skills/" not in path.as_posix() and "fixture-old" in path.parts]
    assert any(json.loads(path.read_text(encoding="utf-8")) == {"local_data": "fixture-old"}
               for path in archives), archives
    log = Path(environment["FIXTURE_INSTALL_LOG"]).read_text(encoding="utf-8").splitlines()
    assert log.count("fixture-old") == 1, log
    repeat = run_install(deployment)
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert "fixture-old" not in servers(home)


def test_failed_replacement_keeps_old_registration_and_payload(deployment):
    _, home, environment = deployment
    first_install(deployment)
    remove_old_source(deployment)
    environment["FIXTURE_DOCX_FAIL"] = "1"
    result = run_install(deployment)
    assert result.returncode != 0
    assert "fixture-old" in servers(home)
    assert (home / ".claude/skills/icode/mcp/fixture-old/server.py").is_file()


@pytest.mark.parametrize("option", ["--skip-mcp", "--no-delete"])
def test_deferred_mcp_cleanup_keeps_old_registered_entry_usable(deployment, option):
    _, home, _ = deployment
    first_install(deployment)
    remove_old_source(deployment)
    result = run_install(deployment, option)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture-old" in servers(home)
    assert (home / ".claude/skills/icode/mcp/fixture-old/server.py").is_file()


def test_dry_run_with_retired_mcp_does_not_write(deployment):
    _, home, _ = deployment
    first_install(deployment)
    remove_old_source(deployment)
    before = {str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in home.rglob("*") if path.is_file()}
    result = run_install(deployment, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    after = {str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mtime_ns)
             for path in home.rglob("*") if path.is_file()}
    assert after == before
    assert "fixture-old" in result.stdout


def test_single_mcp_keeps_the_full_distribution_catalog(deployment):
    source, home, environment = deployment
    first_install(deployment)
    put(source / "mcp/fixture-old/server.py", "# Updated unrelated current module.\n")
    result = run_install(deployment, "fixture-current")
    assert result.returncode == 0, result.stdout + result.stderr
    catalog = home / ".claude/skills/icode/mcp/.icode-mcp-desired.json"
    assert json.loads(catalog.read_text(encoding="utf-8"))["desired_names"] == [
        "fixture-current", "fixture-old"]
    assert "fixture-old" in servers(home)
    log = Path(environment["FIXTURE_INSTALL_LOG"]).read_text(encoding="utf-8").splitlines()
    assert log.count("fixture-old") == 1, log


def test_user_modified_retired_registration_is_retained_and_reported(deployment):
    _, home, _ = deployment
    first_install(deployment)
    config = home / ".claude.json"
    value = json.loads(config.read_text(encoding="utf-8"))
    value["mcpServers"]["fixture-old"] = {"command": "user-customized"}
    config.write_text(json.dumps(value), encoding="utf-8")
    remove_old_source(deployment)
    result = run_install(deployment)
    assert result.returncode != 0
    assert servers(home)["fixture-old"] == {"command": "user-customized"}
    assert (home / ".claude/skills/icode/mcp/fixture-old/server.py").is_file()
    assert "conflict" in result.stdout


def test_installing_another_host_does_not_stale_claude_receipt(deployment):
    source, home, environment = deployment
    first_install(deployment)
    before = (home / ".claude.json").read_bytes()
    (home / ".codebuddy").mkdir()
    result = subprocess.run([bash_binary(), str(source / "mcp/install.sh"), "--client", "codebuddy"],
        cwd=source, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".claude.json").read_bytes() == before
    repeat = run_install(deployment)
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr


def test_no_delete_retains_legacy_skill_without_silent_preflight_failure(deployment):
    _, home, _ = deployment
    first_install(deployment)
    old = home / ".claude/skills/fixture-legacy-skill"
    put(old / "SKILL.md", "user visible legacy skill\n")
    put(old / ".icode-skill-owner.json", json.dumps(
        {"schema_version": 1, "owner": "icode-skill", "skill": old.name}))
    result = run_install(deployment, "--no-delete")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (old / "SKILL.md").read_text(encoding="utf-8") == "user visible legacy skill\n"


def test_shared_skill_retirement_waits_for_the_complete_installation(deployment):
    source, home, environment = deployment
    def publish_skill(name):
        put(source / f"skill-packs/{name}/SKILL.md.template",
            f"---\nname: {name}\ndescription: Use when checking the joint success gate.\n---\n{name}\n")
        put(source / "skill-packs/manifest.json", json.dumps({"schema_version": 1, "skills": [
            {"name": name, "path": name, "entrypoint": "SKILL.md.template"}]}))
    publish_skill("fixture-skill-a")
    first_install(deployment)
    publish_skill("fixture-skill-c")
    remove_old_source(deployment)
    environment["FIXTURE_DOCX_FAIL"] = "1"
    result = run_install(deployment)
    assert result.returncode != 0
    assert (home / ".claude/skills/fixture-skill-a/SKILL.md").is_file()
    assert "fixture-old" in servers(home)
    environment.pop("FIXTURE_DOCX_FAIL")
    retry = run_install(deployment)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert not (home / ".claude/skills/fixture-skill-a").exists()
    assert (home / ".claude/skills/fixture-skill-c/SKILL.md").is_file()
    assert "fixture-old" not in servers(home)


def test_explicit_new_json_host_can_be_created_by_root_sync(deployment):
    source, home, _ = deployment
    put(source / "integrations/codebuddy/commands/icode.md", "# Offline ICODE command\n")
    assert not (home / ".codebuddy").exists()
    result = run_install(deployment, "--client", "codebuddy")
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads((home / ".codebuddy/mcp.json").read_text(encoding="utf-8"))
    assert set(value["mcpServers"]) == {"fixture-old", "fixture-current"}
    assert not (home / ".claude.json").exists()
