"""本地 Ollama provider - 零 KEY 零费用纯本地。

走 OpenAI 兼容协议 (Ollama 自 v0.1.14+ 默认开启 /v1/chat/completions)。
不需要 Ollama 特定 SDK, 复用 OpenAI 兼容协议即可。

依赖: 本地启 Ollama 服务 (ollama serve, 默认 127.0.0.1:11434)
      至少加载一个便宜模型 (如 qwen2.5:7b, 7B 模型 ~4GB 显存)

修复记录 (v1.0):
- error_code 字段统一
- 走 OpenAI 兼容协议 + 共享 _HTTP_LIMITS
"""
import httpx

from providers.base import LLMProvider
from providers.openai_compat import OpenAICompatProvider, _make_error


_LOCAL_OLLAMA_DEFAULT = "http://localhost:11434/v1"


class LocalOllamaProvider(LLMProvider):
    """本地 Ollama 服务, 零 KEY 零费用, 完全离线推理.

    config 字段:
        base_url: Ollama 服务的 OpenAI 兼容端点 (默认 http://localhost:11434/v1)
        model:    已加载的模型名 (如 qwen2.5:7b / llama3.2:3b)
        api_key:  dummy 字符串 (Ollama 一般不校验)

    可选字段:
        timeout: HTTP 超时秒（本地一般很快，默认 60）
    """

    name = "local_ollama"
    supports_schema = True

    def __init__(self, config: dict):
        base_url = config.get("base_url", _LOCAL_OLLAMA_DEFAULT)
        if not config.get("api_key"):
            config = dict(config)
            config["api_key"] = "ollama-dummy-key"
        self._impl = OpenAICompatProvider(config)
        self.base_url = base_url
        self.model_name = config["model"]

    async def invoke(
        self,
        prompt: str,
        schema: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> dict:
        # 先 ping 一下, 避免长时间 hang
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(f"{self.base_url.rstrip('/v1')}/api/tags")
        except httpx.RequestError as e:
            return _make_error(
                "api_connection_error",
                f"[cheap-research 本地 Ollama 不可达] {self.base_url}。\n"
                f"  排查: 确认 ollama serve 已启, 确认 {self.model_name} 已加载, "
                f"检查 base_url (默认 http://localhost:11434/v1)\n"
                f"  原始错误: {e}",
                self.model_name,
            )

        result = await self._impl.invoke(prompt, schema, max_tokens, temperature)
        # local_ollama 成本为 0（覆盖 _pricing 估算）
        if "cost_estimated" in result:
            result["cost_estimated"] = 0.0
        return result
