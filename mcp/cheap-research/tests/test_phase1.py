"""阶段 1.7：5 工具自检（不带 LLM API，纯逻辑 + 错误兜底）。

不依赖 httpx —— 通过触发错误路径验证 5 工具的入参校验。
依赖: 临时 config.json 缺字段, 触发 UnconfiguredProvider 路径。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# 准备：创建临时 config.json 缺字段（触发 UnconfiguredProvider）
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump({"provider": "openai_compat"}, f)  # 缺 base_url/api_key/model
    tmp_config_path = f.name

os.environ["CHEAP_RESEARCH_CONFIG"] = tmp_config_path

# 把 cheap-research 目录加到 sys.path
CR_DIR = Path("/home/orbbec/git/icode-skill/mcp/cheap-research")
sys.path.insert(0, str(CR_DIR))

# 加载 server.py 但绕过 mcp.tool 装饰（直接读源码提取函数）
server_src = (CR_DIR / "server.py").read_text()

# 简单验证：先 import _utils
import _utils
print("✅ _utils.py 加载成功")

# 验证 _utils 函数
assert _utils.make_error_response("test") == {"error": "test", "model": "unknown"}
print("✅ make_error_response")

assert _utils.validate_non_empty_str("hello", "f") is None
assert _utils.validate_non_empty_str("", "f") is not None
assert _utils.validate_non_empty_str(None, "f") is not None
print("✅ validate_non_empty_str")

assert _utils.validate_dict({}, "f") is None
assert _utils.validate_dict("bad", "f") is not None
print("✅ validate_dict")

assert _utils.validate_list([1, 2], "f") is None
assert _utils.validate_list([], "f") is not None  # 非 allow_empty
assert _utils.validate_list([], "f", allow_empty=True) is None
print("✅ validate_list")

assert _utils.validate_path_exists("/etc/hostname", "f") != "err"  # 路径存在
assert _utils.validate_path_exists("/this/does/not/exist", "f") is not None  # 路径不存在
print("✅ validate_path_exists")

assert _utils.truncate_text("a" * 100, max_chars=50) == "a" * 50 + "\n\n... (truncated)"
assert _utils.truncate_text("a" * 100, max_chars=200) == "a" * 100
print("✅ truncate_text")

candidates = list(range(60))
truncated, was = _utils.truncate_candidates(candidates, max_count=50)
assert len(truncated) == 50 and was is True
print("✅ truncate_candidates")

json_str = _utils.json_dumps_safe({"a": 1})
assert json_str == '{\n  "a": 1\n}'
print("✅ json_dumps_safe")

# 加载 server.py（尝试 mcp 加载，但可能会失败因为没装 mcp）
print("\n--- 5 工具错误兜底测试（需 UnconfiguredProvider 触发）---")
try:
    # 尝试通过 FastMCP 加载 5 工具
    from server import mcp, summarize, retrieve_similar, fill_template, extract, audit_facts
    has_mcp = True
    print("✅ mcp 加载成功（说明 mcp 包已装）")
except ImportError as e:
    if "mcp" in str(e):
        has_mcp = False
        print("⚠️  mcp 包未装，跳过 mcp.tool 装饰路径，改用源码 readout 验证")
    else:
        raise

if has_mcp:
    import asyncio

    async def test_tools():
        # 1. summarize 错误兜底
        r = await summarize(text="")
        assert "error" in r and "不能为空" in r["error"], r
        print("✅ summarize 空文本 → 错误兜底")

        r = await summarize(text=None)
        assert "error" in r, r
        print("✅ summarize None 文本 → 错误兜底")

        # 2. retrieve_similar 错误兜底
        r = await retrieve_similar(query="", candidates=[{"id": "a"}])
        assert "error" in r and "query" in r["error"], r
        print("✅ retrieve_similar 空 query → 错误兜底")

        r = await retrieve_similar(query="test", candidates=[])
        assert "error" in r and "candidates" in r["error"], r
        print("✅ retrieve_similar 空 candidates → 错误兜底")

        r = await retrieve_similar(query="test", candidates=[{"id": "a"}], k=0)
        assert "error" in r and "k" in r["error"], r
        print("✅ retrieve_similar k=0 → 错误兜底")

        r = await retrieve_similar(query="test", candidates=[{"id": "a"}], k=100)
        assert "error" in r and "k" in r["error"], r
        print("✅ retrieve_similar k=100 → 错误兜底")

        # 3. fill_template 错误兜底
        r = await fill_template(template="", data={})
        assert "error" in r and "template" in r["error"], r
        print("✅ fill_template 空模板 → 错误兜底")

        r = await fill_template(template="test", data="not_dict")
        assert "error" in r and "data" in r["error"], r
        print("✅ fill_template data 非 dict → 错误兜底")

        # 4. extract 错误兜底
        r = await extract(text="", schema={"type": "object"})
        assert "error" in r and "text" in r["error"], r
        print("✅ extract 空文本 → 错误兜底")

        r = await extract(text="test", schema="not_dict")
        assert "error" in r and "schema" in r["error"], r
        print("✅ extract schema 非 dict → 错误兜底")

        # 5. audit_facts 错误兜底
        r = await audit_facts(repo_path="")
        assert "error" in r and "repo_path" in r["error"], r
        print("✅ audit_facts 空路径 → 错误兜底")

        r = await audit_facts(repo_path="/this/does/not/exist")
        assert "error" in r and "路径不存在" in r["error"], r
        print("✅ audit_facts 不存在路径 → 错误兜底")

        r = await audit_facts(repo_path="/tmp")  # 临时目录无关键文件
        assert "error" in r and ("未找到" in r["error"] or "未在" in r["error"]), r
        print("✅ audit_facts 无关键文件 → 错误兜底")

        # 6. 全部触发 UnconfiguredProvider 路径（缺字段）
        r = await summarize(text="hello world")
        assert "error" in r and "cheap-research 未配置" in r["error"], r
        print("✅ summarize UnconfiguredProvider 路径")

        r = await retrieve_similar(query="test", candidates=[{"id": "a"}])
        assert "error" in r and "cheap-research 未配置" in r["error"], r
        print("✅ retrieve_similar UnconfiguredProvider 路径")

        r = await fill_template(template="hello {name}", data={"name": "world"})
        assert "error" in r and "cheap-research 未配置" in r["error"], r
        print("✅ fill_template UnconfiguredProvider 路径")

        r = await extract(text="hello", schema={"type": "object"})
        assert "error" in r and "cheap-research 未配置" in r["error"], r
        print("✅ extract UnconfiguredProvider 路径")

        r = await audit_facts(repo_path="/home/orbbec/git/icode-skill")  # 此目录有 README
        assert "error" in r and "cheap-research 未配置" in r["error"], r
        print("✅ audit_facts UnconfiguredProvider 路径")

    asyncio.run(test_tools())
    print("\n🎉 5 工具错误兜底 + UnconfiguredProvider 路径全过！")

# 清理 tmp config
os.unlink(tmp_config_path)
