"""OpenAI Chat Completions 兼容 provider - 唯一主 provider。

覆盖任何遵守 OpenAI Chat Completions 接口标准的 LLM API:
  - 官方 OpenAI (GPT-4o-mini / GPT-4o)
  - Anthropic Claude (通过 OpenAI 兼容代理)
  - DeepSeek / Qwen / GLM 等国产模型
  - Gemini 通过 openai-compat 端点
  - OpenRouter 聚合服务
  - 自建 OpenAI 兼容服务

任务语义: 纯文本 LLM 推理 (长上下文压缩 / 提取 / 检索 / 模板填充等)。
不处理图片/视频 —— 那是 vision-bridge 的职责。

修复记录 (v1.0):
- P0-5: cost_estimated 改用 _pricing.py 按 model 估算
- P1-10: failure_retry 1 次（指数退避 1s）
- P2-14: 全局 httpx.Limits (max_connections=10)
- P2-15: error_code 细分（config_missing / api_http_error / api_connection_error / api_timeout / schema_parse_failed）
"""
import asyncio
import json
import re
from typing import Any

import httpx

from providers.base import LLMProvider
from providers._pricing import estimate_cost

# 全局连接池（所有 invoke 共享）
_HTTP_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)
_DEFAULT_TIMEOUT = 60.0


def _make_error(error_code: str, message: str, model: str = "unknown") -> dict:
    """统一错误响应：含 error_code 便于主会话细分处理。"""
    return {
        "error_code": error_code,
        "error": message,
        "model": model,
    }


class OpenAICompatProvider(LLMProvider):
    """完全驱动 OpenAI Chat Completions 兼容 API 的通用 provider.

    必需的 config 字段:
        base_url: API 端点 (不带尾 /)
        api_key:  鉴权 KEY
        model:    模型名

    可选字段:
        timeout: HTTP 超时秒 (默认 60)
    """

    name = "openai_compat"
    supports_schema = True

    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config["api_key"]
        self.model = config["model"]
        self.timeout = float(config.get("timeout", _DEFAULT_TIMEOUT))

    # ---------- 主入口 ----------

    async def invoke(
        self,
        prompt: str,
        schema: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,  # 0.3 是便宜模型经验值（比默认 1.0 更稳）；需要调整改源码
    ) -> dict:
        # 如果有 schema, 在 prompt 末尾追加"按 JSON schema 输出"
        final_prompt = prompt
        if schema is not None:
            schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
            final_prompt = (
                f"{prompt}\n\n"
                f"请严格按以下 JSON schema 输出 (只输出 JSON, 不要其他内容):\n"
                f"```json\n{schema_str}\n```\n\n"
                f"重要约束:\n"
                f"1. 字符串值内的双引号必须用 \\\\\" 转义, 或改用单引号/书名号《》\n"
                f"2. 不要在字符串值内使用未转义的双引号\n"
                f"3. 输出纯 JSON, 不要包裹在代码块中"
            )

        url = f"{self.base_url}/chat/completions"
        request_body = {
            "model": self.model,
            "messages": [{"role": "user", "content": final_prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Retry 1 次（指数退避）
        last_error = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    limits=_HTTP_LIMITS,
                    follow_redirects=True,
                ) as client:
                    r = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                    r.raise_for_status()
                    data = r.json()
                    return self._parse_response(data, schema)

            except httpx.HTTPStatusError as e:
                error_msg = (
                    f"HTTP {r.status_code} {r.reason_phrase}\n"
                    f"  实际请求 URL: {url}\n"
                    f"  base_url: {self.base_url}\n"
                    f"  model: {self.model}\n"
                    f"  排查: 404=base_url 路径错(correct 为 .../v1), 401=api_key 错, "
                    f"404 model=model 名错, timeout=检查 base_url 可达性\n"
                    f"  原始响应: {r.text[:200] if r.text else '(empty)'}"
                )
                last_error = _make_error("api_http_error", error_msg, self.model)
                if attempt == 0:
                    await asyncio.sleep(1)  # 重试前等 1s
                    continue
                return last_error

            except httpx.TimeoutException as e:
                last_error = _make_error(
                    "api_timeout",
                    f"请求超时 ({self.timeout}s)，检查 base_url 可达性",
                    self.model,
                )
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                return last_error

            except httpx.RemoteProtocolError as e:
                last_error = _make_error(
                    "api_connection_error",
                    f"连接错误: {e}，远程端可能中断",
                    self.model,
                )
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                return last_error

            except httpx.RequestError as e:
                last_error = _make_error(
                    "api_connection_error",
                    f"请求失败: {e}",
                    self.model,
                )
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                return last_error

        return last_error or _make_error("api_unknown_error", "未知错误", self.model)

    def _parse_response(self, data: dict, schema: dict | None) -> dict:
        """解析 API 响应。"""
        content = data["choices"][0]["message"]["content"]

        # 剥离推理模型的 <think>...</think> 标签（MiniMax-M3 / DeepSeek-R1 等）
        # 这些模型输出格式: <think>思考过程...</think>\n实际回答
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        # 如果有 schema, 尝试解析 JSON
        parsed: Any = None
        if schema is not None:
            try:
                # 容错 1: 提取 ```json ... ``` 块（用 find 防 substring not found）
                if "```json" in content:
                    start = content.find("```json")
                    if start != -1:
                        start += len("```json")
                        end = content.find("```", start)
                        if end != -1:
                            content = content[start:end].strip()
                        else:
                            # 结尾 ``` 被截断，取 start 之后全部
                            content = content[start:].strip()
                # 容错 2: 提取首个 { ... } 块（防模型输出前后多余文本 / 截断）
                if "{" in content:
                    first_brace = content.find("{")
                    last_brace = content.rfind("}")
                    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                        content = content[first_brace:last_brace + 1]
                parsed = json.loads(content)
            except (json.JSONDecodeError, ValueError) as e:
                return _make_error(
                    "schema_parse_failed",
                    f"cheap-research 输出非 JSON: {e}; raw: {content[:200]}",
                    self.model,
                )

        # 用量 + 成本估算
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        cost = estimate_cost(self.model, total_tokens)

        return {
            "answer": parsed if parsed is not None else content,
            "confidence": 0.85,  # 默认中间值，后续 v1.0 改为真实 confidence
            "model": self.model,
            "tokens_used": total_tokens,
            "cost_estimated": cost,  # 已按 _pricing.py 估算
            "_cost_note": "estimate",  # 标注是粗估（实际成本按 provider 定价）
        }
