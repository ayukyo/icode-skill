"""阶段 2.5：9 增强工具自检（不带 LLM API，纯逻辑 + 错误兜底 + 纯文本成功路径）。

测试策略:
- 错误兜底: 触发入参校验路径, 不调 LLM
- 纯文本成功路径: scan_patterns / trace_refs / parse_project_id / scan_modules 在临时目录上跑
- HTTP 工具: 只测错误兜底（实际 GET 留给真实环境）
- LLM 工具: 只测错误兜底 + 部分入参校验
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# 准备临时 config 缺字段（触发 UnconfiguredProvider）
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump({"provider": "openai_compat"}, f)
    tmp_config_path = f.name
os.environ["CHEAP_RESEARCH_CONFIG"] = tmp_config_path

CR_DIR = Path("/home/orbbec/git/icode-skill/mcp/cheap-research")
sys.path.insert(0, str(CR_DIR))

# 让 server.py 知道 sys.path
sys.path.insert(0, "/home/orbbec/git/icode-skill/mcp/cheap-research")

# 加载
import _utils
print("✅ _utils.py 加载成功")

# 验证 _utils 新增函数
assert _utils.iter_source_files(Path("/home/orbbec/git/icode-skill/mcp/cheap-research"), max_files=5) is not None
print("✅ iter_source_files")

assert _utils.validate_int_range(5, "f", 1, 10) is None
assert _utils.validate_int_range(0, "f", 1, 10) is not None
assert _utils.validate_int_range(11, "f", 1, 10) is not None
assert _utils.validate_int_range("bad", "f", 1, 10) is not None
print("✅ validate_int_range")

assert _utils.validate_url("https://example.com", "f") is None
assert _utils.validate_url("http://test", "f") is None
assert _utils.validate_url("ftp://bad", "f") is not None
assert _utils.validate_url("not_url", "f") is not None
print("✅ validate_url")

# 加载 server.py（FastMCP 装饰）
print("\n--- 9 工具加载 + 错误兜底测试 ---")
try:
    from server import (
        mcp,
        # 5 核心工具（已测过, 这里快速验证存在）
        summarize, retrieve_similar, fill_template, extract, audit_facts,
        # 9 增强工具
        scan_patterns, trace_refs, fetch_remote, apply_migration,
        parse_project_id, scan_modules,
        diff_summary, generate_filename, select_template,
    )
    print("✅ 9 工具加载成功")
except ImportError as e:
    print(f"❌ 加载失败: {e}")
    sys.exit(1)

import asyncio

async def test_enhanced():
    # ========== 纯文本工具（scan_patterns / trace_refs）==========

    # scan_patterns 错误兜底
    r = await scan_patterns(patterns=[])
    assert "error" in r and "patterns" in r["error"], r
    print("✅ scan_patterns 空 patterns → 错误兜底")

    r = await scan_patterns(patterns=["test"], scope_path="/not/exist")
    assert "error" in r and "不存在" in r["error"], r
    print("✅ scan_patterns scope 不存在 → 错误兜底")

    r = await scan_patterns(patterns=["["])  # 非法正则
    assert "error" in r and "正则" in r["error"], r
    print("✅ scan_patterns 非法正则 → 错误兜底")

    # scan_patterns 成功路径（用 cheap-research 目录测试）
    r = await scan_patterns(
        patterns=[r"def\s+\w+"],
        scope_path="/home/orbbec/git/icode-skill/mcp/cheap-research",
        max_files=10,
        max_matches=10,
    )
    assert "answer" in r, r
    assert r["answer"]["total_count"] > 0, r
    print(f"✅ scan_patterns 成功: 找到 {r['answer']['total_count']} 个匹配")

    # trace_refs 错误兜底
    r = await trace_refs(symbol="")
    assert "error" in r and "symbol" in r["error"], r
    print("✅ trace_refs 空 symbol → 错误兜底")

    # trace_refs 成功路径
    r = await trace_refs(
        symbol="summarize",
        scope_path="/home/orbbec/git/icode-skill/mcp/cheap-research",
        max_files=10,
    )
    assert "answer" in r and r["answer"]["count"] > 0, r
    print(f"✅ trace_refs 成功: 找到 {r['answer']['count']} 个引用")

    # ========== HTTP 工具（fetch_remote）==========

    r = await fetch_remote(url="ftp://bad")
    assert "error" in r and "http" in r["error"], r
    print("✅ fetch_remote 非法 URL → 错误兜底")

    r = await fetch_remote(url="not_url")
    assert "error" in r, r
    print("✅ fetch_remote URL 格式错 → 错误兜底")

    # ========== 复合工具（apply_migration）==========

    r = await apply_migration(schema_diff="not_dict")
    assert "error" in r and "schema_diff" in r["error"], r
    print("✅ apply_migration schema_diff 非 dict → 错误兜底")

    r = await apply_migration(schema_diff={"unknown_op": []})
    assert "error" in r and "不支持 op" in r["error"], r
    print("✅ apply_migration 非法 op 类型 → 错误兜底")

    r = await apply_migration(
        schema_diff={"add": [{"path": "../escape.py"}]},
        repo_path="/home/orbbec/git/icode-skill",
    )
    assert "error" in r and "逃逸" in r["error"], r
    print("✅ apply_migration 路径逃逸 → 错误兜底")

    # apply_migration 成功路径
    r = await apply_migration(
        schema_diff={
            "add": [{"path": "src/new.py", "content": "print('hello')"}],
            "remove": [{"path": "src/old.py"}],
        },
        repo_path="/home/orbbec/git/icode-skill",
    )
    assert "answer" in r, r
    assert r["answer"]["op_count"] == 2, r
    assert not r["answer"]["ops"][0]["target"].endswith("?")  # 路径已 resolve
    print(f"✅ apply_migration 成功: 生成 {r['answer']['op_count']} 个 ops")

    # ========== 纯文本工具（parse_project_id / scan_modules）==========

    r = await parse_project_id(repo_path="/not/exist")
    assert "error" in r, r
    print("✅ parse_project_id 路径不存在 → 错误兜底")

    # parse_project_id 成功路径
    r = await parse_project_id(repo_path="/home/orbbec/git/icode-skill")
    assert "answer" in r, r
    assert "project_id" in r["answer"], r
    print(f"✅ parse_project_id 成功: project_id={r['answer']['project_id']}, branch={r['answer']['branch']}")

    # scan_modules 错误兜底
    r = await scan_modules(repo_path="/not/exist")
    assert "error" in r, r
    print("✅ scan_modules 路径不存在 → 错误兜底")

    # scan_modules 成功路径（dev_repo 应该有 .gitmodules 之类的）
    r = await scan_modules(repo_path="/home/orbbec/git/icode-skill")
    assert "answer" in r, r
    print(f"✅ scan_modules 成功: 找到 {r['answer']['count']} 个模块")

    # ========== LLM 工具（diff_summary / generate_filename / select_template）==========

    # diff_summary 错误兜底
    r = await diff_summary(text_a="", text_b="new")
    assert "error" in r and "text_a" in r["error"], r
    print("✅ diff_summary 空 text_a → 错误兜底")

    r = await diff_summary(text_a="old", text_b="")
    assert "error" in r and "text_b" in r["error"], r
    print("✅ diff_summary 空 text_b → 错误兜底")

    r = await diff_summary(text_a="old", text_b="new")
    assert "error" in r and "未配置" in r["error"], r
    print("✅ diff_summary UnconfiguredProvider 路径")

    # generate_filename 错误兜底
    r = await generate_filename(context={})
    assert "error" in r, r
    print("✅ generate_filename 空 context → 错误兜底")

    r = await generate_filename(context={}, prefix="")
    assert "error" in r and "prefix" in r["error"], r
    print("✅ generate_filename 空 prefix → 错误兜底")

    r = await generate_filename(context={"change_type": "feature"})
    assert "error" in r and "未配置" in r["error"], r
    print("✅ generate_filename UnconfiguredProvider 路径")

    # select_template 错误兜底
    r = await select_template(context={})
    assert "error" in r, r
    print("✅ select_template 空 context → 错误兜底")

    r = await select_template(context={"x": 1}, options=[])
    assert "error" in r and "options" in r["error"], r
    print("✅ select_template 空 options → 错误兜底")

    r = await select_template(context={"x": 1})
    assert "error" in r and "未配置" in r["error"], r
    print("✅ select_template UnconfiguredProvider 路径")

asyncio.run(test_enhanced())
print("\n🎉 9 增强工具自检全过！")

os.unlink(tmp_config_path)
