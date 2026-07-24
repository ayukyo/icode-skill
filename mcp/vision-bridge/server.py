"""vision-bridge MCP server 入口。

配置来源: $VISION_BRIDGE_CONFIG 指向的 JSON 文件 (默认 ./config.json)。
session 模型只收文本, 不接触原图/原视频。

启动: python -m mcp (stdin/stdout)
"""
import json
import os
import sys
from pathlib import Path

# 确保 server.py 所在目录 (即 vision-bridge/) 在 sys.path 第一位,
# 这样 from providers.xxx 能解析到 ./providers/。
_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from providers.base import UnconfiguredProvider  # noqa: E402
from providers.local_ocr import LocalOcrProvider  # noqa: E402
from providers.openai_compat import OpenAICompatProvider  # noqa: E402

PROVIDERS = {
    "openai_compat": OpenAICompatProvider,
    "local_ocr": LocalOcrProvider,
}

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}

mcp = FastMCP("vision-bridge")


def load_config() -> dict:
    """读 $VISION_BRIDGE_CONFIG 或 ./config.json."""
    cfg_path = os.environ.get(
        "VISION_BRIDGE_CONFIG",
        str(_SERVER_DIR / "config.json"),
    )
    p = Path(cfg_path)
    if not p.exists():
        raise FileNotFoundError(
            f"未找到配置文件 {cfg_path}。\n"
            f"首次安装请: cp config.example.json config.json, "
            f"然后填 base_url / api_key / model。\n"
            f"详见 README.md。"
        )
    return json.loads(p.read_text())


def get_provider():
    """返回 provider 实例。openai_compat 缺字段时返 UnconfiguredProvider (不抛错)。"""
    cfg = load_config()
    name = cfg.get("provider", "openai_compat").lower()
    if name == "openai_compat":
        missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
        if missing:
            # 缺字段等同未装: 返回 fallback provider,不抛错、不阻塞
            return UnconfiguredProvider(missing=missing)
    cls = PROVIDERS.get(name)
    if not cls:
        raise ValueError(
            f"未知 provider='{name}', 可选: {list(PROVIDERS)}"
        )
    return cls(cfg)


def detect_media_type(path: str) -> str:
    if path.startswith(("http://", "https://")):
        # URL 不根据扩展名假定, 让 provider 自己处理
        # 默认按 image 处理（大多数 URL 都偏静态）
        return "image"
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    return "image"


@mcp.tool()
async def analyze_media(
    media_path: str,
    prompt: str = "",
    media_type: str = "auto",
    max_tokens: int = 1024,
) -> str:
    """分析图片或视频, 返回文本描述。

    Args:
        media_path: 本地文件路径或 http(s) URL
        prompt: 可选附加指令 (如"重点描述红色错误信息")
        media_type: "image" | "video" | "auto" (按扩展名推断)
        max_tokens: 最大输出 token 数

    Returns:
        文本描述 (或错误信息 string)
    """
    provider = get_provider()
    if media_type == "auto":
        media_type = detect_media_type(media_path)
    if media_type == "video" and not provider.supports_video:
        return (
            f"[错误] 当前 provider '{provider.name}' 不支持视频。"
            f"请切到 openai_compat (需装 ffmpeg) 或改传图片。"
        )
    return await provider.analyze(media_path, prompt, media_type, max_tokens)


if __name__ == "__main__":
    mcp.run()
