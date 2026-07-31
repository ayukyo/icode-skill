"""LLMProvider 抽象基类。

任何 provider 必须实现 invoke。
"""
from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """文本 LLM provider 抽象。

    Attributes:
        name: provider 标识
        supports_schema: 是否支持 JSON schema 输出（接口层）
    """

    name: str = "abstract"
    supports_schema: bool = True

    @abstractmethod
    async def invoke(
        self,
        prompt: str,
        schema: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> dict:
        """调用 LLM, 返回结构化结果。

        Args:
            prompt: 用户 prompt
            schema: 可选 JSON schema, 用于约束输出结构
            max_tokens: 最大输出 token 数
            temperature: 生成温度 0~1

        Returns:
            结构化 dict: {"answer": str, "confidence": float, "model": str, "cost": float}
            失败时: {"error": str, "model": str}

        Raises:
            RuntimeError: 处理失败 (API 错误、网络超时等)
        """
        raise NotImplementedError


class UnconfiguredProvider(LLMProvider):
    """cheap-research 已注册但 config.json 缺必填字段时的虚拟 provider。

    invoke() 返回明确的 fallback 提示 dict, 让 session 模型知道
    "cheap-research 不可用, 请按默认会话模型处理该任务"。
    与"未装 cheap-research"行为等价 —— 不报错、不阻塞。
    """

    name = "unconfigured"
    supports_schema = False

    def __init__(self, missing: list[str]):
        self.missing = missing

    async def invoke(
        self,
        prompt: str,
        schema: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> dict:
        miss = ", ".join(self.missing)
        return {
            "error": (
                f"[cheap-research 未配置] 已装但 config.json 缺必填字段: {miss}。"
                f"按默认会话模型处理该任务, 本工具无法继续。"
                f"如需启用: 编辑 ~/.claude/skills/icode/mcp/cheap-research/config.json "
                f"填 base_url/api_key/model 后重启 Claude Code。"
            ),
            "model": "unconfigured",
        }
