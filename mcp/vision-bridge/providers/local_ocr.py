"""零 KEY fallback: 本地 Tesseract OCR, 仅抽图片文字。

不支持视频。不能"理解"图片内容,只 OCR。
需本地装 tesseract:
    Ubuntu/Debian: sudo apt install tesseract-ocr tesseract-ocr-chi-sim
    macOS:          brew install tesseract tesseract-lang
"""
import shutil
import subprocess
from pathlib import Path

from providers.base import MediaProvider


class LocalOcrProvider(MediaProvider):
    """Tesseract OCR, 零 KEY 零费用, 仅图片文字提取."""

    name = "local_ocr"
    supports_video = False

    def __init__(self, config: dict):
        self.lang = config.get("lang", "chi_sim+eng")
        if not shutil.which("tesseract"):
            raise RuntimeError(
                "local_ocr 需要 tesseract CLI。"
                "Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-chi-sim"
            )

    async def analyze(
        self,
        media_path: str,
        prompt: str,
        media_type: str,
        max_tokens: int = 1024,
    ) -> str:
        if media_type == "video":
            return "[错误] local_ocr 仅支持图片, 请换 openai_compat provider 或抽帧后再传。"

        try:
            out = subprocess.run(
                ["tesseract", str(media_path), "-", "-l", self.lang],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"tesseract 失败: {e.stderr or e}") from e

        text = out.stdout.strip()
        if not text:
            return "[OCR] 未识别到文字 (可能图片无文字或语言包不匹配)。"
        header = "[OCR 文字提取结果 — 不理解内容, 仅文字]"
        return f"{header}\n{text}"
