"""Real isolated counterexamples: make -n is not a read-only discovery API."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "preflight_workspace", ROOT / "mcp/icode-workspace/server.py")
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


@pytest.mark.parametrize("makefile", [
    "PROBE := $(shell touch observed)\nall:\n\t@echo ordinary\n",
    "all:\n\t+touch observed\n",
    "all:\n\ttouch observed; $(MAKE) -f child.mk\n",
], ids=["parse-time-shell", "forced-recipe", "recursive-recipe"])
def test_static_profile_does_not_execute_even_when_dry_run_would(tmp_path, monkeypatch, makefile):
    if not shutil.which("make"):
        pytest.skip("GNU make is required for this isolated counterexample")
    # All commands are fixed test data, confined to this fresh fixture.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Makefile").write_text(makefile, encoding="utf-8")
    (repo / "child.mk").write_text("all:\n\t@echo child\n", encoding="utf-8")
    environment = {"PATH": os.defpath, "HOME": str(tmp_path),
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "init", "-q", str(repo)], env=environment, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "Makefile", "child.mk"],
                   env=environment, check=True)
    config = tmp_path / "workspace.json"
    config.write_text(json.dumps({"allowed_roots": [str(tmp_path)]}), encoding="utf-8")
    monkeypatch.setenv("ICODE_WORKSPACE_CONFIG", str(config))
    before = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    answer = workspace.inspect_project_profile(str(repo))["answer"]
    assert not answer["probe_policy"]["execute_build_entrypoint_for_discovery"]
    assert any(item["path"] == "Makefile" for item in answer["build_entrypoints"])
    assert before == {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    # Positive control proves this fixture detects execution, not merely a
    # harmless script that would leave no trace if accidentally invoked.
    subprocess.run(["make", "-n", "all"], cwd=repo, env=environment,
                   capture_output=True, check=True, timeout=5)
    assert (repo / "observed").is_file()
