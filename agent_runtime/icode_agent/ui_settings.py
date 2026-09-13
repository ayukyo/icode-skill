"""ICODE UI 非秘密偏好的严格、原子持久化。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict


DEFAULT_SETTINGS = {
    "host": "auto",
    "model": "",
    "mode": "simple",
    "fallback": True,
    "show_next_step": True,
    "preferred_port": 8765,
    "refresh_interval_seconds": 30,
}
ALLOWED_FIELDS = frozenset(DEFAULT_SETTINGS)


class SettingsError(ValueError):
    """UI 设置损坏或包含未登记内容。"""


def normalize_settings(payload, *, merge_defaults: bool = True) -> Dict:
    if not isinstance(payload, dict):
        raise SettingsError("设置 JSON 根必须是对象")
    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        raise SettingsError(f"设置含未登记字段: {unknown}")
    value = dict(DEFAULT_SETTINGS) if merge_defaults else {}
    value.update(payload)

    if value.get("host") not in {"auto", "codex", "claude"}:
        raise SettingsError("host 必须是 auto/codex/claude")
    if value.get("mode") not in {"simple", "advanced"}:
        raise SettingsError("mode 必须是 simple/advanced")
    model = value.get("model")
    if not isinstance(model, str) or len(model) > 160:
        raise SettingsError("model 必须是最多 160 字符的文本")
    for field in ("fallback", "show_next_step"):
        if not isinstance(value.get(field), bool):
            raise SettingsError(f"{field} 必须是布尔值")
    port = value.get("preferred_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise SettingsError("preferred_port 必须是 0..65535 的整数")
    refresh_interval = value.get("refresh_interval_seconds")
    if isinstance(refresh_interval, bool) \
            or not isinstance(refresh_interval, int) \
            or not 5 <= refresh_interval <= 300:
        raise SettingsError("refresh_interval_seconds 必须是 5..300 的整数")
    return value


class UISettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Dict:
        if not self.path.is_file():
            return dict(DEFAULT_SETTINGS)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SettingsError("设置文件不是可读的 UTF-8 JSON") from exc
        return normalize_settings(payload)

    def save(self, payload) -> Dict:
        settings = normalize_settings(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(settings, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, self.path)
            directory_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise SettingsError("设置文件写入失败") from exc
        return settings
