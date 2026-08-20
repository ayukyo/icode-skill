"""vision-bridge MCP server 入口。

配置来源: $VISION_BRIDGE_CONFIG 指向的 JSON 文件 (默认 ./config.json)。
session 模型只收文本, 不接触原图/原视频。

启动: python -m mcp (stdin/stdout)
"""
import json
import os
import sys
from pathlib import Path

# 强制 stdout/stderr 用 UTF-8,兼容 Windows 默认 GBK 控制台。
# Linux/macOS 默认 UTF-8,这段是 no-op 无副作用。
# 必须在创建 FastMCP 之前执行,避免 MCP 内部 hook sys.stdout 时拿到 GBK 流。
# 出错用 errors='replace' 而非忽略,确保异常堆栈不会丢字。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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


def _run_cli_analyze(argv: list[str]) -> int:
    """本地 CLI 调用通道: 等价于 analyze_media 工具但走进程内调用。

    用途: 客户端(如 codex 用第三方模型)MCP 工具未注入、但能执行本地命令时,
    AI 可用 `python server.py --analyze-media <path> [--prompt ...]` 分析图片/视频,
    结果纯文本输出到 stdout, 由会话模型读取 —— 避免把原图/原视频塞给 session 模型。

    MCP 路径(mcp.run)行为完全不变; 本入口仅增加一个非 MCP 的文本返回通道。
    """
    import argparse
    import asyncio

    ap = argparse.ArgumentParser(
        prog="vision-bridge-cli",
        description="vision-bridge 本地 CLI 调用通道(等价 analyze_media 工具)",
    )
    ap.add_argument("--analyze-media", required=True, metavar="PATH", help="本地文件路径或 http(s) URL")
    ap.add_argument("--prompt", default="", help="可选附加指令")
    ap.add_argument("--media-type", default="auto", choices=["auto", "image", "video"])
    ap.add_argument("--max-tokens", type=int, default=1024)
    args = ap.parse_args(argv)

    async def _run() -> str:
        provider = get_provider()
        mt = args.media_type
        if mt == "auto":
            mt = detect_media_type(args.analyze_media)
        if mt == "video" and not provider.supports_video:
            return (
                f"[错误] 当前 provider '{provider.name}' 不支持视频。"
                f"请切到 openai_compat (需装 ffmpeg) 或改传图片。"
            )
        return await provider.analyze(args.analyze_media, args.prompt, mt, args.max_tokens)

    try:
        result = asyncio.run(_run())
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # provider 内部错误(ffmpeg 缺失/API 错误等)也要可见
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--analyze-media", "-h", "--help"):
        sys.exit(_run_cli_analyze(sys.argv[1:]))
    mcp.run()
