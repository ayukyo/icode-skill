"""vision-bridge MCP server 入口。

配置来源: $VISION_BRIDGE_CONFIG 指向的 JSON 文件 (默认 ./config.json)。
session 模型只收文本, 不接触原图/原视频。

启动: python -m mcp (stdin/stdout)
"""
import json
import hashlib
import os
import sys
from datetime import datetime, timezone
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
    p = Path(os.path.expandvars(os.path.expanduser(cfg_path)))
    if not p.exists():
        return {}
    config = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"配置顶层必须是 JSON object: {p}")
    return config


def get_provider():
    """返回 provider 实例。openai_compat 缺字段时返 UnconfiguredProvider (不抛错)。"""
    cfg = load_config()
    raw_name = cfg.get("provider", "openai_compat")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("provider 必须是非空字符串")
    name = raw_name.strip().lower()
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


def _input_identity(media_path: str) -> dict:
    if media_path.startswith(("http://", "https://")):
        raise ValueError(
            "evidence mode requires a local immutable file so input_sha256 can be verified")
    unresolved = Path(media_path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("media_path must be a regular non-symlink file")
    path = unresolved.resolve(strict=True)
    if not path.is_file():
        raise ValueError("media_path must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "source_path": str(path),
        "input_sha256": "sha256:" + digest.hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def capability_profile() -> dict:
    """Return a non-secret profile; configuration keys are never echoed wholesale."""
    provider = get_provider()
    profile = provider.capability_profile()
    profile.setdefault("configured", provider.name != "unconfigured")
    return profile


@mcp.tool()
async def describe_capabilities() -> str:
    """返回不含密钥的 bridge provider/model/已验证能力画像。"""
    return json.dumps(capability_profile(), ensure_ascii=False, indent=2)


def detect_media_type(path: str) -> str:
    if path.startswith(("http://", "https://")):
        # URL 不根据扩展名假定, 让 provider 自己处理
        # 默认按 image 处理（大多数 URL 都偏静态）
        return "image"
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    return "image"


def _validate_analysis_request(media_type: str, max_tokens: int) -> None:
    if media_type not in {"auto", "image", "video"}:
        raise ValueError("media_type 必须是 auto/image/video")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 1_000_000:
        raise ValueError("max_tokens 必须是 1..1000000 的整数")


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
    _validate_analysis_request(media_type, max_tokens)
    provider = get_provider()
    if media_type == "auto":
        media_type = detect_media_type(media_path)
    if media_type == "video" and not provider.supports_video:
        return (
            f"[错误] 当前 provider '{provider.name}' 不支持视频。"
            f"请切到 openai_compat (需装 ffmpeg) 或改传图片。"
        )
    return await provider.analyze(media_path, prompt, media_type, max_tokens)


async def _analyze_media_evidence(
    media_path: str,
    prompt: str = "",
    media_type: str = "auto",
    max_tokens: int = 1024,
    prompt_profile: str = "general-v1",
    profile_version: str = "v1",
    page: int | None = None,
    crop: str = "",
    dpi: int | None = None,
    tile_index: int | None = None,
) -> str:
    """分析媒体并返回带 hash、模型和页/裁剪来源的 JSON 证据包。"""
    _validate_analysis_request(media_type, max_tokens)
    if not isinstance(prompt_profile, str) or not prompt_profile.strip():
        raise ValueError("prompt_profile must be a non-empty string")
    if not isinstance(profile_version, str) or not profile_version.strip():
        raise ValueError("profile_version must be a non-empty string")
    provider = get_provider()
    effective_type = detect_media_type(media_path) if media_type == "auto" else media_type
    identity = _input_identity(media_path)
    crop_value = _parse_crop(crop)
    if page is not None and page <= 0:
        raise ValueError("page must be positive")
    if dpi is not None and dpi <= 0:
        raise ValueError("dpi must be positive")
    if tile_index is not None and tile_index < 0:
        raise ValueError("tile_index must be non-negative")
    provider_profile = provider.capability_profile()
    result = {
        "schema_version": 1,
        **identity,
        "media_kind": effective_type,
        "media_type": effective_type,
        "page": page,
        "crop": crop_value,
        "dpi": dpi,
        "tile_index": tile_index,
        "channel": "bridge",
        "provider": provider_profile.get("provider", "unknown"),
        "model": provider_profile.get("model", "unknown"),
        "provider_profile": provider_profile,
        "prompt_profile": prompt_profile,
        "profile_version": profile_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "confidence": None,
        "output_pointer": None,
        "limitations": [],
        "disagreement": None,
        "result": None,
    }
    if provider.name == "unconfigured":
        result["status"] = "failed"
        result["limitations"].append("bridge_unconfigured")
        return json.dumps(result, ensure_ascii=False, indent=2)
    if effective_type == "video" and not provider.supports_video:
        result["status"] = "failed"
        result["limitations"].append("provider_does_not_support_video")
        return json.dumps(result, ensure_ascii=False, indent=2)
    try:
        result["result"] = await provider.analyze(
            media_path, prompt, effective_type, max_tokens)
    except Exception as exc:
        result["status"] = "failed"
        result["limitations"].append(f"{type(exc).__name__}: {exc}")
    return json.dumps(result, ensure_ascii=False, indent=2)


def _parse_crop(value: str) -> list[int] | None:
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 4 or not all(item.isdigit() for item in parts):
        raise ValueError("crop must be x,y,width,height")
    parsed = [int(item) for item in parts]
    if parsed[2] <= 0 or parsed[3] <= 0:
        raise ValueError("crop width and height must be positive")
    return parsed


@mcp.tool()
async def analyze_media_evidence(
    media_path: str,
    prompt: str = "",
    media_type: str = "auto",
    max_tokens: int = 1024,
    prompt_profile: str = "general-v1",
    profile_version: str = "v1",
    page: int | None = None,
    crop: str = "",
    dpi: int | None = None,
    tile_index: int | None = None,
) -> str:
    """分析媒体并返回带 hash、模型和页/裁剪来源的 JSON 证据包。"""
    return await _analyze_media_evidence(
        media_path, prompt, media_type, max_tokens, prompt_profile,
        profile_version, page, crop, dpi, tile_index)


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
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--analyze-media", metavar="PATH", help="本地文件路径或 http(s) URL")
    mode.add_argument("--analyze-evidence", metavar="PATH", help="返回带来源的 JSON 证据包")
    mode.add_argument("--capabilities", action="store_true", help="输出不含密钥的能力画像")
    ap.add_argument("--prompt", default="", help="可选附加指令")
    ap.add_argument("--media-type", default="auto", choices=["auto", "image", "video"])
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--prompt-profile", default="general-v1")
    ap.add_argument("--profile-version", default="v1")
    ap.add_argument("--page", type=int)
    ap.add_argument("--crop", default="")
    ap.add_argument("--dpi", type=int)
    ap.add_argument("--tile-index", type=int)
    args = ap.parse_args(argv)

    if args.capabilities:
        try:
            print(json.dumps(capability_profile(), ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            print(f"[错误] {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    media_path = args.analyze_evidence or args.analyze_media

    async def _run() -> str:
        _validate_analysis_request(args.media_type, args.max_tokens)
        provider = get_provider()
        mt = args.media_type
        if mt == "auto":
            mt = detect_media_type(media_path)
        if mt == "video" and not provider.supports_video:
            return (
                f"[错误] 当前 provider '{provider.name}' 不支持视频。"
                f"请切到 openai_compat (需装 ffmpeg) 或改传图片。"
            )
        return await provider.analyze(media_path, args.prompt, mt, args.max_tokens)

    async def _run_evidence() -> str:
        return await _analyze_media_evidence(
            media_path=media_path,
            prompt=args.prompt,
            media_type=args.media_type,
            max_tokens=args.max_tokens,
            prompt_profile=args.prompt_profile,
            profile_version=args.profile_version,
            page=args.page,
            crop=args.crop,
            dpi=args.dpi,
            tile_index=args.tile_index,
        )

    try:
        result = asyncio.run(_run_evidence() if args.analyze_evidence else _run())
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # provider 内部错误(ffmpeg 缺失/API 错误等)也要可见
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in (
            "--analyze-media", "--analyze-evidence", "--capabilities", "-h", "--help"):
        sys.exit(_run_cli_analyze(sys.argv[1:]))
    mcp.run()
