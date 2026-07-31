"""模型价格表（cost_estimated 估算用）。

按 model 字符串匹配，返回 unit_price_per_1M_tokens（USD）。
未匹配用 default ($0.30/M)。
"""

# 价格以 USD / 1M tokens 计（混合 input/output 粗估）
PRICING = {
    # OpenAI
    "gpt-4o-mini": 0.30,
    "gpt-4o": 5.00,
    "gpt-4-turbo": 10.00,
    "gpt-3.5-turbo": 0.50,
    # Anthropic（按 OpenAI 兼容代理）
    "claude-3-5-haiku": 1.00,
    "claude-3-haiku": 0.25,
    "claude-haiku-4-5": 1.00,
    # DeepSeek
    "deepseek-chat": 0.14,
    "deepseek-coder": 0.14,
    # Qwen / 通义
    "qwen2.5-7b-instruct": 0.50,
    "qwen-plus": 1.20,
    "qwen-turbo": 0.30,
    # GLM / 智谱
    "glm-4": 7.00,
    "glm-4-flash": 0.10,
    # Gemini
    "gemini-1.5-flash": 0.075,
    "gemini-1.5-pro": 1.25,
    # Ollama 本地（免费）
    "ollama": 0.0,
    "qwen2.5:7b": 0.0,
    "llama3.2:3b": 0.0,
}

DEFAULT_PRICE = 0.30  # USD / 1M tokens（粗估）


def estimate_cost(model: str, total_tokens: int) -> float:
    """估算调用成本（USD）。

    注意：这是粗估，不区分 input / output 比例。
    实际成本按 provider 定价为准。
    """
    if not total_tokens or total_tokens <= 0:
        return 0.0
    # 模糊匹配 model
    model_lower = model.lower() if model else ""
    price = DEFAULT_PRICE
    for key, p in PRICING.items():
        if key.lower() in model_lower or model_lower in key.lower():
            price = p
            break
    return round((total_tokens / 1_000_000) * price, 6)
