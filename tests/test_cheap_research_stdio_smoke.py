#!/usr/bin/env python3
"""cheap-research stdio 冒烟回归：真实 spawn server 走低层 stdio JSON-RPC。

背景（R-2，ISSUES）：
  mcp>=1.29 在 stdio 服务层用 outputSchema 校验工具返回 dict——
  任何字段缺失置 null → jsonschema 抛 "Output validation error: None is not of type 'string'"，
  导致全部工具（含 describe_capabilities 等 local 工具）在真实场景不可用。
  契约测试全 in-process（FastMCP call_tool 不触发低层 stdio 校验）故漏网。

本测试直接 spawn `python server.py`（真实 stdio），用 mcp 库低层 client 发
initialize + tools/list + tools/call(describe_capabilities)，要求：
  1. initialize 返回合法 capabilities（server 正常起）
  2. tools/list 列出 describe_capabilities
  3. call 返回 result（非 error），输出里不得出现 "Output validation error"

同类工具（任意 local 工具 stdout 冒烟）都可在此追加。
"""
import asyncio
import os
import sys

SERVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp", "cheap-research")

FAIL = 0
PASS = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_path = os.path.join(SERVER_DIR, "server.py")
    if not os.path.isfile(server_path):
        bad(f"server.py 不存在: {server_path}")
        return 1

    # 最小 config：provider 空即可（describe_capabilities 是 local 工具，不走 LLM）。
    # 用临时目录隔离子进程，避免污染真实配置。
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"provider": "openai_compat", "base_url": "", "api_key": "", "model": ""}, fh)
        env = dict(os.environ)
        env["CHEAP_RESEARCH_CONFIG"] = cfg_path

        params = StdioServerParameters(command="python3", args=[server_path], env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                # 1) initialize
                try:
                    init = await session.initialize()
                    ok(f"initialize: server 协议 {init.protocolVersion}")
                except Exception as exc:  # noqa: BLE001
                    bad(f"initialize 失败: {exc}")
                    return 1

                # 2) tools/list
                tools = await session.list_tools()
                tool_names = {t.name for t in tools.tools}
                if "describe_capabilities" in tool_names:
                    ok("tools/list 含 describe_capabilities")
                else:
                    bad(f"tools/list 缺 describe_capabilities（实有 {sorted(tool_names)[:8]}…）")
                    return 1

                # 3) tools/call：真实场景曾在此炸 Output validation error
                try:
                    result = await session.call_tool("describe_capabilities", {})
                    text = ""
                    if getattr(result, "content", None):
                        for part in result.content:
                            text += getattr(part, "text", "") or ""
                    if result.isError:
                        bad(f"describe_capabilities 返回 isError: {text[:300]}")
                        return 1
                    # R-2 症状是 jsonschema 严格措辞 "Output validation error"——只匹配该精确
                    # 措辞，不匹配正常描述里自然出现的 "error" 词。
                    if "Output validation error" in text:
                        bad(f"describe_capabilities 出现 R-2 症状 'Output validation error': {text[:300]}")
                        return 1
                    if not text.strip():
                        bad("describe_capabilities 返回空内容")
                        return 1
                    ok(f"describe_capabilities 返回非 error 内容（{len(text)} 字符）")
                except Exception as exc:  # noqa: BLE001
                    bad(f"tools/call describe_capabilities 异常: {exc}（R-2 复发症状）")
                    return 1

    # 汇总
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))