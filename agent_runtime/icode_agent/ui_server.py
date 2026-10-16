"""ICODE Agent Runtime 的本地、单工单 Web UI 服务。"""

import importlib.util
import json
import os
import re
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict
from urllib.parse import parse_qs, unquote, urlsplit

from .backends.base import AgentBackend, BackendError
from .backends.fake import FakeBackend
from .backends.openai_responses import OpenAIResponsesBackend
from .coordinator import AgentCoordinator, ControlPlaneError, LifecycleAmbiguityError
from .host_runner import ControlLifecycle, HostJobRunner, HostRunnerError
from .models import AgentRequest, CapabilityError
from .ticket_catalog import CatalogError, ResolvedTicket, TicketCatalog
from .ui_settings import SettingsError, UISettingsStore


MAX_REQUEST_BYTES = 64 * 1024
MAX_IMAGE_REFERENCES = 4
MAX_OUTPUT_TOKENS = 32768
MAX_STAGE_PREVIEWS = 5
LOOPBACK_HOST = "127.0.0.1"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_BACKENDS = frozenset({"fake", "openai-responses"})
ALLOWED_FIELDS = frozenset({
    "backend",
    "model",
    "capabilities",
    "prompt",
    "instructions",
    "task_scope",
    "expected_artifact",
    "evidence_boundary",
    "join_condition",
    "request_id",
    "max_output_tokens",
    "images",
    "fake_response",
})
REQUIRED_FIELDS = frozenset({
    "backend",
    "model",
    "capabilities",
    "prompt",
    "task_scope",
    "expected_artifact",
    "evidence_boundary",
    "join_condition",
    "request_id",
})
STEP_RUN_FIELDS = frozenset({
    "ticket_id", "step", "note", "request_id", "expected_revision",
})
CREATE_TICKET_FIELDS = frozenset({"project_id", "requirement", "request_id"})
STAGE_DECISION_REQUIRED = frozenset({
    "ticket_id", "program_id", "stage_id", "challenge_id", "facts_digest",
    "report_sha256", "revision", "decision", "request_id",
})
COCKPIT_ARTIFACTS = frozenset({
    "00_init.md", "log_analysis.md", "01_plan.md", "02_review.md",
    "03_plan_final.md", "04_code_review_fix.md", "05_deepcheck.md",
    "06_audit.md", "07_readme.md", "08_patch.md", "ticket_snapshot.json",
    "archive_manifest.json", "doc_worklist.json", "_brief.md",
})
PROGRESS_STEPS = (
    ("0", "需求确认"),
    ("1", "制定计划"),
    ("2", "审查方案"),
    ("3", "合并定稿"),
    ("4", "编码实现"),
    ("5", "深度复检"),
    ("6", "最终验收"),
)
STATUS_CURRENT_STEP = {
    "log_in_progress": "0", "log_done": "0", "init_in_progress": "0",
    "plan_done": "2", "review_in_progress": "2", "review_done": "3",
    "plan_finalized": "4", "code_in_progress": "4", "code_done": "5",
    "deepcheck_in_progress": "5", "deepcheck_done": "6", "completed": None,
    "debug_in_progress": "0", "debug_done": None,
}
HOST_ERROR_GUIDANCE = {
    "ticket_busy": (
        "这个工单已有任务在运行，请等待完成或先取消现有任务。", "wait"),
    "global_busy": ("当前运行任务较多，请稍后再试。", "wait"),
    "host_unavailable": (
        "没有检测到可用的 Codex 或 Claude Code，请检查运行设置。", "settings"),
    "invalid_host": ("Agent 主机设置无效，请重新选择运行方式。", "settings"),
    "invalid_model": ("模型设置无效，请检查后重试。", "settings"),
    "invalid_revision": ("工单版本已变化，请刷新后重新确认操作。", "refresh"),
    "stale_revision": ("工单已被其他操作更新，请刷新后重试。", "refresh"),
    "duplicate_request": ("这次操作已经提交过，不会重复执行。", "refresh"),
    "ticket_read_only": ("该工单当前只读，请先查看阻断原因。", "inspect"),
    "job_not_found": ("任务记录不存在，可能已在重启后清理。", "refresh"),
}
ASSET_ROOT = Path(__file__).resolve().parent / "ui_assets"
STATIC_ASSETS = {
    "/assets/app.js": (ASSET_ROOT / "app.js", "text/javascript; charset=utf-8"),
    "/assets/style.css": (ASSET_ROOT / "style.css", "text/css; charset=utf-8"),
    "/assets/icode-ticket-hex.svg": (
        ASSET_ROOT / "icode-ticket-hex.svg",
        "image/svg+xml",
    ),
}
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


class UIAccessError(RuntimeError):
    """浏览器请求不满足本机访问边界。"""


class UIRequestError(ValueError):
    """HTTP 请求结构无效。"""


BackendFactory = Callable[[str, Dict], AgentBackend]


def _default_backend_factory(backend_name: str, payload: Dict) -> AgentBackend:
    if backend_name == "fake":
        return FakeBackend(response=payload.get("fake_response", "fake response"))
    if backend_name == "openai-responses":
        return OpenAIResponsesBackend()
    raise UIRequestError(f"未知 backend: {backend_name!r}")


def _require_text(payload: Dict, field: str, *, default=None) -> str:
    value = payload.get(field, default)
    if not isinstance(value, str) or not value.strip():
        raise UIRequestError(f"{field} 必须是非空文本")
    return value.strip()


def _optional_text(payload: Dict, field: str, *, default="") -> str:
    value = payload.get(field, default)
    if not isinstance(value, str):
        raise UIRequestError(f"{field} 必须是文本")
    return value


def normalize_run_payload(payload) -> Dict:
    """把不可信 UI JSON 收敛到 AgentRequest 可消费的严格对象。"""
    if not isinstance(payload, dict):
        raise UIRequestError("请求 JSON 根必须是对象")
    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        raise UIRequestError(f"请求含未登记字段: {unknown}")
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        raise UIRequestError(f"请求缺字段: {missing}")

    backend = _require_text(payload, "backend")
    if backend not in ALLOWED_BACKENDS:
        raise UIRequestError(f"未知 backend: {backend!r}")
    if backend != "fake" and "fake_response" in payload:
        raise UIRequestError("fake_response 只能与 fake backend 一起使用")

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities \
            or any(not isinstance(item, str) for item in capabilities):
        raise UIRequestError("capabilities 必须是非空文本数组")
    if len(set(capabilities)) != len(capabilities):
        raise UIRequestError("capabilities 不得重复")

    images = payload.get("images", [])
    if not isinstance(images, list) or any(
            not isinstance(item, str) or not item.strip() for item in images):
        raise UIRequestError("images 必须是非空文本组成的数组")
    if len(images) > MAX_IMAGE_REFERENCES:
        raise UIRequestError(
            f"第一版 UI 每次最多 {MAX_IMAGE_REFERENCES} 个 image 引用")

    max_output_tokens = payload.get("max_output_tokens", 2048)
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise UIRequestError("max_output_tokens 必须是整数")
    if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise UIRequestError(
            f"max_output_tokens 必须在 1..{MAX_OUTPUT_TOKENS} 范围内")

    return {
        "backend": backend,
        "model": _require_text(payload, "model"),
        "capabilities": list(capabilities),
        "prompt": _require_text(payload, "prompt"),
        "instructions": _optional_text(payload, "instructions"),
        "task_scope": _require_text(payload, "task_scope"),
        "expected_artifact": _require_text(payload, "expected_artifact"),
        "evidence_boundary": _require_text(payload, "evidence_boundary"),
        "join_condition": _require_text(payload, "join_condition"),
        "request_id": _require_text(payload, "request_id"),
        "max_output_tokens": max_output_tokens,
        "images": [item.strip() for item in images],
        "fake_response": _optional_text(
            payload, "fake_response", default="fake response"),
    }


def normalize_step_run_payload(payload) -> Dict:
    if not isinstance(payload, dict):
        raise UIRequestError("请求 JSON 根必须是对象")
    unknown = sorted(set(payload) - STEP_RUN_FIELDS)
    if unknown:
        raise UIRequestError(f"请求含未登记字段: {unknown}")
    missing = sorted(STEP_RUN_FIELDS - set(payload))
    if missing:
        raise UIRequestError(f"请求缺字段: {missing}")
    note = _optional_text(payload, "note")
    return {
        "ticket_id": _require_text(payload, "ticket_id"),
        "step": _require_text(payload, "step"),
        "note": note,
        "request_id": _require_text(payload, "request_id"),
        "expected_revision": _require_text(payload, "expected_revision"),
    }


def normalize_create_ticket_payload(payload) -> Dict:
    if not isinstance(payload, dict):
        raise UIRequestError("请求 JSON 根必须是对象")
    unknown = sorted(set(payload) - CREATE_TICKET_FIELDS)
    if unknown:
        raise UIRequestError(f"请求含未登记字段: {unknown}")
    missing = sorted(CREATE_TICKET_FIELDS - set(payload))
    if missing:
        raise UIRequestError(f"请求缺字段: {missing}")
    requirement = _require_text(payload, "requirement")
    if len(requirement) > 8000:
        raise UIRequestError("requirement 最长 8000 字符")
    return {
        "project_id": _require_text(payload, "project_id"),
        "requirement": requirement,
        "request_id": _require_text(payload, "request_id"),
    }


def normalize_stage_decision_payload(payload) -> Dict:
    if not isinstance(payload, dict):
        raise UIRequestError("请求 JSON 根必须是对象")
    if set(payload) - (STAGE_DECISION_REQUIRED | {"reason"}):
        raise UIRequestError("阶段确认请求含未登记字段")
    if STAGE_DECISION_REQUIRED - set(payload):
        raise UIRequestError("阶段确认请求缺字段")
    normalized = {key: _require_text(payload, key) for key in STAGE_DECISION_REQUIRED}
    if normalized["decision"] not in {"confirm", "reject", "revoke"}:
        raise UIRequestError("阶段动作必须是 confirm/reject/revoke")
    normalized["reason"] = _optional_text(payload, "reason")
    if any(len(value) > 8000 for value in normalized.values()):
        raise UIRequestError("阶段请求字段过长")
    return normalized


class AgentUIService:
    """兼容固定工单模式，并为 v2 提供全局只读目录册和受控步骤执行。"""

    def __init__(self, ticket_dir: Path | None, control_path: Path,
                 backend_factory: BackendFactory | None = None,
                 index_path: Path | None = None,
                 settings_path: Path | None = None,
                 host_runner=None,
                 seed_project_paths=None,
                 initial_ticket_id: str | None = None,
                 initial_project_id: str | None = None,
                 instance_id: str | None = None):
        self.ticket_dir = Path(ticket_dir).resolve() if ticket_dir else None
        self.coordinator = AgentCoordinator(control_path=control_path)
        self._stage_module = None
        self._preview_visual_module = None
        self.backend_factory = backend_factory or _default_backend_factory
        self.token = secrets.token_urlsafe(32)
        self.catalog = TicketCatalog(
            index_path or (Path.home() / ".claude" / "icode_data" / "index.json"),
            seed_projects=seed_project_paths,
        )
        self.settings_store = UISettingsStore(
            settings_path or (Path.home() / ".claude" / "icode_data" / "ui_settings.json"))
        self.lifecycle = ControlLifecycle(control_path)
        jobs_path = (Path(settings_path).expanduser().parent / "ui_jobs.json") \
            if settings_path else (
                Path.home() / ".claude" / "icode_data" / "ui_jobs.json")
        self.host_runner = host_runner or HostJobRunner(
            lifecycle=self.lifecycle, recovery_path=jobs_path)
        self.initial_ticket_id = initial_ticket_id
        self.initial_project_id = initial_project_id
        self.instance_id = instance_id or secrets.token_urlsafe(18)
        self.pinned_ticket_id = None
        if self.ticket_dir is not None:
            # 绑定端口前先验证工单身份和事件链，非法工单不启动 UI。
            self.pinned_ticket_id = self.coordinator.status(
                self.ticket_dir)["ticket_id"]
            if self.initial_ticket_id is None:
                self.initial_ticket_id = self.pinned_ticket_id

    def status(self) -> Dict:
        if self.ticket_dir is None:
            raise UIRequestError("全局模式没有固定工单；请使用 tickets API")
        return self.coordinator.status(self.ticket_dir)

    def run(self, payload) -> Dict:
        if self.ticket_dir is None:
            raise UIRequestError("全局模式禁止旧 /run 暗选工单")
        normalized = normalize_run_payload(payload)
        request = AgentRequest(
            ticket_dir=self.ticket_dir,
            task_scope=normalized["task_scope"],
            expected_artifact=normalized["expected_artifact"],
            evidence_boundary=normalized["evidence_boundary"],
            join_condition=normalized["join_condition"],
            backend=normalized["backend"],
            model=normalized["model"],
            capabilities=frozenset(normalized["capabilities"]),
            prompt=normalized["prompt"],
            request_id=normalized["request_id"],
            instructions=normalized["instructions"],
            images=tuple(normalized["images"]),
            max_output_tokens=normalized["max_output_tokens"],
        )
        backend = self.backend_factory(normalized["backend"], normalized)
        result = self.coordinator.run(request, backend)
        return {
            "ok": True,
            "backend": normalized["backend"],
            "model": normalized["model"],
            "result": result.to_dict(),
        }

    def tickets(self, *, project_id=None, query=None, status=None) -> Dict:
        return self.catalog.snapshot(
            project_id=project_id, query=query, status=status)

    @staticmethod
    def _public_policy(policy: Dict) -> Dict:
        revision = policy.get("revision") or {}
        return {
            "status": policy.get("status"),
            "debug": bool(policy.get("debug")),
            "close_state": policy.get("close_state"),
            "allowed_actions": list(policy.get("allowed_actions") or []),
            "next_step": policy.get("recommended_action"),
            "blocked_reason": policy.get("blocked_reason"),
            "revision": {
                "token": revision.get("token"),
                "event_count": revision.get("event_count"),
            },
            "open_execution": {
                "steps": len(policy.get("open_steps") or {}),
                "operations": len(policy.get("open_operations") or {}),
                "agents": len(policy.get("open_agents") or {}),
            },
            "validation": {"ok": True, "mode": "lightweight"},
        }

    @staticmethod
    def _load_json_projection(path: Path) -> Dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _progress_projection(metadata: Dict) -> list[Dict]:
        completed = {str(item) for item in metadata.get("completed_steps") or []}
        status = metadata.get("status")
        current = STATUS_CURRENT_STEP.get(status, "0")
        progress = []
        for step_id, label in PROGRESS_STEPS:
            if status in {"completed", "debug_done"}:
                state = "done"
            elif step_id == current:
                state = "current"
            elif step_id in completed:
                state = "done"
            else:
                state = "pending"
            progress.append({"id": step_id, "label": label, "state": state})
        return progress

    def _cockpit_projection(self, target: ResolvedTicket) -> Dict:
        metadata = self._load_json_projection(target.out_dir / ".ico_metadata.json")
        snapshot = self._load_json_projection(target.out_dir / "ticket_snapshot.json")
        runs = metadata.get("verification_runs") or []
        if not isinstance(runs, list):
            runs = []
        outcomes = {"pass": 0, "fail": 0, "blocked": 0, "other": 0}
        for run in runs:
            outcome = run.get("outcome") if isinstance(run, dict) else None
            key = outcome if outcome in {"pass", "fail", "blocked"} else "other"
            outcomes[key] += 1
        open_items = snapshot.get("open_items") or {}
        pending = open_items.get("pending_verification") or [] \
            if isinstance(open_items, dict) else []
        artifacts = []
        delivery = metadata.get("delivery_files")
        delivery_names = {value for value in delivery.values()
                          if isinstance(value, str) and Path(value).name == value
                          and value.endswith(".md")} if isinstance(delivery, dict) else set()
        try:
            children = sorted(target.out_dir.iterdir(), key=lambda item: item.name)
        except OSError:
            children = []
        for child in children:
            if child.name not in COCKPIT_ARTIFACTS | delivery_names or not child.is_file() \
                    or child.is_symlink():
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            role = "snapshot" if child.name == "ticket_snapshot.json" else (
                "report" if child.suffix == ".md" else "control")
            artifacts.append({"name": child.name, "role": role, "size": size})
        verdict = metadata.get("delivery_verdict")
        if verdict not in {
                "verified", "verification_pending", "blocked", "not_applicable"}:
            verdict = "unknown"
        return {
            "progress": self._progress_projection(metadata),
            "delivery": {
                "verdict": verdict,
                "close_state": metadata.get("close_state"),
            },
            "verification": {
                "total": len(runs),
                **outcomes,
                "pending": len(pending) if isinstance(pending, list) else 0,
            },
            "artifacts": artifacts,
            "blockers": pending[:20] if isinstance(pending, list) else [],
        }

    def ticket_detail(self, ticket_id: str) -> Dict:
        public = self.catalog.public_ticket(ticket_id)
        if public.get("generation") != "v3" or not public.get("executable"):
            return {
                **public,
                "allowed_actions": [],
                "next_step": None,
                "blocked_reason": "legacy_or_ambiguous",
                "revision": None,
                "open_execution": {"steps": 0, "operations": 0, "agents": 0},
                "validation": {"ok": False, "mode": "legacy-read-only"},
            }
        target = self.catalog.resolve(ticket_id)
        policy = self.lifecycle.projection(target)
        return {
            **public,
            **self._public_policy(policy),
            "cockpit": self._cockpit_projection(target),
            "stage": self._stage_projection(target),
        }

    def _stage_control(self):
        # The executable comes from the existing server startup configuration,
        # never from a browser path. A Python adapter is required here because
        # an ordinary CLI flag must not manufacture human confirmation origin.
        if self._stage_module is None:
            try:
                spec = importlib.util.spec_from_file_location(
                    "icode_ui_stage_control", self.coordinator.control_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if not callable(getattr(module, "stage_projection", None)) \
                        or not callable(getattr(module, "confirm_stage", None)):
                    raise ValueError("stage control adapter unavailable")
            except Exception as exc:
                raise ControlPlaneError("阶段控制适配器不可用") from exc
            self._stage_module = module
        return self._stage_module

    def _stage_projection(self, target: ResolvedTicket) -> Dict:
        try:
            result = self._stage_control().stage_projection(target.out_dir)
        except Exception as exc:
            raise ControlPlaneError("阶段状态不可验证") from exc
        report = result.get("report")
        if isinstance(report, dict) and report.get("report_sha256"):
            try:
                entries, _reader = self._stage_preview_entries(target, report["report_sha256"])
                report["previews"] = [entry["public"] for entry in entries]
            except Exception:
                # Retain readable report text, but never offer confirmation
                # when its frozen preview references cannot be established.
                report["previews"] = []
                result["preview_error"] = "报告预览已失效，请刷新并重新审核。"
                result["can_confirm"] = False
        return result

    def _stage_preview_entries(self, target: ResolvedTicket, report_sha256: str):
        """Resolve only protected, report-bound indices; never Markdown links."""
        if not isinstance(report_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", report_sha256):
            raise UIRequestError("report_sha256 必须是 SHA-256 摘要")
        control = self._stage_control()
        stage = control.stage_module()
        state = stage._resolve(target.out_dir)
        if state is None or not state.get("stage_id"):
            raise ControlPlaneError("没有可查看的阶段报告")
        review = stage._latest_review(state["program"], state["stage_id"])
        if not review or review.get("report_sha256") != report_sha256:
            raise ControlPlaneError("阶段报告已变化，请刷新")
        stage._check_report(target.out_dir, review)
        snapshot = review["snapshot"]
        reader = control.verification_evidence_module().bundle_module().Reader(target.out_dir)
        receipts = snapshot["evidence_metadata"]["extensions"]["verification"].get("visual_reviews") or []
        entries = []
        for run in snapshot["accepted_runs"]:
            ref = run.get("evidence_bundle")
            if ref is None:
                continue
            if not isinstance(ref.get("sha256"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", ref["sha256"]):
                raise ControlPlaneError("阶段证据包摘要无效")
            bundle = reader.json(reader.read(ref["path"], ref["sha256"]))
            if any(bundle.get(key) != run.get(key) for key in ("run_id", "candidate_id")):
                raise ControlPlaneError("阶段证据包来源不匹配")
            items = {item["id"]: item for item in bundle["items"]}
            if len(items) != len(bundle["items"]):
                raise ControlPlaneError("阶段预览索引不明确")
            for group in (bundle.get("visual") or {}).get("frame_sets") or []:
                for frame in group["frames"]:
                    item = items[frame["preview_item_id"]]
                    if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]):
                        raise ControlPlaneError("阶段预览摘要无效")
                    # Validate the protected path without reading every image.
                    # The selected image is hashed on each binary GET below.
                    reader.path(item["path"])
                    observations = []
                    review_ids = {ref["review_id"] for ref in frame.get("review_refs") or []}
                    for receipt in receipts:
                        if receipt.get("review_id") not in review_ids or any(
                                receipt.get(key) != run.get(key) for key in ("run_id", "candidate_id")):
                            continue
                        for row in receipt.get("frame_results") or []:
                            if row.get("frame_set_id") == group["id"] and row.get("frame_id") == frame["id"]:
                                if row.get("input_sha256") != item["sha256"]:
                                    raise ControlPlaneError("逐帧结论与预览摘要不一致")
                                observations.append(f"{row.get('channel', '')}：{row.get('outcome', '')}；{row.get('conclusion', '')}")
                    title = f"{group['id']} / {frame['id']} · {frame.get('monotonic', '未知')} s · " + (
                        "；".join(observations) if observations else "尚无已冻结的逐帧结论")
                    entries.append({"item": item, "public": {
                        "index": len(entries), "title": title[:8000],
                        "sha256": item["sha256"], "report_sha256": report_sha256}})
                    if len(entries) == MAX_STAGE_PREVIEWS:
                        reader.finish()
                        return entries, reader
        reader.finish()
        return entries, reader

    def stage_preview(self, ticket_id: str, index: int, report_sha256: str):
        target = self.catalog.resolve(ticket_id)
        try:
            entries, reader = self._stage_preview_entries(target, report_sha256)
            if type(index) is not int or not 0 <= index < len(entries):
                raise UIRequestError("阶段预览序号无效")
            item = entries[index]["item"]
            if type(item.get("bytes")) is not int or item["bytes"] <= 0:
                raise ControlPlaneError("阶段预览大小无效")
            data = reader.read(item["path"], item["sha256"], item["bytes"])
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                fourcc, content_type = "PNG", "image/png"
            elif data.startswith(b"\xff\xd8"):
                fourcc, content_type = "JPEG", "image/jpeg"
            else:
                raise ControlPlaneError("仅支持 PNG/JPEG 阶段预览")
            if self._preview_visual_module is None:
                # Reuse the bounded compressed-image parser from the trusted
                # control installation. This never runs FFmpeg or a provider.
                spec = importlib.util.spec_from_file_location(
                    "icode_ui_preview_visual", self.coordinator.control_path.with_name("visual_evidence.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._preview_visual_module = module
            self._preview_visual_module._dimensions(data, fourcc)
            reader.finish()
            # A concurrent review replacement must not bind this image to the
            # report the browser loaded before that replacement.
            stage = self._stage_control().stage_module()
            state = stage._resolve(target.out_dir)
            review = stage._latest_review(state["program"], state["stage_id"])
            if review is None or review.get("report_sha256") != report_sha256:
                raise ControlPlaneError("阶段报告已变化，请刷新")
            stage._check_report(target.out_dir, review)
            return data, content_type
        except UIRequestError:
            raise
        except Exception as exc:
            raise ControlPlaneError("阶段预览或报告已失效，请刷新报告") from exc

    def decide_stage(self, payload, *, trusted_confirmation) -> Dict:
        normalized = normalize_stage_decision_payload(payload)
        target = self.catalog.resolve(normalized.pop("ticket_id"))
        try:
            return self._stage_control().confirm_stage(
                target.out_dir, normalized, trusted_confirmation=trusted_confirmation)
        except Exception as exc:
            raise ControlPlaneError("阶段快照或确认来源已失效，请刷新报告") from exc

    def create_ticket(self, payload) -> Dict:
        normalized = normalize_create_ticket_payload(payload)
        workspace = self.catalog.resolve_project(normalized["project_id"])
        created = self.lifecycle.create_next(
            workspace=workspace,
            requirement=normalized["requirement"],
            request_id=normalized["request_id"],
            index_path=self.catalog.index_path,
        )
        ticket_id = created.get("ticket_id")
        if not isinstance(ticket_id, str) or not ticket_id:
            raise UIRequestError("控制面创建回执缺少 ticket_id")
        return {
            "ok": True,
            "already_applied": bool(created.get("already_applied")),
            "ticket": self.ticket_detail(ticket_id),
        }

    def bootstrap(self) -> Dict:
        catalog = self.tickets()
        return {
            "ok": True,
            **catalog,
            "schema_version": 2,
            "settings": self.settings_store.load(),
            "hosts": self.host_runner.capabilities(),
            "jobs": self.host_runner.list(),
            "initial_ticket_id": self.initial_ticket_id,
            "initial_project_id": self.initial_project_id,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }

    def refresh(self, ticket_id: str | None = None) -> Dict:
        catalog = self.tickets()
        detail = None
        ticket_error = None
        if ticket_id:
            try:
                detail = self.ticket_detail(ticket_id)
            except (CatalogError, HostRunnerError, ControlPlaneError):
                # 单张历史/损坏工单不得拖垮全局目录。若索引身份仍可解析，
                # 只返回脱敏的只读卡片；路径和控制面原始错误均不下发浏览器。
                try:
                    public = self.catalog.public_ticket(ticket_id)
                except CatalogError:
                    public = None
                if public is not None:
                    detail = {
                        **public,
                        "allowed_actions": [],
                        "next_step": None,
                        "blocked_reason": "ticket_detail_unavailable",
                        "revision": None,
                        "open_execution": {
                            "steps": 0, "operations": 0, "agents": 0,
                        },
                        "validation": {
                            "ok": False, "mode": "control-unavailable",
                        },
                    }
                ticket_error = {
                    "code": "ticket_detail_unavailable",
                    "message": "工单详情暂不可用，已降级为只读；目录仍可使用。",
                }
        return {
            "ok": True,
            **catalog,
            "schema_version": 2,
            "ticket": detail,
            "ticket_error": ticket_error,
            "jobs": self.host_runner.list(),
            "hosts": self.host_runner.capabilities(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def settings(self) -> Dict:
        return self.settings_store.load()

    def update_settings(self, payload) -> Dict:
        return self.settings_store.save(payload)

    def run_step(self, payload) -> Dict:
        normalized = normalize_step_run_payload(payload)
        target = self.catalog.resolve(normalized["ticket_id"])
        return self.host_runner.start(
            target,
            step=normalized["step"],
            note=normalized["note"],
            settings=self.settings_store.load(),
            request_id=normalized["request_id"],
            expected_revision=normalized["expected_revision"],
        )


class AgentUIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Winsock address reuse differs from POSIX and can mask a live port owner.
    allow_reuse_address = os.name != "nt"

    def __init__(self, server_address, service: AgentUIService):
        self.service = service
        super().__init__(server_address, AgentUIRequestHandler)

    @property
    def ui_token(self) -> str:
        return self.service.token

    @property
    def ui_instance_id(self) -> str:
        return self.service.instance_id


class AgentUIRequestHandler(BaseHTTPRequestHandler):
    server_version = "ICODEAgentUI/0.4.0"
    sys_version = ""

    def log_message(self, _format, *_args):
        # prompt/provider 错误不得进入默认 HTTP 日志。
        return

    def _security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _send_bytes(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, error_class: str, message: str,
                         **details):
        self._send_json(status, {
            "ok": False,
            "error_class": error_class,
            "error": message,
            **details,
        })

    def _valid_host(self) -> bool:
        raw = self.headers.get("Host", "")
        try:
            parsed = urlsplit(f"//{raw}")
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return False
        return host in ALLOWED_HOSTS and port in {None, self.server.server_port}

    def _valid_origin(self) -> bool:
        raw = self.headers.get("Origin")
        if not raw:
            return True
        try:
            parsed = urlsplit(raw)
            return (
                parsed.scheme == "http"
                and parsed.hostname in ALLOWED_HOSTS
                and parsed.port == self.server.server_port
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            return False

    def _check_host(self) -> bool:
        if self._valid_host():
            return True
        self._send_error_json(421, "UIAccessError", "Host 不是当前 loopback UI")
        return False

    def _valid_token(self) -> bool:
        supplied = self.headers.get("X-ICODE-UI-Token", "")
        return bool(supplied) and secrets.compare_digest(
            supplied, self.server.ui_token)

    def _check_api_access(self, *, write=False) -> bool:
        if write and not self._valid_origin():
            self._send_error_json(403, "UIAccessError", "Origin 不是当前 loopback UI")
            return False
        if not self._valid_token():
            self._send_error_json(403, "UIAccessError", "UI 会话令牌无效")
            return False
        return True

    def _send_known_error(self, exc) -> None:
        if isinstance(exc, UIRequestError):
            self._send_error_json(400, "UIRequestError", str(exc))
        elif isinstance(exc, SettingsError):
            self._send_error_json(400, "SettingsError", str(exc))
        elif isinstance(exc, CatalogError):
            self._send_error_json(409, "CatalogError", str(exc))
        elif isinstance(exc, HostRunnerError):
            message, recovery_action = HOST_ERROR_GUIDANCE.get(
                exc.code,
                ("任务暂时无法执行，请查看工单阻断原因。", "inspect"),
            )
            self._send_error_json(
                409,
                "HostRunnerError",
                message,
                error_code=exc.code,
                recovery_action=recovery_action,
            )
        elif isinstance(exc, ControlPlaneError):
            self._send_error_json(409, "ControlPlaneError", "控制面拒绝生成可信状态投影")
        else:
            self._send_error_json(
                500, "UIInternalError", f"UI 未分类异常（{type(exc).__name__}）")

    def _read_json_body(self):
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise UIRequestError("POST 只接受 application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise UIRequestError("POST 缺 Content-Length")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise UIRequestError("Content-Length 非法") from exc
        if length < 0:
            raise UIRequestError("Content-Length 非法")
        if length > MAX_REQUEST_BYTES:
            raise OverflowError("请求体超过 64 KiB")
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UIRequestError("请求体不是合法 UTF-8 JSON") from exc

    def do_GET(self):
        if not self._check_host():
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            try:
                template = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
            except OSError:
                self._send_error_json(500, "UIAssetError", "UI 首页资源不可用")
                return
            html = template.replace("{{ICODE_UI_TOKEN}}", self.server.ui_token)
            self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        asset = STATIC_ASSETS.get(path)
        if asset is not None:
            path, content_type = asset
            try:
                body = path.read_bytes()
            except OSError:
                self._send_error_json(500, "UIAssetError", "UI 静态资源不可用")
                return
            self._send_bytes(200, body, content_type)
            return
        if path == "/api/v1/health":
            self._send_json(200, {
                "ok": True,
                "schema_version": 1,
                "service": "icode-agent-ui",
                "instance_id": self.server.ui_instance_id,
                "mode": (
                    "fixed" if self.server.service.ticket_dir is not None
                    else "global"
                ),
            })
            return
        if path == "/api/v1/status":
            try:
                status = self.server.service.status()
            except Exception as exc:
                self._send_known_error(exc)
                return
            self._send_json(200, {"ok": True, "status": status})
            return
        if path.startswith("/api/v1/") and not self._check_api_access():
            return
        try:
            if path == "/api/v1/bootstrap":
                self._send_json(200, self.server.service.bootstrap())
                return
            if path == "/api/v1/refresh":
                query = parse_qs(parsed.query, keep_blank_values=False)
                ticket_id = (query.get("ticket_id") or [None])[0]
                self._send_json(
                    200, self.server.service.refresh(ticket_id=ticket_id))
                return
            if path == "/api/v1/tickets":
                query = parse_qs(parsed.query, keep_blank_values=False)
                result = self.server.service.tickets(
                    project_id=(query.get("project") or [None])[0],
                    query=(query.get("query") or [None])[0],
                    status=(query.get("status") or [None])[0],
                )
                self._send_json(200, {"ok": True, **result})
                return
            if path.startswith("/api/v1/stage-previews/"):
                segments = unquote(path[len("/api/v1/stage-previews/"):]).split("/")
                query = parse_qs(parsed.query, keep_blank_values=True)
                if len(segments) != 2 or not segments[0] or not re.fullmatch(r"0|[1-9][0-9]{0,2}", segments[1]):
                    raise UIRequestError("阶段预览路径必须包含工单和序号")
                if set(query) != {"report_sha256"} or len(query["report_sha256"]) != 1:
                    raise UIRequestError("阶段预览只接受唯一 report_sha256")
                body, content_type = self.server.service.stage_preview(
                    segments[0], int(segments[1]), query["report_sha256"][0])
                self._send_bytes(200, body, content_type)
                return
            if path.startswith("/api/v1/tickets/"):
                ticket_id = unquote(path[len("/api/v1/tickets/"):])
                if not ticket_id or "/" in ticket_id:
                    raise UIRequestError("ticket_id 路径段非法")
                detail = self.server.service.ticket_detail(ticket_id)
                self._send_json(200, {"ok": True, "ticket": detail})
                return
            if path == "/api/v1/jobs":
                self._send_json(200, {
                    "ok": True, "jobs": self.server.service.host_runner.list()})
                return
            if path == "/api/v1/job-events":
                query = parse_qs(parsed.query, keep_blank_values=False)
                raw_after = (query.get("after") or ["0"])[0]
                try:
                    after = int(raw_after)
                except (TypeError, ValueError) as exc:
                    raise UIRequestError("after 必须是非负整数") from exc
                ticket_id = (query.get("ticket_id") or [None])[0]
                result = self.server.service.host_runner.events(
                    after=after, ticket_id=ticket_id)
                self._send_json(200, {"ok": True, **result})
                return
            if path.startswith("/api/v1/jobs/"):
                job_id = unquote(path[len("/api/v1/jobs/"):])
                if not job_id or "/" in job_id:
                    raise UIRequestError("job_id 路径段非法")
                self._send_json(200, {
                    "ok": True,
                    "job": self.server.service.host_runner.get(job_id),
                })
                return
            if path == "/api/v1/settings":
                self._send_json(200, {
                    "ok": True, "settings": self.server.service.settings()})
                return
        except Exception as exc:
            self._send_known_error(exc)
            return
        self._send_error_json(404, "UIRequestError", "路径不存在")

    def do_POST(self):
        if not self._check_host():
            return
        path = urlsplit(self.path).path
        known_path = path in {
            "/api/v1/run", "/api/v1/steps/run", "/api/v1/tickets/create",
            "/api/v1/stages/decision",
        } \
            or (path.startswith("/api/v1/jobs/") and path.endswith("/cancel"))
        if not known_path:
            self._send_error_json(404, "UIRequestError", "路径不存在")
            return
        if not self._check_api_access(write=True):
            return
        if path == "/api/v1/stages/decision" and not self.headers.get("Origin"):
            self._send_error_json(403, "UIAccessError", "阶段确认必须来自当前 UI 页面")
            return
        try:
            payload = self._read_json_body()
            if path == "/api/v1/run":
                response = self.server.service.run(payload)
                response_status = 200
            elif path == "/api/v1/steps/run":
                response = {"ok": True, "job": self.server.service.run_step(payload)}
                response_status = 202
            elif path == "/api/v1/tickets/create":
                response = self.server.service.create_ticket(payload)
                response_status = 200 if response["already_applied"] else 201
            elif path == "/api/v1/stages/decision":
                # No actor/source/path is accepted from JSON. This callback is
                # constructed only after loopback Host, explicit Origin and
                # session-token checks. It audits this UI action, not a claim
                # of cryptographic human identity against same-user programs.
                def confirmation_source(request):
                    intents = {"confirm": "确认本阶段验收", "reject": "拒绝本阶段验收",
                               "revoke": "撤销本阶段验收"}
                    return {"source": "ui", "source_id": (
                        f"ui:{self.server.ui_instance_id}:{request['request_id']}"),
                        "intent": intents[request["decision"]]}
                response = self.server.service.decide_stage(
                    payload, trusted_confirmation=confirmation_source)
                response_status = 200
            else:
                suffix = "/cancel"
                job_id = unquote(path[len("/api/v1/jobs/"):-len(suffix)])
                if not job_id or "/" in job_id:
                    raise UIRequestError("job_id 路径段非法")
                if payload != {}:
                    raise UIRequestError("cancel 请求 JSON 必须是空对象")
                response = {
                    "ok": True,
                    "job": self.server.service.host_runner.cancel(job_id),
                }
                response_status = 200
        except OverflowError:
            self._send_error_json(413, "UIRequestError", "请求体超过 64 KiB")
            return
        except UIRequestError as exc:
            status = 415 if "application/json" in str(exc) else 400
            self._send_error_json(status, "UIRequestError", str(exc))
            return
        except (CatalogError, HostRunnerError, SettingsError) as exc:
            self._send_known_error(exc)
            return
        except CapabilityError as exc:
            self._send_error_json(400, "CapabilityError", str(exc))
            return
        except LifecycleAmbiguityError as exc:
            self._send_error_json(409, "LifecycleAmbiguityError", str(exc))
            return
        except ControlPlaneError:
            self._send_error_json(
                409, "ControlPlaneError", "控制面拒绝 Agent 生命周期变更")
            return
        except BackendError as exc:
            self._send_error_json(
                502, "BackendError", f"Backend 调用失败（{exc.code}）")
            return
        except Exception as exc:
            self._send_error_json(
                500,
                "UIInternalError",
                f"UI 未分类异常（{type(exc).__name__}）",
            )
            return
        self._send_json(response_status, response)

    def do_PUT(self):
        if not self._check_host():
            return
        path = urlsplit(self.path).path
        if path != "/api/v1/settings":
            self._send_error_json(404, "UIRequestError", "路径不存在")
            return
        if not self._check_api_access(write=True):
            return
        try:
            payload = self._read_json_body()
            settings = self.server.service.update_settings(payload)
        except OverflowError:
            self._send_error_json(413, "UIRequestError", "请求体超过 64 KiB")
            return
        except (UIRequestError, SettingsError) as exc:
            self._send_known_error(exc)
            return
        except Exception as exc:
            self._send_known_error(exc)
            return
        self._send_json(200, {"ok": True, "settings": settings})

    def do_OPTIONS(self):
        if not self._check_host():
            return
        self._send_error_json(405, "UIRequestError", "不支持 OPTIONS")


def create_ui_server(ticket_dir: Path | None, control_path: Path, port: int = 0,
                     backend_factory: BackendFactory | None = None,
                     index_path: Path | None = None,
                     settings_path: Path | None = None,
                     host_runner=None,
                     seed_project_paths=None,
                     initial_ticket_id: str | None = None,
                     initial_project_id: str | None = None,
                     instance_id: str | None = None) -> AgentUIHTTPServer:
    """创建但不启动本地 UI server；调用方负责 serve_forever/server_close。"""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port 必须是 0..65535 的整数")
    service = AgentUIService(
        ticket_dir=ticket_dir,
        control_path=control_path,
        backend_factory=backend_factory,
        index_path=index_path,
        settings_path=settings_path,
        host_runner=host_runner,
        seed_project_paths=seed_project_paths,
        initial_ticket_id=initial_ticket_id,
        initial_project_id=initial_project_id,
        instance_id=instance_id,
    )
    return AgentUIHTTPServer((LOOPBACK_HOST, port), service)
