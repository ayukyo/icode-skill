"""MediaProvider 抽象基类。

任何 provider 必须实现 analyze。
"""
from abc import ABC, abstractmethod


_SENSITIVE_PROFILE_KEYS = {
    "api_key", "apikey", "authorization", "token", "access_token",
    "refresh_token", "secret", "client_secret", "password",
}


def _safe_quality_profile(value) -> dict:
    """Keep only flat scalar quality claims and never reflect credentials."""
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item for key, item in value.items()
        if not _sensitive_key(str(key))
        and isinstance(item, (str, int, float, bool, type(None)))
    }


def _sensitive_key(key: str) -> bool:
    normalized = "_".join(part for part in key.lower().replace("-", "_").split("_") if part)
    return normalized in _SENSITIVE_PROFILE_KEYS or any(
        marker in normalized for marker in ("password", "secret", "token", "api_key"))


class MediaProvider(ABC):
    """视觉 provider 抽象。

    Attributes:
        name: provider 标识
        supports_video: 是否支持视频输入
    """

    name: str = "abstract"
    supports_video: bool = False

    def capability_profile(self) -> dict:
        """Return non-secret, explicitly declared capability metadata."""
        declared = getattr(self, "declared_capabilities", [])
        if not isinstance(declared, (list, tuple, set)):
            declared = []
        model = getattr(self, "model", "unknown")
        profile_version = getattr(self, "profile_version", None)
        return {
            "provider": self.name,
            "model": model if isinstance(model, str) else "unknown",
            "profile_version": profile_version if isinstance(profile_version, str) else None,
            "declared_capabilities": sorted(set(
                item for item in declared if isinstance(item, str) and item.strip()
            )),
            "quality_profile": _safe_quality_profile(
                getattr(self, "quality_profile", {})),
            "transport_support": {
                "image": True,
                "video": self.supports_video,
            },
        }

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
        "vision-bridge 不可用, 请回到 ICODE 能力路由"。
    与"未装 vision-bridge"行为等价 —— 不报错、不阻塞，也不猜测 session 能力。
    """

    name = "unconfigured"
    supports_video = False

    def __init__(self, missing: list[str]):
        self.missing = missing

    def capability_profile(self) -> dict:
        profile = super().capability_profile()
        profile["configured"] = False
        profile["missing"] = list(self.missing)
        return profile

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
            f"本工具无法继续；请按 media_routing 合同：宿主已证明多模态才走 native，"
            f"否则降级 text_only 并记录视觉缺口。"
            f"如需启用: 编辑 ~/.claude/skills/icode/mcp/vision-bridge/config.json "
            f"填 base_url/api_key/model 后重启 Claude Code。"
        )
