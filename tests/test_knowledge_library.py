import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from knowledge_library import LibraryError, catalog, config_path, configure, private_root, publish, resolve_root
from lint_knowledge_article import validate


ARTICLE = """# 状态机与事件重放

## 问题
进程重启后，怎样恢复上一次已确认的状态？

## 核心原理
把事件视为状态变化的输入，按确定顺序重放同一序列。

## 流程图或对比表
| 阶段 | 输入 | 输出 |
|---|---|---|
| 重放 | 历史事件 | 当前状态 |

表中每一行说明重放只改变内存中的状态投影。

## 最小示例
初始值为零，依次应用加一和加二，结果为三。

## 适用边界与误区
若事件有外部副作用，重放时必须隔离副作用。

## 自测问题
重复应用同一事件时，哪一层负责去重？
"""


def candidates(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "logic.py"
    source_file.write_text("state = 0\nstate += 1\n", encoding="utf-8")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    article = drafts / "chapter.md"
    metadata = drafts / "chapter.meta.json"
    source = drafts / "chapter.source.json"
    article.write_text(ARTICLE, encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "schema_version": 1, "volume": "state-machines", "chapter": "event-replay",
        "title": "状态机与事件重放", "summary": "用事件序列恢复确定性状态",
        "order": 10, "tags": ["状态机", "恢复"], "related": [],
        "created_at": now, "updated_at": now,
    }
    metadata.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    snippet = source_file.read_bytes()
    private = {
        "schema_version": 1, "article_id": "state-machines/event-replay",
        "generated_at": now, "project_root": str(project), "ticket_id": "T-1",
        "baselines": [{"repo": str(project), "branch": "main", "head": "a" * 40, "dirty": True}],
        "source_refs": [{"repo": str(project), "path": "logic.py", "symbol": "state",
                         "start_line": 1, "end_line": 2,
                         "sha256": hashlib.sha256(snippet).hexdigest()}],
        "private_terms": ["ProjectSecret"],
        "decision_record": {"reasons": ["有通用状态恢复概念"], "excluded": [],
                            "verified_facts": ["加法结果可复算"], "uncertainties": []},
    }
    source.write_text(json.dumps(private, ensure_ascii=False), encoding="utf-8")
    return article, metadata, source


def test_configured_library_add_and_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    library = tmp_path / "knowledge"
    library.mkdir()
    assert configure(str(library))["library_root"] == str(library)
    assert config_path() == tmp_path / "home" / ".claude" / "icode_data" / "knowledge_config.json"
    assert resolve_root()[0] == library
    article, metadata, source = candidates(tmp_path)
    assert validate(article, metadata, source) == []

    added = publish(library, article, metadata, source, "add", None, None)
    public_article = Path(added["article"])
    assert public_article.read_text(encoding="utf-8") == ARTICLE
    assert Path(added["catalog"]).read_text(encoding="utf-8").count("- [state-machines]") == 1
    assert "event-replay.md" in Path(added["volume_index"]).read_text(encoding="utf-8")
    assert Path(added["private_source"]).is_relative_to(private_root(library))
    assert not Path(added["private_source"]).is_relative_to(library)
    assert len(catalog(library)) == 1

    with pytest.raises(LibraryError, match="already exists"):
        publish(library, article, metadata, source, "add", None, None)
    article.write_text(ARTICLE.replace("结果为三", "结果仍为三"), encoding="utf-8")
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    metadata.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    before = catalog(library)[0]
    with pytest.raises(LibraryError, match="changed since"):
        publish(library, article, metadata, source, "revise", "0" * 64, before["metadata_sha256"])
    revised = publish(library, article, metadata, source, "revise",
                      before["article_sha256"], before["metadata_sha256"])
    assert "仍为三" in public_article.read_text(encoding="utf-8")
    assert (Path(revised["backup"]) / "article.md").read_text(encoding="utf-8") == ARTICLE
    assert revised["committed"] is False and revised["pushed"] is False


def test_privacy_and_handwritten_index_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    library = tmp_path / "knowledge"
    library.mkdir()
    article, metadata, source = candidates(tmp_path)
    article.write_text(ARTICLE + "\n/home/private/ProjectSecret\n", encoding="utf-8")
    errors = validate(article, metadata, source)
    assert any("internal marker" in error for error in errors)
    assert any("private term" in error for error in errors)
    article.write_text(ARTICLE, encoding="utf-8")
    (library / "CATALOG.md").write_text("# My manual catalog\n", encoding="utf-8")
    with pytest.raises(LibraryError, match="hand-written index"):
        publish(library, article, metadata, source, "add", None, None)
    assert not (library / "volumes" / "state-machines" / "chapters" / "event-replay.md").exists()


def test_source_hash_drift_is_rejected(tmp_path):
    article, metadata, source = candidates(tmp_path)
    (tmp_path / "project" / "logic.py").write_text("state = 9\n", encoding="utf-8")
    assert any("line/hash drift" in error for error in validate(article, metadata, source))


def test_missing_default_catalog_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root, origin = resolve_root()
    assert origin == "default"
    assert root == tmp_path / "home" / ".claude" / "icode_data" / "knowledge"
    assert catalog(root) == []
    assert not root.exists()


def test_public_config_template_requires_user_path(tmp_path, monkeypatch):
    template_path = ROOT / "templates" / "knowledge_config.json.template"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert set(template) == {"schema_version", "library_root"}
    assert template["schema_version"] == 1
    assert Path(template["library_root"]).is_absolute()
    assert "/home/orbbec/" not in template["library_root"]

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    configured = config_path()
    configured.parent.mkdir(parents=True)
    configured.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(LibraryError, match="does not exist"):
        resolve_root()


def test_private_state_cannot_be_nested_in_public_library(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    library = tmp_path / "home" / ".claude" / "icode_data"
    library.mkdir(parents=True)
    with pytest.raises(LibraryError, match="outside the public library"):
        private_root(library)


def test_catalog_rejects_corrupt_existing_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    library = tmp_path / "knowledge"
    library.mkdir()
    article, metadata, source = candidates(tmp_path)
    saved = publish(library, article, metadata, source, "add", None, None)
    public_meta = Path(saved["metadata"])
    corrupt = json.loads(public_meta.read_text(encoding="utf-8"))
    corrupt["related"] = [{"unexpected": "object"}]
    public_meta.write_text(json.dumps(corrupt, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LibraryError, match="catalog fields are invalid"):
        catalog(library)


def test_cli_configure_catalog_publish_and_cross_volume_links(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    library = tmp_path / "knowledge"
    library.mkdir()
    article, metadata, source = candidates(tmp_path)
    command = [sys.executable, str(ROOT / "tools" / "knowledge_library.py")]

    def run(*args):
        result = subprocess.run([*command, *map(str, args)], env=env, text=True,
                                capture_output=True, check=True)
        return json.loads(result.stdout)

    assert run("configure", "--root", library)["library_root"] == str(library)
    assert run("resolve")["origin"] == "config"
    first = run("publish", article, "--metadata", metadata, "--source", source, "--mode", "add")
    assert Path(first["article"]).is_file()
    assert len(run("catalog")["chapters"]) == 1

    second_meta = json.loads(metadata.read_text(encoding="utf-8"))
    second_meta.update(volume="state-recovery", chapter="replay-boundaries",
                       title="事件重放的边界", related=["state-machines/event-replay"])
    metadata.write_text(json.dumps(second_meta, ensure_ascii=False), encoding="utf-8")
    article.write_text(ARTICLE.replace("状态机与事件重放", "事件重放的边界"), encoding="utf-8")
    second_source = json.loads(source.read_text(encoding="utf-8"))
    second_source["article_id"] = "state-recovery/replay-boundaries"
    source.write_text(json.dumps(second_source, ensure_ascii=False), encoding="utf-8")
    second = run("publish", article, "--metadata", metadata, "--source", source, "--mode", "add")
    index = Path(second["volume_index"]).read_text(encoding="utf-8")
    assert "../state-machines/chapters/event-replay.md" in index
    assert len(run("catalog")["chapters"]) == 2

    second_meta["related"] = ["state-machines/missing"]
    metadata.write_text(json.dumps(second_meta, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LibraryError, match="related chapter does not exist"):
        publish(library, article, metadata, source, "revise",
                second["article_sha256"], second["metadata_sha256"])
