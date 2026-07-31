"""阶段 3：P0/P1 修复自检。

测试项：
- P0-1: fetch_remote SSRF 防护（拒绝内网/loopback/metadata）
- P0-4: apply_migration is_relative_to 严防
- P0-5: cost_estimated 价格表
- P1-9: _logger 监控
- P1-10: openai_compat retry 1 次
- P2-15: error_code 字段
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# 准备临时 config 缺字段
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump({"provider": "openai_compat"}, f)
    tmp_config_path = f.name
os.environ["CHEAP_RESEARCH_CONFIG"] = tmp_config_path

CR_DIR = Path("/home/orbbec/git/icode-skill/mcp/cheap-research")
sys.path.insert(0, str(CR_DIR))

# 加载
from _utils import make_error_response
print("✅ _utils 加载成功")

# 加载 _pricing
from providers._pricing import estimate_cost, PRICING
print(f"✅ _pricing 加载: {len(PRICING)} 个模型")

# 加载 _logger
from providers._logger import get_logger, log_call
logger = get_logger()
log_call("test_phase3", "start", test_name="P0/P1 修复")
print("✅ _logger 加载 + log_call 工作")

# 验证 _pricing 价格估算
assert estimate_cost("gpt-4o-mini", 1_000_000) == 0.30
assert estimate_cost("claude-3-5-haiku", 1_000_000) == 1.00
assert estimate_cost("deepseek-chat", 1_000_000) == 0.14
assert estimate_cost("qwen2.5:7b", 1_000_000) == 0.0  # 本地 ollama 免费
assert estimate_cost("unknown-model", 1_000_000) == 0.30  # 默认
assert estimate_cost("gpt-4o-mini", 0) == 0.0
print("✅ _pricing cost_estimated 估算正确")

# 加载 server.py（FastMCP 装饰）
from server import (
    mcp, fetch_remote, apply_migration,
    _validate_url_safe, make_error_response,
)
print("✅ server.py 加载成功（含 SSRF 防护函数）")

# 验证 SSRF 防护
ssrf_test_cases = [
    # (url, expected_blocked, reason)
    ("http://localhost:8080/admin", True, "loopback"),
    ("http://127.0.0.1/admin", True, "loopback"),
    ("http://10.0.0.1/internal", True, "private 10.0.0.0/8"),
    ("http://192.168.1.1/router", True, "private 192.168.0.0/16"),
    ("http://172.16.0.1/internal", True, "private 172.16.0.0/12"),
    ("http://169.254.169.254/latest/meta-data/", True, "AWS metadata link-local"),
    ("http://100.100.100.200/latest/meta-data/", True, "阿里云 metadata"),
    ("ftp://example.com", True, "scheme 不允许"),
    ("http://example.com", False, "公网合法"),
    ("https://api.openai.com/v1", False, "公网合法"),
]

for url, should_block, reason in ssrf_test_cases:
    err = _validate_url_safe(url)
    if should_block:
        assert err is not None, f"FAIL: {url} 应被拦截但通过了 ({reason})"
    else:
        # 公网 URL 可能因为 DNS 解析失败而报错（不是测试环境问题），但不能返回 SSRF 错误
        if err is not None and "SSRF" in err:
            raise AssertionError(f"FAIL: {url} 被误判为 SSRF ({err})")
        # else 接受（DNS 失败/公网均 OK）

print(f"✅ SSRF 防护: {sum(1 for _, b, _ in ssrf_test_cases if b)} 个拦截 + {sum(1 for _, b, _ in ssrf_test_cases if not b)} 个允许")

# 验证 fetch_remote 工具调用（无实际 HTTP）
async def test_fetch_remote():
    # P0-1: SSRF 拦截
    r = await fetch_remote(url="http://localhost:8080")
    assert "error_code" in r and r["error_code"] == "ssrf_blocked", r
    print("✅ fetch_remote SSRF 拦截（localhost）")

    r = await fetch_remote(url="http://169.254.169.254/")
    assert "error_code" in r and r["error_code"] == "ssrf_blocked", r
    print("✅ fetch_remote SSRF 拦截（AWS metadata）")

    # 入参校验
    r = await fetch_remote(url="not_url")
    assert "error" in r and "http://" in r["error"], r
    print("✅ fetch_remote URL 格式错误 → 错误兜底")

    # 错误响应有 error_code 字段
    r = await fetch_remote(url="http://localhost:8080")
    assert "error_code" in r, r
    print("✅ fetch_remote 错误响应含 error_code 字段")

# 验证 apply_migration 路径安全
async def test_apply_migration():
    # P0-4: is_relative_to 严防
    r = await apply_migration(
        schema_diff={"add": [{"path": "/etc/passwd"}]},
        repo_path="/home/orbbec/git/icode-skill",
    )
    assert "error" in r and ("逃逸" in r["error"] or "不在 repo" in r["error"]), r
    print("✅ apply_migration 绝对路径逃逸 → 阻止")

    r = await apply_migration(
        schema_diff={"add": [{"path": "."}]},
        repo_path="/home/orbbec/git/icode-skill",
    )
    # 路径 "." 会被 resolve 为 repo 自身 → 应被拒绝
    assert "error" in r and ("逃逸" in r["error"] or "根" in r["error"]), r
    print("✅ apply_migration 指向 repo 根 → 阻止")

    # 正常路径仍 OK
    r = await apply_migration(
        schema_diff={"add": [{"path": "src/new.py", "content": "print('hi')"}]},
        repo_path="/home/orbbec/git/icode-skill",
    )
    assert "answer" in r, r
    print("✅ apply_migration 合法路径 → 成功")

asyncio.run(test_fetch_remote())
asyncio.run(test_apply_migration())

# 验证 openai_compat.py 的 retry 机制（mock httpx）
import httpx
from providers.openai_compat import OpenAICompatProvider, _make_error

# 验证 _make_error 含 error_code
err = _make_error("test_code", "test msg", "test_model")
assert err["error_code"] == "test_code"
assert err["error"] == "test msg"
assert err["model"] == "test_model"
print("✅ _make_error 含 error_code 字段")

# 验证 retry 逻辑（mock 失败 1 次，成功 1 次）
provider = OpenAICompatProvider({
    "base_url": "http://test",
    "api_key": "test",
    "model": "test",
    "timeout": 5,
})

call_count = {"count": 0}

# 真实测 retry：patch httpx.AsyncClient.post
from unittest.mock import AsyncMock, patch

async def test_retry_real():
    """真实测 retry：模拟 invoke 失败 1 次后成功。"""
    class MockResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "choices": [{"message": {"content": '{"answer": "ok"}'}}],
                "usage": {"total_tokens": 100},
            }

    # 第一次抛 ConnectError，第二次成功
    async def mock_post(*args, **kwargs):
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise httpx.ConnectError("mock connection error")
        return MockResponse()

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=mock_post)):
        result = await provider.invoke(prompt="test")

    assert call_count["count"] == 2, f"应重试 1 次（总 2 次），实际 {call_count['count']}"
    assert "answer" in result, result
    # 没传 schema，answer 是字符串
    assert result["answer"] == '{"answer": "ok"}', result
    print("✅ retry 1 次后成功（真实 patch）")

    # 一直失败 → 错误响应
    call_count["count"] = 0
    async def fake_fail(*args, **kwargs):
        call_count["count"] += 1
        raise httpx.ConnectError("always fail")

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_fail)):
        result = await provider.invoke(prompt="test")

    assert call_count["count"] == 2, f"应重试 1 次（总 2 次），实际 {call_count['count']}"
    assert "error_code" in result
    assert result["error_code"] == "api_connection_error"
    print("✅ 持续失败 → 2 次后返 error_code")

asyncio.run(test_retry_real())

# 清理
os.unlink(tmp_config_path)
print("\n🎉 P0/P1 修复全过！")
