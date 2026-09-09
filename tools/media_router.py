#!/usr/bin/env python3
"""Choose a media-analysis channel and create reproducible evidence metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


MODES = {"auto", "native", "bridge", "dual", "text_only"}
CAPABILITY_STATES = {"supported", "unsupported", "unknown"}
BRIDGE_STATES = {"available", "unavailable", "unknown"}
TASK_REQUIREMENTS = {
    "general": {"visual_reasoning"},
    "ocr": {"ocr"},
    "small_text": {"small_text"},
    "table": {"table"},
    "diagram": {"diagram", "spatial_reasoning"},
    "schematic": {"schematic", "spatial_reasoning"},
    "video": {"video"},
}
SENSITIVE_CONFIG_KEYS = {
    "api_key", "apikey", "authorization", "token", "access_token",
    "refresh_token", "secret", "client_secret", "password",
}


class MediaRouteError(ValueError):
    """Raised when the routing or evidence input is invalid."""


def _safe_profile(raw: dict | None) -> dict:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise MediaRouteError("bridge profile must be a JSON object")
    capabilities = raw.get("declared_capabilities", [])
    if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item.strip() for item in capabilities):
        raise MediaRouteError("declared_capabilities must be a string array")
    quality = raw.get("quality_profile", {})
    if quality is None:
        quality = {}
    if not isinstance(quality, dict):
        raise MediaRouteError("quality_profile must be a JSON object")
    safe_quality = {
        str(key): value for key, value in quality.items()
        if not _is_sensitive_key(str(key))
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    return {
        "provider": _safe_text(raw.get("provider"), "unknown"),
        "model": _safe_text(raw.get("model"), "unknown"),
        "profile_version": _safe_text(raw.get("profile_version"), None),
        "declared_capabilities": sorted(set(capabilities)),
        "quality_profile": safe_quality,
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return normalized in SENSITIVE_CONFIG_KEYS or any(
        marker in normalized for marker in ("password", "secret", "token", "api_key"))


def _safe_text(value, default: str | None) -> str | None:
    return value if isinstance(value, str) and value.strip() else default


def load_profile(path: str | None) -> dict:
    if not path:
        return _safe_profile(None)
    profile_path = Path(path).expanduser().resolve(strict=True)
    return _safe_profile(json.loads(profile_path.read_text(encoding="utf-8")))


def bridge_qualification(task: str, profile: dict) -> tuple[str, list[str]]:
    required = TASK_REQUIREMENTS[task]
    declared = set(profile["declared_capabilities"])
    missing = sorted(required - declared)
    if not declared:
        return "unknown", missing
    return ("qualified", []) if not missing else ("unqualified", missing)


def route_media(mode: str, native: str, bridge: str, task: str,
                risk: str, profile: dict) -> dict:
    if mode not in MODES:
        raise MediaRouteError(f"unsupported mode: {mode}")
    if native not in CAPABILITY_STATES:
        raise MediaRouteError(f"unsupported native capability state: {native}")
    if bridge not in BRIDGE_STATES:
        raise MediaRouteError(f"unsupported bridge state: {bridge}")
    if task not in TASK_REQUIREMENTS:
        raise MediaRouteError(f"unsupported task: {task}")
    if risk not in {"normal", "high"}:
        raise MediaRouteError(f"unsupported risk: {risk}")

    qualification, missing = bridge_qualification(task, profile)
    bridge_ready = bridge == "available"
    native_ready = native == "supported"
    selected = mode
    rationale: list[str] = []

    if mode == "auto":
        if native_ready and risk == "high" and bridge_ready:
            selected = "dual"
            rationale.append("high-risk evidence uses independent native and bridge review")
        elif native_ready:
            selected = "native"
            rationale.append("host explicitly attests native multimodal support")
        elif bridge_ready:
            selected = "bridge"
            rationale.append("native support is not attested; bridge avoids unsafe trial injection")
        else:
            selected = "text_only"
            rationale.append("no attested visual channel is available")

    if selected == "native" and not native_ready:
        return _blocked_result(mode, selected, native, bridge, task, risk,
                               profile, qualification, missing,
                               "native mode requires explicit host-attested support")
    if selected == "bridge" and not bridge_ready:
        return _blocked_result(mode, selected, native, bridge, task, risk,
                               profile, qualification, missing,
                               "bridge mode requires a healthy configured bridge")
    if selected == "dual" and not (native_ready and bridge_ready):
        return _blocked_result(mode, selected, native, bridge, task, risk,
                               profile, qualification, missing,
                               "dual mode requires both attested native support and a healthy bridge")

    primary = {
        "native": "native",
        "bridge": "bridge",
        "dual": "native",
        "text_only": "deterministic_text",
    }[selected]
    secondary = "bridge" if selected == "dual" else None
    status = "ready"
    claim_ceiling = "visual conclusion may be used with source-bound evidence"
    if selected == "text_only":
        status = "manual_visual_gap"
        claim_ceiling = "text and metadata only; visual or spatial claims remain unresolved"
    elif selected in {"bridge", "dual"} and qualification != "qualified":
        status = "partial"
        claim_ceiling = (
            "bridge output is candidate evidence only until source-level or qualified visual "
            "verification closes the missing capabilities"
        )
        rationale.append("bridge quality profile does not prove every task capability")
    if selected == "dual":
        rationale.append("channel disagreement must remain unresolved; neither result overwrites the other")

    return {
        "schema_version": 1,
        "requested_mode": mode,
        "selected_mode": selected,
        "status": status,
        "task": task,
        "risk": risk,
        "primary_channel": primary,
        "secondary_channel": secondary,
        "native_media_injection_allowed": selected in {"native", "dual"},
        "native_capability": native,
        "bridge_state": bridge,
        "bridge_profile": profile,
        "bridge_qualification": qualification,
        "missing_bridge_capabilities": missing,
        "rationale": rationale,
        "claim_ceiling": claim_ceiling,
        "evidence_contract": evidence_contract(),
    }


def _blocked_result(mode: str, selected: str, native: str, bridge: str,
                    task: str, risk: str, profile: dict, qualification: str,
                    missing: list[str], reason: str) -> dict:
    return {
        "schema_version": 1,
        "requested_mode": mode,
        "selected_mode": selected,
        "status": "blocked",
        "task": task,
        "risk": risk,
        "primary_channel": None,
        "secondary_channel": None,
        "native_media_injection_allowed": False,
        "native_capability": native,
        "bridge_state": bridge,
        "bridge_profile": profile,
        "bridge_qualification": qualification,
        "missing_bridge_capabilities": missing,
        "rationale": [reason],
        "claim_ceiling": "no visual claim",
        "evidence_contract": evidence_contract(),
    }


def evidence_contract() -> dict:
    return {
        "required": [
            "input_sha256", "source_path", "media_kind", "channel", "provider",
            "model", "prompt_profile", "profile_version", "timestamp", "status",
        ],
        "region_fields": ["page", "crop", "dpi", "tile_index"],
        "result_fields": ["confidence", "output_pointer", "limitations", "disagreement"],
        "secret_fields_prohibited": sorted(SENSITIVE_CONFIG_KEYS),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def parse_crop(value: str | None) -> list[int] | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d+,\d+,\d+,\d+", value):
        raise MediaRouteError("crop must be x,y,width,height")
    crop = [int(item) for item in value.split(",")]
    if crop[2] <= 0 or crop[3] <= 0:
        raise MediaRouteError("crop width and height must be positive")
    return crop


def build_evidence(args: argparse.Namespace) -> dict:
    unresolved = Path(args.path).expanduser()
    if unresolved.is_symlink():
        raise MediaRouteError("evidence source must be a regular non-symlink file")
    source = unresolved.resolve(strict=True)
    if not source.is_file():
        raise MediaRouteError("evidence source must be a regular non-symlink file")
    return {
        "schema_version": 1,
        "input_sha256": sha256_file(source),
        "source_path": str(source),
        "size_bytes": source.stat().st_size,
        "media_kind": args.media_kind,
        "page": args.page,
        "crop": parse_crop(args.crop),
        "dpi": args.dpi,
        "tile_index": args.tile_index,
        "channel": args.channel,
        "provider": args.provider,
        "model": args.model,
        "prompt_profile": args.prompt_profile,
        "profile_version": args.profile_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": args.status,
        "confidence": None,
        "output_pointer": args.output_pointer,
        "limitations": [],
        "disagreement": None,
    }


def tile_plan(width: int, height: int, tile_size: int, overlap: int) -> dict:
    if min(width, height, tile_size) <= 0:
        raise MediaRouteError("width, height, and tile-size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise MediaRouteError("overlap must be >= 0 and smaller than tile-size")
    step = tile_size - overlap
    x_count = max(1, math.ceil(max(0, width - overlap) / step))
    y_count = max(1, math.ceil(max(0, height - overlap) / step))
    tiles = []
    index = 0
    for row in range(y_count):
        y = min(row * step, max(0, height - tile_size))
        for column in range(x_count):
            x = min(column * step, max(0, width - tile_size))
            crop_width = min(tile_size, width - x)
            crop_height = min(tile_size, height - y)
            tiles.append({
                "tile_index": index,
                "row": row,
                "column": column,
                "crop": [x, y, crop_width, crop_height],
            })
            index += 1
    unique = []
    seen = set()
    for tile in tiles:
        key = tuple(tile["crop"])
        if key not in seen:
            seen.add(key)
            unique.append(tile)
    return {
        "schema_version": 1,
        "source_dimensions": [width, height],
        "tile_size": tile_size,
        "overlap": overlap,
        "tiles": unique,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    route = subparsers.add_parser("route", help="select the safe media-analysis channel")
    route.add_argument("--mode", choices=sorted(MODES), default="auto")
    route.add_argument("--native", choices=sorted(CAPABILITY_STATES), default="unknown")
    route.add_argument("--bridge", choices=sorted(BRIDGE_STATES), default="unknown")
    route.add_argument("--bridge-profile")
    route.add_argument("--task", choices=sorted(TASK_REQUIREMENTS), default="general")
    route.add_argument("--risk", choices=["normal", "high"], default="normal")

    evidence = subparsers.add_parser("evidence", help="hash and describe one analyzed region")
    evidence.add_argument("--path", required=True)
    evidence.add_argument("--media-kind", choices=["image", "video", "pdf_page"], required=True)
    evidence.add_argument("--channel", choices=["native", "bridge", "deterministic_text"], required=True)
    evidence.add_argument("--provider", required=True)
    evidence.add_argument("--model", required=True)
    evidence.add_argument("--prompt-profile", required=True)
    evidence.add_argument("--profile-version", required=True)
    evidence.add_argument("--page", type=int)
    evidence.add_argument("--crop")
    evidence.add_argument("--dpi", type=int)
    evidence.add_argument("--tile-index", type=int)
    evidence.add_argument("--status", choices=["success", "partial", "failed"], default="success")
    evidence.add_argument("--output-pointer")

    tiles = subparsers.add_parser("tiles", help="create deterministic overlapping crop coordinates")
    tiles.add_argument("--width", type=int, required=True)
    tiles.add_argument("--height", type=int, required=True)
    tiles.add_argument("--tile-size", type=int, default=1536)
    tiles.add_argument("--overlap", type=int, default=128)

    args = parser.parse_args()
    try:
        if args.command == "route":
            result = route_media(args.mode, args.native, args.bridge, args.task,
                                 args.risk, load_profile(args.bridge_profile))
        elif args.command == "evidence":
            result = build_evidence(args)
        else:
            result = tile_plan(args.width, args.height, args.tile_size, args.overlap)
    except (MediaRouteError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
