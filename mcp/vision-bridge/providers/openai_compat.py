"""OpenAI Chat Completions 兼容 provider - 唯一主 provider。

覆盖任何遵守 OpenAI 接口标准的视觉 API:
  - 官方 OpenAI
  - Claude 通过 OpenAI 代理
  - MiniMax M3 等国产模型
  - Gemini 通过 openai-compat 端点
  - OpenRouter 聚合服务
  - 自建 OpenAI 兼容服务

视频走 ffmpeg 抽帧 + 多张 image_url,不依赖任何平台特定视频字段。
"""
import base64
import os
import shutil
import subprocess
import tempfile
from mimetypes import guess_type
from pathlib import Path

import httpx

from providers.base import MediaProvider


VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class OpenAICompatProvider(MediaProvider):
    """完全驱动 OpenAI Chat Completions 兼容 API 的通用 provider.

    必需的 config 字段:
        base_url: API 端点 (不带尾 /)
        api_key:  鉴权 KEY
        model:    模型名

    可选字段:
        timeout:      HTTP 超时秒 (默认 120)
        video_frames: 视频抽帧数 (默认 8)
    """

    name = "openai_compat"
    supports_video = True

    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config["api_key"]
        self.model = config["model"]
        self.timeout = int(config.get("timeout", 120))
        self.video_frames = int(config.get("video_frames", 8))

    # ---------- 辅助:本地文件转 data URL ----------

    def _to_data_url(self, path: str) -> str:
        """本地文件 -> data URL;http(s) URL 直接返回。"""
        if path.startswith(("http://", "https://")):
            return path
        p = Path(path)
        mime, _ = guess_type(str(p))
        if not mime:
            mime = "application/octet-stream"
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    # ---------- 视频抽帧(ffmpeg) ----------

    def _extract_video_frames(self, video_path: str) -> list[str]:
        """ffmpeg 均匀抽帧成临时图片,返回 [data_url, ...]。"""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError(
                "video 需要 ffmpeg/ffmpeg。请先安装:sudo apt install ffmpeg"
            )
        frames = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pattern = str(tmp_path / "frame_%03d.jpg")
            try:
                probe = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        video_path,
                    ],
                    capture_output=True, text=True, check=True,
                )
                duration = float(probe.stdout.strip() or 1.0)
                interval = max(duration / self.video_frames, 0.1)
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", video_path,
                        "-vf", f"fps=1/{interval}",
                        "-frames:v", str(self.video_frames),
                        pattern,
                    ],
                    check=True, capture_output=True,
                )
                for img in sorted(tmp_path.glob("frame_*.jpg")):
                    mime, _ = guess_type(str(img))
                    b64 = base64.b64encode(img.read_bytes()).decode()
                    frames.append(f"data:image/jpeg;base64,{b64}")
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"ffmpeg 抽帧失败: {e.stderr or e}") from e
        return frames

    # ---------- 主入口 ----------

    async def analyze(
        self,
        media_path: str,
        prompt: str,
        media_type: str,
        max_tokens: int = 1024,
    ) -> str:
        if media_type == "video":
            return await self._video(media_path, prompt, max_tokens)
        return await self._image_or_url(media_path, prompt, max_tokens)

    async def _image_or_url(self, path: str, prompt: str, max_tokens: int) -> str:
        data_url = self._to_data_url(path)
        return await self._chat(
            content=[
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt or "请详细描述这张图片。"},
            ],
            max_tokens=max_tokens,
        )

    async def _video(self, path: str, prompt: str, max_tokens: int) -> str:
        frames = self._extract_video_frames(path)
        if not frames:
            return "[错误] 视频抽帧失败,请检查 ffmpeg 与文件。"
        content = [{"type": "image_url", "image_url": {"url": f}} for f in frames]
        content.append({
            "type": "text",
            "text": prompt or f"以下是视频的 {len(frames)} 帧关键画面,请综合描述视频内容。",
        })
        return await self._chat(content=content, max_tokens=max_tokens)

    async def _chat(self, content: list, max_tokens: int) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": max_tokens,
                },
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
