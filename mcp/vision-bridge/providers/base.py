"""MediaProvider 抽象基类。

任何 provider 必须实现 analyze。
"""
from abc import ABC, abstractmethod


class MediaProvider(ABC):
    """视觉 provider 抽象。

    Attributes:
        name: provider 标识
        supports_video: 是否支持视频输入
    """

    name: str = "abstract"
    supports_video: bool = False

    @abstractmethod
    async def analyze(
        self,
        media_path: str,
        prompt: str,
        media_type: str,
        max_tokens: int = 1024,
    ) -> str:
        """分析媒体返回文本描述。

        Args:
            media_path: 本地路径或 http(s) URL
            prompt: 可选附加指令
            media_type: "image" 或 "video"
            max_tokens: 输出最大 token 数

        Raises:
            RuntimeError: 处理失败 (ffmpeg 缺失、OCR 失败、API 错误等)
        """
        raise NotImplementedError


class UnconfiguredProvider(MediaProvider):
    """vision-bridge 已注册但 config.json 缺必填字段时的虚拟 provider。

    analyze() 返回明确的 fallback 提示字符串, 让 session 模型知道
    "vision-bridge 不可用, 请按默认会话模型处理原图"。
    与"未装 vision-bridge"行为等价 —— 不报错、不阻塞。
    """

    name = "unconfigured"
    supports_video = False

    def __init__(self, missing: list[str]):
        self.missing = missing

    async def analyze(
        self,
        media_path: str,
        prompt: str,
        media_type: str,
        max_tokens: int = 1024,
    ) -> str:
        miss = ", ".join(self.missing)
        return (
            f"[vision-bridge 未配置] 已装但 config.json 缺必填字段: {miss}。"
            f"按默认会话模型原生多模态能力处理原图, 本工具无法继续。"
            f"如需启用: 编辑 ~/.claude/skills/icode/mcp/vision-bridge/config.json "
            f"填 base_url/api_key/model 后重启 Claude Code。"
        )
