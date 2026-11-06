"""Shared validation and stable errors for the local outsource protocol."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


class OutsourceError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self):
        return {"ok": False, "error_code": self.code, "error": str(self), "details": self.details}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_json(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_absolute(path, must_exist=True):
    """Require an absolute, non-link path including every existing ancestor."""
    value = os.fspath(path)
    if value.replace('\\', '/').startswith(('//?/', '//./')):
        raise OutsourceError("unsafe_path", "Windows device namespace aliases are forbidden")
    candidate = Path(path)
    if os.name == 'nt' and (value.replace('\\', '/').startswith('//') or any(part.endswith(('.', ' ')) or ':' in part for part in candidate.parts[1:])):
        raise OutsourceError("unsafe_path", "Windows UNC, stream and ambiguous path aliases are forbidden")
    if not candidate.is_absolute():
        raise OutsourceError("unsafe_path", "An absolute path is required")
    for part in (candidate, *candidate.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OutsourceError("unsafe_path", "Links and reparse points are forbidden", {"path": str(part)})
    try:
        return candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise OutsourceError("unsafe_path", str(exc)) from exc
