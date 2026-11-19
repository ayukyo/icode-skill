#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""icode 工单控制面（vNext schema v3）——单一写入口 + 状态机 + 事件链 + 幂等。

真源与契约：
- 状态机/门禁分级/关闭阶段：mcp/workflow-gate/gates.json「state_machine」段（唯一真源，本文件不复制）
- metadata/index/event 三 schema：schemas/*.schema.json（draft-07，内置轻量校验器，无第三方依赖）
- 使用契约（何时用哪个子命令、降级路径、legacy 适配）：references/control_plane.md

子命令：
  resolve-ticket  工单身份解析（--dir / --ticket / --latest）；可按状态/产物过滤，--ticket 多义即拒绝（exit 4）
  create          原子创建 metadata + 出生事件（空目录所有权检查）
  validate        工单整体校验（metadata schema + 状态-事件一致性 + 事件链完整性 + 索引指针 + 三 linter）
  event           追加事件（哈希链；仅 vNext 工单）
  step/artifact   步骤端口、Reactive 边界、产物与终结回执
  operation       长动作回执（按副作用类别禁止盲重放）
  policy/trace    只读执行策略与统一时间线
  transition      状态流转（fail-closed：合法性 → 门禁 linter → 原子写 + state_changed 事件）
  metadata-update 原子 set/append 已登记业务字段（控制字段由专用命令独占）
  record-claim    原子记录结构化 claim 与对应事件
  record-skill-run 原子记录共享技能路由观测与对应事件
  record-agent-spawn/result 原子记录模型/子代理调用边界与终态（正文只存摘要）
  index-write     全局索引单一 writer（写前重读合并 → 整文件校验 → 原子写 → 写后唯一性验证）
  index-update    原子更新索引独有的命中/stale 字段
  migration       legacy → vNext 迁移（dry-run 三分类报告 / --apply 单工单幂等）
  record-verification  原子记录构建/实机验证与对应事件
  archive-manifest     生成/校验归档 hash 与 linter roundtrip
  close-phase     关闭分阶段状态（幂等重放 + 禁止跨阶段跳转 + closed 终态回放摘要）
  reopen          在归档控制根原子解冻已 closed 工单（保留历史链）
  snapshot        生成/校验 ticket_snapshot.json（可由事件日志校验 hash）

退出码：0=成功；1=违例/fail-closed（状态不前移）；2=参数错误；3=legacy 拒绝（提示迁移入口）；
       4=身份多义（resolve-ticket 命中多个候选，输出候选清单，不猜测）。

并发：metadata/事件/索引写入均在 {out_dir}/.icontrol.lock 或索引侧文件锁内完成（fcntl.flock）。
降级：本脚本不可用时 fail-closed；vNext 状态、事件和索引禁止直写。
"""
import argparse
import contextlib
# 跨平台 advisory 锁:POSIX 上 fcntl.flock (整文件锁),Windows 上 msvcrt.locking
# (字节范围锁) fallback。fcntl 在原生 Windows Python 中不存在,直接 import
# 会让 vNext 工单系统整体不可用。
try:
    import fcntl as _fcntl_mod
except ImportError:  # pragma: no cover - Windows 路径
    _fcntl_mod = None
try:
    import msvcrt as _msvcrt_mod
except ImportError:  # pragma: no cover - POSIX 路径
    _msvcrt_mod = None


def _acquire_exclusive_lock(fh):
    """跨平台 advisory 排它锁(阻塞等待)。

    优先 fcntl.flock;Windows 上 fcntl 缺失时 fallback 到 msvcrt.locking。
    msvcrt.locking 是字节范围锁,要求锁定区间至少 1 字节;helper 在文件
    为空时 best-effort 写入 \\0 占位字节以保证锁定语义,
    fcntl 路径上 lock 文件保持原 0 字节语义。
    """
    if _fcntl_mod is not None:
        _fcntl_mod.flock(fh.fileno(), _fcntl_mod.LOCK_EX)
        return
    if _msvcrt_mod is None:
        raise RuntimeError(
            "工单并发锁不可降级:既无 fcntl 也无 msvcrt"
            "(非 POSIX 也非 Windows Python)"
        )
    try:
        fh.seek(0, 2)
        size = fh.tell()
        if size < 1:
            fh.seek(0)
            fh.write("\0")
            fh.flush()
        fh.seek(0)
    except OSError:
        # 占位字节写入失败不致命,msvcrt.locking 会自行报错。
        pass
    _msvcrt_mod.locking(fh.fileno(), _msvcrt_mod.LK_LOCK, 1)


def _release_exclusive_lock(fh):
    if _fcntl_mod is not None:
        _fcntl_mod.flock(fh.fileno(), _fcntl_mod.LOCK_UN)
        return
    if _msvcrt_mod is None:
        return
    try:
        _msvcrt_mod.locking(fh.fileno(), _msvcrt_mod.LK_UNLCK, 1)
    except OSError:
        # 解锁失败不应阻塞 __exit__ 的清理(close)。
        pass
import fnmatch
import glob as globlib
import hashlib
import json
import math
import os
import re
import io
import importlib.util
import subprocess
import shutil
import stat
import sys
import tempfile
import time
import uuid
import copy
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

SKILL_ROOT = Path(__file__).resolve().parent.parent
GATES_JSON = SKILL_ROOT / "mcp" / "workflow-gate" / "gates.json"
SCHEMA_DIR = SKILL_ROOT / "schemas"
SCHEMA_METADATA = SCHEMA_DIR / "ticket-metadata.schema.json"
SCHEMA_EVENT = SCHEMA_DIR / "ticket-event.schema.json"
SCHEMA_INDEX = SCHEMA_DIR / "ticket-index.schema.json"
INDEX_PATH = Path.home() / ".claude" / "icode_data" / "index.json"
METADATA_NAME = ".ico_metadata.json"
EVENTS_NAME = ".ico_events.jsonl"
SNAPSHOT_NAME = "ticket_snapshot.json"
ARCHIVE_MANIFEST_NAME = "archive_manifest.json"
TXN_NAME = ".icontrol_txn.json"
GENESIS_HASH = "0" * 64
INIT_GUIDE_REQUIRED_HEADINGS = (
    "1. 背景与目标",
    "2. 现状盘点",
    "3. 新增需求点",
    "4. 影响面与关联模块",
    "5. 关键问题与待决策项",
    "6. 链路图",
    "7. 4 维度验证清单",
)
DELIVERY_VERDICTS = ["verified", "verification_pending", "blocked", "not_applicable"]
CONTROL_EVENT_TYPES = {
    "lite_review_invoked", "lite_review_returned",
    "ticket_created", "step_started", "step_finished", "artifact_written", "gate_checked",
    "operation_started", "operation_finished", "state_changed", "claim_recorded",
    "verification_recorded", "skill_run_recorded", "snapshot_written",
    "close_phase", "ticket_reopened", "metadata_updated", "index_updated",
    "migration_applied", "idempotent_hit", "agent_spawned", "agent_result",
}
AGENT_CAPABILITIES = {"text", "image", "tools", "reasoning"}
AGENT_RESULTS = {"joined", "timed_out", "stopped", "failed"}
AGENT_ADOPTION = {"yes", "partial", "no"}
MAX_OPEN_AGENT_SPAWNS = 3
MAX_EXECUTION_BINDING_ANCESTORS = 256
CONTROLLED_BIRTH_FIELDS = {
    "candidate_tracking", "review_receipts", "excluded_side_effects",
    "execution_binding",
    "schema_version", "ticket_id", "requirement", "created_at", "status",
    "completed_steps", "indexed", "debug", "project_path", "close_state",
    "delivery_verdict", "claims", "verification_runs", "control_plane_degraded",
    "workflow_gate_schema_version", "thinking_gate_schema_version",
    "mcp_gate_schema_version", "mcp_gate_contract_version",
    # 采纳边界只能由控制面为存量工单写入（adopt_gate_contract）；出生时禁止调用方
    # 注入——未来时刻会把新工单的全部 trace 行伪装成历史行按 v1 解释；过去的历史
    # 边界同理（把新工单全部行按当前合同过度约束之外，还可配合伪造采纳时刻）。
    "mcp_gate_contract_adopted_at", "mcp_gate_contract_history_boundary",
}

# ---------------------------------------------------------------- 基础设施

class ControlError(Exception):
    """携带退出码的业务错误。"""

    def __init__(self, message, exit_code=1, **extra):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.extra = extra

    def report(self):
        return {"ok": False, "error": self.message, **self.extra}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def reject_nonfinite_json(value):
    raise ValueError(f"JSON 禁止非有限数 {value}")


def strict_json_loads(value):
    return json.loads(value, parse_constant=reject_nonfinite_json)


class _DigestReader(io.RawIOBase):
    """Hash original bytes beneath the unchanged UTF-8 newline decoder."""
    def __init__(self, handle):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.byte_count = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        size = self.handle.readinto(buffer)
        if size:
            self.digest.update(memoryview(buffer)[:size])
            self.byte_count += size
        return size


class _ControlReadObservation:
    """One call's actual reads; never persisted or externally supplied trust.

    Historical qualification rehashes original control files before and after
    checking original evidence. No extra raw event-log copy is retained.
    """
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.files = {}
        self.objects = None
        self.parent = self._parent()

    @staticmethod
    def _identity(info, *, cross_source=False):
        value = info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns
        return value if cross_source and os.name == "nt" else value + (info.st_ctime_ns,)

    @staticmethod
    def _ordinary(path, *, directory=False):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 \
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ControlError("stage observation requires ordinary control paths", gate_id="stage_gate")
        return info

    def _parent(self):
        info = self._ordinary(self.directory, directory=True)
        return str(self.directory.resolve(strict=True)), info.st_dev, info.st_ino, info.st_mode

    def _check_parent(self):
        if self._parent() != self.parent or os.path.lexists(self.directory / TXN_NAME):
            raise ControlError("stage observation root or pending transaction changed", gate_id="stage_gate")

    @contextlib.contextmanager
    def _reader(self, path):
        path = Path(path).absolute()
        if path.parent != self.directory or path.name not in {METADATA_NAME, EVENTS_NAME}:
            raise ControlError("stage observation path mismatch", gate_id="stage_gate")
        self._check_parent()
        before = self._ordinary(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if self._identity(before, cross_source=True) != self._identity(opened, cross_source=True):
                raise ControlError("stage observation file replaced before reading", gate_id="stage_gate")
            reader = _DigestReader(handle)
            with io.TextIOWrapper(io.BufferedReader(reader), encoding="utf-8", newline=None) as text:
                yield text, reader
                if reader.byte_count != before.st_size \
                        or self._identity(os.fstat(handle.fileno())) != self._identity(opened) \
                        or self._identity(self._ordinary(path)) != self._identity(before):
                    raise ControlError("stage observation file changed while reading", gate_id="stage_gate")
                self._check_parent()

    @contextlib.contextmanager
    def text(self, path):
        with self._reader(path) as (text, reader):
            yield text
            record = (self._identity(self._ordinary(Path(path))), reader.byte_count, reader.digest.hexdigest())
        name = Path(path).name
        if name in self.files and self.files[name] != record:
            raise ControlError("stage observation repeated control read changed", gate_id="stage_gate")
        self.files[name] = record

    def bind(self, directory, meta, events):
        if Path(directory).absolute() != self.directory or set(self.files) != {METADATA_NAME, EVENTS_NAME}:
            raise ControlError("stage observation is incomplete", gate_id="stage_gate")
        self.objects = meta, events

    def check(self, directory):
        if Path(directory).absolute() != self.directory or self.objects is None \
                or set(self.files) != {METADATA_NAME, EVENTS_NAME}:
            raise ControlError("stage observation is not a verified control read", gate_id="stage_gate")
        self._check_parent()
        for name, expected in self.files.items():
            # Rehash binary chunks, avoiding another parse or retained raw copy.
            path = self.directory / name
            before = self._ordinary(path)
            if self._identity(before) != expected[0]:
                raise ControlError("stage observation control version changed", gate_id="stage_gate")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if self._identity(before, cross_source=True) != self._identity(opened, cross_source=True):
                    raise ControlError("stage observation file replaced", gate_id="stage_gate")
                digest, size = hashlib.sha256(), 0
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                if self._identity(os.fstat(handle.fileno())) != self._identity(opened):
                    raise ControlError("stage observation file changed during recheck", gate_id="stage_gate")
            if (self._identity(self._ordinary(path)), size, digest.hexdigest()) != expected:
                raise ControlError("stage observation raw bytes changed", gate_id="stage_gate")
        if any(self._identity(self._ordinary(self.directory / name)) != value[0]
               for name, value in self.files.items()):
            raise ControlError("stage observation changed across control rechecks", gate_id="stage_gate")
        self._check_parent()
        return self.objects


def _load_raw_json(path, what, *, _observation=None):
    control_source = {"control_path": str(path), "control_root": str(Path(path).parent)} \
                     if Path(path).name in {METADATA_NAME, TXN_NAME} else {}
    try:
        source = open(path, "r", encoding="utf-8") if _observation is None else _observation.text(path)
        with source as f:
            if Path(path).name in {METADATA_NAME, TXN_NAME}:
                return storage_module().strict_json_loads(f.read())
            return json.load(f, parse_constant=reject_nonfinite_json)
    except FileNotFoundError:
        raise ControlError(f"{what} 不存在: {path}", exit_code=1, path=str(path), **control_source)
    except (json.JSONDecodeError, ValueError) as exc:
        if control_source:
            syntax = exc if isinstance(exc, json.JSONDecodeError) else exc.__cause__
            control_source.update(reason="invalid_control_json" if isinstance(syntax, json.JSONDecodeError)
                                  else "invalid_candidate_storage", recovery_action="repair_control_ledger")
            if isinstance(syntax, json.JSONDecodeError):
                control_source.update(line=syntax.lineno, column=syntax.colno)
        raise ControlError(f"{what} JSON 解析失败: {path}: {exc}", exit_code=1, path=str(path), **control_source) from exc


_STORAGE_MODULE = None


def storage_module():
    global _STORAGE_MODULE
    if _STORAGE_MODULE is None:
        spec = importlib.util.spec_from_file_location("control_candidate_storage", SKILL_ROOT / "tools/candidate_storage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _STORAGE_MODULE = module
    return _STORAGE_MODULE


def _logical_ledger_validator(event, prior, *, event_schema=None, seen_ids=None):
    """The unchanged logical envelope contract, without recursive ledger reads."""
    errors = check_schema(event, event_schema if event_schema is not None else
                          load_json(SCHEMA_EVENT, "ticket-event.schema.json"))
    if errors:
        raise ValueError("event_schema: " + "; ".join(errors))
    duplicate_id = event["event_id"] in seen_ids if seen_ids is not None else \
                   any(old["event_id"] == event["event_id"] for old in prior)
    if duplicate_id:
        raise ValueError("duplicate event_id")
    if prior and event["ticket_id"] != prior[0]["ticket_id"]:
        raise ValueError("ledger ticket identity mismatch")
    previous = prior[-1]["event_hash"] if prior else GENESIS_HASH
    if event["previous_event_hash"] != previous or event["event_hash"] != canonical_event_hash(event):
        raise ValueError("event_hash or previous_event_hash mismatch")
    if seen_ids is not None:
        seen_ids.add(event["event_id"])


def decode_control_ledger(records, *, limits=None, out_dir=None):
    """Pure adapter also used by stage's independently bounded raw reader.

    ``out_dir`` enables the content-addressed definition side store; without
    it only definitions inlined in the ledger itself can be resolved, which
    keeps legacy inline ledgers readable.
    """
    store = FileDefinitionStore(out_dir) if out_dir is not None else None
    try:
        schema = load_json(SCHEMA_EVENT, "ticket-event.schema.json")
        seen = set()
        def validator(event, prior):
            _logical_ledger_validator(event, prior, event_schema=schema, seen_ids=seen)
        return storage_module().decode_events(records, limits=limits,
                       logical_event_validator=validator, definition_store=store)
    except storage_module().StorageError as exc:
        raise ControlError("candidate storage ledger: " + str(exc), gate_id="candidate_storage") from exc


def _committed_ledger(out_dir, *, _observation=None):
    path = Path(out_dir) / EVENTS_NAME
    try:
        records, _ = _read_raw_events(out_dir, _observation=_observation)
        return decode_control_ledger(records, out_dir=out_dir)
    except ControlError as exc:
        exc.extra.setdefault("path", str(path))
        exc.extra.setdefault("control_path", exc.extra["path"])
        exc.extra.setdefault("control_root", str(path.parent))
        exc.extra.setdefault("reason", "invalid_candidate_storage")
        exc.extra.setdefault("recovery_action", "repair_control_ledger")
        raise


def load_json(path, what, *, _observation=None):
    raw = _load_raw_json(path, what, _observation=_observation)
    path = Path(path)
    kinds = {METADATA_NAME: "metadata", TXN_NAME: "transaction"}
    if path.name not in kinds or not storage_module().is_envelope(raw):
        return raw
    try:
        ledger = _committed_ledger(path.parent, _observation=_observation)
    except ControlError as exc:
        # A typed metadata read can fail in its ledger before yielding metadata.
        # This identity is diagnostic only; it never grants qualification.
        if kinds[path.name] == "metadata" and isinstance(raw.get("value"), dict):
            exc.extra.setdefault("ticket_id", raw["value"].get("ticket_id"))
        raise
    try:
        value = storage_module().decode_document(raw, kind=kinds[path.name], registry=ledger.registry,
                                                 definition_store=FileDefinitionStore(path.parent))
    except storage_module().StorageError as exc:
        raise ControlError("candidate storage document: " + str(exc), gate_id="candidate_storage",
                           path=str(path), control_path=str(path), control_root=str(path.parent),
                           reason="invalid_candidate_storage", recovery_action="repair_control_ledger") from exc
    if path.name == METADATA_NAME and ledger.events and value.get("ticket_id") != ledger.events[0]["ticket_id"]:
        raise ControlError("candidate storage metadata/ledger ticket mismatch", gate_id="candidate_storage",
                           path=str(path), control_path=str(path), control_root=str(path.parent),
                           reason="invalid_candidate_storage", recovery_action="repair_control_ledger")
    return value


def read_metadata_readonly(out_dir):
    """Stable read adapter; pending definitions never authorize a readonly view."""
    directory = Path(out_dir)
    if os.path.lexists(directory / TXN_NAME):
        raise ControlError("控制面事务待恢复，拒绝只读资格判断", gate_id="pending_transaction")
    value = load_metadata(directory)
    if os.path.lexists(directory / TXN_NAME):
        raise ControlError("读取期间出现控制面事务", gate_id="pending_transaction")
    return value


def _storage_enabled(out_dir, *, new_metadata=None):
    directory = Path(out_dir)
    for name in (METADATA_NAME, TXN_NAME):
        path = directory / name
        if path.is_file():
            raw = _load_raw_json(path, "candidate storage mode")
            if storage_module().is_envelope(raw):
                # The decoder, not a version-looking flag, authorizes v2.
                storage_module()._unwrap(raw, "metadata" if name == METADATA_NAME else "transaction")
                return True
            if name == METADATA_NAME:
                return False
    return isinstance(new_metadata, dict) and new_metadata.get("schema_version") == 3


def _encode_control_document(value, kind, registry, enabled, *, require_definitions=False):
    if not enabled:
        return value
    try:
        return storage_module().encode_document(value, kind=kind, committed_registry=registry,
                                                require_definitions=require_definitions)
    except storage_module().StorageError as exc:
        raise ControlError("candidate storage encode: " + str(exc), gate_id="candidate_storage") from exc


def write_metadata(out_dir, value, *, registry=None, enabled=None, require_definitions=False):
    """Typed physical writer; callers retain the original lock/transaction guard.

    ``require_definitions`` keeps manifest slots as references and fails when a
    definition is neither committed nor supplied by the caller's registry. The
    side store must already contain those definitions, or decoding fails.
    """
    if enabled is None:
        enabled = _storage_enabled(out_dir, new_metadata=value)
    if registry is None and enabled:
        registry = _committed_ledger(out_dir).registry
    physical = _encode_control_document(value, "metadata", registry, enabled,
                                        require_definitions=require_definitions)
    atomic_write_json(Path(out_dir) / METADATA_NAME, physical)
    return physical


_ATOMIC_REPLACE_ATTEMPTS = 5
_ATOMIC_REPLACE_RETRY_SECONDS = 0.02


def replace_with_retry(source, destination):
    """Windows 并发读句柄可短暂阻止替换；仅重试共享/权限冲突。"""
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 == _ATOMIC_REPLACE_ATTEMPTS:
                raise
            time.sleep(_ATOMIC_REPLACE_RETRY_SECONDS)


def atomic_write_json(path, obj):
    """临时文件 + fsync + 同目录原子 rename。"""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            typed_control = path.name in {METADATA_NAME, TXN_NAME} and storage_module().is_envelope(obj)
            if typed_control:
                storage_module()._unwrap(obj, "metadata" if path.name == METADATA_NAME else "transaction")
                json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            else:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_bytes(path, data):
    """二进制内容原子替换，用于保持 JSONL 的原始字节序列。"""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        replace_with_retry(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


DEFINITION_STORE_DIR = ".ico_storage"


class FileDefinitionStore:
    """Content-addressed side store for candidate manifest definitions.

    Ledger events and metadata keep only ``$icode_manifest_ref`` references;
    the first definition is persisted here. Files are immutable, named by the
    manifest digest, and verified on every read. A rolled-back transaction may
    leave unreferenced orphan files; they are harmless and can be pruned
    offline without touching the authenticated ledger.
    """
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self, out_dir):
        self.root = Path(out_dir) / DEFINITION_STORE_DIR / "definitions"

    @staticmethod
    def _validate(kind, sha):
        if kind not in ("file_manifest", "index_manifest"):
            raise storage_module().StorageError("unknown manifest definition kind: " + str(kind))
        if not isinstance(sha, str) or re.fullmatch("[0-9a-f]{64}", sha) is None:
            raise storage_module().StorageError("invalid manifest definition digest")

    def _path(self, kind, sha):
        self._validate(kind, sha)
        return self.root / kind / (sha + ".json")

    def load(self, kind, sha):
        self._validate(kind, sha)
        path = self._path(kind, sha)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise storage_module().StorageError("manifest definition read failed: " + str(exc)) from exc
        if len(data) > self.MAX_BYTES:
            raise storage_module().StorageError("manifest definition exceeds side-store limit")
        if storage_module()._manifest_digest_bytes(kind, data) != sha:
            raise storage_module().StorageError("corrupt manifest definition side file")
        return data

    def save(self, kind, sha, rows_json):
        self._validate(kind, sha)
        path = self._path(kind, sha)
        try:
            existing = path.read_bytes()
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise storage_module().StorageError("manifest definition read failed: " + str(exc)) from exc
        if existing is not None:
            if storage_module()._manifest_digest_bytes(kind, existing) != sha:
                raise storage_module().StorageError("refusing to overwrite a corrupt manifest definition")
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(path, rows_json)
        except OSError as exc:
            raise storage_module().StorageError("manifest definition write failed: " + str(exc)) from exc


def atomic_append_line(path, line):
    """JSONL 追加：由上层目录锁串行化，追加后 flush + fsync。"""
    path = Path(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


class DirLock:
    """工单目录级排它锁(fcntl.flock,Windows 上 msvcrt.locking fallback;阻塞等待,防并发写撕裂)。"""

    def __init__(self, out_dir):
        self.path = Path(out_dir) / ".icontrol.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w", encoding="utf-8")
        _acquire_exclusive_lock(self.fh)
        return self

    def __exit__(self, *exc):
        try:
            _release_exclusive_lock(self.fh)
        finally:
            self.fh.close()
        return False


class FileLock:
    """任意共享文件的旁路锁;锁文件固定存在,业务文件仍可原子 rename。"""

    def __init__(self, path):
        self.path = Path(str(path) + ".lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+", encoding="utf-8")
        _acquire_exclusive_lock(self.fh)
        return self

    def __exit__(self, *exc):
        try:
            _release_exclusive_lock(self.fh)
        finally:
            self.fh.close()
        return False


# ---------------------------------------------------------------- 轻量 JSON Schema 校验器
# 支持子集：type/enum/const/required/properties/additionalProperties/items/minItems/maxItems/
#           minimum/maximum/pattern/minLength/propertyNames(pattern)/if/then/else。schema 文件保持 draft-07 合法，
#           也可被外部 jsonschema 库消费；本内置实现保证零依赖环境下行为一致。

def _check_type(value, types, path, errors):
    type_map = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
                            and math.isfinite(v),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if isinstance(types, str):
        types = [types]
    if not any(type_map[t](value) for t in types if t in type_map):
        got = type(value).__name__
        errors.append(f"{path}: 类型不符，期望 {types}，实际 {got}")


_SCHEMA_ARRAY_MEMO = None


def check_schema(value, schema, path="$", errors=None):
    """Keep diagnostics mutable and exact; reuse only successful pure arrays."""
    global _SCHEMA_ARRAY_MEMO
    if errors is None:
        errors = []
    if isinstance(value, list) and len(value) >= 64:
        if _SCHEMA_ARRAY_MEMO is None:
            spec = importlib.util.spec_from_file_location(
                "schema_validation_memo", Path(__file__).with_name("validation_memo.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _SCHEMA_ARRAY_MEMO = module.PureJsonMemo(
                lambda values, rules: not _check_schema(values, rules))
        if _SCHEMA_ARRAY_MEMO.check(value, schema):
            return errors
    return _check_schema(value, schema, path, errors)


def _check_schema(value, schema, path="$", errors=None):
    if errors is None:
        errors = []
    if "if" in schema:
        # 条件分支的试探错误不污染实际校验，保证零依赖校验与 draft-07 一致。
        branch = "then" if not check_schema(value, schema["if"], path, []) else "else"
        if branch in schema:
            check_schema(value, schema[branch], path, errors)
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 必须为常量 {schema['const']!r}，实际 {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 值 {value!r} 不在枚举 {schema['enum']} 内")
    if "type" in schema:
        before_type_errors = len(errors)
        _check_type(value, schema["type"], path, errors)
        # 类型不符时不再深入，避免级联噪音
        if len(errors) != before_type_errors:
            return errors
    if isinstance(value, str):
        if "pattern" in schema:
            import re
            if re.search(schema["pattern"], value) is None:
                errors.append(f"{path}: 不匹配模式 {schema['pattern']!r}（实际 {value!r}）")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: 长度 {len(value)} 小于 minLength={schema['minLength']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: 元素数 {len(value)} 小于 minItems={schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: 元素数 {len(value)} 大于 maxItems={schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                check_schema(item, item_schema, f"{path}[{i}]", errors)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: 值 {value} 小于 minimum={schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: 值 {value} 大于 maximum={schema['maximum']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: 缺少必填字段 {req!r}")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in props:
                check_schema(sub, props[key], f"{path}.{key}", errors)
            elif addl is False:
                errors.append(f"{path}: 顶层出现未登记字段 {key!r}（核心字段禁止任意新增；实验字段应放入 extensions.<namespace>）")
            elif isinstance(addl, dict):
                check_schema(sub, addl, f"{path}.{key}", errors)
        pn = schema.get("propertyNames")
        if pn and "pattern" in pn:
            import re
            for key in value:
                if re.search(pn["pattern"], key) is None:
                    errors.append(f"{path}: 键名 {key!r} 不匹配 propertyNames 模式 {pn['pattern']!r}")
    return errors


def validate_against(value, schema_path, gate_id):
    schema = load_json(schema_path, f"schema {schema_path.name}")
    errors = check_schema(value, schema)
    if schema_path == SCHEMA_METADATA and isinstance(value, dict):
        delivery = value.get("delivery_files")
        if isinstance(delivery, dict) and delivery.get("report") == delivery.get("brief"):
            errors.append("$.delivery_files: 交付报告与简报必须是不同文件")
    return [{"gate_id": gate_id, "path": e.split(":")[0], "detail": e} for e in errors]


def validate_index(index, gate_id="index_schema"):
    """校验混合代际索引：legacy 最小可读，vNext 条目严格校验。"""
    schema = load_json(SCHEMA_INDEX, f"schema {SCHEMA_INDEX.name}")
    errors = check_schema(index, schema)
    strict_schema = schema.get("definitions", {}).get("vnextEntry")
    if not strict_schema:
        errors.append("$: schema 缺少 definitions.vnextEntry")
    else:
        tickets = (index.get("tickets", [])
                   if isinstance(index, dict) and isinstance(index.get("tickets"), list)
                   else [])
        for pos, item in enumerate(tickets):
            if isinstance(item, dict) and "control_schema_version" in item:
                check_schema(item, strict_schema, f"$.tickets[{pos}]", errors)
    return [{"gate_id": gate_id, "path": e.split(":")[0], "detail": e} for e in errors]


def execution_binding_shape_ok(binding):
    """Validate portable recorded identities without accessing the filesystem."""
    if not isinstance(binding, dict) or set(binding) != {"version", "path", "ancestors"}:
        return False
    if type(binding["version"]) is not int or binding["version"] != 1:
        return False
    raw = binding["path"]
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return False
    cls = PureWindowsPath if PureWindowsPath(raw).is_absolute() else PurePosixPath
    root = cls(raw)
    if not root.is_absolute() or str(root) != raw or ".." in root.parts:
        return False
    depth = len(root.parents) + 1
    if depth > MAX_EXECUTION_BINDING_ANCESTORS:
        return False
    chain = list(reversed(root.parents)) + [root]
    rows = binding["ancestors"]
    if not isinstance(rows, list) or len(rows) != depth:
        return False
    for row, expected in zip(rows, chain):
        if not isinstance(row, dict) or set(row) != {"path", "device", "inode"}:
            return False
        if row["path"] != str(expected):
            return False
        if type(row["device"]) is not int or row["device"] < 0:
            return False
        if type(row["inode"]) is not int or row["inode"] <= 0:
            return False
    return True


def execution_binding_topology_ok(meta):
    if "execution_binding" not in meta:
        return True
    history = meta.get("checkout_history") or []
    return (execution_binding_shape_ok(meta["execution_binding"])
            and meta.get("active_checkout") is None
            and isinstance(history, list)
            and not any(isinstance(row, dict) and row.get("state") == "active"
                        for row in history))


def capture_execution_binding(raw):
    """Capture every directory identity; never canonicalize untrusted input silently."""
    try:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("path text")
        root = Path(raw)
        if not root.is_absolute() or str(root) != raw or ".." in root.parts:
            raise ValueError("path not canonical")
        if len(root.parents) + 1 > MAX_EXECUTION_BINDING_ANCESTORS:
            raise ValueError("path depth")
        chain = list(reversed(root.parents)) + [root]
        rows = []
        for path in chain:
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError("linked or non-directory component")
            if type(info.st_dev) is not int or info.st_dev < 0:
                raise ValueError("device identity unavailable")
            if type(info.st_ino) is not int or info.st_ino <= 0:
                raise ValueError("inode identity unavailable")
            rows.append({"path": str(path), "device": info.st_dev, "inode": info.st_ino})
        if root.resolve(strict=True) != root:
            raise ValueError("path resolution changed")
        value = {"version": 1, "path": str(root), "ancestors": rows}
        if not execution_binding_shape_ok(value):
            raise ValueError("binding shape")
        return value
    except (OSError, RuntimeError, ValueError) as exc:
        raise ControlError("执行根目录对象校验失败", gate_id="execution_binding_identity") from exc


def bound_execution_workspace(out_dir, meta):
    binding = meta["execution_binding"]
    if not execution_binding_topology_ok(meta):
        raise ControlError("执行根绑定与 checkout 拓扑冲突", gate_id="execution_binding_topology")
    if capture_execution_binding(binding["path"]) != binding:
        raise ControlError("执行根或祖先目录对象已替换", gate_id="execution_binding_identity")
    root = Path(binding["path"])
    control = containing_workspace(out_dir)
    if root.is_relative_to(control) or control.is_relative_to(root):
        raise ControlError("控制根与执行根不能互相包含", gate_id="execution_binding_topology")
    return root


def require_valid_metadata(meta):
    violations = validate_against(meta, SCHEMA_METADATA, "metadata_schema")
    if violations:
        raise ControlError(
            "metadata 未通过 v3 schema，拒绝执行变更",
            exit_code=1,
            gate_id="metadata_schema",
            violations=violations,
            hint="先运行 validate --dir <out_dir> 并修复字段类型/枚举；实验字段移入 extensions.<namespace>",
        )
    profile = workflow_contract(meta)
    if meta.get("mode") != "lite" and str(meta.get("status", "")).startswith("lite_"):
        raise ControlError("旧模式不能使用 lite 状态", gate_id="workflow_profile")
    if meta.get("mode") == "lite" and (meta.get("status") not in profile["state_machine"]["states"]
            or any(step not in profile["step_order"] for step in meta.get("completed_steps", []))):
        raise ControlError("lite 模式与状态/步骤不匹配", gate_id="workflow_profile")
    if not execution_binding_topology_ok(meta):
        raise ControlError("执行根绑定与 checkout 拓扑冲突", gate_id="execution_binding_topology")


# ---------------------------------------------------------------- gates.json 状态机

def lite_module():
    name = "icode_lite_workflow"
    if name not in sys.modules:
        path = SKILL_ROOT / "tools/lite_workflow.py"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError("无法创建 Lite 工作流加载器")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except (OSError, ImportError, SyntaxError, TypeError, ValueError) as exc:
            sys.modules.pop(name, None)
            raise ControlError(f"Lite 工作流模块不可用：{exc}",
                               gate_id="workflow_profile") from exc
    return sys.modules[name]


def workflow_contract(meta=None):
    try:
        return lite_module().workflow_contract(meta or {}, load_json(GATES_JSON, "gates.json"))
    except (KeyError, ValueError, TypeError) as exc:
        raise ControlError(str(exc), gate_id="workflow_profile") from exc


def load_state_machine(meta=None):
    return workflow_contract(meta)["state_machine"]


def load_execution_model(meta=None):
    return workflow_contract(meta)["execution_model"]


def legal_transition(sm, from_status, to_status):
    for t in sm["transitions"]:
        if t["from"] == from_status and t["to"] == to_status:
            return t
    return None


def next_transitions(sm, from_status):
    return [t["to"] for t in sm["transitions"] if t["from"] == from_status]


# ---------------------------------------------------------------- 工单定位与元数据

def load_metadata(out_dir, *, _observation=None):
    out_dir = Path(out_dir)
    return load_json(out_dir / METADATA_NAME, "工单 metadata") if _observation is None else \
        load_json(out_dir / METADATA_NAME, "工单 metadata", _observation=_observation)


_FAST_POLICY_MODULE = None
_FAST_CONTRACT_MODULE = None


def fast_policy_module():
    global _FAST_POLICY_MODULE
    if _FAST_POLICY_MODULE is None:
        spec = importlib.util.spec_from_file_location("control_fast_policy", SKILL_ROOT / "tools/fast_policy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _FAST_POLICY_MODULE = module
    return _FAST_POLICY_MODULE


def protect_fast_policy_update(before, after):
    """A supported policy and an already-full run cannot silently downgrade."""
    old = before.get("risk_profile") or {}
    new = after.get("risk_profile") or {}
    old_version = old.get("policy_version") if isinstance(old, dict) else None
    new_version = new.get("policy_version") if isinstance(new, dict) else None
    if old_version == 2 and (type(new_version) is not int or new_version != 2):
        raise ControlError("fast policy version cannot downgrade", gate_id="fast_policy_version")
    if new_version == 2 and fast_policy_module().effective_mode(before) == "full" \
            and fast_policy_module().effective_mode(after) != "full":
        raise ControlError("effective full run cannot downgrade to fast", gate_id="fast_policy_mode")


def workflow_effective_mode(meta):
    profile = meta.get("risk_profile")
    if isinstance(profile, dict) and profile.get("policy_version") == 2:
        return fast_policy_module().effective_mode(meta)
    effective = profile.get("effective_mode") if isinstance(profile, dict) else None
    return effective if effective in ("fast", "full") else meta.get("mode", "full")


def step_execution_profile(meta, step):
    """Project instructions only after admission; never persist this view."""
    policy = fast_policy_module()
    profile = meta.get("risk_profile") or {}
    catalog = load_json(GATES_JSON, "workflow gate catalog") if (
        policy.effective_mode(meta) == "fast" and profile.get("policy_version") == 2) else {}
    result = policy.build_execution_profile(meta, step, catalog, SKILL_ROOT)
    if result["state"] != "compact":
        documents = {"init": "00_init.md", "plan": "01_plan.md", "review": "02_review.md",
                     "merge": "03_merge.md", "code": "04_code.md", "deepcheck": "05_deepcheck.md",
                     "audit": "06_audit.md", "readme": "07_readme.md", "patch": "08_patch.md"}
        # A stale compact view must not loop through the main step's compact
        # navigation. Read its applicable body/shared rules in the same mode.
        result["fallback"] = {"route": "existing_step_rules",
            "read_applicable_body_and_shared_rules": True, "ignore_compact_navigation": True,
            "preserve_effective_mode": True}
        if step in documents:
            result["fallback"]["document"] = "steps/" + documents[step]
    return result


def _fast_reference_context(base, path):
    """Bind the chosen root and ordinary ancestors without sibling timestamps."""
    parents = []
    for parent in path.parents:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 \
                or not stat.S_ISDIR(info.st_mode):
            raise ValueError("linked or nonordinary reference parent")
        parents.append((str(parent), info.st_dev, info.st_ino, info.st_mode))
    root = base.resolve(strict=True)
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("reference escapes root")
    return str(root), tuple(parents)


def fast_policy_admission(out_dir, meta, step):
    """Check v2 routing and real reference bytes; legacy admission is unchanged.

    A risk assessment selects mandatory work, rather than certifying that work.
    Plan investigation is allowed before classification. Implementation requires
    a scope-bound classification plus the original applicable contracts.
    """
    profile = meta.get("risk_profile")
    version = profile.get("policy_version", 1) if isinstance(profile, dict) else 1
    if type(version) is int and version == 1:
        return {"ok": True, "legacy": True}
    if step not in {"review", "merge", "code", "patch", "deepcheck", "audit", "audit-verified", "deploy"}:
        return {"ok": True, "route": "investigation", "reasons": [], "required_checks": []}
    catalog = load_json(SKILL_ROOT / "mcp/workflow-gate/gates.json", "workflow gate catalog")
    policy = fast_policy_module()
    result = policy.assess_fast_risk(meta, catalog)
    reasons = list(result["reasons"]) if result["route"] in {"blocked", "replan"} else []
    if result["route"] == "full" and policy.effective_mode(meta) != "full":
        reasons.append("full_required")
    deadline = None
    if result["route"] == "fast":
        # Do not resolve links before checking them: a relative spelling must
        # remain inside the chosen control or active checkout root throughout.
        refs = profile["classification"]["evidence_refs"]
        limits = catalog["fast_risk_upgrade"]["policy_v2"]["evidence_limits"]
        if len(refs) > limits["max_files"]:
            return dict(result, ok=False, reasons=["evidence_budget_exceeded"])
        deadline = time.monotonic() + limits["timeout_seconds"]
        total_bytes = 0
        for ref in refs:
            base = Path(out_dir) if ref["scope"] == "ticket" else execution_workspace(out_dir, meta)
            path = base / ref["path"]
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("linked reference")
                parents = _fast_reference_context(base, path)
                before = path.stat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ValueError("nonordinary reference")
                if not path.resolve().is_relative_to(base.resolve()):
                    raise ValueError("reference escapes root")
                total_bytes += before.st_size
                if total_bytes > limits["max_bytes"] or time.monotonic() >= deadline:
                    reasons.append("evidence_budget_exceeded")
                    break
                digest = hashlib.sha256()
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
                with os.fdopen(os.open(path, flags), "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != \
                            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                        raise ValueError("reference changed while opening")
                    if _fast_reference_context(base, path) != parents:
                        raise ValueError("reference parent changed while opening")
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        if time.monotonic() >= deadline:
                            raise ValueError("reference read deadline exceeded")
                        digest.update(block)
                    if os.fstat(handle.fileno()).st_nlink != 1:
                        raise ValueError("reference acquired another link")
                if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError("reference replaced by a link")
                after = path.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != \
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    reasons.append("evidence_changed_during_read")
                elif digest.hexdigest() != ref["sha256"]:
                    reasons.append("evidence_hash_mismatch")
                if _fast_reference_context(base, path) != parents:
                    raise ValueError("reference parent changed during read")
            except (OSError, ValueError):
                reasons.append("invalid_evidence_file")
    if step in {"code", "patch", "deploy", "audit-verified"}:
        global _FAST_CONTRACT_MODULE
        if _FAST_CONTRACT_MODULE is None:
            spec = importlib.util.spec_from_file_location("control_fast_contract", SKILL_ROOT / "tools/lint_workflow_contract.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _FAST_CONTRACT_MODULE = module
        checks = result["required_checks"]
        if "identity_impact" in checks:
            impact = meta.get("impact_contract")
            if not isinstance(impact, dict) or impact.get("identity_change") is not True \
                    or _FAST_CONTRACT_MODULE.validate_identity_change(meta, catalog):
                reasons.append("missing_identity_impact")
        if "lifecycle_acceptance" in checks:
            acceptance = meta.get("acceptance_contract")
            if not isinstance(acceptance, dict) or acceptance.get("requires_lifecycle") is not True \
                    or not _FAST_CONTRACT_MODULE.matrix_complete(acceptance, catalog)[0]:
                reasons.append("missing_lifecycle_acceptance")
        if "real_environment_verification" in checks:
            contract = meta.get("verification_contract")
            if not isinstance(contract, dict) or contract.get("required") is not True \
                    or contract.get("requirements_pending") is True \
                    or any(not isinstance(contract.get(key), list) or not contract[key] for key in
                           ("required_layers", "required_consumers", "required_scenarios")):
                reasons.append("missing_real_environment_contract")
    if deadline is not None and time.monotonic() >= deadline:
        reasons.append("evidence_budget_exceeded")
    return dict(result, ok=not reasons, reasons=list(dict.fromkeys(reasons)) or result["reasons"])


def is_vnext(meta):
    return meta.get("schema_version") == 3


def require_vnext(meta, out_dir):
    if not is_vnext(meta):
        raise ControlError(
            "该工单为 legacy 工单（metadata 无 schema_version=3），控制面拒绝变更（legacy 只读适配，"
            "不被静默伪装成 vNext）；如需接入控制面请先执行: "
            f"python3 tools/icode_control.py migration --dir {out_dir}",
            exit_code=3, legacy=True, out_dir=str(out_dir))


def containing_workspace(out_dir):
    """返回包含 .icode_output 段的工程根（out_dir 形如 <root>/.icode_output/.icode_output_N）。"""
    out_dir = Path(out_dir).resolve()
    parts = out_dir.parts
    if ".icode_output" not in parts:
        raise ControlError(f"out_dir 不在 .icode_output 目录体系内: {out_dir}", exit_code=2)
    idx = parts.index(".icode_output")
    return Path(*parts[:idx])


def classify_ticket_dir(out_dir):
    """校验标准工单目录形状，返回 (workspace, is_debug)。"""
    out_dir = Path(out_dir).resolve()
    workspace = containing_workspace(out_dir)
    try:
        rel = out_dir.relative_to(workspace)
    except ValueError:
        raise ControlError(f"工单目录不在工作区内: {out_dir}", exit_code=2,
                           gate_id="ticket_dir_shape")
    parts = rel.parts
    normal = (len(parts) == 2 and parts[0] == ".icode_output"
              and parts[1].startswith(".icode_output_")
              and parts[1].removeprefix(".icode_output_").isdigit())
    debug = (len(parts) == 3 and parts[:2] == (".icode_output", ".debug")
             and parts[2].startswith(".icode_output_")
             and parts[2].removeprefix(".icode_output_").isdigit())
    if not normal and not debug:
        raise ControlError(
            "工单目录必须是 <workspace>/.icode_output/.icode_output_N，debug 必须位于 "
            "<workspace>/.icode_output/.debug/.icode_output_N",
            exit_code=2, gate_id="ticket_dir_shape", out_dir=str(out_dir))
    if not workspace.is_dir():
        raise ControlError(f"工作区根不存在，拒绝由 create 递归创建: {workspace}", exit_code=2,
                           gate_id="workspace_exists")
    return workspace, debug


def resolution_filter_failures(out_dir, meta, args):
    """返回 resolve-ticket 可选过滤条件的不满足项；状态为任一匹配，产物为全部存在。"""
    if not isinstance(meta, dict):
        raise ControlError("metadata 顶层必须是 JSON 对象", exit_code=1,
                           gate_id="metadata_shape")
    failures = []
    required_statuses = list(getattr(args, "require_status", None) or [])
    required_artifacts = list(getattr(args, "require_artifact", None) or [])
    if required_statuses and meta.get("status") not in required_statuses:
        failures.append(
            f"status={meta.get('status')!r} 不在要求集合 {required_statuses!r}")
    for raw in required_artifacts:
        artifact = Path(raw)
        ticket_root = Path(out_dir).resolve()
        candidate = (ticket_root / artifact).resolve()
        try:
            candidate.relative_to(ticket_root)
        except ValueError:
            raise ControlError(
                f"--require-artifact 经符号链接逃出工单目录: {raw!r}",
                exit_code=2, gate_id="resolve_artifact_path")
        if not candidate.is_file():
            failures.append(f"缺少必需产物 {raw!r}")
    if getattr(args, "require_init_ready", False):
        failures.extend(init_guide_source_failures(out_dir, meta))
    return failures


def init_guide_source_failures(out_dir, meta):
    """检查 init 分析是否具备派生指南所需的最小结构与实质内容。"""
    failures = []
    if meta.get("status") != "init_in_progress":
        failures.append("init guide 源的 status 必须为 init_in_progress")
    completed_steps = meta.get("completed_steps")
    if not isinstance(completed_steps, list) or "0" not in completed_steps:
        failures.append("init guide 源的 completed_steps 必须包含 '0'")
    source = Path(out_dir) / "00_init.md"
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        failures.append(f"00_init.md 不可读或不是 UTF-8: {exc}")
        return failures
    if not text.strip():
        failures.append("00_init.md 为空")
        return failures
    for heading in INIT_GUIDE_REQUIRED_HEADINGS:
        match = re.search(rf"^##\s+{re.escape(heading)}(?:\s|（|\(|$)", text, re.MULTILINE)
        if match is None:
            failures.append(f"00_init.md 缺少完整结构章节: {heading}")
            continue
        next_heading = re.search(r"^##\s+", text[match.end():], re.MULTILINE)
        end = match.end() + next_heading.start() if next_heading else len(text)
        body = text[match.end():end].strip()
        plain = re.sub(r"[`#>*_{}|\[\]()-]", "", body).strip()
        if (not plain or re.fullmatch(r"(?is)(待补|todo|tbd|n/?a|\.\.\.)[：:].*", plain)
                or plain.casefold() in {"待补", "todo", "tbd", "n/a", "na", "..."}):
            failures.append(f"00_init.md 章节无实质内容: {heading}")
    return failures


def validate_resolution_args(args):
    for status in list(getattr(args, "require_status", None) or []):
        if not isinstance(status, str) or not status.strip():
            raise ControlError("--require-status 不得为空", exit_code=2,
                               gate_id="resolve_status_filter")
    for raw in list(getattr(args, "require_artifact", None) or []):
        artifact = Path(raw)
        if artifact.is_absolute() or not artifact.parts or ".." in artifact.parts:
            raise ControlError(
                f"--require-artifact 只接受工单目录内的安全相对路径: {raw!r}",
                exit_code=2, gate_id="resolve_artifact_path")


def require_resolution_filters(out_dir, meta, args):
    failures = resolution_filter_failures(out_dir, meta, args)
    if failures:
        raise ControlError(
            f"工单 {Path(out_dir).resolve()} 不满足解析过滤条件",
            exit_code=1, gate_id="resolve_ticket_filter", failures=failures,
            hint="核对 --require-status/--require-artifact，或先完成对应入口分析")


def resolve_dir(args) -> dict:
    """工单身份解析。优先级：--dir 显式 > --ticket 扫描+索引兜底 > --latest（只读便利）。"""
    validate_resolution_args(args)
    workspace = Path(args.workspace).resolve() if args.workspace else None
    if args.dir:
        out_dir = Path(args.dir).resolve()
        meta = load_metadata(out_dir)
        require_resolution_filters(out_dir, meta, args)
        return {"resolved": True, "source": "dir", "out_dir": str(out_dir),
                "ticket_id": meta.get("ticket_id"), "status": meta.get("status"),
                "schema_version": meta.get("schema_version", None), "warnings": []}
    if args.ticket:
        scan_roots = []
        if workspace:
            scan_roots.append(workspace / ".icode_output")
        else:
            scan_roots.append(Path.cwd() / ".icode_output")
        candidates = []
        for root in scan_roots:
            if not root.is_dir():
                continue
            for sub in sorted(root.glob(".icode_output_*")):
                mpath = sub / METADATA_NAME
                if not mpath.is_file():
                    continue
                try:
                    meta = load_json(mpath, "metadata")
                except ControlError:
                    continue
                if meta.get("ticket_id") == args.ticket:
                    candidates.append(sub)
        if len(candidates) > 1:
            raise ControlError(
                f"ticket_id={args.ticket} 命中 {len(candidates)} 个目录（目录误复用风险），拒绝猜测",
                exit_code=4,
                candidates=[str(c) for c in candidates],
                hint="用 --dir 显式指定目标目录，或先人工处理重复目录（参见 dir_and_metadata.md 硬熔断①）")
        if candidates:
            out_dir = candidates[0].resolve()
            meta = load_metadata(out_dir)
            require_resolution_filters(out_dir, meta, args)
            return {"resolved": True, "source": "scan", "out_dir": str(out_dir),
                    "ticket_id": meta.get("ticket_id"), "status": meta.get("status"),
                    "schema_version": meta.get("schema_version", None), "warnings": []}
        # 扫描未命中 → 全局索引兜底
        if INDEX_PATH.is_file():
            index = load_json(INDEX_PATH, "全局索引")
            matches = [entry for entry in index.get("tickets", [])
                       if entry.get("ticket_id") == args.ticket]
            if len(matches) > 1:
                raise ControlError(
                    f"ticket_id={args.ticket!r} 在全局索引中重复，拒绝猜测",
                    exit_code=4, gate_id="index_ticket_ambiguity", candidates=matches)
            if matches:
                entry = matches[0]
                if not isinstance(entry.get("project_path"), str) or not isinstance(entry.get("out_dir"), str):
                    raise ControlError(
                        f"索引中 ticket_id={args.ticket!r} 的 legacy 条目缺 project_path/out_dir，"
                        "无法解析目录",
                        exit_code=1, gate_id="index_pointer", index_entry=entry,
                        hint="用 --dir 显式指定工单目录；后续 index-write 会接管并规范化该条目")
                raw_out_dir = Path(entry["out_dir"])
                cand = (raw_out_dir if raw_out_dir.is_absolute()
                        else Path(entry["project_path"]) / raw_out_dir)
                archive = Path(entry["archive_path"]).resolve() \
                    if entry.get("archive_path") else None
                backup = Path(entry["backup_path"]).resolve() \
                    if entry.get("backup_path") else None
                chosen = None
                mismatches = []
                for candidate in (cand, archive, backup):
                    if candidate is None or not (candidate / METADATA_NAME).is_file():
                        continue
                    candidate_meta = load_metadata(candidate)
                    if candidate_meta.get("ticket_id") == args.ticket:
                        chosen = candidate
                        meta = candidate_meta
                        break
                    mismatches.append({"path": str(candidate),
                                       "actual": candidate_meta.get("ticket_id")})
                if chosen is None:
                    raise ControlError(
                        f"索引 ticket_id={args.ticket!r} 的源/归档/备份均不可读或身份不符",
                        exit_code=1, gate_id="index_pointer", index_entry=entry,
                        mismatches=mismatches,
                        hint="用 /icode list 核对保留位置；不要把复用路径中的其他工单当成目标")
                cand = chosen
                require_resolution_filters(cand, meta, args)
                return {"resolved": True, "source": "index", "out_dir": str(cand),
                        "ticket_id": args.ticket, "status": meta.get("status"),
                        "schema_version": meta.get("schema_version", None),
                        "warnings": ["身份经全局索引兜底解析，目录扫描未命中",
                                     "原工单目录失效，已切换到归档/备份控制根"
                                     if cand != Path(entry["project_path"]) / entry["out_dir"]
                                     else "工单目录由索引唯一解析"]}
        raise ControlError(f"ticket_id={args.ticket} 在扫描与全局索引中均未命中", exit_code=1)
    if args.latest:
        if not workspace:
            raise ControlError("--latest 需 --workspace <工程根>", exit_code=2)
        root = Path(workspace) / ".icode_output"
        numbered = []
        rejected = []
        filters_enabled = bool(getattr(args, "require_status", None) or
                               getattr(args, "require_artifact", None) or
                               getattr(args, "require_init_ready", False))
        for sub in root.glob(".icode_output_*"):
            suffix = sub.name.removeprefix(".icode_output_")
            if suffix.isdigit() and (sub / METADATA_NAME).is_file():
                if not filters_enabled:
                    numbered.append((int(suffix), sub, None))
                    continue
                if sub.resolve().parent != root.resolve():
                    rejected.append({"out_dir": str(sub),
                                     "failures": ["工单目录符号链接逃出当前工作区"]})
                    continue
                try:
                    meta = load_json(sub / METADATA_NAME, "metadata")
                    failures = resolution_filter_failures(sub, meta, args)
                except ControlError as exc:
                    rejected.append({"out_dir": str(sub), "reason": str(exc)})
                    continue
                if failures:
                    rejected.append({"out_dir": str(sub), "failures": failures})
                    continue
                numbered.append((int(suffix), sub, meta))
        best_entry = max(numbered, default=None, key=lambda item: item[0])
        if best_entry is None:
            qualifier = "且满足过滤条件" if (getattr(args, "require_status", None) or
                                               getattr(args, "require_artifact", None)) else ""
            extra = {"rejected": rejected}
            if qualifier:
                extra["gate_id"] = "resolve_ticket_filter"
            raise ControlError(
                f"{root} 下无含 metadata {qualifier}的工单目录", exit_code=1,
                **extra)
        _, best, meta = best_entry
        if meta is None:  # 无过滤器时保留旧行为：选最新后再解析，损坏 metadata 必须报错。
            meta = load_json(best / METADATA_NAME, "metadata")
        return {"resolved": True, "source": "latest", "out_dir": str(best.resolve()),
                "ticket_id": meta.get("ticket_id"), "status": meta.get("status"),
                "schema_version": meta.get("schema_version", None),
                "warnings": ["--latest 为只读便利解析；变更类命令请用 --ticket/--dir 显式解析"]}
    raise ControlError("resolve-ticket 需要 --dir / --ticket / --latest 之一", exit_code=2)


# ---------------------------------------------------------------- 事件链

def canonical_event_hash(event):
    material = {k: v for k, v in event.items() if k != "event_hash"}
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_raw_events(out_dir, *, _observation=None):
    path = Path(out_dir) / EVENTS_NAME
    events = []
    if not path.exists():
        return events, path
    if not path.is_file():
        raise ControlError(f"事件日志路径存在但不是普通文件: {path}", exit_code=1,
                           gate_id="event_log_path")
    storage_limits = storage_module().StorageLimits()
    if path.stat().st_size > storage_limits.max_raw_bytes:
        raise ControlError("candidate storage budget exceeded: max_raw_bytes", gate_id="candidate_storage")
    try:
        source = open(path, "r", encoding="utf-8") if _observation is None else _observation.text(path)
        with source as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                if len(events) >= storage_limits.max_events:
                    raise ControlError("candidate storage budget exceeded: max_events", gate_id="candidate_storage")
                try:
                    events.append(storage_module().strict_json_loads(line))
                except (json.JSONDecodeError, ValueError) as exc:
                    syntax = exc if isinstance(exc, json.JSONDecodeError) else exc.__cause__
                    details = {"path": str(path), "control_path": str(path), "control_root": str(path.parent),
                               "line": lineno, "recovery_action": "repair_control_ledger",
                               "reason": "invalid_control_json" if isinstance(syntax, json.JSONDecodeError)
                                         else "invalid_candidate_storage"}
                    if isinstance(syntax, json.JSONDecodeError):
                        details["column"] = syntax.colno
                    raise ControlError(f"事件日志第 {lineno} 行 JSON 解析失败（可能被截断/篡改）: {path}",
                                       exit_code=1, event_chain_broken_at_line=lineno, **details) from exc
    except OSError as exc:
        raise ControlError(f"事件日志读取失败: {path}: {exc}", exit_code=1,
                           gate_id="event_log_io")
    return events, path


def read_events(out_dir, *, _observation=None):
    events, path = _read_raw_events(out_dir, _observation=_observation)
    if any(storage_module().is_envelope(event) for event in events):
        events = decode_control_ledger(events, out_dir=out_dir).events
    return events, path


def _event_schema_for(event_type):
    """Load the event schema and reject an unknown event type before any read."""
    event_schema = load_json(SCHEMA_EVENT, "ticket-event.schema.json")
    allowed_types = event_schema["properties"]["event_type"]["enum"]
    if event_type not in allowed_types:
        raise ControlError(f"未知事件类型 {event_type!r}，允许: {allowed_types}", exit_code=2)
    return event_schema


def build_prepared_event(events, meta, event_type, payload, request_id=None, actor="icode",
                         *, event_schema=None):
    """Construct the next chained event from an already verified ledger.

    The caller owns chain verification. Batched writes use this to chain several
    events against one verified baseline instead of re-reading the ledger per
    member; a single write keeps using ``prepare_event``.
    """
    event_schema = event_schema if event_schema is not None else _event_schema_for(event_type)
    prev_hash = events[-1]["event_hash"] if events else GENESIS_HASH
    event = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "ticket_id": meta.get("ticket_id"),
        "request_id": request_id,
        "timestamp": now_iso(),
        "actor": actor,
        "event_type": event_type,
        "payload": payload or {},
        "previous_event_hash": prev_hash,
        "event_hash": "",
    }
    event["event_hash"] = canonical_event_hash(event)
    errs = check_schema(event, event_schema)
    if errs:
        raise ControlError("事件构造未通过 event schema: " + "; ".join(errs), exit_code=1)
    return event


def prepare_event(out_dir, meta, event_type, payload, request_id=None, actor="icode"):
    """锁定内校验现有链并构造下一事件；不落盘。"""
    # event schema 真源校验 event_type 枚举（先于链读取，保持既有错误优先级）
    event_schema = _event_schema_for(event_type)
    events, problems = verify_event_chain(out_dir, meta, semantic=False)
    if problems:
        raise ControlError("现有事件链不完整，拒绝追加事件", exit_code=1,
                           gate_id="event_chain", violations=problems)
    return build_prepared_event(events, meta, event_type, payload, request_id, actor,
                                event_schema=event_schema)


def append_prepared_event(out_dir, event, *, ledger=None):
    """Append one prepared event.

    ``ledger`` lets a batch reuse one decoded ledger instead of re-reading the
    file per member; the caller then owns keeping it current.
    """
    path = Path(out_dir) / EVENTS_NAME
    updated_registry = None
    store = FileDefinitionStore(out_dir)
    try:
        physical = event
        if ledger is not None:
            _logical_ledger_validator(event, ledger.events)
            physical, updated_registry = storage_module().encode_event(
                event, committed_registry=ledger.registry, definition_store=store)
        elif _storage_enabled(out_dir):
            current = _committed_ledger(out_dir)
            _logical_ledger_validator(event, current.events)
            physical, _ = storage_module().encode_event(event, committed_registry=current.registry,
                                                        definition_store=store)
        atomic_append_line(path, json.dumps(physical, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        raise ControlError(f"事件日志追加失败: {path}: {exc}", exit_code=1,
                           gate_id="event_log_io")
    if ledger is not None:
        ledger.events.append(event)
        ledger.registry = updated_registry


def append_event(out_dir, meta, event_type, payload, request_id=None, actor="icode",
                 *, git_provider=None, require_git_provider=False, inspection_provider_factory=None,
                 inspection_deadline=None, prepared=None, storage_ledger=None):
    """兼容事件-only 写入；调用方必须已持有 DirLock。

    ``prepared`` publishes an event the caller already chained against a
    verified ledger; every guard below still runs. ``storage_ledger`` is the
    optional decoded ledger reused by a batch.
    """
    access = _code_access("code", git_provider, require_git_provider, inspection_provider_factory, inspection_deadline)
    if access:
        _code_guard(out_dir, meta, **access)
    stage_token = _stage_write_guard(out_dir, event_type, payload, git_provider=git_provider, require_git_provider=require_git_provider)
    if prepared is not None:
        if prepared.get("event_type") != event_type or prepared.get("ticket_id") != meta.get("ticket_id"):
            raise ControlError("预构造事件与发布参数不一致", exit_code=1,
                               gate_id="event_preparation")
        event = prepared
    else:
        if stage_token is not None and event_type in {"step_started", "operation_started"}:
            payload = dict(payload, stage_generation=stage_token["generation"], stage_id=stage_token["stage_id"])
        event = prepare_event(out_dir, meta, event_type, payload, request_id=request_id, actor=actor)
    if access:
        _code_guard(out_dir, meta, **access)
    _stage_publication_check(stage_token, git_provider=git_provider, require_git_provider=require_git_provider)
    append_prepared_event(out_dir, event, ledger=storage_ledger)
    if access:
        # A late refusal does not erase an event already appended.
        _code_guard(out_dir, meta, **access)
    return event


def verify_event_chain(out_dir, meta=None, semantic=True, *, git_provider=None, require_git_provider=False,
                       _observation=None):
    events, _ = read_events(out_dir) if _observation is None else read_events(out_dir, _observation=_observation)
    problems = []
    prev = GENESIS_HASH
    seen_event_ids = set()
    for i, ev in enumerate(events, 1):
        schema_errors = check_schema(ev, load_json(SCHEMA_EVENT, "ticket-event.schema.json"))
        problems.extend(f"第 {i} 行 event_schema: {err}" for err in schema_errors)
        event_id = ev.get("event_id")
        if event_id in seen_event_ids:
            problems.append(f"第 {i} 行 event_id 重复: {event_id!r}")
        seen_event_ids.add(event_id)
        if meta is not None and ev.get("ticket_id") != meta.get("ticket_id"):
            problems.append(
                f"第 {i} 行 event_ticket_id={ev.get('ticket_id')!r} 与 metadata.ticket_id="
                f"{meta.get('ticket_id')!r} 不一致")
        if ev.get("previous_event_hash") != prev:
            problems.append(f"第 {i} 行 previous_event_hash 断链")
        if ev.get("event_hash") != canonical_event_hash(ev):
            problems.append(f"第 {i} 行 event_hash 与内容不符（疑似篡改/局部修改）")
        prev = ev.get("event_hash", prev)
    if _candidate_access_requested(git_provider, require_git_provider):
        try:
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
            if semantic is not True:
                raise ControlError("B1 requires semantic event validation", gate_id="candidate_provider_context")
            if meta is not None and is_vnext(meta):
                problems.extend(f"event_semantics: {item}"
                                for item in validate_event_semantics(events, meta, candidate_api=candidate_module(),
                                    git_provider=git_provider, require_git_provider=require_git_provider))
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        except ControlError as exc:
            problems.append("candidate_provider_context: " + exc.message)
    elif semantic and meta is not None and is_vnext(meta):
        problems.extend(f"event_semantics: {item}"
                        for item in validate_event_semantics(events, meta))
    return events, problems


class ExecutionBindingMirror:
    """Stream binding consistency and open IDs, without duplicating the event log."""
    def __init__(self, metadata):
        self.metadata = metadata
        self.binding = None
        self.seen = False
        self.open_steps = set()
        self.open_operations = set()
        self.open_agents = set()
        self.closed = False
        self.issues = set()

    def consume(self, event):
        kind = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            # Missing pairing identities still represent unclosed execution.
            payload = {}
        for start, finish, key, opened in (
            ("step_started", "step_finished", "attempt", self.open_steps),
            ("operation_started", "operation_finished", "attempt", self.open_operations),
            ("agent_spawned", "agent_result", "spawn_id", self.open_agents),
        ):
            identity = payload.get(key)
            if kind == start:
                if isinstance(identity, str) and identity:
                    opened.add(identity)
                else:
                    # Legacy starts without pairing identity cannot prove quiescence.
                    opened.add(None)
            elif kind == finish and isinstance(identity, str):
                opened.discard(identity)
        if kind == "close_phase":
            self.closed = True
        elif kind == "ticket_reopened":
            self.closed = False
            if self.seen:
                self.issues.add("binding_checkout_conflict")
        if kind != "metadata_updated":
            if "execution_root_binding" in payload:
                self.issues.add("binding_wrong_event_type")
            return
        updates = payload.get("set")
        appends = payload.get("append")
        touches = ((isinstance(updates, dict) and "execution_binding" in updates)
                   or (isinstance(appends, dict) and "execution_binding" in appends))
        marked = "execution_root_binding" in payload
        if not touches and not marked:
            if self.seen:
                for change in (updates, appends):
                    if isinstance(change, dict) and change.get("active_checkout") is not None:
                        self.issues.add("binding_checkout_conflict")
                    history = change.get("checkout_history") if isinstance(change, dict) else None
                    if isinstance(history, list) and any(
                        isinstance(row, dict) and row.get("state") == "active" for row in history
                    ):
                        self.issues.add("binding_checkout_conflict")
            return
        required = {"execution_root_binding", "set", "append", "metadata_hash_after"}
        recorded_hash = payload.get("metadata_hash_after")
        if (type(payload.get("execution_root_binding")) is not int
                or payload.get("execution_root_binding") != 1
                or set(payload) != required
                or not isinstance(recorded_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", recorded_hash) is None
                or not isinstance(updates, dict)
                or set(updates) != {"execution_binding"}
                or appends != {}
                or not isinstance(event.get("request_id"), str)
                or not event["request_id"].strip()
                or event.get("actor") != "icode"):
            self.issues.add("binding_event_shape")
        if self.seen:
            self.issues.add("binding_event_duplicate")
        if self.open_steps or self.open_operations or self.open_agents or self.closed:
            self.issues.add("binding_not_quiescent")
        candidate = updates.get("execution_binding") if isinstance(updates, dict) else None
        if not execution_binding_shape_ok(candidate):
            self.issues.add("binding_shape")
        self.binding = candidate
        self.seen = True

    def finish(self):
        meta = self.metadata
        if isinstance(meta, dict):
            if not execution_binding_topology_ok(meta):
                self.issues.add("binding_checkout_conflict")
            if ("execution_binding" in meta) != self.seen:
                self.issues.add("binding_metadata_mismatch")
            elif self.seen and meta["execution_binding"] != self.binding:
                self.issues.add("binding_metadata_mismatch")
        return sorted(self.issues)


def state_change_payload(event):
    if event.get("event_type") == "state_changed":
        return event.get("payload") or {}
    if event.get("event_type") == "verification_recorded":
        return (event.get("payload") or {}).get("lite_invalidation")
    return None


def validate_event_semantics(events, meta, *, candidate_api=None, git_provider=None, require_git_provider=False):
    """校验控制事件的顺序和 payload 语义，防止自洽 hash 掩盖伪造生命周期。"""
    problems = []
    sm = load_state_machine(meta)
    births = [(idx, event) for idx, event in enumerate(events)
              if event.get("event_type") == "ticket_created"]
    migrations = [(idx, event) for idx, event in enumerate(events)
                  if event.get("event_type") == "migration_applied"]
    if births and migrations:
        problems.append("事件链同时含 ticket_created 与 migration_applied，工单来源多义")
    if len(births) > 1:
        problems.append("ticket_created 事件只能出现一次")
    if len(migrations) > 1:
        problems.append("migration_applied 事件只能出现一次")
    if events and events[0].get("event_type") not in {"ticket_created", "migration_applied"}:
        problems.append("首事件必须是 ticket_created 或 migration_applied")

    current_state = None
    if births:
        birth_idx, birth = births[0]
        if birth_idx != 0:
            problems.append("ticket_created 必须是事件链首事件")
        birth_kind = birth.get("payload", {}).get("birth_kind")
        initial_by_kind = {
            "init": "init_in_progress", "plan": "init_in_progress",
            "log": "log_in_progress", "debug-init": "debug_in_progress",
            "debug-log": "debug_in_progress", "lite": "lite_prepare_in_progress",
        }
        current_state = initial_by_kind.get(birth_kind)
        if meta.get("mode") == "lite":
            expected_identity = {"mode": "lite", "workflow_version": 1,
                                 "requirement_digest": canonical_digest(meta["requirement"])}
            if birth["payload"].get("workflow_identity") != expected_identity:
                problems.append("lite 出生身份或原始需求被改写")
        if current_state is None:
            problems.append(f"ticket_created.birth_kind 非法或缺失: {birth_kind!r}")
        expected_debug = isinstance(birth_kind, str) and birth_kind.startswith("debug-")
        if bool(meta.get("debug")) != expected_debug:
            problems.append("ticket_created.birth_kind 与 metadata.debug 不一致")

    review_return_mirror = ExecutionBindingMirror(meta)
    for idx, event in enumerate(events, 1):
        review_return_mirror.consume(event)
        payload = state_change_payload(event)
        if payload is None:
            continue
        source = payload.get("from")
        target = payload.get("to")
        if source not in sm["states"] or target not in sm["states"]:
            problems.append(f"第 {idx} 行 state_changed.from/to 不在状态词表")
            continue
        if current_state is not None and source != current_state:
            problems.append(
                f"第 {idx} 行 state_changed.from={source!r} 与事件序列当前状态 "
                f"{current_state!r} 不一致")
        transition = legal_transition(sm, source, target)
        if transition is None:
            problems.append(f"第 {idx} 行记录非法状态流转 {source!r} → {target!r}")
        if transition and (transition.get("review_return") or
                           (source == "completed" and target == "review_in_progress"
                            and "review_return" in payload)):
            if not review_return_payload_ok(payload) or not event.get("request_id"):
                problems.append(f"第 {idx} 行审查恢复缺少修订、原因或请求身份")
            if (review_return_mirror.open_steps or review_return_mirror.open_operations
                    or review_return_mirror.open_agents or review_return_mirror.closed):
                problems.append(f"第 {idx} 行审查恢复发生于未静止或关闭工单")
        elif "review_return" in payload:
            problems.append(f"第 {idx} 行非恢复流转携带审查恢复标记")
        delivery = payload.get("delivery_verdict")
        if target == "completed" and delivery not in DELIVERY_VERDICTS:
            problems.append(f"第 {idx} 行 completed 流转缺合法 delivery_verdict")
        if target != "completed" and delivery is not None:
            problems.append(f"第 {idx} 行非 completed 流转携带 delivery_verdict")
        current_state = target
    if current_state is not None and current_state != meta.get("status"):
        problems.append(
            f"事件序列最终状态 {current_state!r} 与 metadata.status={meta.get('status')!r} 不一致")

    close_state = None
    phases = sm["close_phases"]
    for idx, event in enumerate(events, 1):
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "close_phase":
            source = payload.get("from")
            target = payload.get("to")
            expected_idx = 0 if close_state is None else phases.index(close_state) + 1
            expected = phases[expected_idx] if expected_idx < len(phases) else None
            if source != close_state or target != expected:
                problems.append(
                    f"第 {idx} 行 close_phase 顺序非法：from={source!r}, to={target!r}, "
                    f"期望 from={close_state!r}, to={expected!r}")
            if target in phases:
                close_state = target
        elif event_type == "ticket_reopened":
            if close_state != "closed" or payload.get("from") != "closed" \
                    or payload.get("to") is not None:
                problems.append(
                    f"第 {idx} 行 ticket_reopened 只能从 closed 原子解冻到 null")
            close_state = None
    if close_state != meta.get("close_state"):
        problems.append(
            f"事件序列最终 close_state={close_state!r} 与 metadata.close_state="
            f"{meta.get('close_state')!r} 不一致")

    problems.extend(validate_execution_event_semantics(events, meta))

    event_runs = [{key: value for key, value in (event.get("payload") or {}).items()
                   if key not in {"metadata_hash_after", "lite_invalidation"}}
                  for event in events
                  if event.get("event_type") == "verification_recorded"]
    metadata_runs = meta.get("verification_runs") or []
    if event_runs != metadata_runs:
        problems.append("verification_recorded 事件与 metadata.verification_runs 不一致")
    event_claims = [{key: value for key, value in (event.get("payload") or {}).items()
                     if key != "metadata_hash_after"}
                    for event in events
                    if event.get("event_type") == "claim_recorded"]
    metadata_claims = meta.get("claims") or []
    if event_claims != metadata_claims:
        problems.append("claim_recorded 事件与 metadata.claims 不一致")
    event_skill_runs = [{key: value for key, value in (event.get("payload") or {}).items()
                         if key != "metadata_hash_after"}
                        for event in events
                        if event.get("event_type") == "skill_run_recorded"]
    metadata_skill_runs = ((((meta.get("extensions") or {}).get("skills") or {})
                            .get("runs")) or [])
    if event_skill_runs != metadata_skill_runs:
        problems.append("skill_run_recorded 事件与 metadata.extensions.skills.runs 不一致")
    event_agent_spawns, agent_problems = reconstruct_agent_spawns(events)
    problems.extend(agent_problems)
    metadata_agent_spawns = ((((meta.get("extensions") or {}).get("agent") or {})
                              .get("spawns")) or [])
    if event_agent_spawns != metadata_agent_spawns:
        problems.append("agent_spawned/agent_result 事件与 metadata.extensions.agent.spawns 不一致")
    hashed_events = [event for event in events
                     if isinstance(event.get("payload"), dict)
                     and event["payload"].get("metadata_hash_after")]
    if hashed_events:
        recorded_hash = hashed_events[-1]["payload"]["metadata_hash_after"]
        if recorded_hash != metadata_hash(meta):
            problems.append(
                "metadata 与最后一条受控写事件的 metadata_hash_after 不一致，"
                "疑似绕过 metadata-update/专用命令直写")
    binding_mirror = ExecutionBindingMirror(meta)
    for event in events:
        binding_mirror.consume(event)
    problems.extend(binding_mirror.finish())
    problems.extend(candidate_event_issues(events, meta))
    problems.extend(stage_module().event_issues(events, meta))
    event_candidate_api = candidate_module() if candidate_api is None else candidate_api
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    problems.extend(verification_evidence_module().event_issues(events, meta, event_candidate_api, **access))
    return problems


def reconstruct_agent_spawns(events):
    """从专用事件重建 Agent 生命周期；metadata 只是该账本的物化视图。"""
    problems = []
    spawns = []
    by_id = {}
    required_spawn = {
        "spawn_id", "at", "task_scope", "expected_artifact", "evidence_boundary",
        "join_condition", "backend", "model", "capabilities",
    }
    required_result = {
        "spawn_id", "result", "adopted", "adoption_reason", "result_at",
        "evidence_refs", "summary_digest",
    }
    for line, event in enumerate(events, 1):
        event_type = event.get("event_type")
        if event_type not in {"agent_spawned", "agent_result"}:
            continue
        payload = dict(event.get("payload") or {})
        payload.pop("metadata_hash_after", None)
        if event_type == "agent_spawned":
            missing = sorted(required_spawn - set(payload))
            if missing:
                problems.append(f"第 {line} 行 agent_spawned 缺字段: {missing}")
                continue
            spawn_id = payload.get("spawn_id")
            if not isinstance(spawn_id, str) or not spawn_id:
                problems.append(f"第 {line} 行 agent_spawned.spawn_id 非法")
                continue
            if spawn_id in by_id:
                problems.append(f"第 {line} 行 agent spawn_id 重复: {spawn_id!r}")
                continue
            capabilities = payload.get("capabilities")
            if not isinstance(capabilities, list) or not capabilities \
                    or "text" not in capabilities \
                    or any(item not in AGENT_CAPABILITIES for item in capabilities) \
                    or len(set(capabilities)) != len(capabilities):
                problems.append(f"第 {line} 行 agent_spawned.capabilities 非法")
            record = payload
            spawns.append(record)
            by_id[spawn_id] = record
            if sum(1 for item in spawns if "result" not in item) > MAX_OPEN_AGENT_SPAWNS:
                problems.append(
                    f"第 {line} 行开放 agent spawn 超过上限 {MAX_OPEN_AGENT_SPAWNS}")
            continue

        missing = sorted(required_result - set(payload))
        if missing:
            problems.append(f"第 {line} 行 agent_result 缺字段: {missing}")
            continue
        spawn_id = payload.get("spawn_id")
        record = by_id.get(spawn_id)
        if record is None:
            problems.append(f"第 {line} 行 agent_result 引用未知 spawn_id: {spawn_id!r}")
            continue
        if "result" in record:
            problems.append(f"第 {line} 行 agent spawn 重复终结: {spawn_id!r}")
            continue
        if payload.get("result") not in AGENT_RESULTS:
            problems.append(f"第 {line} 行 agent_result.result 非法")
        if payload.get("adopted") not in AGENT_ADOPTION:
            problems.append(f"第 {line} 行 agent_result.adopted 非法")
        if payload.get("result") != "joined" and payload.get("adopted") != "no":
            problems.append(f"第 {line} 行失败 Agent 结果只能 adopted=no")
        record.update({key: value for key, value in payload.items() if key != "spawn_id"})
    return spawns, problems


def validate_execution_event_semantics(events, meta=None):
    """校验 v1 执行事件配对；未标版本的旧 step 事件保持可读兼容。"""
    problems = []
    model = load_execution_model(meta)
    catalog_problems = validate_execution_catalog(model)
    if catalog_problems:
        return [f"execution_model catalog: {item}" for item in catalog_problems]
    steps = {}
    operations = {}
    for line, event in enumerate(events, 1):
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if payload.get("execution_model_version") != model["schema_version"]:
            continue
        attempt = payload.get("attempt")
        if not isinstance(attempt, str) or not attempt:
            problems.append(f"第 {line} 行 {event_type} 缺非空 attempt")
            continue
        if event_type == "step_started":
            step = payload.get("step")
            if step not in model["step_contracts"]:
                problems.append(f"第 {line} 行 step_started.step 非法: {step!r}")
            if attempt in steps:
                problems.append(f"第 {line} 行 step attempt 重复启动: {attempt!r}")
            if re.fullmatch(r"[0-9a-f]{64}", payload.get("input_digest", "")) is None:
                problems.append(f"第 {line} 行 step_started.input_digest 非法")
            if re.fullmatch(r"[0-9a-f]{64}", payload.get("contract_digest", "")) is None:
                problems.append(f"第 {line} 行 step_started.contract_digest 非法")
            steps[attempt] = {"step": step, "finished": False, "line": line,
                              "checks": set()}
        elif event_type == "gate_checked":
            state = steps.get(attempt)
            if state is None:
                problems.append(f"第 {line} 行 gate_checked 引用不存在的 step attempt: {attempt!r}")
                continue
            if state["finished"]:
                problems.append(f"第 {line} 行 gate_checked 出现在 step_finished 之后")
            if payload.get("step") != state["step"]:
                problems.append(f"第 {line} 行 gate_checked.step 与启动事件不一致")
            boundary = payload.get("boundary")
            if boundary not in model["boundaries"]:
                problems.append(f"第 {line} 行 gate_checked.boundary 非法: {boundary!r}")
            else:
                state["checks"].add(boundary)
            if payload.get("result") not in {"pass", "blocked"}:
                problems.append(f"第 {line} 行 gate_checked.result 非法")
            for key in ("captured_digest", "current_digest"):
                if re.fullmatch(r"[0-9a-f]{64}", payload.get(key, "")) is None:
                    problems.append(f"第 {line} 行 gate_checked.{key} 非法")
        elif event_type == "artifact_written":
            state = steps.get(attempt)
            if state is None:
                problems.append(f"第 {line} 行 artifact_written 引用不存在的 step attempt")
                continue
            if state["finished"]:
                problems.append(f"第 {line} 行 artifact_written 出现在 step_finished 之后")
            if payload.get("step") != state["step"]:
                problems.append(f"第 {line} 行 artifact_written.step 与启动事件不一致")
            if not isinstance(payload.get("sha256"), str) \
                    or re.fullmatch(r"[0-9a-f]{64}", payload.get("sha256", "")) is None:
                problems.append(f"第 {line} 行 artifact_written.sha256 非法")
        elif event_type == "step_finished":
            state = steps.get(attempt)
            if state is None:
                problems.append(f"第 {line} 行 step_finished 引用不存在的 step attempt")
                continue
            if state["finished"]:
                problems.append(f"第 {line} 行 step attempt 重复终结: {attempt!r}")
            if payload.get("step") != state["step"]:
                problems.append(f"第 {line} 行 step_finished.step 与启动事件不一致")
            if payload.get("outcome") not in model["step_outcomes"]:
                problems.append(f"第 {line} 行 step_finished.outcome 非法")
            if not isinstance(payload.get("duration_ms"), int) or payload["duration_ms"] < 0:
                problems.append(f"第 {line} 行 step_finished.duration_ms 非法")
            state["finished"] = True
        elif event_type == "operation_started":
            op_class = payload.get("class")
            if not isinstance(payload.get("name"), str) or not payload["name"]:
                problems.append(f"第 {line} 行 operation_started.name 非法")
            if op_class not in model["operation_classes"]:
                problems.append(f"第 {line} 行 operation_started.class 非法: {op_class!r}")
            if re.fullmatch(r"[0-9a-f]{64}", payload.get("input_digest", "")) is None:
                problems.append(f"第 {line} 行 operation_started.input_digest 非法")
            if not isinstance(payload.get("idempotency_provided"), bool):
                problems.append(f"第 {line} 行 operation_started.idempotency_provided 非布尔")
            if attempt in operations:
                problems.append(f"第 {line} 行 operation attempt 重复启动: {attempt!r}")
            operations[attempt] = {"name": payload.get("name"), "class": op_class,
                                   "finished": False, "line": line}
        elif event_type == "operation_finished":
            state = operations.get(attempt)
            if state is None:
                problems.append(f"第 {line} 行 operation_finished 引用不存在的 attempt")
                continue
            if state["finished"]:
                problems.append(f"第 {line} 行 operation attempt 重复终结: {attempt!r}")
            if payload.get("name") != state["name"] or payload.get("class") != state["class"]:
                problems.append(f"第 {line} 行 operation_finished 与启动事件身份不一致")
            if payload.get("outcome") not in model["step_outcomes"]:
                problems.append(f"第 {line} 行 operation_finished.outcome 非法")
            if not isinstance(payload.get("duration_ms"), int) or payload["duration_ms"] < 0:
                problems.append(f"第 {line} 行 operation_finished.duration_ms 非法")
            if not isinstance(payload.get("evidence"), str) or not payload["evidence"].strip():
                problems.append(f"第 {line} 行 operation_finished 缺 evidence")
            if not isinstance(payload.get("after_check"), str) or not payload["after_check"].strip():
                problems.append(f"第 {line} 行 operation_finished 缺 after_check")
            if not isinstance(payload.get("decision"), dict):
                problems.append(f"第 {line} 行 operation_finished 缺 decision 对象")
            state["finished"] = True
    return problems


def metadata_hash(meta):
    raw = json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_event_time(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def elapsed_ms(start, end=None):
    start_at = parse_event_time(start)
    end_at = parse_event_time(end or now_iso())
    if start_at is None or end_at is None:
        return None
    return max(0, int((end_at - start_at).total_seconds() * 1000))


def json_pointer_get(document, pointer):
    """返回 (存在, 值)；仅实现 execution_model 使用的 RFC6901 对象/数组读取。"""
    if pointer == "":
        return True, document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False, None
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def execution_workspace(out_dir, meta):
    if "execution_binding" in meta:
        return bound_execution_workspace(out_dir, meta)
    active = meta.get("active_checkout")
    if isinstance(active, dict):
        active_path = active.get("path") or active.get("worktree_path")
        if isinstance(active_path, str) and active_path.strip():
            return Path(active_path).expanduser().resolve()
    raw = meta.get("project_path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return containing_workspace(out_dir)


def trusted_execution_workspace(out_dir, meta):
    """为外部 Agent 返回经拓扑校验的执行根；拒绝直接信任任意 metadata 路径。"""
    if "execution_binding" in meta:
        return bound_execution_workspace(out_dir, meta)
    active = meta.get("active_checkout")
    active_history = [item for item in (meta.get("checkout_history") or [])
                      if isinstance(item, dict) and item.get("state") == "active"]
    if active_history:
        history_path = active_history[0].get("path") or active_history[0].get("worktree_path")
        active_path = (active.get("path") or active.get("worktree_path")) if isinstance(active, dict) else None
        if (len(active_history) != 1 or not isinstance(history_path, str) or not history_path.strip()
                or not Path(history_path).is_absolute() or not Path(history_path).is_dir()
                or not isinstance(active_path, str) or not active_path.strip()
                or not Path(active_path).is_absolute() or not Path(active_path).is_dir()
                or Path(history_path).resolve() != Path(active_path).resolve()):
            raise ControlError(
                "checkout_history 与 active_checkout 活动根不唯一或路径无效",
                exit_code=1, gate_id="execution_root_topology")

    if isinstance(active, dict):
        if active.get("state") != "active":
            raise ControlError(
                "active_checkout 尚未获得 active 状态，拒绝作为执行根",
                exit_code=1, gate_id="execution_root_topology")
        raw = active.get("path") or active.get("worktree_path")
        if not isinstance(raw, str) or not raw.strip() or not Path(raw).is_absolute():
            raise ControlError(
                "active_checkout.path 必须是绝对路径",
                exit_code=1, gate_id="execution_root_topology")
        candidate = Path(raw).resolve()
        if not candidate.is_dir():
            raise ControlError(
                "active_checkout.path 不存在或不是目录",
                exit_code=1, gate_id="execution_root_topology")
        root_proc = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10)
        branch_proc = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10)
        if root_proc.returncode != 0 or Path(root_proc.stdout.strip()).resolve() != candidate:
            raise ControlError(
                "active_checkout.path 不是独立 Git checkout 根",
                exit_code=1, gate_id="execution_root_topology")
        expected_branch = active.get("branch")
        if isinstance(expected_branch, str) and expected_branch.strip() \
                and (branch_proc.returncode != 0
                     or branch_proc.stdout.strip() != expected_branch):
            raise ControlError(
                "active_checkout.branch 与实际 Git 分支不一致",
                exit_code=1, gate_id="execution_root_topology")
        return candidate

    raw_project = meta.get("project_path")
    if isinstance(raw_project, str) and raw_project.strip():
        workspace = Path(raw_project).expanduser().resolve()
    else:
        workspace = containing_workspace(out_dir)
    if not workspace.is_dir():
        raise ControlError(
            "工单工程根不存在或不是目录",
            exit_code=1, gate_id="execution_root_topology")
    return workspace


def port_path(base, raw):
    candidate = Path(raw).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def file_fact(path, label=None):
    path = Path(path)
    fact = {"path": label or str(path), "exists": path.is_file()}
    if fact["exists"]:
        fact["sha256"] = file_sha256(path)
        fact["size"] = path.stat().st_size
    return fact


def section_ignoring_file_fact(path, label, sections):
    """按「忽略指定章节」计算文件事实，供受保护计划端口使用。

    终审合同要求把「实现偏差备忘」回写到 03_plan_final.md，而该文件同时是 audit 的
    受保护输入。整文件取哈希会让合规回写被判成漂移，因此这里只对**第一个被忽略章节
    之前**的正文取哈希：保护范围收敛为「计划正文」，被显式允许改写的章节（定稿时预留、
    终审回填）不参与比对。尾部空行一并归一化，使「定稿时还没有备忘段、终审才追加」与
    「定稿时就带空备忘段」得到相同摘要。`ignore_sections` 必须在端口上显式声明，未声明的
    端口保持整文件保护。
    """
    data = Path(path).read_bytes()
    text = data.decode("utf-8")
    cut = len(text)
    for heading in sections:
        index = text.find(heading)
        if index != -1:
            cut = min(cut, index)
    body = text[:cut].rstrip("\r\n").encode("utf-8")
    return {"path": label or str(path), "exists": True,
            "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
            "ignored_sections": list(sections)}


def port_value(out_dir, meta, port):
    """Resolve an explicit delivery path, retaining historical fixed names."""
    value = port.get("value")
    pointer = port.get("path_pointer")
    if pointer:
        exists, override = json_pointer_get(meta, pointer)
        if exists:
            if not isinstance(override, str) or not override.strip() or Path(override).is_absolute() \
                    or not port_path(Path(out_dir), override).is_relative_to(Path(out_dir).resolve()):
                raise ControlError("交付产物路径必须位于工单目录内", gate_id="step_ports")
            value = override
    return value


def requires_execution_receipts(events):
    return any(event.get("event_type") == "ticket_created" and
               (event.get("payload") or {}).get("execution_contract_version") == 1
               for event in events)


def completion_receipt_issues(out_dir, meta, events, step):
    starts = [e for e in events if e.get("event_type") == "step_started" and
              (e.get("payload") or {}).get("step") == step]
    if not starts:
        return [f"{step}:missing_step_start"]
    start = starts[-1]
    if meta.get("mode") == "lite":
        order = workflow_contract(meta)["step_order"]
        for event in events[events.index(start) + 1:]:
            target = (state_change_payload(event) or {}).get("to")
            if target in {"lite_prepare_in_progress", "lite_code_in_progress"} and step in order[(0 if target == "lite_prepare_in_progress" else 1):]:
                return [step + ":before_lite_return"]
    # Returning to review invalidates workflow completion, not retained raw
    # executions. New steps may honestly reuse unchanged build/test artifacts.
    returns = [i for i, e in enumerate(events) if e.get("event_type") == "state_changed"
               and (e.get("payload") or {}).get("review_return") == 1]
    if (returns and step in {"review", "merge", "code", "deepcheck", "audit"}
            and events.index(start) < returns[-1]):
        return [f"{step}:before_review_return"]
    finish = step_attempt_finished(events, start["payload"]["attempt"])
    outcome = (finish.get("payload") or {}).get("outcome") if finish else None
    if outcome not in ({"success"} if meta.get("mode") == "lite" else {"success", "degraded"}):
        return [f"{step}:missing_advancing_receipt"]
    if outcome == "degraded" and meta.get("mode") != "lite":
        return []  # Existing explicit debt contracts still govern degradation.
    model = load_execution_model(meta)
    _, missing = validate_step_outputs(out_dir, meta, model, step)
    missing += missing_output_receipts(out_dir, meta, step_contract(model, step), events, start)
    return missing


def resolve_port(out_dir, meta, port, *, git_provider=None, require_git_provider=False):
    if not _candidate_access_requested(git_provider, require_git_provider):
        return _resolve_port_legacy(out_dir, meta, port)
    module = candidate_module()
    deadline = time.monotonic() + module.DEFAULT_LIMITS["timeout_seconds"]
    _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
    workspace = execution_workspace(out_dir, meta)
    try:
        module._candidate_provider(workspace, git_provider, require_git_provider, deadline=deadline)
        if port.get("kind") == "workspace_git_head":
            targets = [port_path(workspace, port_value(out_dir, meta, port) or ".")]
            for field, keys in (
                ("sub_worktrees", ("worktree_path", "path")),
                ("submission_contracts", ("repo_path", "worktree_path")),
            ):
                if field not in meta:
                    continue
                declarations = meta[field]
                if not isinstance(declarations, list):
                    raise module.CandidateError("unsupported provider port roots")
                for item in declarations:
                    if not isinstance(item, dict):
                        raise module.CandidateError("unsupported provider port roots")
                    raw_paths = [item[key] for key in keys if key in item]
                    if not raw_paths or any(not isinstance(raw, str) or not raw.strip()
                                            or not Path(raw).is_absolute() for raw in raw_paths):
                        raise module.CandidateError("unsupported provider port roots")
                    targets.extend(Path(raw).expanduser().resolve() for raw in raw_paths)
            if any(target != workspace for target in targets):
                raise module.CandidateError("unsupported provider port roots")
            # Same bounded FACTS contract used by capture_candidate, not raw Git.
            facts = module._candidate_facts(
                workspace, git_provider, min(8192, module.DEFAULT_LIMITS["max_git_bytes"]),
                deadline=deadline)
            roots = [{"root": workspace.as_posix(), "head": facts["head"]}]
            result = {"id": port.get("id"), "kind": "workspace_git_head", "exists": True,
                      "roots": roots, "digest": canonical_digest(roots)}
        else:
            result = _resolve_port_legacy(out_dir, meta, port)
        _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        module._candidate_provider(workspace, git_provider, require_git_provider, deadline=deadline)
    except (module.CandidateError, OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
        raise ControlError(str(exc), gate_id="candidate_provider_context") from exc
    return result


def _resolve_port_legacy(out_dir, meta, port):
    """把静态端口解析为不含正文的事实摘要，供漂移比较和输出校验。"""
    kind = port.get("kind")
    value = port_value(out_dir, meta, port)
    workspace = execution_workspace(out_dir, meta)
    result = {"id": port.get("id"), "kind": kind, "exists": False}
    if kind == "metadata_pointer":
        exists, current = json_pointer_get(meta, value)
        result.update({"pointer": value, "exists": exists and current is not None})
        if exists:
            result["digest"] = canonical_digest(current)
        return result
    if kind == "ticket_file":
        path = port_path(Path(out_dir), value)
        sections = [item for item in (port.get("ignore_sections") or [])
                    if isinstance(item, str) and item.strip()]
        if sections and path.is_file():
            return {"id": port.get("id"), "kind": kind,
                    **section_ignoring_file_fact(path, str(Path(value)), sections)}
        return {"id": port.get("id"), "kind": kind,
                **file_fact(path, str(Path(value)))}
    if kind == "ticket_glob":
        paths = sorted(
            path for path in Path(out_dir).glob(value)
            if path.is_file()
        )
        items = [file_fact(path, str(path.relative_to(Path(out_dir)))) for path in paths]
        result.update({"pattern": value, "exists": bool(items), "items": items,
                       "digest": canonical_digest(items)})
        return result
    if kind == "metadata_files":
        pointer_exists, raw_paths = json_pointer_get(meta, value)
        base = workspace if port.get("base", "ticket") == "workspace" else Path(out_dir)
        valid_list = pointer_exists and isinstance(raw_paths, list) and bool(raw_paths)
        items = []
        if isinstance(raw_paths, list):
            for raw_path in raw_paths:
                if not isinstance(raw_path, str) or not raw_path.strip():
                    items.append({"path": repr(raw_path), "exists": False})
                    continue
                path = port_path(base, raw_path)
                label = str(path.relative_to(base)) if path.is_relative_to(base) else str(path)
                items.append(file_fact(path, label))
        all_present = valid_list and all(item["exists"] for item in items)
        result.update({"pointer": value, "base": port.get("base", "ticket"),
                       "exists": all_present, "items": items,
                       "digest": canonical_digest(items)})
        return result
    if kind == "workspace_git_head":
        candidates = [port_path(workspace, value or ".")]
        for item in meta.get("sub_worktrees") or []:
            if isinstance(item, dict):
                raw = item.get("worktree_path") or item.get("path")
                if isinstance(raw, str) and raw.strip():
                    candidates.append(Path(raw).expanduser().resolve())
        for item in meta.get("submission_contracts") or []:
            if isinstance(item, dict):
                raw = item.get("repo_path") or item.get("worktree_path")
                if isinstance(raw, str) and raw.strip():
                    candidates.append(Path(raw).expanduser().resolve())
        roots = {}
        failures = []
        for target in candidates:
            try:
                root_proc = subprocess.run(
                    ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=10)
                if root_proc.returncode != 0:
                    continue  # 明确不是 Git 仓库：稳定的“无事实”结论，不当作故障
                head_proc = subprocess.run(
                    ["git", "-C", str(target), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=10)
            except FileNotFoundError:
                continue  # 本机没有 git 可执行文件：同样是稳定结论
            except (OSError, subprocess.TimeoutExpired) as exc:
                # 瞬时读取失败若被 continue 掉，roots 会变小、digest 随之变化，
                # 进而被误判成“工作区漂移”。这里必须 fail-closed 到明确错误。
                failures.append(f"{target}: {type(exc).__name__}")
                continue
            if head_proc.returncode != 0:
                failures.append(f"{target}: HEAD 读取失败")
                continue
            if root_proc.returncode == 0 and head_proc.returncode == 0:
                roots[root_proc.stdout.strip()] = head_proc.stdout.strip()
        if failures:
            raise ControlError(
                "workspace_git_head 端口解析失败（瞬时环境故障，不能当作工作区漂移）: "
                + "; ".join(failures),
                exit_code=1, gate_id="step_ports_transient")
        facts = [{"root": root, "head": roots[root]} for root in sorted(roots)]
        if facts:
            result.update({"exists": True, "roots": facts,
                           "digest": canonical_digest(facts)})
        return result
    if kind == "external_artifact":
        rendered = str(value).replace("<workspace>", str(workspace))
        rendered = str(Path(rendered).expanduser())
        matches = sorted(Path(path).resolve() for path in globlib.glob(rendered))
        facts = []
        for path in matches:
            if path.is_file():
                facts.append(file_fact(path))
            elif path.is_dir():
                facts.append({"path": str(path), "exists": True, "type": "directory"})
        result.update({"pattern": rendered, "exists": bool(facts), "items": facts,
                       "digest": canonical_digest(facts)})
        return result
    raise ControlError(f"execution_model 端口 kind 非法: {kind!r}", exit_code=1,
                       gate_id="execution_model_catalog")


def validate_execution_catalog(model):
    problems = []
    if model.get("schema_version") != 1:
        problems.append("execution_model.schema_version 必须为 1")
    boundaries = model.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries or len(boundaries) != len(set(boundaries)):
        problems.append("execution_model.boundaries 必须是非空无重复数组")
        boundaries = []
    input_kinds = set(model.get("input_kinds") or [])
    output_kinds = set(model.get("output_kinds") or [])
    contracts = model.get("step_contracts")
    if not isinstance(contracts, dict) or not contracts:
        problems.append("execution_model.step_contracts 必须是非空对象")
        contracts = {}
    finish_steps = model.get("finish_gate_steps", [])
    if not isinstance(finish_steps, list) or any(
            not isinstance(step, str) or step not in contracts for step in finish_steps):
        problems.append("execution_model.finish_gate_steps 必须只引用已登记步骤")
    for step, contract in contracts.items():
        prefix = f"execution_model.step_contracts.{step}"
        if not isinstance(contract, dict):
            problems.append(f"{prefix} 非对象")
            continue
        for field, allowed in (("inputs", input_kinds), ("outputs", output_kinds)):
            ports = contract.get(field)
            if not isinstance(ports, list):
                problems.append(f"{prefix}.{field} 非数组")
                continue
            ids = []
            for index, port in enumerate(ports):
                if not isinstance(port, dict):
                    problems.append(f"{prefix}.{field}[{index}] 非对象")
                    continue
                port_id = port.get("id")
                if not isinstance(port_id, str) or not port_id:
                    problems.append(f"{prefix}.{field}[{index}].id 必须是非空字符串")
                else:
                    ids.append(port_id)
                if port.get("kind") not in allowed:
                    problems.append(f"{prefix}.{field}[{index}].kind 非法: {port.get('kind')!r}")
                if not isinstance(port.get("value"), str) or not port.get("value"):
                    problems.append(f"{prefix}.{field}[{index}].value 必须是非空字符串")
                if "path_pointer" in port and (
                        port.get("kind") != "ticket_file" or not isinstance(port["path_pointer"], str)
                        or not port["path_pointer"].startswith("/")):
                    problems.append(f"{prefix}.{field}[{index}].path_pointer 必须是文件端口的 JSON pointer")
                if field == "outputs" and port.get("kind") == "metadata_pointer" \
                        and not isinstance(port.get("receipt_event"), str):
                    problems.append(
                        f"{prefix}.{field}[{index}] metadata_pointer 缺 receipt_event")
            if len(ids) != len(set(ids)):
                problems.append(f"{prefix}.{field} id 重复")
        checks = contract.get("required_checks")
        if not isinstance(checks, list) or any(item not in boundaries for item in checks):
            problems.append(f"{prefix}.required_checks 含未知边界")
        routes = contract.get("drift_routes")
        input_ids = {item.get("id") for item in contract.get("inputs", [])
                     if isinstance(item, dict)}
        if not isinstance(routes, dict) or "default" not in routes:
            problems.append(f"{prefix}.drift_routes 缺 default")
        elif any(key != "default" and key not in input_ids for key in routes):
            problems.append(f"{prefix}.drift_routes 引用了未知输入")
    classes = model.get("operation_classes")
    failures = model.get("failure_policies")
    if not isinstance(classes, dict) or not classes:
        problems.append("execution_model.operation_classes 必须是非空对象")
    if not isinstance(failures, dict) or not failures:
        problems.append("execution_model.failure_policies 必须是非空对象")
    return problems


def step_contract(model, step):
    problems = validate_execution_catalog(model)
    if problems:
        raise ControlError("execution_model 机器契约无效", exit_code=1,
                           gate_id="execution_model_catalog", violations=problems)
    contract = model["step_contracts"].get(step)
    if contract is None:
        raise ControlError(f"未知 step {step!r}，允许: {sorted(model['step_contracts'])}",
                           exit_code=2, gate_id="step_contract")
    return contract


def capture_step_inputs(out_dir, meta, model, step, *, git_provider=None, require_git_provider=False):
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    module = candidate_module() if access else None
    deadline = time.monotonic() + module.DEFAULT_LIMITS["timeout_seconds"] if access else None
    if access:
        _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
    contract = step_contract(model, step)
    facts = [resolve_port(out_dir, meta, port, **access) for port in contract["inputs"]]
    by_id = {fact["id"]: fact for fact in facts}
    missing = [port["id"] for port in contract["inputs"]
               if port.get("required", False) and not by_id[port["id"]]["exists"]]
    protected_ids = [port["id"] for port in contract["inputs"]
                     if port.get("protected", False)]
    protected = {port_id: by_id[port_id] for port_id in protected_ids}
    contract_digest = canonical_digest(contract)
    captured = {
        "contract_digest": contract_digest,
        "inputs": facts,
        "protected": protected,
        "input_digest": canonical_digest(protected),
    }, missing
    if access:
        _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        try:
            module._candidate_provider(execution_workspace(out_dir, meta), git_provider,
                                       require_git_provider, deadline=deadline)
        except (module.CandidateError, OSError, UnicodeError) as exc:
            raise ControlError(str(exc), gate_id="candidate_provider_context") from exc
    return captured


# 从 cda2936 的步骤合同升级到新增可选枚举证据端口的合同。
# 绑定新旧完整摘要，后续的端口定义、检查项或路由变化需要重新验证。
COMPATIBLE_STEP_CONTRACT_UPGRADES = {
    "plan": (
        "5e35e5eca8ad8ff36fc06846a3eaf979f24f33dccd0a9ad7646739e2d9d505e1",
        "fd05b95f25fe18cbaf57a54359fa3846d25b22b54601e30f5aacc515d8c1257e",
    ),
    "review": (
        "2e4a1e550e281626826dda608ef940edbd634f28944f86e48afc600f3c7a3368",
        "b7a5142cab10494f11eef10f3ce2bde45448cd08f60817b12d6b1dcd490d989e",
    ),
    "deepcheck": (
        "367a3c124c8954d6996d2710e81df9c14061ca539d727b58ba7bf0e89b38cf58",
        "d92a42475ca6e19db39b5eafa3a6ff0ef634ec8011b0ab08c9f3fd467a71da4c",
    ),
}


def attempt_upgrade_compatible(start_payload, contract, captured_protected=None):
    """仅已核验的合同升级可沿用旧 attempt 的受保护输入。"""
    upgrade = COMPATIBLE_STEP_CONTRACT_UPGRADES.get(start_payload.get("step"))
    if upgrade is None or (start_payload.get("contract_digest"), canonical_digest(contract)) != upgrade:
        return False
    old_inputs = start_payload.get("inputs")
    if not isinstance(old_inputs, list) or not old_inputs:
        return False
    old_ids = {fact.get("id") for fact in old_inputs if isinstance(fact, dict)}
    if not old_ids:
        return False
    current_ids = {port.get("id") for port in contract.get("inputs") or []}
    if not current_ids.issuperset(old_ids):
        return False  # 旧输入端口被移除 → 真实合同变更，不兼容
    protected = start_payload.get("protected")
    if not isinstance(protected, dict) or not protected:
        return False
    recorded = start_payload.get("input_digest")
    if not isinstance(recorded, str) or re.fullmatch(r"[0-9a-f]{64}", recorded) is None:
        return False
    if canonical_digest(protected) != recorded:
        return False  # 记录与摘要不自洽：无法区分升级与篡改
    if captured_protected is not None:
        for port_id, fact in protected.items():
            if captured_protected.get(port_id) != fact:
                return False  # 旧端口内容漂移 → 真实输入漂移
    return True


def validate_step_outputs(out_dir, meta, model, step, allow_incomplete=False,
                          *, git_provider=None, require_git_provider=False, inspection_provider_factory=None, inspection_deadline=None,
                          check_inspections=True):
    access = _code_access(step, git_provider, require_git_provider, inspection_provider_factory, inspection_deadline)
    if access:
        _code_inspection_provider(out_dir, meta, **access)
    contract = step_contract(model, step)
    facts = [resolve_port(out_dir, meta, port, **_code_candidate_access(access)) for port in contract["outputs"]]
    by_id = {fact["id"]: fact for fact in facts}
    missing = [port["id"] for port in contract["outputs"]
               if port.get("required", True) and not by_id[port["id"]]["exists"]]
    if step == "review" and by_id.get("review_manifest", {}).get("exists"):
        missing.extend(review_evidence_issues(out_dir, meta))
    if check_inspections and step in ("deepcheck", "audit") and by_id.get("inspection_coverage", {}).get("exists"):
        missing.extend(inspection_coverage_issues(out_dir, meta, step, allow_incomplete=allow_incomplete, **access))
    if check_inspections and by_id.get("inspection_worklist", {}).get("exists"):
        missing.extend(inspection_worklist_issues(out_dir, meta, step, allow_incomplete=allow_incomplete, **access))
    if access:
        _code_guard(out_dir, meta, **access)
    return facts, missing



PROTECTED_INSPECTION_STEPS = frozenset({"code"})


def _code_access(step, provider, required, factory, deadline=None):
    if not (_candidate_access_requested(provider, required) or factory is not None):
        return {}
    if step not in PROTECTED_INSPECTION_STEPS:
        raise ControlError("only code inspection is enabled", gate_id="candidate_business_propagation_pending")
    return dict(git_provider=provider, require_git_provider=required,
                inspection_provider_factory=factory,
                inspection_deadline=deadline if deadline is not None else
                    time.monotonic() + candidate_module().DEFAULT_LIMITS["timeout_seconds"])


def _code_candidate_access(access):
    return {key: access[key] for key in ("git_provider", "require_git_provider")} if access else {}


def _code_guard(out_dir, meta, *, git_provider, require_git_provider,
                inspection_provider_factory, inspection_deadline):
    if git_provider is None or not callable(inspection_provider_factory):
        raise ControlError("complete code provider pair required", gate_id="candidate_provider_context")
    _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
    try:
        candidate_module()._candidate_provider(execution_workspace(out_dir, meta), git_provider,
            require_git_provider, deadline=inspection_deadline)
    except (candidate_module().CandidateError, OSError, UnicodeError) as exc:
        raise ControlError(str(exc), gate_id="candidate_provider_context") from exc


def _code_inspection_provider(out_dir, meta, **access):
    _code_guard(out_dir, meta, **access)
    helper = inspection_module()  # Per invocation owner; None keeps the legacy loader.
    workspace = execution_workspace(out_dir, meta)
    try:
        transport = access["inspection_provider_factory"](helper, workspace)
        _code_guard(out_dir, meta, **access)
        if not isinstance(transport, helper.InspectionGitProvider) or transport.workspace != workspace:
            raise helper.InspectionError("inspection provider workspace/type mismatch")
    except ControlError:
        raise
    except Exception as exc:
        raise ControlError(str(exc), gate_id="candidate_provider_context") from exc

    class BoundProvider(helper.InspectionGitProvider):
        def __init__(self):
            self.workspace = workspace

        def query(self, request, *, limit):
            _code_guard(out_dir, meta, **access)
            try:
                # Validate the actual transport result in this same API universe.
                data, _ = helper._provider_query(workspace, transport, request, limit)
            except Exception as exc:
                raise ControlError(str(exc), gate_id="candidate_provider_context") from exc
            _code_guard(out_dir, meta, **access)
            return helper.GitQueryResult(data, 0, False)

    return helper, BoundProvider()


def _code_output(encoded, out_dir, meta, access):
    if access:
        _code_guard(out_dir, meta, **access)
    print(encoded)


def inspection_module():
    """Load the bundled stdlib helper without depending on the invocation cwd."""
    spec = importlib.util.spec_from_file_location("icode_inspection_worklist", SKILL_ROOT / "tools/inspection_worklist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspection_worklist_issues(out_dir, meta, step, attempt=None, allow_incomplete=False,
                               *, git_provider=None, require_git_provider=False, inspection_provider_factory=None, inspection_deadline=None):
    access = _code_access(step, git_provider, require_git_provider, inspection_provider_factory, inspection_deadline)
    if access:
        _code_guard(out_dir, meta, **access)
    path = Path(out_dir) / f"{step}_worklist.json"
    if path.is_symlink() or not path.is_file():
        return ["inspection_worklist:missing_or_symlink"]
    try:
        report = load_json(path, "inspection worklist")
        if not isinstance(report, dict):
            return ["inspection_worklist:invalid=report must be an object"]
        helper, provider = _code_inspection_provider(out_dir, meta, **access) if access else (inspection_module(), None)
        inspection_access = dict(git_provider=provider) if access else {}
        workspace = execution_workspace(out_dir, meta)
        events, chain_issues = verify_event_chain(out_dir, meta, **_code_candidate_access(access))
        writers = [e for e in events if e.get("event_type") == "artifact_written"
            and (e.get("payload") or {}).get("step") == step
            and (e.get("payload") or {}).get("output") == "inspection_worklist"]
        # A report cannot invent an old attempt to suppress a genuine writer's
        # version marker, including during scope-only/read-only output checks.
        current_attempt = attempt if attempt is not None else (writers[-1]["payload"].get("attempt") if writers else None)
        required_version = 1 if inspection_requires_current_checks(events, step, current_attempt) else None
        issues = helper.validate_worklist(report, workspace, code_files=meta.get("code_files") or [],
            step=step, ticket_id=meta.get("ticket_id"), attempt=current_attempt, allow_incomplete=allow_incomplete,
            required_checks_version=required_version, **inspection_access)
        if chain_issues:
            issues.append("event chain invalid")
        profile = meta.get("risk_profile")
        effective = workflow_effective_mode(meta)
        if report.get("mode") != effective:
            issues.append("effective_mode mismatch")
        if current_attempt is not None:
            receipts = [e for e in events if e.get("event_type") == "artifact_written"
                and (e.get("payload") or {}).get("attempt") == current_attempt
                and (e.get("payload") or {}).get("step") == step
                and (e.get("payload") or {}).get("output") == "inspection_worklist"
                and (e.get("payload") or {}).get("inspection_declaration_hash")]
            if chain_issues or not receipts or any(e["payload"]["inspection_declaration_hash"] != inspection_declaration_hash(report) for e in receipts):
                issues.append("declaration/receipt drift")
        findings = report.get("findings") if isinstance(report, dict) else None
        issues.extend(helper.validate_findings(findings, report, workspace, **inspection_access))
        if access:
            _code_guard(out_dir, meta, **access)
        return ["inspection_worklist:" + str(item) for item in issues]
    except (ControlError, ValueError, OSError, TypeError) as exc:
        if access and isinstance(exc, ControlError):
            raise
        return [f"inspection_worklist:invalid={exc}"]


def inspection_declaration_hash(report):
    keys = ("schema_version", "workspace", "step", "ticket_id", "attempt", "mode", "required_phases",
            "code_files", "related", "scopes", "baselines", "resolved_baselines", "max_files", "max_bytes")
    declaration = {k: report.get(k) for k in keys}
    if "checks_version" in report or "checks" in report:
        declaration.update(checks_version=report.get("checks_version"),
                           checks=inspection_module().check_declarations(report))
    return hashlib.sha256(json.dumps(declaration, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def inspection_requires_current_checks(events, step, attempt):
    """Only verified writer events decide upgrade requirements, never report self-claims."""
    return any(e.get("event_type") == "artifact_written"
        and (e.get("payload") or {}).get("output") == "inspection_worklist"
        and (e.get("payload") or {}).get("step") == step
        and (e.get("payload") or {}).get("attempt") == attempt
        and type((e.get("payload") or {}).get("inspection_checks_version")) is int
        and e["payload"]["inspection_checks_version"] == 1 for e in events)


def review_evidence_issues(out_dir, meta, events=None, start=None, allow_legacy=False):
    """Check round counts and original receipts; hashes do not prove review understanding."""
    root = Path(out_dir).resolve()
    path = root / "review_manifest.json"
    if path.is_symlink():
        return ["review_manifest:symlink"]
    if not path.is_file():
        if allow_legacy and (root / "review_round_1.json").is_file():
            try:
                detail = load_json(root / "review_round_1.json", "review round")
                if isinstance(detail, dict) and all(isinstance(detail.get(k), list) for k in
                        ("new_issues", "refuted_issues", "pending_verification")):
                    return []
            except (OSError, ValueError, ControlError):
                pass
        return ["review_manifest:missing"]
    try:
        manifest = load_json(path, "review manifest")
    except (OSError, ValueError, ControlError) as exc:
        return [f"review_manifest:invalid_json:{exc}"]
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1 \
            or manifest.get("ticket_id") != meta.get("ticket_id"):
        return ["review_manifest:identity"]
    rounds = manifest.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        return ["review_manifest:empty_rounds"]
    issues = []
    keys = ("new_issues", "refuted_issues", "pending_verification")
    for number, row in enumerate(rounds, 1):
        label = f"review_manifest:round={number}"
        if not isinstance(row, dict) or type(row.get("round")) is not int or row["round"] != number:
            issues.append(label + ":sequence")
            continue
        origin = row.get("origin_attempt")
        if not isinstance(origin, str) or not origin.strip():
            issues.append(label + ":origin_attempt")
        if number == 1 and manifest.get("review_run") != origin:
            issues.append(label + ":review_run")
        if events is not None and not any(e.get("event_type") == "step_started" and
                (e.get("payload") or {}).get("step") == "review" and
                (e.get("payload") or {}).get("attempt") == origin for e in events):
            issues.append(label + ":unknown_origin_attempt")
        if events is not None and not any(e.get("event_type") == "artifact_written" and
                (e.get("payload") or {}).get("attempt") == origin and
                (e.get("payload") or {}).get("output") == "review_manifest" and
                ((e.get("payload") or {}).get("round_digests") or {}).get(str(number)) == canonical_digest(row)
                for e in events):
            issues.append(label + ":missing_origin_round_receipt")
        if not all(type(row.get(key)) is int and row[key] >= 0 for key in keys):
            issues.append(label + ":counts")
            continue
        detail_path = row.get("detail_path")
        required = number == 1 or any(row[key] for key in keys)
        if detail_path is None:
            if required or row.get("detail_sha256") is not None:
                issues.append(label + ":missing_detail")
            continue
        if detail_path != f"review_round_{number}.json":
            issues.append(label + ":detail_path")
            continue
        detail_file = root / detail_path
        if detail_file.is_symlink() or not detail_file.is_file():
            issues.append(label + ":missing_detail")
            continue
        if row.get("detail_sha256") != file_sha256(detail_file):
            issues.append(label + ":detail_hash")
            continue
        try:
            detail = load_json(detail_file, "review round")
        except (OSError, ValueError, ControlError):
            issues.append(label + ":invalid_detail_json")
            continue
        if not isinstance(detail, dict) or any(not isinstance(detail.get(key), list) or
                len(detail[key]) != row[key] for key in keys):
            issues.append(label + ":detail_counts")
        if isinstance(detail, dict) and "has_new_issues" in detail and detail["has_new_issues"] is not bool(row["new_issues"]):
            issues.append(label + ":has_new_issues")
        if events is not None:
            origins = [e for e in events if e.get("event_type") == "step_started" and
                       (e.get("payload") or {}).get("step") == "review" and
                       (e.get("payload") or {}).get("attempt") == origin]
            receipts = [e for e in events if e.get("event_type") == "artifact_written" and
                        (e.get("payload") or {}).get("attempt") == origin and
                        (e.get("payload") or {}).get("path") == str(detail_file) and
                        (e.get("payload") or {}).get("sha256") == row.get("detail_sha256")]
            if len(origins) != 1 or not any(events.index(e) > events.index(origins[0]) for e in receipts):
                issues.append(label + ":missing_origin_receipt")
    if events is not None:
        # A new state-machine entry starts a new review run. A blocked/restarted
        # step within that entry keeps genuine earlier round receipts.
        entries = [i for i, e in enumerate(events) if e.get("event_type") == "state_changed" and
                   (e.get("payload") or {}).get("to") == "review_in_progress"]
        first = [i for i, e in enumerate(events) if e.get("event_type") == "step_started" and
                 (e.get("payload") or {}).get("attempt") == manifest.get("review_run")]
        if len(first) != 1 or (entries and first[0] < entries[-1]):
            issues.append("review_manifest:stale_review_run")
        elif any(not any(i >= first[0] and e.get("event_type") == "step_started" and
                 (e.get("payload") or {}).get("step") == "review" and
                 (e.get("payload") or {}).get("attempt") == row.get("origin_attempt")
                 for i, e in enumerate(events)) for row in rounds if isinstance(row, dict)):
            issues.append("review_manifest:origin_outside_run")
        if start is not None:
            current = (start.get("payload") or {}).get("attempt")
            if not any(e.get("event_type") == "artifact_written" and
                       (e.get("payload") or {}).get("attempt") == current and
                       (e.get("payload") or {}).get("path") == str(path) and
                       (e.get("payload") or {}).get("sha256") == file_sha256(path)
                       for e in events[events.index(start) + 1:]):
                issues.append("review_manifest:missing_current_receipt")
    return issues


def dedup_attempt_has_mcp_evidence(out_dir, step, attempt):
    """只有本 attempt 的成功 trace 才能支持本轮 MCP 去重声明。"""
    if step != "deepcheck" or not attempt:
        return False
    path = Path(out_dir) / ".mcp_gate_trace.jsonl"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        relevant = [row for row in rows if isinstance(row, dict) and row.get("gate_id") == "deepcheck.dedup"]
        if not relevant:
            return False
        row = relevant[-1]
        if row.get("attempt") != attempt or row.get("ticket_id") != load_metadata(out_dir).get("ticket_id"):
            return False
        if row.get("eligible") is not True or row.get("decision") not in {"called", "cache_hit"} or row.get("result") != "success":
            return False
        spec = importlib.util.spec_from_file_location("icode_dedup_gate", SKILL_ROOT / "tools/lint_mcp_coverage.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        catalog = load_json(SKILL_ROOT / "mcp/cheap-research/gates.json", "cheap gate catalog")
        gate = next(item for item in catalog["gates"] if item.get("id") == "deepcheck.dedup")
        return not helper.validate_row(row, gate, 1)
    except (OSError, ValueError, TypeError, KeyError, StopIteration, ImportError, ControlError):
        return False


def dedup_code_files_covered(workspace, code_files, sources, helper):
    """Documents stay in review coverage, but do not enter the function catalog."""
    for name in code_files:
        if not isinstance(name, str) or not name.strip():
            return False
        path = port_path(workspace, name)
        if not path.is_relative_to(workspace):
            return False
        suffix = path.suffix.lower()
        if suffix in helper._SOURCE_SUFFIXES:
            if path.relative_to(workspace).as_posix() not in sources:
                return False
        elif suffix not in helper._NON_SOURCE_SUFFIXES and path.name not in helper._NON_SOURCE_NAMES:
            return False
    return True


def audit_reuses_deepcheck_dedup(out_dir, meta, report, events, audit_start, *,
                                  git_provider=None, require_git_provider=False,
                                  inspection_provider_factory=None, inspection_deadline=None):
    """Reuse only a receipted full deepcheck whose original source still matches."""
    # This path's enumerator and historical checks have no protected-provider
    # implementation. Refuse before *any* native Git or candidate access.
    if (git_provider is not None or require_git_provider is not False
            or inspection_provider_factory is not None or inspection_deadline is not None):
        return False
    try:
        checked_events, problems = verify_event_chain(out_dir, meta)
        if problems or checked_events != events:
            return False
        prior = events[:events.index(audit_start)]
        starts = [e for e in prior if e.get("event_type") == "step_started"
                  and (e.get("payload") or {}).get("step") == "deepcheck"]
        if not starts:
            return False
        start = starts[-1]
        payload = start["payload"]
        attempt = payload.get("attempt")
        if payload.get("dedup_evidence_version") != 1 or payload.get("effective_mode") != "full":
            return False
        finishes = [e for e in prior if e.get("event_type") == "step_finished"
                    and (e.get("payload") or {}).get("step") == "deepcheck"
                    and (e.get("payload") or {}).get("attempt") == attempt]
        if len(finishes) != 1 or finishes[0]["payload"].get("outcome") != "success":
            return False
        finish = finishes[0]
        path = Path(out_dir) / "deepcheck_coverage.json"
        if path.is_symlink() or not path.is_file():
            return False
        digest = file_sha256(path)
        middle = prior[prior.index(start) + 1:prior.index(finish)]
        if not any(e.get("event_type") == "artifact_written" and
                   (e.get("payload") or {}).get("step") == "deepcheck" and
                   (e.get("payload") or {}).get("attempt") == attempt and
                   (e.get("payload") or {}).get("output") == "inspection_coverage" and
                   (e.get("payload") or {}).get("path") == str(path.resolve()) and
                   (e.get("payload") or {}).get("sha256") == digest for e in middle):
            return False
        deep = load_json(path, "deepcheck coverage")
        if (not isinstance(deep, dict) or deep.get("attempt") != attempt
                or deep.get("review_scope") != report.get("review_scope")
                or deep.get("dedup_status") != report.get("dedup_status")
                or inspection_coverage_issues(out_dir, meta, "deepcheck", attempt)):
            return False
        spec = importlib.util.spec_from_file_location("icode_audit_dedup", SKILL_ROOT / "tools/manual_dedup_evidence.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        workspace = execution_workspace(out_dir, meta)
        sources = helper.collect_sources(workspace, deep["review_scope"])
        # Ignored source files cannot silently disappear from the denominator.
        if not dedup_code_files_covered(workspace, meta.get("code_files", []), sources, helper):
            return False
        if deep.get("dedup_status") == "complete_within_scope" and "manual_dedup" not in deep:
            # MCP trace alone does not bind every source in the declared scope.
            receipt = (finish.get("payload") or {}).get("candidate_receipt") or {}
            old = receipt.get("candidate")
            if (receipt.get("attempt") != attempt or receipt.get("step") != "deepcheck"
                    or receipt.get("event_id") != finish.get("event_id")
                    or not candidate_module().snapshot_identity_ok(old)):
                return False
            before = {row["path"]: row.get("sha256") for row in old["manifest"]}
            current = candidate_snapshot(out_dir, meta, events=events)
            now = {row["path"]: row.get("sha256") for row in current["manifest"]}
            if (not candidate_module().review_source_equivalence(old, current)["equivalent"]
                    or any(before.get(name) != digest or now.get(name) != digest for name, digest in sources.items())):
                return False
        return True
    except (ControlError, OSError, ValueError, TypeError, KeyError, IndexError, ImportError):
        return False


def inspection_coverage_issues(out_dir, meta, step, attempt=None, allow_incomplete=False,
                               *, git_provider=None, require_git_provider=False,
                               inspection_provider_factory=None, inspection_deadline=None):
    """Validate declared reads against current source; never equate a hash with a Read."""
    path = Path(out_dir) / f"{step}_coverage.json"
    if path.is_symlink() or not path.is_file():
        return [f"{step}_coverage:missing_or_symlink"]
    try:
        report = load_json(path, "inspection coverage")
    except (OSError, ControlError, ValueError):
        return [f"{step}_coverage:invalid_json"]
    issues = []
    if not isinstance(report, dict):
        return [f"{step}_coverage:not_object"]
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1 or report.get("ticket_id") != meta.get("ticket_id"):
        issues.append(f"{step}_coverage:identity")
    if attempt is not None and report.get("attempt") != attempt:
        issues.append(f"{step}_coverage:attempt")
    scope = report.get("review_scope")
    if not isinstance(scope, list) or not scope or any(not isinstance(p, str) or not p.strip() for p in scope):
        issues.append(f"{step}_coverage:review_scope")
    status = report.get("coverage_status")
    incomplete = status in ("partial", "degraded")
    if status not in ("complete_within_scope", "partial", "degraded"):
        issues.append(f"{step}_coverage:coverage_status")
    if any(not isinstance(report.get(key), list) or any(not isinstance(p, str) or not p.strip() for p in report[key])
           for key in ("unobserved", "dedup_unobserved")):
        issues.append(f"{step}_coverage:unobserved_type")
    if incomplete and (not isinstance(report.get("debt_reason"), str) or not report["debt_reason"].strip()):
        issues.append(f"{step}_coverage:debt_reason")
    if incomplete and not (report.get("unobserved") or report.get("dedup_unobserved")):
        issues.append(f"{step}_coverage:missing_debt_items")
    if not (allow_incomplete and incomplete) and (status != "complete_within_scope" or report.get("unobserved") != []):
        issues.append(f"{step}_coverage:incomplete")
    dedup = report.get("dedup_status")
    if dedup not in ("complete_within_scope", "not_eligible", "partial", "degraded"):
        issues.append(f"{step}_coverage:dedup_status")
    if not (allow_incomplete and incomplete) and (dedup not in ("complete_within_scope", "not_eligible") or report.get("dedup_unobserved") != []):
        issues.append(f"{step}_coverage:dedup_incomplete")
    if dedup == "not_eligible" and (not isinstance(report.get("dedup_reason"), str) or not report["dedup_reason"].strip()):
        issues.append(f"{step}_coverage:dedup_reason")
    profile = meta.get("risk_profile")
    effective_mode = workflow_effective_mode(meta)
    phases = ["audit"] if step == "audit" else ["reverse"] if effective_mode == "fast" else ["reverse", "fixed", "free"]
    reads = report.get("read_phases")
    workspace = execution_workspace(out_dir, meta)
    current_attempt = attempt or report.get("attempt")
    events, _ = read_events(out_dir)
    starts = [event for event in events if event.get("event_type") == "step_started"
              and (event.get("payload") or {}).get("attempt") == current_attempt
              and (event.get("payload") or {}).get("step") == step]
    new_dedup_contract = len(starts) == 1 and (starts[0].get("payload") or {}).get("dedup_evidence_version") == 1
    if dedup == "complete_within_scope" and "manual_dedup" not in report:
        if new_dedup_contract:
            proven = (audit_reuses_deepcheck_dedup(out_dir, meta, report, events, starts[0],
                        git_provider=git_provider, require_git_provider=require_git_provider,
                        inspection_provider_factory=inspection_provider_factory, inspection_deadline=inspection_deadline)
                      if step == "audit" else dedup_attempt_has_mcp_evidence(out_dir, step, current_attempt))
            if not proven:
                issues.append(f"{step}_coverage:manual_dedup_required")
    if (new_dedup_contract and step == "audit" and effective_mode != "fast"
            and dedup == "not_eligible" and "manual_dedup" not in report
            and not audit_reuses_deepcheck_dedup(out_dir, meta, report, events, starts[0],
                    git_provider=git_provider, require_git_provider=require_git_provider,
                    inspection_provider_factory=inspection_provider_factory, inspection_deadline=inspection_deadline)):
        issues.append(f"{step}_coverage:dedup_not_eligible_unverified")
    if ("manual_dedup" in report and dedup in {"complete_within_scope", "not_eligible"}) or (
            new_dedup_contract and step == "deepcheck" and effective_mode != "fast" and dedup == "not_eligible"):
        try:
            spec = importlib.util.spec_from_file_location("icode_manual_dedup", SKILL_ROOT / "tools/manual_dedup_evidence.py")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            sources = helper.collect_sources(workspace, scope,
                require_git_provider=bool(require_git_provider or git_provider is not None or inspection_provider_factory is not None))
            if new_dedup_contract and not dedup_code_files_covered(
                    workspace, meta.get("code_files") or [], sources, helper):
                raise ValueError("code file absent from dedup source enumeration")
            manual_issues = None
            if "manual_dedup" in report:
                manual_issues = helper.validate_report(report["manual_dedup"], workspace, meta.get("ticket_id"),
                                                       current_attempt, scope, sources)
                issues.extend(f"{step}_coverage:manual_dedup:{item}" for item in manual_issues)
                if new_dedup_contract and step == "audit" and dedup == "not_eligible" and not manual_issues:
                    threshold = load_json(SKILL_ROOT / "mcp/cheap-research/gates.json", "cheap gate catalog")["constants"]["dedup_min_functions"]
                    count = sum(len(source["functions"]) for source in report["manual_dedup"]["sources"])
                    if count >= threshold:
                        issues.append(f"{step}_coverage:dedup_not_eligible_threshold={count}/{threshold}")
            if new_dedup_contract and step == "deepcheck" and effective_mode != "fast" and dedup == "not_eligible":
                import ast
                count = 0
                if any(Path(relative).suffix.lower() not in {".py", ".pyi"} for relative in sources):
                    if manual_issues is None or manual_issues:
                        raise ValueError("non-Python not_eligible requires verified manual catalog")
                    count = sum(len(source["functions"]) for source in report["manual_dedup"]["sources"])
                else:
                    for relative in sources:
                        data = helper._read_source(workspace / relative, helper._DEFAULT_SOURCE_MAX_BYTES)
                        if hashlib.sha256(data).hexdigest() != sources[relative]:
                            raise ValueError(f"source changed after enumeration: {relative}")
                        tree = ast.parse(data, filename=relative)
                        count += sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
                catalog = load_json(SKILL_ROOT / "mcp/cheap-research/gates.json", "cheap gate catalog")
                threshold = catalog["constants"]["dedup_min_functions"]
                if count >= threshold:
                    issues.append(f"{step}_coverage:dedup_not_eligible_threshold={count}/{threshold}")
                trace_path = Path(out_dir) / ".mcp_gate_trace.jsonl"
                if trace_path.is_symlink() or not trace_path.is_file():
                    raise ValueError("not_eligible requires current attempt trace")
                rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                rows = [row for row in rows if isinstance(row, dict) and row.get("gate_id") == "deepcheck.dedup"]
                trace = rows[-1] if rows else {}
                evidence = trace.get("evidence") or {}
                if (trace.get("attempt") != current_attempt or trace.get("ticket_id") != meta.get("ticket_id")
                        or trace.get("decision") != "skipped_not_eligible" or trace.get("eligible") is not False
                        or evidence.get("function_count") != count or evidence.get("threshold") != threshold):
                    issues.append(f"{step}_coverage:dedup_not_eligible_trace")
        except (OSError, ValueError, ImportError, KeyError, TypeError, SyntaxError) as exc:
            issues.append(f"{step}_coverage:manual_dedup:{exc}")
    code_files = meta.get("code_files") or []
    if not isinstance(code_files, list) or not code_files or any(not isinstance(p, str) or not p.strip() for p in code_files):
        issues.append(f"{step}_coverage:empty_code_files")
        return issues
    if allow_incomplete and incomplete:
        if not isinstance(reads, dict):
            issues.append(f"{step}_coverage:read_phases_type")
        return issues
    for phase in phases:
        files = reads.get(phase) if isinstance(reads, dict) else None
        if not isinstance(files, dict):
            issues.append(f"{step}_coverage:missing_phase={phase}")
            continue
        for relative in code_files:
            source = port_path(workspace, relative)
            if isinstance(scope, list) and all(isinstance(p, str) and p.strip() for p in scope) and not any(
                    source.is_relative_to(port_path(workspace, p)) for p in scope):
                issues.append(f"{step}_coverage:code_outside_scope={relative}")
            if not source.is_file() or files.get(relative) != file_sha256(source):
                issues.append(f"{step}_coverage:unconfirmed_read={phase}:{relative}")
    return issues


def _authenticated_inspection_review(out_dir, meta, model, events, start):
    """Read an immutable finished review version, without rebuilding old source.

    A finish alone is insufficient: its real boundary checks and ticket output
    bytes must still agree with the native writer receipts and final facts.
    """
    payload = start.get("payload") or {}
    step, attempt = payload.get("step"), payload.get("attempt")
    contract = step_contract(model, step)
    finish = step_attempt_finished(events, attempt)
    digest_matches = payload.get("contract_digest") == canonical_digest(contract)
    if (not digest_matches and not attempt_upgrade_compatible(payload, contract)) or not finish \
            or finish["payload"].get("outcome") not in ("success", "degraded"):
        return None
    first, last = events.index(start), events.index(finish)
    between = events[first + 1:last]
    checks = {}
    for event in between:
        fact = event.get("payload") or {}
        if event.get("event_type") == "gate_checked" and fact.get("attempt") == attempt:
            checks[fact.get("boundary")] = fact.get("result")
    if checks != finish["payload"].get("checks") or any(
            checks.get(boundary) != "pass" for boundary in contract["required_checks"]):
        return None
    for port in contract["outputs"]:
        if port["kind"] != "ticket_file" or not port.get("required", True):
            continue
        path = port_path(Path(out_dir), port_value(out_dir, meta, port))
        if path.is_symlink() or not path.is_file():
            return None
        digest = file_sha256(path)
        writes = [event["payload"] for event in between if event.get("event_type") == "artifact_written"
                  and event["payload"].get("attempt") == attempt and event["payload"].get("output") == port["id"]]
        final_facts = [fact for fact in finish["payload"].get("outputs", []) if fact.get("id") == port["id"]]
        if not writes or writes[-1].get("path") != str(path) or writes[-1].get("sha256") != digest \
                or len(final_facts) != 1 or final_facts[0].get("sha256") != digest:
            return None
    report = load_json(Path(out_dir) / f"{step}_worklist.json", "inspection worklist")
    if not isinstance(report, dict) or report.get("step") != step or report.get("attempt") != attempt \
            or report.get("ticket_id") != meta.get("ticket_id"):
        return None
    declaration = inspection_declaration_hash(report)
    writes = [event["payload"] for event in between if event.get("event_type") == "artifact_written"
              and event["payload"].get("attempt") == attempt
              and event["payload"].get("output") == "inspection_worklist"]
    # Generic artifact receipts may repeat the same output without claiming
    # inspection proof; they cannot substitute for a native declaration.
    declared = [fact for fact in writes if "inspection_declaration_hash" in fact]
    if not declared or any(fact["inspection_declaration_hash"] != declaration for fact in declared):
        return None
    receipt = finish["payload"].get("candidate_receipt") or {}
    candidate = receipt.get("candidate") if finish["payload"].get("outcome") == "success" else payload.get("candidate_input")
    if not candidate_module().snapshot_identity_ok(candidate):
        return None
    files = inspection_module()._flatten(report)
    manifest = {fact["path"]: fact for fact in candidate["manifest"]}
    if report.get("workspace") != candidate["identity"]["checkout_realpath"] \
            or report.get("code_files") != candidate["code_files"] or any(
                path not in manifest or fact.get("sha256") != manifest[path].get("sha256")
                for path, fact in files.items()):
        return None
    return {"start": start, "finish": finish, "report": report, "candidate": candidate, "files": files}


def _inspection_history_extends(recorded, current):
    """Prove ordinary native history with the candidate backend's linear bound.

    Protected check-outputs never enters historical supersession. Its typed
    provider contract cannot be replaced by this native-only Git query.
    """
    if recorded["head"] == current["head"]:
        return recorded["head_tree"] == current["head_tree"]
    module = candidate_module()
    root = Path(current["identity"]["checkout_realpath"])
    # Grafts alter rev-list parents; replacement objects are disabled below.
    # Neither can establish the original immutable commit relationship.
    if (Path(current["identity"]["common_git_dir"]) / "info/grafts").exists():
        return False
    old_head, new_head = recorded["head"], current["head"]
    ancestors = module._git(root, ["--no-replace-objects", "rev-list", "--parents", "--ancestry-path",
                                  "--max-count=65", f"{old_head}..{new_head}"],
                            module.DEFAULT_LIMITS["max_git_bytes"]).decode("ascii").splitlines()
    # Keep the existing candidate LINEAR semantics: at most 64 commits, with
    # one parent at each edge. Merge/rewrite/rollback cannot certify history.
    if not ancestors or len(ancestors) > 64:
        return False
    parent = old_head
    for line in reversed(ancestors):
        parts = line.split()
        if len(parts) != 2 or parts[1] != parent:
            return False
        parent = parts[0]
    return parent == new_head


def inspection_superseded_by_current_review(out_dir, meta, model, events, step, start):
    """Accept historical bytes only behind a complete later current review.

    This is a final-check interpretation, never a writer or a replacement for
    the latest review's strict source/Read/coverage validation.
    """
    order = ("code", "deepcheck", "audit")
    if step not in order[:-1] or not start:
        return None
    starts = [event for event in events if event.get("event_type") == "step_started"
              and (event.get("payload") or {}).get("step") in order]
    latest = starts[-1] if starts else None
    if not latest or order.index(latest["payload"]["step"]) <= order.index(step):
        return None
    try:
        old = _authenticated_inspection_review(out_dir, meta, model, events, start)
        new = _authenticated_inspection_review(out_dir, meta, model, events, latest)
        if not old or not new or events.index(old["finish"]) >= events.index(latest) \
                or new["finish"]["payload"].get("outcome") != "success":
            return None
        current = candidate_snapshot(out_dir, meta, events=events)
        # A later source revision/linear commit within this checkout/base is
        # allowed; roots, exclusions and the implementation contract stay bound.
        stable = ("identity", "base", "scope_contract",
                  "excluded_side_effects", "control_artifact_exclusions")
        if any(old["candidate"].get(key) != current.get(key) for key in stable) \
                or not set(old["candidate"]["code_files"]).issubset(current["code_files"]) \
                or new["candidate"]["candidate_id"] != current["candidate_id"] \
                or old["report"].get("resolved_baselines") != new["report"].get("resolved_baselines") \
                or not set(old["files"]).issubset(new["files"]):
            return None
        if not _inspection_history_extends(old["candidate"], current):
            return None
        later_step, attempt = latest["payload"]["step"], latest["payload"]["attempt"]
        if inspection_worklist_issues(out_dir, meta, later_step, attempt) \
                or inspection_coverage_issues(out_dir, meta, later_step, attempt):
            return None
        return {"step": later_step, "attempt": attempt, "event_id": new["finish"]["event_id"]}
    except (ControlError, ValueError, OSError, KeyError, TypeError, inspection_module().InspectionError):
        # Unknown or incomplete proof leaves the original current-source
        # rejection in place. No error-string whitelist can grant acceptance.
        return None


def cmd_check_outputs(args, *, git_provider=None, require_git_provider=False, inspection_provider_factory=None):
    """Single read-only final product check; historical receipts are not invented."""
    out_dir = Path(args.dir).resolve()
    access = _code_access(args.step, git_provider, require_git_provider, inspection_provider_factory)
    candidate_access = _code_candidate_access(access)
    meta = load_metadata(out_dir)
    require_vnext(meta, out_dir)
    require_valid_metadata(meta)
    if access:
        _code_inspection_provider(out_dir, meta, **access)
    events, problems = verify_event_chain(out_dir, meta, **candidate_access)
    model = load_execution_model(load_metadata(out_dir))
    steps = workflow_contract(meta)["step_order"] if args.step in {"audit", "lite_review"} else [args.step]
    warnings = []
    for step in steps:
        contract = step_contract(model, step)
        starts = [e for e in events if e.get("event_type") == "step_started" and
                  (e.get("payload") or {}).get("step") == step]
        policy = load_state_machine(load_metadata(out_dir))["gate_policy"]
        completion = {policy["step_by_target"][target]: number for target, number in
                      policy["completed_step_by_target"].items() if target in policy["step_by_target"]}
        old_contract = bool(starts) and (starts[-1].get("payload") or {}).get("contract_digest") != canonical_digest(contract)
        upgrade_only_drift = False
        if old_contract and not access:
            try:
                captured, _capture_missing = capture_step_inputs(out_dir, meta, model, step)
                upgrade_only_drift = attempt_upgrade_compatible(
                    (starts[-1].get("payload") or {}), contract,
                    captured_protected=captured.get("protected"))
            except ControlError:
                upgrade_only_drift = False
        latest_start = starts[-1] if starts else None
        attempt = (latest_start.get("payload") or {}).get("attempt") if latest_start else None
        finished = step_attempt_finished(events, attempt) if attempt is not None else None
        historical = bool(old_contract and finished and (finished.get("payload") or {}).get("outcome") in ("success", "degraded")) or (
                      not starts and completion.get(step) in (meta.get("completed_steps") or []))
        if old_contract and not historical and not upgrade_only_drift:
            problems.append(f"{step}:contract_drift_reenter")
        degraded = bool(finished and (finished.get("payload") or {}).get("outcome") == "degraded")
        superseded = inspection_superseded_by_current_review(out_dir, meta, model, events, step, latest_start) \
            if not problems and not access and not historical else None
        # Check each inspection once, with its authenticated attempt below.
        facts, missing = validate_step_outputs(out_dir, meta, model, step, allow_incomplete=degraded,
                                               check_inspections=False, **access)
        # New evidence ports apply to new attempts, not completed old contracts.
        if historical:
            new_ports = {"review_manifest", "inspection_coverage", "inspection_worklist"}
            missing = [item for item in missing if item not in new_ports and not item.startswith("inspection_worklist:")]
            warnings.append(f"{step}:historical_evidence_untracked")
        problems.extend(f"{step}:{item}" for item in missing)
        if step == "review":
            problems.extend(review_evidence_issues(out_dir, meta, None if historical else events,
                                                   allow_legacy=historical))
        if step in ("code", "deepcheck", "audit") and not historical:
            if not superseded:
                problems.extend(inspection_worklist_issues(out_dir, meta, step, attempt, allow_incomplete=degraded, **access))
            else:
                warnings.append(f"{step}:historical_inspection_superseded_by={superseded['step']}:{superseded['attempt']}")
            if latest_start is None:
                problems.append(f"{step}:inspection_worklist:missing_step_start")
            else:
                receipt_contract = dict(contract, outputs=[p for p in contract["outputs"]
                                                           if p["id"] == "inspection_worklist"])
                problems.extend(f"{step}:receipt={item}" for item in missing_output_receipts(
                    out_dir, meta, receipt_contract, events, latest_start))
            if degraded:
                warnings.append(f"{step}:inspection_debt")
        if step in ("deepcheck", "audit"):
            coverage_exists = any(f["id"] == "inspection_coverage" and f["exists"] for f in facts)
            if not historical and coverage_exists:
                if not superseded:
                    problems.extend(inspection_coverage_issues(out_dir, meta, step, attempt, allow_incomplete=degraded, **access))
                if latest_start is None:
                    problems.append(f"{step}:inspection_coverage:missing_step_start")
                else:
                    # Final checking uses the latest real attempt and the exact artifact hash.
                    receipt_contract = dict(contract, outputs=[p for p in contract["outputs"]
                                                               if p["id"] == "inspection_coverage"])
                    problems.extend(f"{step}:receipt={item}" for item in missing_output_receipts(
                        out_dir, meta, receipt_contract, events, latest_start))
            if degraded:
                warnings.append(f"{step}:verification_debt")
            _, missing_inputs = capture_step_inputs(out_dir, meta, model, step)
            problems.extend(f"{step}:input={item}" for item in missing_inputs)
    result = dict(ok=not problems, step=args.step, read_only=True, violations=problems, warnings=warnings)
    _code_output(json.dumps(result, ensure_ascii=False, indent=2), out_dir, meta, access)
    return 0 if result["ok"] else 1


def output_port_targets(out_dir, meta, port):
    workspace = execution_workspace(out_dir, meta)
    kind = port["kind"]
    value = port_value(out_dir, meta, port)
    if kind == "ticket_file":
        return [port_path(Path(out_dir), value)]
    if kind == "ticket_glob":
        return sorted(path.resolve() for path in Path(out_dir).glob(value) if path.is_file())
    if kind == "metadata_files":
        exists, raw_paths = json_pointer_get(meta, value)
        base = workspace if port.get("base", "ticket") == "workspace" else Path(out_dir)
        if not exists or not isinstance(raw_paths, list):
            return []
        return [port_path(base, raw) for raw in raw_paths
                if isinstance(raw, str) and raw.strip()]
    if kind == "external_artifact":
        rendered = str(value).replace("<workspace>", str(workspace))
        return sorted(Path(path).resolve() for path in globlib.glob(
            str(Path(rendered).expanduser())) if Path(path).is_file())
    return []


def missing_output_receipts(out_dir, meta, contract, events, start):
    start_index = next(index for index, event in enumerate(events)
                       if event.get("event_id") == start.get("event_id"))
    later = events[start_index + 1:]
    attempt = (start.get("payload") or {}).get("attempt")
    artifacts = [event.get("payload") or {} for event in later
                 if event.get("event_type") == "artifact_written"
                 and (event.get("payload") or {}).get("attempt") == attempt]
    missing = []
    for port in contract["outputs"]:
        if not port.get("required", True):
            continue
        if port["kind"] == "metadata_pointer":
            receipt_event = port.get("receipt_event")
            if not any(event.get("event_type") == receipt_event for event in later):
                missing.append(f"{port['id']}:event={receipt_event}")
            continue
        targets = output_port_targets(out_dir, meta, port)
        for target in targets:
            expected_hash = file_sha256(target) if target.is_file() else None
            if not any(item.get("output") == port["id"]
                       and item.get("path") == str(target.resolve())
                       and item.get("sha256") == expected_hash
                       for item in artifacts):
                missing.append(f"{port['id']}:{target}")
    return missing


def attempt_events(events, attempt):
    return [event for event in events
            if (event.get("payload") or {}).get("attempt") == attempt]


def find_idempotent_event(events, request_id, event_type, payload, actor=None,
                          payload_keys=None):
    """返回精确重放事件；同 request_id 被其他操作使用时硬拒绝。"""
    if not request_id:
        return None
    matches = [event for event in events if event.get("request_id") == request_id]
    if not matches:
        return None
    if len(matches) == 1:
        event = matches[0]
        same_actor = actor is None or event.get("actor") == actor
        existing_payload = event.get("payload") or {}
        comparable_existing = dict(existing_payload)
        if "metadata_hash_after" not in payload:
            comparable_existing.pop("metadata_hash_after", None)
        if event_type in {"step_started", "operation_started"}:
            for key in ("stage_generation", "stage_id"):
                if key not in payload:
                    comparable_existing.pop(key, None)
        same_payload = comparable_existing == payload
        if payload_keys is not None:
            same_payload = all(existing_payload.get(key) == payload.get(key)
                               for key in payload_keys)
        if (event.get("event_type") == event_type and same_payload and same_actor):
            return event
    raise ControlError(
        f"request_id={request_id!r} 已被不同操作使用，拒绝模糊重放",
        exit_code=1, gate_id="idempotency_conflict", request_id=request_id,
        existing=[{"event_id": event.get("event_id"),
                   "event_type": event.get("event_type"),
                   "payload": event.get("payload")} for event in matches],
        attempted={"event_type": event_type, "payload": payload})


def recover_pending_transaction(out_dir):
    """调用方持锁。恢复上次 metadata+event 双写中断：未落事件则回滚，已落事件则完成。"""
    out_dir = Path(out_dir)
    txn_path = out_dir / TXN_NAME
    if not txn_path.is_file():
        return None
    # Parsing a legacy line is not proof that the event was never appended.
    # Authenticate the entire committed ledger before either recovery branch.
    events = _committed_ledger(out_dir).events
    txn = load_json(txn_path, "控制面事务日志")
    before = txn.get("before_metadata")
    after = txn.get("after_metadata")
    event = txn.get("event")
    if (before is not None and not isinstance(before, dict)) or not isinstance(after, dict) or not isinstance(event, dict):
        raise ControlError("控制面事务日志结构损坏，拒绝自动恢复", exit_code=1,
                           gate_id="transaction_recovery", transaction=str(txn_path))
    if (type(txn.get("schema_version")) is not int or txn["schema_version"] != 1
            or event.get("ticket_id") != after.get("ticket_id")
            or (before is not None and before.get("ticket_id") != after.get("ticket_id"))
                or not isinstance(event.get("payload"), dict)
                or event["payload"].get("metadata_hash_after") != metadata_hash(after)):
        raise ControlError("控制面事务日志身份或 after hash 损坏", gate_id="transaction_recovery")
    prior = events
    for index, committed in enumerate(events):
        if committed.get("event_hash") == event.get("event_hash"):
            prior = events[:index]
            break
    prior_hashes = [(item.get("payload") or {}).get("metadata_hash_after") for item in prior]
    prior_hashes = [value for value in prior_hashes if value is not None]
    if prior_hashes and metadata_hash(before) != prior_hashes[-1]:
        raise ControlError("控制面事务 before 与已提交 metadata projection 不符", gate_id="transaction_recovery")
    try:
        _logical_ledger_validator(event, prior)
    except (ValueError, TypeError) as exc:
        raise ControlError("控制面事务事件损坏: " + str(exc), gate_id="transaction_recovery") from exc
    meta_path = out_dir / METADATA_NAME
    current = load_json(meta_path, "工单 metadata") if meta_path.is_file() else None
    event_present = any(e.get("event_hash") == event.get("event_hash") for e in events)
    current_hash = metadata_hash(current)
    if event_present:
        if current is not None and current_hash not in {metadata_hash(before), metadata_hash(after)}:
            raise ControlError("事务事件已落盘但 metadata 已被并发改写，拒绝猜测恢复", exit_code=1,
                               gate_id="transaction_recovery")
        _finalize_committed_metadata(out_dir, after)
        txn_path.unlink()
        return "completed"
    if current_hash == metadata_hash(after):
        if before is None:
            meta_path.unlink(missing_ok=True)
        else:
            write_metadata(out_dir, before)
    elif current_hash != metadata_hash(before):
        raise ControlError("事务事件未落盘且 metadata 已被并发改写，拒绝猜测恢复", exit_code=1,
                           gate_id="transaction_recovery")
    txn_path.unlink()
    return "rolled_back"


def _finalize_committed_metadata(out_dir, after):
    """An appended event always completes after, including physical compaction."""
    enabled = _storage_enabled(out_dir, new_metadata=after)
    ledger = _committed_ledger(out_dir) if enabled else None
    physical = _encode_control_document(after, "metadata", ledger.registry if ledger else None, enabled)
    path = Path(out_dir) / METADATA_NAME
    current = _load_raw_json(path, "compact metadata") if path.is_file() else None
    if current != physical:
        atomic_write_json(path, physical)


GATE_CONTRACT_CATALOG = SKILL_ROOT / "mcp/cheap-research/gates.json"
# 出生派生字段：由工具升级引入的出生字段。旧版本工具创建的工单缺这些字段，
# 幂等重试比较时按当前出生值补齐（见 comparable_birth_metadata）。
BIRTH_DERIVED_FIELDS = frozenset({"mcp_gate_contract_version"})


def comparable_birth_metadata(current, expected_meta):
    """幂等重试比较视图：对缺失的出生派生字段按当前出生值补齐。

    request_id 幂等事件与事件链完整性由调用方先行校验；旧版本工具创建的
    metadata 缺少升级后新增的出生字段（如 mcp_gate_contract_version），补齐后
    比较使升级后的幂等重试返回 already_applied 而非 ticket_create_exists。
    其余字段（含工单后续进展）仍要求精确一致。
    """
    comparable = dict(current)
    for field in sorted(BIRTH_DERIVED_FIELDS):
        if field not in comparable and field in expected_meta:
            comparable[field] = expected_meta[field]
    return comparable


def gate_contract_open_attempt_start(events):
    """升级采纳时未终结 attempt 的最早 start 时刻（aware datetime）；无则 None。

    事件时间取 `timestamp`（now_iso 本地 naive），按本地时区解析后参与比较，
    与采纳时刻（UTC aware）可比。
    """
    started = {}
    for event in events:
        payload = event.get("payload") or {}
        if event.get("event_type") == "step_started":
            attempt = payload.get("attempt")
            if attempt is not None and attempt not in started:
                started[attempt] = event.get("timestamp") or payload.get("at") or event.get("at")
        elif event.get("event_type") == "step_finished":
            started.pop(payload.get("attempt"), None)
    times = [parse_control_timestamp(t) for t in started.values()]
    times = [t for t in times if t is not None]
    return min(times) if times else None


def parse_control_timestamp(value):
    """ISO-8601 → aware datetime；naive 按本地时区解释（now_iso 约定），无法解析返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def adopt_gate_contract(meta, events=None):
    """存量工单续跑时绑定当前资格合同（就地修改并返回）。

    旧版本工具创建的 metadata 缺少 `mcp_gate_contract_version`；其历史 trace 行
    由 linter 按合同 v1 原始解释保留。为使续跑产生的新记录与历史记录可靠区分并被
    强制采用当前合同，控制面在下一次 metadata 写入时补齐出生字段并记录采纳时刻
    `mcp_gate_contract_adopted_at`（显式 UTC）。若采纳时存在升级前未终结的 attempt
    （升级后、采纳前可能已写入新 trace），历史边界回退为 `mcp_gate_contract_history_boundary`
    = 其最早 start 时刻：该时刻之后的行一律按当前合同解释（宁可过度约束也不让
    旧式 skip 伪装成历史记录），该 attempt 须受控终结并重启后按当前合同重新留痕。
    """
    if meta.get("mcp_gate_contract_version") is None:
        catalog = load_json(GATE_CONTRACT_CATALOG, "cheap gate catalog")
        meta["mcp_gate_contract_version"] = catalog["contract_version"]
        adopted_at = datetime.now(timezone.utc).isoformat()
        meta["mcp_gate_contract_adopted_at"] = adopted_at
        boundary = adopted_at
        if events:
            earliest_open = gate_contract_open_attempt_start(events)
            if earliest_open:
                boundary = earliest_open.isoformat()
        meta["mcp_gate_contract_history_boundary"] = boundary
    return meta


def commit_metadata_and_event(out_dir, before, after, event_type, payload,
                              request_id=None, actor="icode", event_id=None, *,
                              git_provider=None, require_git_provider=False, before_publish=None,
                              before_append=None):
    """调用方持锁。事务日志 + metadata 原子替换 + event 追加；普通 I/O 失败回滚旧 metadata。"""
    if event_type != "ticket_created" and isinstance(after, dict):
        # 存量工单的每次控制面写入都是一次续跑：先绑定当前资格合同（幂等）。
        # 采纳时检查未终结 attempt，历史边界回退到其最早 start（覆盖升级后、
        # 采纳前写入的 trace）。
        if after.get("mcp_gate_contract_version") is None:
            try:
                pending_events, chain_problems = verify_event_chain(out_dir, after, semantic=False)
            except (OSError, ValueError, ControlError) as exc:
                raise ControlError("无法验证事件链，拒绝采纳资格合同",
                                   gate_id="event_chain_invalid") from exc
            if chain_problems:
                raise ControlError("事件链无效，拒绝采纳资格合同",
                                   gate_id="event_chain_invalid",
                                   violations=chain_problems)
            adopt_gate_contract(after, events=pending_events)
    out_dir = Path(out_dir)
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if before_append is not None and not callable(before_append):
        raise ControlError("candidate pre-append check must be callable", gate_id="candidate_provider_context")
    if access:
        _candidate_context_guard(out_dir, before, git_provider, require_git_provider)
        if (out_dir / TXN_NAME).exists():
            raise ControlError("provider transaction recovery is not enabled", gate_id="candidate_transaction_pending")
        if not callable(before_publish):
            raise ControlError("candidate publication check missing", gate_id="candidate_provider_context")
        _events, problems = verify_event_chain(out_dir, before, **access)
        if problems:
            raise ControlError("candidate transaction event chain invalid", gate_id="event_chain", violations=problems)
        # The full history has been consumed; later guards revalidate independently.
        del _events
    if event_type == "ticket_created":
        stage_module().protect_update({}, after)
    elif event_type == "metadata_updated" and not any(payload.get(key) for key in ("stage_control", "serial_claim_control", "visual_review_control", "evidence_review_control")):
        stage_module().protect_update(before or {}, after)
    stage_token = _stage_write_guard(out_dir, event_type, payload, git_provider=git_provider, require_git_provider=require_git_provider)
    if (before or {}).get("extensions", {}).get("stage_binding") is not None:
        state = stage_module()._resolve(out_dir, **access)
        stage_module().require_inherited_contract(state, after)
        if stage_token is not None:
            stage_token["proposed_contract"] = copy.deepcopy(after.get("verification_contract") or {})
        # Keep only the copied contract across preparation and publication reads.
        del state
    if before is not None:
        protect_fast_policy_update(before, after)
    require_valid_metadata(after)
    event_payload = dict(payload)
    event_payload["metadata_hash_after"] = metadata_hash(after)
    event = prepare_event(out_dir, after, event_type, event_payload,
                          request_id=request_id, actor=actor)
    if event_id is not None:
        event["event_id"] = event_id
        event["event_hash"] = canonical_event_hash(event)
    # Full before-state semantics were checked above; prepare_event only builds
    # the existing hash/schema envelope against the after-state, without access.
    if access:
        _candidate_context_guard(out_dir, before, git_provider, require_git_provider)
    txn_path = out_dir / TXN_NAME
    enabled = _storage_enabled(out_dir, new_metadata=after)
    registry = _committed_ledger(out_dir).registry if enabled else None
    prospective = None
    if enabled:
        try:
            # First manifest definitions are persisted to the content-addressed
            # side store here, so the event, journal and metadata below keep
            # references only instead of repeating the full manifest.
            _, prospective = storage_module().encode_event(
                event, committed_registry=registry, definition_store=FileDefinitionStore(out_dir))
            # Before publishing anything, prove that final metadata can resolve
            # exclusively from prior definitions plus this natural event.
            storage_module().encode_document(after, kind="metadata", committed_registry=prospective,
                                             require_definitions=True)
        except storage_module().StorageError as exc:
            raise ControlError("candidate storage preflight: " + str(exc), gate_id="candidate_storage") from exc
    journal = {
        "schema_version": 1,
        "created_at": now_iso(),
        "before_metadata": before,
        "after_metadata": after,
        "event": event,
    }
    atomic_write_json(txn_path, _encode_control_document(
        journal, "transaction", prospective if enabled else None, enabled, require_definitions=True))
    try:
        if access:
            _candidate_context_guard(out_dir, before, git_provider, require_git_provider)
        write_metadata(out_dir, after, registry=prospective if enabled else None, enabled=enabled,
                       require_definitions=True)
        if access:
            before_publish()
            _candidate_context_guard(out_dir, before, git_provider, require_git_provider)
        if before_append is not None:
            before_append()
        _stage_publication_check(stage_token, git_provider=git_provider, require_git_provider=require_git_provider)
        append_prepared_event(out_dir, event)
        _finalize_committed_metadata(out_dir, after)
    except BaseException:
        # append 的 write 已成功但 fsync/返回失败时，事件可能已落盘。
        # 此时应以事件链为准完成 metadata，不能回滚成「新事件+旧状态」。
        event_absence_confirmed = False
        try:
            events = _committed_ledger(out_dir).events
            if any(item.get("event_hash") == event.get("event_hash") for item in events):
                _finalize_committed_metadata(out_dir, after)
                txn_path.unlink(missing_ok=True)
                return event
            event_absence_confirmed = True
        except BaseException:
            pass
        try:
            if not event_absence_confirmed:
                # Broken/uncertain append or failed postappend compaction cannot
                # authorize rollback. Keep journal and current logical after.
                raise
            if before is None:
                (out_dir / METADATA_NAME).unlink(missing_ok=True)
            else:
                write_metadata(out_dir, before, registry=registry, enabled=enabled)
            if event_absence_confirmed:
                txn_path.unlink(missing_ok=True)
        except BaseException:
            # 保留事务日志，让下一次变更显式恢复或 validate 报 pending_transaction。
            pass
        raise
    txn_path.unlink(missing_ok=True)
    return event


# ---------------------------------------------------------------- 门禁 linter（复用三现有 linter）

def run_gate_linters(out_dir, to_status=None, delivery_verdict=None, legacy=False, step=None, *, metadata=None):
    meta = metadata if metadata is not None else load_metadata(out_dir)
    sm = load_state_machine(meta)
    if meta.get("mode") == "lite":
        return lite_gate_reports(out_dir, meta, step or sm["gate_policy"]["step_by_target"].get(to_status), delivery_verdict)
    lint_cfg = sm["gate_policy"]["gate_linters"]
    # step 真源 = gates.json state_machine.gate_policy.step_by_target（7 项全量：
    # log/plan/review/merge/code/deepcheck/audit）。thinking_gate/mcp_coverage/
    # workflow_contract 三个 linter 统一用此真源取值，禁止另维护硬编码映射
    # （此前 review_done/deepcheck_done/log_done/completed 缺失 → 缺 --step →
    # 全量扫描误伤条件门，见回归修复）。
    step = step or sm["gate_policy"].get("step_by_target", {}).get(to_status)
    # completed + delivery_verdict=verified → 用较严的 audit-verified 变体（含交付证据门）
    if step == "audit" and delivery_verdict == "verified":
        step = "audit-verified"
    if metadata is not None or (Path(out_dir) / METADATA_NAME).is_file():
        admission = fast_policy_admission(out_dir, metadata if metadata is not None else load_metadata(out_dir), step)
        if not admission["ok"]:
            return [{"gate_id": "fast_risk_admission", "ok": False, "fail_closed": True,
                     "report": admission}]
    results = []
    for gate_id, cmd in lint_cfg.items():
        full_cmd = list(cmd)
        # Only the three bundled Python gates share the CP's trusted runtime.
        # Preserve custom commands and their existing text/locale semantics.
        fixed_python_linter = (
            gate_id in ("thinking_gate", "mcp_coverage", "workflow_contract")
            and full_cmd[:2] == ["python3", f"tools/lint_{gate_id}.py"]
        )
        if fixed_python_linter:
            # Keep discovery packages unchanged when bundled gates import helpers.
            full_cmd[:1] = [sys.executable, "-B", "-X", "utf8"]
        if legacy:
            full_cmd = [arg for arg in full_cmd if arg != "--strict"]
        # 按 linter 归一化 audit-verified：workflow_contract 的 STEP_GATES 有
        # "audit-verified" 变体（含交付证据门）；而 cheap-research / reasoning-gate
        # 两个 catalog 只有 "audit"（无 audit-verified key）。若把 audit-verified
        # 原样传给它俩，total_in_scope=0 → audit MCP/思考门整体被跳过（verified
        # 终态漏检）。因此仅这两个 linter 归一化回 audit，workflow_contract 保留。
        linter_step = step
        if gate_id in ("thinking_gate", "mcp_coverage") and linter_step == "audit-verified":
            linter_step = "audit"
        if linter_step and gate_id in ("thinking_gate", "mcp_coverage", "workflow_contract"):
            full_cmd += ["--step", linter_step]
        full_cmd += [str(out_dir)]
        try:
            proc = subprocess.run(full_cmd, capture_output=True, text=not fixed_python_linter, timeout=180,
                                  cwd=str(SKILL_ROOT))
        except subprocess.TimeoutExpired:
            results.append({"gate_id": gate_id, "ok": False, "fail_closed": True,
                            "detail": "linter 超时（180s）", "hint": f"手动运行: {' '.join(full_cmd)}"})
            continue
        except OSError as exc:
            results.append({"gate_id": gate_id, "ok": False, "fail_closed": True,
                            "detail": f"linter 启动失败: {exc}", "hint": f"手动运行: {' '.join(full_cmd)}"})
            continue
        stdout, stderr = proc.stdout, proc.stderr
        if fixed_python_linter:
            # Decode in this thread: Windows text-mode reader failures can
            # otherwise lose the output. str is retained only for old mocks.
            try:
                stdout = stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout
                stderr = stderr.decode("utf-8") if isinstance(stderr, bytes) else stderr
            except UnicodeDecodeError:
                results.append({"gate_id": gate_id, "ok": False, "fail_closed": True,
                                "detail": "linter 输出不是合法 UTF-8",
                                "hint": f"修复后重试: {' '.join(full_cmd)}"})
                continue
        report = None
        try:
            report = strict_json_loads(stdout)
        except (json.JSONDecodeError, ValueError):
            pass
        # 所有门禁命令都声明 --json；exit 0 但没有 JSON 报告不能当成可审计通过。
        ok = proc.returncode == 0 and isinstance(report, dict)
        entry = {"gate_id": gate_id, "ok": ok, "returncode": proc.returncode,
                 "cmd": full_cmd}
        if isinstance(report, dict):
            entry["report"] = report
            if isinstance(report.get("enumeration_debt"), int):
                # 枚举/去重债务必须从校验报告透传到门禁结果与状态推进输出：
                # 门禁通过不代表债务已解决，调用方需要从推进结果看到它。
                entry["enumeration_debt"] = report["enumeration_debt"]
        else:
            raw = (stdout or stderr or "")[-500:]
            entry["detail"] = "linter 未返回合法 JSON 对象" + (f": {raw}" if raw else "")
        if not ok:
            entry["fail_closed"] = True
            entry["hint"] = f"修复后重试: {' '.join(full_cmd)}"
        results.append(entry)
    return results


_GATE_COVERAGE_MODULE = None


def gate_coverage_module():
    """加载 cheap-research 覆盖校验器（跨步骤债务余额与其共享同一实现）。"""
    global _GATE_COVERAGE_MODULE
    if _GATE_COVERAGE_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "icode_gate_coverage", SKILL_ROOT / "tools/lint_mcp_coverage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _GATE_COVERAGE_MODULE = module
    return _GATE_COVERAGE_MODULE


def last_recorded_gate_enumeration_debt(out_dir):
    """事件链中最近一次记录的债务余额；返回 (是否记录过, 值)。"""
    try:
        events, _problems = read_events(out_dir)
    except Exception:
        return False, None
    for event in reversed(events):
        if event.get("event_type") != "state_changed":
            continue
        payload = event.get("payload") or {}
        if "gate_enumeration_debt" in payload:
            return True, payload.get("gate_enumeration_debt")
    return False, None


def ticket_enumeration_debt(out_dir, meta):
    """工单级未解决枚举/去重债务余额；返回 None 表示无法判定（不是清偿）。

    与单次状态推进的门禁报告不同：报告只看被推进步骤的 in-scope gates，
    review 产生的债务在后续 plan_finalized/completed 推进的报告中会归零；
    余额跨步骤持续存在，交付结论反映的是工单尚未清偿的债务。清偿要求债务
    gate 出现通过资格与证据校验的有效最终记录（pending/中断/非法行不清偿；
    unavailable/degraded 还需 availability.evidence_ref 指向工单目录内真实
    存在的人工等价证据文件）。trace 不存在时结合事件链判定：从未记录过债务
    或最近记录为 0 → 确认无债务（0）；曾记录未清偿/未知债务且 trace 缺失 →
    无法核实（None）——trace 丢失不得被报告为债务归零。trace 不可读/含
    不完整 JSON 同样返回 None。
    """
    path = Path(out_dir) / ".mcp_gate_trace.jsonl"
    if path.is_symlink():
        return None  # 符号链接的 trace 不可作为证据读取，无法确认债务状态
    if not path.is_file():
        recorded, value = last_recorded_gate_enumeration_debt(out_dir)
        if not recorded or value == 0:
            return 0  # 从未记录过债务 / 最近已确认清偿：确认无债务
        return None  # 曾记录未清偿（或未知）债务且 trace 缺失：无法核实
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, ValueError):
        return None  # 不可读/不完整 JSON：未知，不得当作清偿
    if any(not isinstance(row, dict) or row.get("ticket_id") != meta.get("ticket_id") for row in rows):
        return None  # 他单或非对象记录不能作为本工单的确定余额
    try:
        return gate_coverage_module().ticket_enumeration_debt(rows, meta, out_dir=Path(out_dir))
    except (TypeError, AttributeError):
        return None


# ---------------------------------------------------------------- 子命令实现

def _load_lite_review_report(out_dir, fact):
    path = Path(out_dir) / fact["report_path"]
    if path.parent != Path(out_dir) or path.is_symlink() or not path.is_file():
        raise ControlError("不可变审核报告缺失或被改写", gate_id="lite_review_report")
    try:
        # 签名和解析必须使用同一份字节，不在校验后重新打开可变路径。
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != fact.get("report_sha256"):
            raise ControlError("不可变审核报告缺失或被改写", gate_id="lite_review_report")
        return strict_json_loads(data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ControlError("审核报告读取或JSON解析失败", gate_id="lite_review_report") from exc


def lite_review_records(events):
    """将宿主子代理与历史 CLI 回执投影到同一套审核读取接口。"""
    invocations, returns = [], []
    native = {}
    for event in events:
        kind, payload = event.get("event_type"), event.get("payload") or {}
        if kind == "lite_review_invoked":
            invocations.append(payload)
        elif kind == "lite_review_returned":
            returns.append({**payload, "source": "legacy"})
        elif kind == "agent_spawned" and payload.get("lite_review_attempt"):
            invocation = {**payload["lite_review_binding"], "attempt": payload["lite_review_attempt"],
                          "invocation_id": payload["spawn_id"],
                          "host_receipt_challenge": payload.get("host_receipt_challenge")}
            native[payload["spawn_id"]] = invocation
            invocations.append(invocation)
        elif kind == "agent_result" and payload.get("spawn_id") in native:
            report = payload.get("lite_review_report") or {}
            # adopted=no/partial 的返回仅供追溯：保留记录，但不得作为审核通过依据。
            adopted = payload.get("adopted")
            attestation = payload.get("host_attestation") or {}
            attested = (attestation.get("launched") is True
                        and attestation.get("challenge") == native[payload["spawn_id"]].get("host_receipt_challenge")
                        and attestation.get("spawn_id") == payload["spawn_id"]
                        and attestation.get("host_session_id") == payload.get("host_session_id"))
            # 降级审核只能用于待验证交付，仍须追踪本轮发现的问题。
            degraded = (payload.get("host_attestation_degraded") or {}).get("status") == "unavailable"
            trusted = (payload.get("result") == "joined" and bool(report)
                       and adopted == "yes" and attested)
            degraded_only = (not trusted and degraded and payload.get("result") == "joined"
                             and bool(report) and adopted == "yes")
            returns.append({**native[payload["spawn_id"]], **report,
                            "host_session_id": payload.get("host_session_id"),
                            "adopted": adopted,
                            "host_receipt": payload.get("host_receipt"),
                            "host_attestation": payload.get("host_attestation"),
                            "host_attestation_degraded": payload.get("host_attestation_degraded"),
                            "source": "host-subagent",
                            "outcome": ("success" if trusted
                                        else "degraded" if degraded_only else "failure")})
    return invocations, returns


def lite_open_findings(out_dir, events, *, pending_delivery=False, current_report=None):
    """待验证交付允许降级复审解决降级问题，可信交付仍需逐项可信确认。"""
    findings = {}
    needs_attestation = set()
    for fact in lite_review_records(events)[1]:
        if not fact.get("report_path") or not fact.get("report_sha256"):
            continue
        trusted = fact.get("outcome") == "success"
        if not trusted and fact.get("source") != "host-subagent":
            # 旧 CLI 失败事件可能指向未通过 schema 校验的报告。
            continue
        provisional = bool(fact.get("host_attestation_degraded"))
        if current_report is not None and fact["invocation_id"] == current_report[0]:
            report = current_report[1]
        else:
            report = _load_lite_review_report(out_dir, fact)
        items = report.get("findings")
        if not isinstance(items, list):
            continue
        for finding in items:
            if not isinstance(finding, dict) or not finding.get("id"):
                continue
            key = finding["id"]
            can_resolve = trusted or (pending_delivery and fact["outcome"] == "degraded"
                                      and key not in needs_attestation)
            if finding.get("status") == "resolved":
                if can_resolve and finding.get("evidence"):
                    findings.pop(key, None)
                    needs_attestation.discard(key)
            elif finding.get("blocking", True):
                if not provisional:
                    needs_attestation.add(key)
                findings[key] = finding
    return findings


def lite_review_fact(out_dir, meta, events, attempt=None, *, candidate=None, allow_rejected=False,
                     return_report=False):
    starts = [e for e in events if e.get("event_type") == "step_started"
              and e["payload"].get("step") == "lite_review"]
    if not starts:
        raise ControlError("缺少独立审核 attempt", gate_id="lite_review_required")
    start = starts[-1]
    attempt = attempt or start["payload"]["attempt"]
    if attempt != start["payload"]["attempt"]:
        raise ControlError("旧审核 attempt 不可签收", gate_id="lite_review_stale")
    invocations, results = lite_review_records(events)
    returns = [r for r in results if r.get("attempt") == attempt]
    if not returns:
        raise ControlError("独立审核尚未真实返回", gate_id="lite_review_required")
    fact = returns[-1]
    invokes = [r for r in invocations if r.get("invocation_id") == fact.get("invocation_id")]
    candidate = candidate if candidate is not None else candidate_snapshot(out_dir, meta, events=events)
    degradation = _host_adapter_degradation(meta)
    acceptable = {"success"} | ({"degraded"} if degradation is not None else set())
    if len(invokes) != 1 or fact.get("outcome") not in acceptable \
            or not fact.get("host_session_id"):
        raise ControlError("缺少可信宿主审核结果", gate_id="lite_review_required")
    # 新宿主子代理回执必须携带绑定本次登记的 challenge 回执与可信适配器确认；历史 CLI 回执按原规则读取。
    if fact.get("source") == "host-subagent":
        challenge = (fact.get("host_receipt") or {}).get("challenge")
        if not isinstance(challenge, str) or not challenge \
                or (fact.get("host_receipt") or {}).get("spawn_id") != fact.get("invocation_id") \
                or (fact.get("host_receipt") or {}).get("host_session_id") != fact.get("host_session_id"):
            raise ControlError("宿主审核回执未绑定本次启动，不能作为审核依据",
                               gate_id="lite_review_host_receipt")
        attestation = fact.get("host_attestation") or {}
        if fact.get("outcome") == "degraded":
            # 已确认是本工单记录的降级审核：只作留痕，绝不当作可信依据。
            if degradation is None:
                raise ControlError("审核结果未获宿主确认，不能作为依据",
                                   gate_id="lite_review_host_unattested")
        elif attestation.get("launched") is not True \
                or attestation.get("challenge") != challenge \
                or attestation.get("spawn_id") != fact.get("invocation_id") \
                or attestation.get("host_session_id") != fact.get("host_session_id") \
                or attestation.get("stored_report_sha256") != fact.get("report_sha256") \
                or not fact.get("host_attestation"):
            raise ControlError("缺少可信宿主确认，审核结果不能作为通过依据",
                               gate_id="lite_review_host_unattested")
        else:
            _require_pinned_adapter(meta, attestation)
    invocation = invokes[0]
    reviewed = start["payload"].get("candidate_input")
    # 保留原报告身份；普通线性提交仅通过已有的严格源码等价证明复用。
    proof = candidate_module().compare_candidates(reviewed, candidate)
    if invocation.get("attempt") != attempt or not proof["equivalent"] \
            or invocation.get("candidate_id") != (reviewed or {}).get("candidate_id") \
            or invocation.get("contract_digest") != lite_module().contract_digest(meta):
        raise ControlError("审核的代码或合同已过期", gate_id="lite_review_stale")
    # 回退后的旧结果不能重新生效；所有绑定都来自受控执行器事件。
    if any((state_change_payload(e) or {}).get("to") in {
            "lite_prepare_in_progress", "lite_code_in_progress"}
           for e in events[events.index(start) + 1:]):
        raise ControlError("审核前已有回退失效边界", gate_id="lite_review_stale")
    report = _load_lite_review_report(out_dir, fact)
    schema_issues = validate_against(report, SCHEMA_DIR / "lite-review.schema.json", "lite_review_schema")
    if schema_issues:
        raise ControlError("审核报告不满足 schema", gate_id="lite_review_report", violations=schema_issues)
    issues = lite_module().review_issues(report, meta,
                                        invocation["candidate_id"], attempt, allow_rejected=allow_rejected)
    # 合法拒绝结论可以展示；展示权限不产生通过或关闭历史问题的资格。
    if allow_rejected and report["verdict"] in {"changes_required", "blocked"}:
        if issues:
            raise ControlError("审核报告身份不匹配", gate_id="lite_review_report", violations=issues)
        return report if return_report else candidate
    outstanding = lite_open_findings(
        out_dir, events, pending_delivery=fact["outcome"] == "degraded",
        current_report=(fact["invocation_id"], report))
    issues += ["unresolved_previous_finding:" + key for key in outstanding]
    if issues:
        raise ControlError("独立审核未通过", gate_id="lite_review_report", violations=issues)
    return report if return_report else candidate


def cmd_lite_preflight(args):
    """只读预检：确认宿主适配器来源可信，供实施前检查。"""
    out_dir = Path(args.dir).resolve()
    if not out_dir.is_dir():
        raise ControlError(f"工单目录不存在: {out_dir}", exit_code=2, gate_id="lite_preflight_dir")
    observation = _observe_host_adapter()
    if observation["status"] == "pinned":
        response = {"host_attestation": "available", "adapter_sha256": observation["sha256"],
                    "config_path": observation["config_path"], "delivery_ceiling": "verified"}
    else:
        response = {"host_attestation": "unavailable", "reason": observation["reason"],
                    "detail": observation["detail"], "delivery_ceiling": "verification_pending"}
    print(json.dumps({"ok": True, **response}, ensure_ascii=False))
    return 0


def cmd_lite_report(args):
    out_dir = Path(args.dir).resolve()
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        if meta.get("mode") != "lite":
            raise ControlError("仅 lite 工单可派生主报告", gate_id="workflow_profile")
        events, issues = verify_event_chain(out_dir, meta)
        if issues:
            raise ControlError("事件链不完整", gate_id="event_chain", violations=issues)
        # 主报告不属于审核签名输入，不生成虚假的执行/完成事件。
        returns = [r for r in lite_review_records(events)[1] if r.get("outcome") in {"success", "degraded"}]
        review = (lite_review_fact(out_dir, meta, events, allow_rejected=True, return_report=True)
                  if returns else None)
        atomic_write_bytes(out_dir / "lite.md", lite_module().render_report(meta, review).encode("utf-8"))
    print(json.dumps({"ok": True, "path": str(out_dir / "lite.md")}, ensure_ascii=False))
    return 0


def lite_evidence_issues(out_dir, meta, events):
    candidate = candidate_snapshot(out_dir, meta, events=events)
    runs = meta.get("verification_runs") or []
    issues, pending = lite_module().delivery_issues(meta, runs)
    api = verification_evidence_module()
    contract = meta.get("verification_contract") or {}
    cells = api.required_cells(contract)
    settings = api.metric_settings(contract, cells or [])
    if cells is None or settings is None:
        return issues + ["verification_contract_invalid"], pending
    context = {"enabled": True, "current": candidate, "events": events, "reasons": [],
               "candidate_api": candidate_module(), "evidence_root": str(out_dir),
               "review_validator": api.protected_review_validator(out_dir, meta, verify=verify_event_chain)}
    selected = api.eligible_latest_runs(meta, context, source_runs=runs)
    issues += selected["reasons"]
    profile, baseline_ref, metrics = settings
    for cell in cells:
        raw = [r for r in runs if api.cell(r) == cell]
        if not raw and cell in pending:
            continue
        latest = selected["latest"].get(cell)
        if raw and (latest or {}).get("run_id") != raw[-1].get("run_id"):
            issues.append("latest_verification_ineligible:" + "/".join(cell))
        state = api.unit_state(latest, profile, [m for m in metrics if api.cell(m) == cell],
                               baseline_ref, meta=meta, evidence_root=str(out_dir),
                               review_validator=context["review_validator"])
        if state != "satisfied":
            issues.append(state + ":" + "/".join(cell))
    return sorted(set(issues)), pending


LITE_HOST_DEGRADED = "host_attestation_unavailable"


def _host_adapter_degradation(meta):
    """返回工单记录的宿主适配器不可用事实；可用时返回 None。"""
    pinned = ((meta.get("extensions") or {}).get("lite") or {}).get("host_adapter") or {}
    if pinned.get("status") == "unavailable":
        return pinned
    return None


def _pinned_adapter_sha256(meta):
    """返回进入 Code 时冻结的宿主适配器摘要；缺失即 fail-closed。

    适配器已记不可用属于**降级**路径，由调用方先用 _host_adapter_degradation
    判定；只有"未降级却没有身份"这种自相矛盾状态才在这里拒绝。
    """
    pinned = ((meta.get("extensions") or {}).get("lite") or {}).get("host_adapter") or {}
    if pinned.get("status") == "unavailable":
        raise ControlError(
            "本工单记录宿主适配器不可用（%s），不能按可信审核签收；"
            "只能以 verification_pending 交付"
            % (pinned.get("detail") or "lite_host_adapter_unavailable"),
            gate_id="lite_host_adapter_unavailable")
    expected = pinned.get("sha256")
    if not isinstance(expected, str) or not expected:
        raise ControlError("缺少 Prepare 阶段确认的宿主适配器身份，不能作为审核依据",
                           gate_id="lite_host_adapter_unavailable")
    return expected


def _require_pinned_adapter(meta, attestation):
    """签收结果的适配器身份必须等于冻结值。

    真正的执行前比对在 _host_attest 内完成；这里复核落盘记录的身份自洽，
    避免把不一致的确认写进事件链。
    """
    expected = _pinned_adapter_sha256(meta)
    if attestation.get("adapter_sha256") != expected:
        raise ControlError("签收所用宿主适配器与 Prepare 阶段确认的身份不一致",
                           gate_id="lite_host_adapter_unavailable")


def lite_step_gate(out_dir, meta, events, step, attempt=None):
    issues = lite_module().prepare_issues(meta)
    lint = workflow_lint_module()
    catalog = load_json(GATES_JSON, "gates")
    issues += lint.validate_requirement_deltas(meta, catalog)
    if step in {"lite_code", "lite_review"}:
        issues += lint.validate_identity_change(meta, catalog)
    if step == "lite_prepare":
        # 用户停止点允许记录规划完成，但不能进入实施。
        issues = [x for x in issues if x != "user_stop_after_prepare"]
    elif step == "lite_review":
        lite_review_fact(out_dir, meta, events, attempt)
    if step in {"lite_code", "lite_review"}:
        evidence_issues, _ = lite_evidence_issues(out_dir, meta, events)
        issues += evidence_issues
    if issues:
        raise ControlError("lite 质量门未通过", gate_id="lite_quality_gate", violations=issues)


def lite_gate_reports(out_dir, meta, step, delivery_verdict):
    events, issues = verify_event_chain(out_dir, meta)
    if issues:
        raise ControlError("事件链不完整", gate_id="event_chain", violations=issues)
    if step and step != "verify":
        if step not in workflow_contract(meta)["step_order"]:
            raise ControlError("未知 lite 步骤", gate_id="workflow_profile")
        lite_step_gate(out_dir, meta, events, step)
    elif meta.get("status") == "completed":
        lite_step_gate(out_dir, meta, events, "lite_review")
    return [{"gate_id": "lite_contract", "ok": True}]


def lite_transition_guard(out_dir, meta, events, target, verdict):
    current = meta["status"]
    if target in {"lite_prepare_in_progress", "lite_code_in_progress"}:
        execution = execution_trace(events)
        if execution["open_steps"] or execution["open_operations"] or execution["open_agents"]:
            raise ControlError("回退前须终结开放执行", gate_id="lite_return_open_execution")
    if target == "lite_code_in_progress":
        issues = lite_module().prepare_issues(meta)
        if issues:
            raise ControlError("G1 规划准入未通过", gate_id="lite_prepare_gate", violations=issues)
    if target == "completed":
        lite_review_fact(out_dir, meta, events)
        issues, pending = lite_evidence_issues(out_dir, meta, events)
        # 无适配器降级：只能以 verification_pending 交付，永远不能声称 verified。
        if _host_adapter_degradation(meta) is not None:
            pending = list(pending) + [LITE_HOST_DEGRADED]
        allowed = "verification_pending" if pending else "verified"
        if verdict == "verified":
            applicability = verification_applicability(out_dir, meta)
            if not applicability["ok"]:
                issues += applicability["reasons"]
            lint = workflow_lint_module()
            issues += lint.validate_acceptance(meta, load_json(GATES_JSON, "gates"))
        if issues or verdict != allowed:
            raise ControlError("G3 当前证据不支持交付结论", gate_id="lite_delivery_gate",
                               violations=issues, expected_verdict=allowed)
    return current


def _active_lite_review(meta, events, attempt):
    if meta.get("mode") != "lite" or meta.get("status") != "lite_review_in_progress":
        raise ControlError("仅当前 lite 审核阶段可登记审核", gate_id="lite_review_entry")
    start = find_step_start(events, attempt)
    if start["payload"].get("step") != "lite_review" or step_attempt_finished(events, attempt):
        raise ControlError("review attempt 已结束或不匹配", gate_id="lite_review_entry")
    return start["payload"].get("candidate_input")


def _lite_review_input(meta, events, spawn, *, out_dir=None, candidate=None):
    """构造只读审核输入；challenge 必须与登记一致。"""
    challenge = spawn.get("host_receipt_challenge")
    if not isinstance(challenge, str) or not challenge:
        raise ControlError("审核登记缺少宿主挑战", gate_id="lite_review_entry")
    out_dir = Path(out_dir) if out_dir is not None else None
    if out_dir is None:
        raise ControlError("缺少工单目录", gate_id="lite_review_entry")
    if candidate is None:
        candidate = candidate_snapshot(out_dir, meta, events=events)
    return {
        "workspace": str(execution_workspace(out_dir, meta)),
        "metadata": str(out_dir / METADATA_NAME),
        "events": str(out_dir / EVENTS_NAME),
        "rules": str(SKILL_ROOT / "references/lite_review.md"),
        "baseline": candidate["base"],
        "candidate_id": candidate["candidate_id"],
        "host_receipt_challenge": challenge,
        "task": "新建独立只读上下文，按审核规则读取原始需求、合同、候选差异、"
                "历史未关闭问题及当前验收证据。按字段读取材料，返回结构化审核 JSON。"
                "宿主须在 record-agent-result 时通过 --host-receipt 回传含 host_receipt_challenge 的启动/返回回执。",
    }


def cmd_lite_review(args):
    """登记将由当前宿主实际启动的独立子代理。"""
    return cmd_record_agent_spawn(argparse.Namespace(
        dir=args.dir, task_scope="Lite 独立审核", expected_artifact="结构化审核 JSON",
        evidence_boundary="只读当前工单、实际差异与验收证据",
        join_condition="宿主返回新子代理身份及结构化结论",
        backend="host-subagent", model="inherit", capability=["text", "tools"],
        exclusive_key="lite-review", request_id=args.request_id, actor="icode",
        lite_review_attempt=args.attempt))


def cmd_resolve_ticket(args):
    result = resolve_dir(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _read_utf8_text(path, label, *, max_bytes=1024 * 1024):
    """Read a user-supplied UTF-8 text file without shell argv decoding.

    Windows shells may pass non-ASCII argv through the ANSI code page; a file
    argument is the reliable path for Chinese requirements and JSON payloads.
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ControlError(f"{label} 读取失败: {exc}", exit_code=2) from exc
    if len(data) > max_bytes:
        raise ControlError(f"{label} 超过 {max_bytes} 字节上限", exit_code=2)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ControlError(f"{label} 必须是 UTF-8: {exc}", exit_code=2) from exc
    return text


def _resolve_requirement_arg(args):
    """Resolve --requirement / --requirement-file once, before digests."""
    inline = getattr(args, "requirement", None)
    file_path = getattr(args, "requirement_file", None)
    if inline is not None and file_path is not None:
        raise ControlError("--requirement 与 --requirement-file 只能二选一", exit_code=2)
    if file_path is not None:
        text = _read_utf8_text(file_path, "--requirement-file")
        if not text.strip():
            raise ControlError("--requirement-file 内容为空", exit_code=2)
        args.requirement = text
        args.requirement_file = None
        return text
    if not isinstance(inline, str) or not inline.strip():
        raise ControlError("create 需要 --requirement 或 --requirement-file", exit_code=2)
    return inline


def _read_json_argument(inline, file_path, label):
    """Resolve an inline JSON argument or its UTF-8 file variant."""
    if inline is not None and file_path is not None:
        raise ControlError(f"{label} 与 {label}-file 只能二选一", exit_code=2)
    if file_path is not None:
        return strict_json_loads(_read_utf8_text(file_path, f"{label}-file"))
    return strict_json_loads(inline) if inline else {}


def _cmd_create_plain(args):
    _resolve_requirement_arg(args)
    out_dir = Path(args.dir).resolve()
    workspace, path_is_debug = classify_ticket_dir(out_dir)
    birth_is_debug = args.birth.startswith("debug-")
    if path_is_debug != birth_is_debug:
        raise ControlError(
            f"出生类型 {args.birth!r} 与目录域不一致：debug 出生只能写 .debug 域，"
            "正常出生不得写 .debug 域",
            exit_code=2, gate_id="birth_directory_isolation")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    try:
        seed = strict_json_loads(args.metadata_json) if args.metadata_json else {}
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError(f"--metadata-json 不是合法 JSON: {exc}", exit_code=2)
    if not isinstance(seed, dict):
        raise ControlError("--metadata-json 必须是 JSON 对象", exit_code=2)
    protected = sorted(CONTROLLED_BIRTH_FIELDS.intersection(seed))
    if protected:
        raise ControlError(
            f"--metadata-json 含控制面受保护字段: {protected}", exit_code=2,
            gate_id="birth_protected_fields",
            hint="这些字段由 create/transition/record-verification/close-phase 自动维护；"
                 "仅传 requirement_summary、keywords、mode 等业务字段。")
    birth = {
        "init": ("init_in_progress", ["0"], False),
        "plan": ("init_in_progress", [], False),
        "lite": ("lite_prepare_in_progress", [], False),
        "log": ("log_in_progress", [], False),
        "debug-init": ("debug_in_progress", ["0"], True),
        "debug-log": ("debug_in_progress", [], True),
    }
    status, completed_steps, debug = birth[args.birth]
    meta = dict(seed)
    if args.birth == "lite":
        if meta.get("mode", "lite") != "lite" or meta.get("workflow_version", 1) != 1:
            raise ControlError("lite 出生必须 mode=lite/workflow_version=1", gate_id="workflow_profile")
        meta.update(mode="lite", workflow_version=1)
        meta.setdefault("requirement_deltas", [])
        meta.setdefault("semantic_decisions", [])
    elif meta.get("mode") == "lite":
        raise ControlError("lite 模式必须使用 lite 出生", gate_id="workflow_profile")
    if args.birth != "lite":
        if meta.get("risk_profile") is not None and not isinstance(meta["risk_profile"], dict):
            raise ControlError("risk_profile 必须是 JSON 对象或 null", exit_code=2,
                               gate_id="fast_risk_profile")
        profile = dict(meta.get("risk_profile") or {})
        if "policy_version" in profile and (type(profile["policy_version"]) is not int or profile["policy_version"] != 2):
            raise ControlError("新工单必须使用风险策略版本2；历史兼容仅适用于已有工单", exit_code=2,
                               gate_id="fast_risk_profile")
        # Bind the policy at every new birth, including initially full tickets, so
        # later updates cannot opt back into historical admission by omitting it.
        initial_mode = "fast" if meta.get("mode") == "fast" else "full"
        profile["policy_version"] = 2
        profile.setdefault("requested_mode", initial_mode)
        profile.setdefault("effective_mode", initial_mode)
        meta["risk_profile"] = profile
    if verification_evidence_module().real_env_risk(meta) and meta.get("verification_contract") is None:
        meta["verification_contract"] = verification_evidence_module().pending_contract()
    if verification_evidence_module().build_starts(meta):
        raise ControlError("build-start receipts cannot be seeded at ticket creation", gate_id="build_start_writer")
    _cheap_catalog = load_json(GATE_CONTRACT_CATALOG, "cheap gate catalog")
    meta.update({
        "schema_version": 3,
        "ticket_id": args.ticket_id,
        "requirement": args.requirement,
        "created_at": now_iso(),
        "status": status,
        "completed_steps": completed_steps,
        "indexed": False,
        "project_path": str(workspace),
        "workflow_gate_schema_version": 1,
        "thinking_gate_schema_version": 1,
        "mcp_gate_schema_version": _cheap_catalog["trace_schema_version"],
        # 资格合同版本绑定于出生（受保护字段，事后不可改写）：新工单的 trace 行按合同 v2
        # 解释；存量工单无此字段，其历史行按合同 v1 旧语义解释，不追溯改判。
        "mcp_gate_contract_version": _cheap_catalog["contract_version"],
    })
    # 完成态 transition 走 --strict，linter 要求 requirement_deltas 显式存在；而
    # references/dir_and_metadata.md 声明「可选，缺失视为 []」。新工单在出生处补空数组，
    # 让两处口径一致；用户显式传入时保留（setdefault 不覆盖）。
    meta.setdefault("requirement_deltas", [])
    # 同理 semantic_decisions：init/debug 出生尚未产生语义决策，但 --strict 要求
    # 字段显式存在。空数组表示「尚未决策」，由 plan 按需填充；不改变语义门禁
    # （非空且 status != resolved 时仍按原规则阻断）。
    meta.setdefault("semantic_decisions", [])
    if debug:
        meta["debug"] = True
    elif meta.get("debug"):
        raise ControlError("非 debug 出生类型禁止 metadata.debug=true", exit_code=2,
                           gate_id="birth_kind_mismatch")
    repo_head = None
    if not debug:
        try:
            repo_head = candidate_module().repository_head(workspace)
        except (candidate_module().CandidateError, OSError, UnicodeError) as exc:
            raise ControlError(f"Git工程候选绑定失败: {exc}", gate_id="candidate_capture") from exc
    scan_start = time.monotonic()
    if debug:
        # debug 工单不进入 code/patch，候选快照没有消费点。跳过全仓候选扫描，
        # 避免把完整 manifest 内联进 ticket_created 事件（大仓可达数十 MB）。
        # 候选相关参数对 debug 没有语义，显式拒绝而不是静默丢弃。
        if getattr(args, "exclude", None) or getattr(args, "candidate_limits_json", None):
            raise ControlError(
                "debug 出生不建立候选合同，--exclude/--candidate-limits-json 不适用",
                exit_code=2, gate_id="birth_candidate_contract")
        print("candidate scan: skipped (debug birth)", file=sys.stderr)
    elif repo_head:
        limits = candidate_limits(getattr(args, "candidate_limits_json", None))
        meta["excluded_side_effects"] = getattr(args, "exclude", None) or []
        meta["review_receipts"] = []
        candidate = candidate_snapshot(out_dir, meta, base=repo_head, limits=limits, events=[])
        # Reuse candidate path/overlap validation and persist its canonical
        # declaration in both the birth metadata and authenticated event.
        meta["excluded_side_effects"] = candidate["excluded_side_effects"]
        meta["candidate_tracking"] = {"version": 1, "mode": "enabled",
                                      "base": repo_head, "current": candidate, "limits": limits}
        # Birth observability: a large repository can spend a long time hashing
        # every tracked/untracked file before the first event is written.
        elapsed_ms = int((time.monotonic() - scan_start) * 1000)
        print(f"candidate scan: {len(candidate.get('manifest') or [])} files, "
              f"{len(candidate.get('index_manifest') or [])} index rows, {elapsed_ms} ms",
              file=sys.stderr)
    elif getattr(args, "exclude", None):
        raise ControlError("非 Git 出生无法绑定候选排除项", gate_id="candidate_capture")
    else:
        print("candidate scan: skipped (non-git birth)", file=sys.stderr)
    require_valid_metadata(meta)
    payload = {"birth_kind": args.birth,
               "execution_contract_version": 1,
               "requirement_summary": meta.get("requirement_summary") or args.requirement[:100]}
    if meta.get("mode") == "lite":
        payload["workflow_identity"] = {"mode": "lite", "workflow_version": 1,
                                        "requirement_digest": canonical_digest(meta["requirement"])}
    if getattr(args, "stage_id", None):
        payload["stage_birth"] = {"stage_id": args.stage_id, "input_digest": _stage_birth_digest(args)}
    if "candidate_tracking" in meta:
        payload["candidate_tracking"] = meta["candidate_tracking"]
        payload["excluded_side_effects"] = meta["excluded_side_effects"]
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta_path = out_dir / METADATA_NAME
        if meta_path.exists():
            current = load_metadata(out_dir)
            events, problems = verify_event_chain(out_dir, current)
            if problems:
                raise ControlError("已有工单事件链不完整，拒绝覆盖创建", exit_code=1,
                                   gate_id="event_chain", violations=problems)
            prior = find_idempotent_event(
                events, args.request_id, "ticket_created", payload)
            comparable_meta = dict(meta)
            comparable_meta["created_at"] = current.get("created_at")
            if prior and comparable_birth_metadata(current, comparable_meta) == comparable_meta:
                print(json.dumps({"ok": True, "already_applied": True,
                                  "ticket_id": args.ticket_id,
                                  "event_id": prior["event_id"], "out_dir": str(out_dir)},
                                 ensure_ascii=False, indent=2))
                return 0
            raise ControlError(
                f"目标目录已有 metadata，拒绝覆盖创建: {meta_path}",
                exit_code=1, gate_id="ticket_create_exists")
        if (out_dir / EVENTS_NAME).exists():
            raise ControlError("目标目录已有事件日志但无 metadata，拒绝猜测创建",
                               exit_code=1, gate_id="orphan_event_log")
        unexpected = sorted(
            item.name for item in out_dir.iterdir() if item.name != ".icontrol.lock")
        if unexpected:
            raise ControlError(
                f"目标目录非空，拒绝把已有产物认领为新工单: {unexpected}",
                exit_code=1, gate_id="ticket_create_nonempty")
        event = commit_metadata_and_event(
            out_dir, None, meta, "ticket_created", payload,
            request_id=args.request_id)
    print(json.dumps({"ok": True, "ticket_id": args.ticket_id,
                      "status": status, "event_id": event["event_id"],
                      "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0


def _stage_birth_digest(args):
    identity = {"ticket_id": args.ticket_id, "requirement": args.requirement, "birth": args.birth,
        "stage_id": args.stage_id, "metadata": strict_json_loads(args.metadata_json) if args.metadata_json else {},
        "candidate_limits": strict_json_loads(args.candidate_limits_json) if args.candidate_limits_json else None}
    if getattr(args, "exclude", None):
        identity["candidate_exclusions"] = args.exclude
    return canonical_digest(identity)


def cmd_create(args):
    """A staged project's new ordinary ticket must name its planned binding."""
    _resolve_requirement_arg(args)
    out_dir = Path(args.dir).resolve()
    workspace, _debug = classify_ticket_dir(out_dir)
    locator = stage_module().locator_path(workspace)
    if args.birth == "lite" and (locator.parent.exists() or stage_module().discover_stage_history(workspace) is not None):
        raise ControlError("lite 首版不支持已启用阶段计划的工程，请使用既有阶段流程", gate_id="lite_stage_unsupported")
    if not locator.parent.exists():
        if stage_module().discover_stage_history(workspace) is not None:
            raise ControlError("stage: project locator directory missing; recover it with stage register on the authenticated coordinator",
                               gate_id="stage_gate")
        return _cmd_create_plain(args)
    if not locator.is_file():
        raise ControlError("stage: enabled project locator missing; refusing ordinary birth", gate_id="stage_gate")
    ref = load_json(locator, "stage locator")
    stage_id = getattr(args, "stage_id", None)
    if not stage_id:
        raise ControlError("stage: enabled project create requires --stage-id", gate_id="stage_gate")
    coordinator = ref.get("coordinator_root")
    # Hold the program identity across the first ticket write and binding.
    # bind_stage uses the same internal writer, avoiding non-reentrant locks.
    def _bind_created(request_id):
        with DirLock(out_dir):
            return stage_module()._bind_stage_locked(coordinator, stage_id, out_dir, request_id=request_id)
    with DirLock(coordinator):
        recover_pending_transaction(coordinator)
        state = stage_module()._resolve(coordinator)
        if state is None or stage_id not in [row["id"] for row in state["program"]["plan"]["stages"]]:
            raise ControlError("stage: unknown planned stage", gate_id="stage_gate")
        try:
            seed = strict_json_loads(args.metadata_json) if args.metadata_json else {}
        except (json.JSONDecodeError, ValueError) as exc:
            raise ControlError(f"--metadata-json 不是合法 JSON: {exc}", exit_code=2) from exc
        if not isinstance(seed, dict):
            raise ControlError("--metadata-json must be an object", exit_code=2)
        # Inheritance runs before the ordinary birth validator; use its same
        # schema first so malformed contracts keep the controlled error path.
        contract_schema = load_json(SCHEMA_METADATA, "metadata schema")["properties"]["verification_contract"]
        contract_errors = check_schema(seed.get("verification_contract"), contract_schema)
        if contract_errors:
            raise ControlError("invalid birth verification contract", exit_code=2,
                               gate_id="stage_gate", violations=contract_errors)
        stage_module().consolidation_module().require_contract(
                stage_module().ctl(), state["program"], stage_id, seed.get("verification_contract") or {})
        existing = state["program"]["bindings"].get(stage_id)
        if existing is not None and existing["control_root"] != str(out_dir):
            raise ControlError("stage: stage already bound to another ticket", gate_id="stage_gate")
        if args.request_id and (out_dir / METADATA_NAME).is_file():
            current, events = stage_module()._verified(out_dir)
            prior = next((event for event in events if event.get("event_type") == "ticket_created" and event.get("request_id") == args.request_id), None)
            if prior is not None:
                expected_birth = {"stage_id": stage_id, "input_digest": _stage_birth_digest(args)}
                if current["ticket_id"] != args.ticket_id or (prior.get("payload") or {}).get("stage_birth") != expected_birth:
                    raise ControlError("stage: create request_id conflicts with another birth payload", gate_id="idempotency_conflict")
                _bind_created(args.request_id + ":stage-bind")
                print(json.dumps({"ok": True, "already_applied": True, "ticket_id": args.ticket_id,
                    "event_id": prior["event_id"], "out_dir": str(out_dir), "stage_binding": stage_id}, ensure_ascii=False, indent=2))
                return 0
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code = _cmd_create_plain(args)
        result = json.loads(stream.getvalue())
        _bind_created((args.request_id or str(uuid.uuid4())) + ":stage-bind")
        result["stage_binding"] = stage_id
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code


def _find_ui_create_request(output_root, request_id):
    """查找 create-next 的可恢复出生记录；损坏目录不冒充幂等命中。"""
    if not output_root.is_dir():
        return None
    matches = []
    for item in output_root.iterdir():
        if not item.is_dir() or re.fullmatch(r"\.icode_output_[0-9]+", item.name) is None:
            continue
        meta_path = item / METADATA_NAME
        if not meta_path.is_file():
            continue
        try:
            meta = load_json(meta_path, "UI 草稿 metadata")
        except ControlError:
            continue
        ui_ext = (meta.get("extensions") or {}).get("ui") \
            if isinstance(meta.get("extensions"), dict) else None
        if isinstance(ui_ext, dict) and ui_ext.get("draft_request_id") == request_id:
            matches.append((item.resolve(), meta))
    if len(matches) > 1:
        raise ControlError(
            "同一 UI 创建幂等键命中多个工单，拒绝猜测",
            exit_code=1, gate_id="create_next_request_ambiguous")
    return matches[0] if matches else None


def cmd_create_next(args):
    """为 UI 原子分配普通工单编号，出生并写入全局索引。"""
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir() or workspace == Path("/"):
        raise ControlError(
            "workspace 必须是已存在且非系统根目录的工程目录",
            exit_code=2, gate_id="workspace_exists")
    requirement = args.requirement.strip() if isinstance(args.requirement, str) else ""
    if not requirement or len(requirement) > 8000:
        raise ControlError(
            "requirement 必须是 1..8000 字符的非空文本", exit_code=2,
            gate_id="create_next_input")
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", args.request_id or "") is None:
        raise ControlError(
            "request-id 格式非法", exit_code=2, gate_id="create_next_input")

    index_path = Path(args.index).expanduser() if args.index else INDEX_PATH
    index_path.parent.mkdir(parents=True, exist_ok=True)
    output_root = workspace / ".icode_output"
    output_root.mkdir(parents=True, exist_ok=True)

    # 锁顺序固定为 index -> project sequence -> ticket，和 index-write 一致。
    with FileLock(index_path):
        with FileLock(output_root / ".create-next-sequence"):
            index = (load_json(index_path, "全局索引") if index_path.is_file()
                     else {"version": 1, "updated_at": now_iso(), "tickets": []})
            violations = validate_index(index)
            identity = index_identity_violations(index)
            if violations or identity:
                raise ControlError(
                    "现有索引不合法，拒绝创建新工单",
                    exit_code=1, gate_id="index_precheck",
                    violations=violations + [
                        {"gate_id": "index_identity", "detail": item}
                        for item in identity
                    ])

            recovered = _find_ui_create_request(output_root, args.request_id)
            if recovered is not None:
                out_dir, meta = recovered
                if meta.get("requirement") != requirement:
                    raise ControlError(
                        "相同 request-id 对应不同 requirement，拒绝重放",
                        exit_code=1, gate_id="idempotency_conflict")
                indexed_matches = [
                    item for item in index.get("tickets", [])
                    if item.get("ticket_id") == meta.get("ticket_id")
                    and canonical_index_entry_path(item) == out_dir
                ]
                if meta.get("indexed") is not True or len(indexed_matches) != 1:
                    with contextlib.redirect_stdout(io.StringIO()):
                        cmd_index_write(argparse.Namespace(
                            ticket_dir=str(out_dir), index=str(index_path),
                            _index_lock_held=True))
                current = load_metadata(out_dir)
                print(json.dumps({
                    "ok": True,
                    "already_applied": True,
                    "ticket_id": current.get("ticket_id"),
                    "status": current.get("status"),
                    "indexed": current.get("indexed") is True,
                    "out_dir": str(out_dir),
                }, ensure_ascii=False, indent=2))
                return 0

            numbers = []
            for item in output_root.iterdir():
                match = re.fullmatch(r"\.icode_output_([0-9]+)", item.name)
                if match and item.is_dir():
                    numbers.append(int(match.group(1)))
            number = max(numbers, default=0) + 1
            out_dir = output_root / f".icode_output_{number}"
            ticket_id = f"{workspace.name}-{number}"
            if any(item.get("ticket_id") == ticket_id
                   and Path(str(item.get("project_path", ""))).expanduser().resolve() != workspace
                   for item in index.get("tickets", []) if isinstance(item, dict)):
                short_hash = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:4]
                ticket_id = f"{ticket_id}-{short_hash}"

            seed = {
                "requirement_summary": requirement[:100],
                "extensions": {"ui": {"draft_request_id": args.request_id}},
            }
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_create(argparse.Namespace(
                    dir=str(out_dir), ticket_id=ticket_id,
                    requirement=requirement, birth="plan",
                    metadata_json=json.dumps(seed, ensure_ascii=False),
                    request_id=f"{args.request_id}:birth"))
                cmd_index_write(argparse.Namespace(
                    ticket_dir=str(out_dir), index=str(index_path),
                    _index_lock_held=True))
            current = load_metadata(out_dir)
            print(json.dumps({
                "ok": True,
                "already_applied": False,
                "ticket_id": ticket_id,
                "status": current.get("status"),
                "indexed": current.get("indexed") is True,
                "out_dir": str(out_dir),
            }, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args):
    out_dir = Path(args.dir).resolve()
    if args.skip_linters and os.environ.get("ICODE_CONTROL_TEST_MODE") != "1":
        raise ControlError(
            "--skip-linters 仅允许在 ICODE_CONTROL_TEST_MODE=1 的测试夹具中使用",
            exit_code=1, gate_id="production_gate_bypass")
    meta = load_metadata(out_dir)
    violations = []
    warnings = []
    legacy = not is_vnext(meta)

    if legacy:
        warnings.append({
            "gate_id": "legacy_untracked",
            "detail": "legacy 工单（metadata 无 schema_version=3）：metadata 不做严格 schema 校验；"
                      "三 linter 以宽松模式运行（legacy-untracked 不阻断只读审计）。",
            "hint": f"如需接入控制面: python3 tools/icode_control.py migration --dir {out_dir}",
        })
    else:
        violations.extend(validate_against(meta, SCHEMA_METADATA, "metadata_schema"))
        if meta.get("status") == "completed" and meta.get("delivery_verdict") not in DELIVERY_VERDICTS:
            violations.append({
                "gate_id": "delivery_evidence_layer", "path": "$.delivery_verdict",
                "detail": "completed 工单缺显式 delivery_verdict。",
            })
        if meta.get("control_plane_degraded"):
            violations.append({
                "gate_id": "control_plane_degraded",
                "path": "$.control_plane_degraded",
                "detail": "控制面曾降级直写（control_plane_degraded=true）；状态未经 fail-closed 门禁前移，"
                          "需人工复核该工单后清除标志并补齐事件。",
            })

    txn_path = Path(out_dir) / TXN_NAME
    if txn_path.exists():
        violations.append({
            "gate_id": "pending_transaction",
            "path": f"$.{TXN_NAME}",
            "detail": "检测到未完成控制面事务；状态/事件可能处于可恢复中间态。",
            "hint": "修复底层 I/O 问题后重试原变更命令，控制面会先自动恢复；不要手工删除事务文件。",
        })

    # 状态-事件一致性（vNext）
    events, chain_problems = [], []
    if (Path(out_dir) / EVENTS_NAME).exists():
        events, chain_problems = verify_event_chain(out_dir, meta)
        violations.extend({"gate_id": "event_chain", "path": f"$.ico_events.jsonl#{i}",
                           "detail": p} for i, p in enumerate(chain_problems, 1))
        state_events = [dict(e, payload=state_change_payload(e)) for e in events if state_change_payload(e) is not None]
        if not legacy and state_events:
            last = state_events[-1]
            if meta.get("status") != last["payload"].get("to"):
                violations.append({
                    "gate_id": "status_event_consistency",
                    "path": "$.status",
                    "detail": f"metadata.status={meta.get('status')!r} 与最后一条 state_changed 事件"
                              f" to={last['payload'].get('to')!r} 不一致——状态被绕过 transition 直写。",
                    "hint": f"用 transition --dir {out_dir} --to <status> 重放正确状态，"
                            "或人工核对后补 state_changed 事件。",
                })
        close_events = [e for e in events if e.get("event_type") == "close_phase"]
        if not legacy:
            if meta.get("close_state") is not None and not close_events:
                violations.append({
                    "gate_id": "close_event_consistency", "path": "$.close_state",
                    "detail": "metadata.close_state 非空但事件链没有 close_phase；关闭状态疑似被绕过控制面直写。",
                })
            elif close_events and meta.get("close_state") != close_events[-1]["payload"].get("to"):
                violations.append({
                    "gate_id": "close_event_consistency", "path": "$.close_state",
                    "detail": f"metadata.close_state={meta.get('close_state')!r} 与最后 close_phase.to="
                              f"{close_events[-1]['payload'].get('to')!r} 不一致。",
                })

    if not legacy:
        birth_events = [e for e in events if e.get("event_type") == "ticket_created"]
        migration_events = [e for e in events if e.get("event_type") == "migration_applied"]
        if not birth_events and not migration_events:
            violations.append({
                "gate_id": "ticket_birth_event", "path": f"$.{EVENTS_NAME}",
                "detail": "vNext 工单缺 ticket_created（或 legacy 迁移产生的 migration_applied）事件。",
            })
        state_events = [dict(e, payload=state_change_payload(e)) for e in events if state_change_payload(e) is not None]
        birth_states = {"init_in_progress", "log_in_progress", "debug_in_progress", "lite_prepare_in_progress"}
        if not state_events and not migration_events and meta.get("status") not in birth_states:
            violations.append({
                "gate_id": "status_event_consistency", "path": "$.status",
                "detail": f"metadata.status={meta.get('status')!r} 不是出生态但事件链无 state_changed；"
                          "状态疑似在创建时绕过 transition 直写。",
            })
        completed_by_target = load_state_machine(meta)["gate_policy"].get(
            "completed_step_by_target", {})
        completed_steps = set(meta.get("completed_steps") or [])
        event_backed_targets = {}
        for state_event in state_events:
            payload = state_event.get("payload") or {}
            if payload.get("review_return") == 1:
                for invalidated in ("2", "3", "4", "5", "6"):
                    event_backed_targets.pop(invalidated, None)
            if meta.get("mode") == "lite" and payload.get("to") in {"lite_prepare_in_progress", "lite_code_in_progress"}:
                order = workflow_contract(meta)["step_order"]
                cutoff = 0 if payload["to"] == "lite_prepare_in_progress" else 1
                for invalidated in order[cutoff:]:
                    event_backed_targets.pop(invalidated, None)
            target = payload.get("to")
            expected_step = completed_by_target.get(target)
            if expected_step:
                event_backed_targets[expected_step] = target
        event_backed_steps = set(event_backed_targets)
        for expected_step, target in event_backed_targets.items():
            if expected_step not in completed_steps:
                violations.append({
                    "gate_id": "completed_steps_consistency", "path": "$.completed_steps",
                    "detail": f"state_changed 已到 {target!r}，但 completed_steps 缺 {expected_step!r}。",
                })
        unsupported_steps = completed_steps - event_backed_steps - {"0"}
        if unsupported_steps and not migration_events:
            violations.append({
                "gate_id": "completed_steps_consistency", "path": "$.completed_steps",
                "detail": f"completed_steps 含无对应完成态事件的步骤: "
                          f"{sorted(unsupported_steps)}",
            })

    if not legacy and requires_execution_receipts(events):
        step = load_state_machine(meta)["gate_policy"]["step_by_target"].get(meta.get("status"))
        state_events = [dict(e, payload=state_change_payload(e)) for e in events if state_change_payload(e) is not None]
        test_fixture = (os.environ.get("ICODE_CONTROL_TEST_MODE") == "1" and state_events and
                        state_events[-1].get("payload", {}).get("gates_skipped_for_test") is True)
        if step and not test_fixture:
            violations.extend({"gate_id": "step_receipt", "path": "$", "detail": issue}
                              for issue in completion_receipt_issues(out_dir, meta, events, step))

    # 索引指针一致性。测试/离线审计可显式绑定索引，避免误读另一套全局状态。
    index_path = Path(args.index).expanduser() if args.index else INDEX_PATH
    if meta.get("indexed") and index_path.is_file():
        index = load_json(index_path, "全局索引")
        violations.extend(validate_index(index))
        violations.extend({"gate_id": "index_identity", "path": "$.tickets",
                           "detail": item}
                          for item in index_identity_violations(index))
        entries = [e for e in index.get("tickets", []) if e.get("ticket_id") == meta.get("ticket_id")]
        if not entries:
            warnings.append({"gate_id": "index_pointer",
                             "detail": "metadata.indexed=true 但全局索引无对应条目"})
        else:
            want = str(out_dir)
            for e in entries:
                if not isinstance(e.get("project_path"), str) or not isinstance(e.get("out_dir"), str):
                    violations.append({
                        "gate_id": "index_pointer", "path": "$.indexed",
                        "detail": "本工单索引条目缺 project_path/out_dir，无法解析身份指针"})
                    continue
                have = str((Path(e["project_path"]) / e["out_dir"]).resolve())
                indexed_archive = Path(e["archive_path"]).resolve() \
                    if e.get("archive_path") else None
                if have != want and indexed_archive != out_dir:
                    violations.append({
                        "gate_id": "index_pointer", "path": "$.indexed",
                        "detail": f"索引条目 out_dir 指向 {have}，与当前工单目录 {want} 不一致"})
    elif meta.get("indexed"):
        warnings.append({"gate_id": "index_pointer",
                         "detail": f"metadata.indexed=true 但索引文件不存在: {index_path}"})

    # 三 linter：vNext 一律 --strict（fail-closed）；legacy 宽松（不阻断只读审计）
    if not args.skip_linters:
        for report in run_gate_linters(out_dir, legacy=legacy, metadata=meta):
            if report["ok"]:
                continue
            entry = {
                "gate_id": report["gate_id"], "path": "$",
                "returncode": report.get("returncode"),
                "detail": report.get("detail") or json.dumps(
                    report.get("report", {}), ensure_ascii=False)[-800:],
                "hint": report.get("hint"),
            }
            (violations if not legacy else warnings).append(entry)

    result = {
        "ok": len(violations) == 0,
        "mode": "legacy_readonly" if legacy else "vnext_strict",
        "out_dir": str(out_dir),
        "ticket_id": meta.get("ticket_id"),
        "status": meta.get("status"),
        "close_state": meta.get("close_state"),
        "delivery_verdict": meta.get("delivery_verdict"),
        "event_count": len(events),
        "violations": violations,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def cmd_event(args):
    out_dir = Path(args.dir).resolve()
    if args.type in CONTROL_EVENT_TYPES:
        raise ControlError(
            f"事件类型 {args.type!r} 由专用控制面命令独占，禁止通过 event 伪造",
            exit_code=2, gate_id="reserved_event_type",
            hint="使用 create/transition/metadata-update/record-verification/snapshot/"
                 "close-phase/reopen/index-write/migration")
    try:
        payload = strict_json_loads(args.payload) if args.payload else {}
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError(f"--payload 不是合法 JSON: {exc}", exit_code=2)
    if not isinstance(payload, dict):
        raise ControlError("--payload 必须是 JSON 对象", exit_code=2)
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止追加通用业务事件；需修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝追加事件", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(events, args.request_id, args.type, payload, args.actor)
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id,
                              "event_id": prior["event_id"],
                              "event_hash": prior["event_hash"],
                              "event_type": prior["event_type"]},
                             ensure_ascii=False, indent=2))
            return 0
        event = append_event(out_dir, meta, args.type, payload,
                             request_id=args.request_id, actor=args.actor)
    print(json.dumps({"ok": True, "event_id": event["event_id"],
                      "event_hash": event["event_hash"], "event_type": event["event_type"]},
                     ensure_ascii=False, indent=2))
    return 0


def workflow_revision(meta, events, stage):
    """One revision contract for action previews and controlled review return."""
    base = {"ticket_id": meta.get("ticket_id"), "metadata_hash": metadata_hash(meta),
            "last_event_hash": events[-1]["event_hash"] if events else GENESIS_HASH,
            "event_count": len(events), "stage_revision": stage.get("revision")}
    return {**base, "token": canonical_digest(base)}


def review_return_payload_ok(payload):
    reason = payload.get("reason")
    token = payload.get("expected_revision")
    return (type(payload.get("review_return")) is int and payload["review_return"] == 1
            and isinstance(reason, str) and 0 < len(reason.strip()) <= 2048
            and isinstance(token, str) and re.fullmatch(r"[0-9a-f]{64}", token) is not None)


def review_return_problem(meta, events, stage):
    """Fail closed on legacy unpaired starts as well as current execution."""
    if meta.get("close_state") is not None:
        return "closed_ticket_status_frozen"
    mirror = ExecutionBindingMirror(meta)
    for event in events:
        mirror.consume(event)
    if mirror.open_steps or mirror.open_operations or mirror.open_agents or mirror.closed:
        return "review_return_quiescence"
    if stage.get("enabled") and (stage.get("blocked") or not stage.get("stage_id")
                                  or stage.get("state") == "accepted"):
        return "stage_gate"
    return None


def cmd_transition(args):
    out_dir = Path(args.dir).resolve()
    stage_action = _stage_write_action("state_changed", {"to": args.to})
    sm = load_state_machine(load_metadata(out_dir))
    to_status = args.to
    expected_revision = getattr(args, "expected_revision", None)
    reason = getattr(args, "reason", None)
    return_request = {"to": to_status, "delivery_verdict": args.delivery_verdict,
                      "review_return": 1 if expected_revision is not None or reason is not None else None,
                      "expected_revision": expected_revision, "reason": reason}
    request_keys = tuple(return_request)
    if to_status not in sm["states"]:
        raise ControlError(f"未知目标状态 {to_status!r}，合法状态: {sm['states']}", exit_code=2)
    if args.delivery_verdict and args.delivery_verdict not in DELIVERY_VERDICTS:
        raise ControlError(f"--delivery-verdict 必须 ∈ {DELIVERY_VERDICTS}", exit_code=2)
    if args.delivery_verdict and to_status != "completed":
        raise ControlError("--delivery-verdict 只允许与 --to completed 一起使用",
                           exit_code=2, gate_id="delivery_verdict_target")
    if to_status == "completed" and not args.delivery_verdict:
        raise ControlError(
            "transition --to completed 必须显式携带 --delivery-verdict（交付分层契约："
            "宿主验证通过不得自动映射为 verified）",
            exit_code=1, gate_id="delivery_evidence_layer",
            hint="--delivery-verdict ∈ {verified, verification_pending, blocked, not_applicable}")
    if args.skip_gates and os.environ.get("ICODE_CONTROL_TEST_MODE") != "1":
        raise ControlError(
            "--skip-gates 仅允许在 ICODE_CONTROL_TEST_MODE=1 的测试夹具中使用",
            exit_code=1, gate_id="production_gate_bypass")

    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        # Legacy migration diagnostics precede stage admission for every target;
        # the locked read below is still the authoritative transition baseline.
        if stage_action is not None:
            require_stage_action(out_dir, stage_action)
        if meta.get("close_state") is not None:
            raise ControlError(
                f"工单已进入关闭流程 close_state={meta.get('close_state')!r}，禁止再改变 status；"
                "关闭后修改必须先按 /icode worktree --reopen 建立新活动 checkout",
                exit_code=1, gate_id="closed_ticket_status_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，状态禁止前移", exit_code=1,
                               gate_id="event_chain", violations=problems)
        current = meta.get("status")
        baseline_metadata_hash = metadata_hash(meta)
        baseline_event_hash = events[-1]["event_hash"] if events else GENESIS_HASH
        trans = legal_transition(sm, current, to_status)
        prior = find_idempotent_event(
            events, args.request_id, "state_changed",
            return_request, payload_keys=request_keys)
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "to": to_status,
                              "at": prior["timestamp"], "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
            return 0

        is_review_return = bool(trans and (trans.get("review_return") or
                                           (current == "completed" and to_status == "review_in_progress")))
        if is_review_return:
            if not review_return_payload_ok(return_request) or not args.request_id:
                raise ControlError("返回审查需要当前 revision、明确原因和 request-id",
                                   gate_id="review_return_request")
            stage = require_stage_action(out_dir, "review")
            problem = review_return_problem(meta, events, stage)
            if problem:
                raise ControlError("返回审查要求未接受、未关闭且执行静止的工单", gate_id=problem)
            trusted_execution_workspace(out_dir, meta)
            actual = workflow_revision(meta, events, stage)["token"]
            if expected_revision != actual:
                raise ControlError("审查恢复预览已过期", gate_id="revision_mismatch",
                                   expected_revision=expected_revision, actual_revision=actual)
        elif expected_revision is not None or reason is not None:
            raise ControlError("revision/reason 仅用于受控返回审查", gate_id="review_return_request")

        # Only historical births may be untracked. New births explicitly opt in
        # through the immutable event, so omitting start cannot bypass receipts.
        if meta.get("mode") == "lite":
            sm = load_state_machine(meta)
            lite_transition_guard(out_dir, meta, events, to_status, args.delivery_verdict)
        tracked_step = sm["gate_policy"].get("step_by_target", {}).get(to_status)
        if tracked_step:
            starts = [event for event in events
                      if event.get("event_type") == "step_started"
                      and (event.get("payload") or {}).get("execution_model_version") == 1
                      and (event.get("payload") or {}).get("step") == tracked_step]
            if starts or (requires_execution_receipts(events) and not args.skip_gates):
                receipt_issues = completion_receipt_issues(out_dir, meta, events, tracked_step)
                if receipt_issues:
                    raise ControlError("步骤完成回执或当前产物不完整", exit_code=1,
                                       gate_id="step_receipt", violations=receipt_issues)
        open_side_effects = []
        for event in events:
            if event.get("event_type") != "operation_started":
                continue
            payload = event.get("payload") or {}
            if payload.get("execution_model_version") != 1 or payload.get("class") == "read_only":
                continue
            if operation_finished(events, payload.get("attempt")) is None:
                open_side_effects.append({"name": payload.get("name"),
                                          "attempt": payload.get("attempt"),
                                          "class": payload.get("class")})
        if open_side_effects:
            raise ControlError(
                "存在无终结回执的有副作用长动作，禁止推进完成态", exit_code=1,
                gate_id="operation_receipt", open_operations=open_side_effects)

    if trans is None:
        allowed = next_transitions(sm, current)
        raise ControlError(
            f"非法状态流转 {current!r} → {to_status!r}（状态机 fail-closed，状态未前移）",
            exit_code=1, gate_id="state_machine", expected_from=current, attempted_to=to_status,
            legal_next=allowed,
            hint=f"合法下一状态: {allowed}；如有特殊需要先人工核对状态机（gates.json state_machine）")

    transition_payload = {
        "from": current, "to": to_status,
        "delivery_verdict": args.delivery_verdict,
        "note": trans.get("note"),
    }
    if is_review_return:
        transition_payload.update({k: return_request[k] for k in
                                   ("review_return", "expected_revision", "reason")})
    if args.skip_gates:
        transition_payload["gates_skipped_for_test"] = True

    # 门禁：完成态跑三 linter（debug 孪生跳过）；completed 强制显式 delivery_verdict
    gate_reports = []
    gates_skipped = bool(args.skip_gates)
    # 工单级未解决债务余额：跨步骤按当前有效 trace 汇总，不随被推进步骤变化；
    # 对债务 gate 真实重做（最后一条不再携带债务状态）即清偿归零。
    enumeration_debt_balance = ticket_enumeration_debt(out_dir, meta)
    if to_status == "completed" and args.delivery_verdict == "verified" \
            and meta.get("mode") != "lite" and enumeration_debt_balance != 0:
        raise ControlError("枚举或去重证据仍待核验，交付结论不能为 verified",
                           gate_id="gate_enumeration_debt",
                           enumeration_debt=enumeration_debt_balance)
    if to_status in sm["gate_policy"]["gated_targets"] and not meta.get("debug") and not gates_skipped:
        gate_reports = run_gate_linters(out_dir, to_status, args.delivery_verdict, metadata=meta)
        failed = [g for g in gate_reports if not g["ok"]]
        if failed:
            print(json.dumps({
                "ok": False, "fail_closed": True, "status": current,
                "error": f"门禁未通过，状态不前移（{current!r} → {to_status!r} 被拒绝）",
                "failed_gates": [{"gate_id": g["gate_id"],
                                  "enumeration_debt": g.get("enumeration_debt"),
                                  "detail": g.get("detail") or
                                  json.dumps(g.get("report", {}), ensure_ascii=False)[:600],
                                  "hint": g.get("hint")} for g in failed],
                "gate_reports": gate_reports,
            }, ensure_ascii=False, indent=2))
            return 1
    # 锁内 CAS：门禁期间可能有其他写入，因此再次重读并校验。
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta2 = load_metadata(out_dir)
        require_vnext(meta2, out_dir)
        require_valid_metadata(meta2)
        raw_events2, _ = read_events(out_dir)
        current_event_hash = raw_events2[-1].get("event_hash") if raw_events2 else GENESIS_HASH
        if (metadata_hash(meta2) != baseline_metadata_hash
                or current_event_hash != baseline_event_hash):
            raise ControlError(
                "门禁运行期间 metadata 或事件链已变化；为避免用旧输入的"
                "linter 结果推进新状态，本次流转放弃，请重试",
                exit_code=1, gate_id="concurrent_write",
                expected_status=current, actual_status=meta2.get("status"),
                metadata_changed=metadata_hash(meta2) != baseline_metadata_hash,
                event_chain_changed=current_event_hash != baseline_event_hash)
        events2, problems = verify_event_chain(out_dir, meta2)
        if problems:
            raise ControlError("现有事件链不完整，状态禁止前移", exit_code=1,
                               gate_id="event_chain", violations=problems)
        if meta2.get("mode") == "lite":
            lite_transition_guard(out_dir, meta2, events2, to_status, args.delivery_verdict)
        if tracked_step and requires_execution_receipts(events2) and not args.skip_gates:
            receipt_issues = completion_receipt_issues(out_dir, meta2, events2, tracked_step)
            if receipt_issues:
                raise ControlError("门禁执行期间产物身份变化", gate_id="step_receipt",
                                   violations=receipt_issues)
        prior = find_idempotent_event(
            events2, args.request_id, "state_changed",
            return_request, payload_keys=request_keys)
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "to": to_status,
                              "at": prior["timestamp"], "event_id": prior["event_id"]},
                                 ensure_ascii=False, indent=2))
            return 0
        before_meta = dict(meta2)
        if is_review_return:
            stage = require_stage_action(out_dir, "review")
            problem = review_return_problem(meta2, events2, stage)
            if problem:
                raise ControlError("审查恢复发布前执行资格改变", gate_id=problem)
            trusted_execution_workspace(out_dir, meta2)
            if workflow_revision(meta2, events2, stage)["token"] != expected_revision:
                raise ControlError("审查恢复发布前 revision 改变", gate_id="revision_mismatch")
            meta2["completed_steps"] = [s for s in meta2.get("completed_steps", [])
                                        if s not in {"2", "3", "4", "5", "6"}]
            meta2.update(delivery_verdict="verification_pending", verdict="unknown",
                         total_rounds=1, clean_rounds=0, extended_rounds=0)
        if meta2.get("mode") == "lite" and to_status in {"lite_prepare_in_progress", "lite_code_in_progress"}:
            order = workflow_contract(meta2)["step_order"]
            cutoff = 0 if to_status == "lite_prepare_in_progress" else 1
            meta2["completed_steps"] = [x for x in meta2.get("completed_steps", []) if x in order[:cutoff]]
            meta2["delivery_verdict"] = "verification_pending"
            # 进入 Code 时把已核验的宿主适配器身份冻结进工单：签收阶段必须仍使用
            # 同一适配器，防止实施期间信任根被换成"只回显"的程序。回退到 Prepare
            # 不解除冻结，重新进入 Code 时按当前宿主信任根重新确认并再次冻结。
            if to_status == "lite_code_in_progress":
                extensions = dict(meta2.get("extensions") or {})
                lite_ns = dict(extensions.get("lite") or {})
                observation = _observe_host_adapter()
                observation.pop("config_path", None)
                timestamp = "pinned_at" if observation["status"] == "pinned" else "observed_at"
                lite_ns["host_adapter"] = {**observation, timestamp: now_iso()}
                extensions["lite"] = lite_ns
                meta2["extensions"] = extensions
        meta2["status"] = to_status
        if current == "completed" and to_status.endswith("_in_progress"):
            # 终态被重开后，旧的现场交付结论不再能表示新一轮已完成。
            meta2["delivery_verdict"] = "verification_pending"
        completed_step = sm["gate_policy"].get("completed_step_by_target", {}).get(to_status)
        if completed_step:
            completed_steps = list(meta2.get("completed_steps") or [])
            if completed_step not in completed_steps:
                completed_steps.append(completed_step)
            meta2["completed_steps"] = completed_steps
        if args.delivery_verdict:
            meta2["delivery_verdict"] = args.delivery_verdict
        # 债务余额无条件进入 state_changed 事件 payload（可持续追踪）与状态推进
        # 输出：门禁通过不代表债务已解决，0 显式证明清偿，null 表示 trace 不可读
        # 无法判定（不得当作清偿），交付结论必须可见。
        transition_payload["gate_enumeration_debt"] = enumeration_debt_balance
        transition_payload["gate_enumeration_debt_unknown"] = enumeration_debt_balance is None
        event = commit_metadata_and_event(
            out_dir, before_meta, meta2, "state_changed", transition_payload,
            request_id=args.request_id)
    # 状态已提交：同步全局索引中的 status，避免检索侧读到过期状态。
    # 索引刷新失败只降级为提示，不回滚状态转换（索引可由 index-write 重建）。
    index_refresh = "skipped"
    if meta2.get("indexed") and not meta2.get("debug"):
        try:
            index_refresh = refresh_index_status(out_dir, meta2)
        except Exception as exc:  # noqa: BLE001 - 索引刷新绝不影响状态流转
            index_refresh = f"failed:{type(exc).__name__}"
    print(json.dumps({"ok": True, "from": current, "to": to_status,
                      "delivery_verdict": args.delivery_verdict,
                      "gates_skipped": gates_skipped,
                      "enumeration_debt": enumeration_debt_balance,
                      "enumeration_debt_unknown": enumeration_debt_balance is None,
                      "event_id": event["event_id"],
                      "index_refresh": index_refresh,
                      "gates": [{g["gate_id"]: g["ok"]} for g in gate_reports]},
                     ensure_ascii=False, indent=2))
    return 0


def refresh_index_status(out_dir, meta, index_path=None):
    """状态流转成功后，把该工单在全局索引中的 status 同步为最新值。

    背景：索引是检索侧真源之一，历史检索会按 status 过滤/展示。此前只有
    `index-write` 会写索引，`transition` 之后的每次状态变更都不回流，导致
    已完成工单在索引里长期停留在 `init_in_progress`（实测 demo-17）。

    实现边界（刻意保守，避免与 index-write 的单一 writer 契约冲突）：
    - 只更新 `status` / `delivery_verdict` / `updated_at` 三个派生字段；
      身份三元组（ticket_id / project_path / out_dir）与其它已登记字段不动。
    - 索引不存在 / 工单未入索引 / 值已一致 → 幂等跳过，不抛错。
    - 复用 FileLock 串行化 + 临时文件 fsync + 原子 rename。
    - 调用方必须容忍失败：索引可由 `index-write` 重建，绝不因索引刷新失败
      回滚已经提交的状态转换。
    """
    ticket_id = meta.get("ticket_id")
    path = Path(index_path) if index_path else INDEX_PATH
    if not ticket_id or not path.is_file():
        return "skipped"
    with FileLock(path):
        index = load_json(path, "全局索引")
        tickets = index.get("tickets")
        if not isinstance(tickets, list):
            return "skipped"
        target = None
        for item in tickets:
            if isinstance(item, dict) and item.get("ticket_id") == ticket_id:
                target = item
                break
        if target is None:
            return "not_indexed"
        new_status = meta.get("status")
        new_verdict = meta.get("delivery_verdict")
        if (target.get("status") == new_status
                and target.get("delivery_verdict") == new_verdict):
            return "up_to_date"
        target["status"] = new_status
        if new_verdict:
            target["delivery_verdict"] = new_verdict
        index["updated_at"] = now_iso()
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(index, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return "updated"


def serialize_index_out_dir(relative):
    """索引 wire 格式固定使用正斜杠，独立于宿主系统路径分隔符。"""
    return relative.as_posix()


def build_index_entry(out_dir, meta, identity=None):
    out_dir = Path(out_dir).resolve()
    if identity is None:
        workspace = containing_workspace(out_dir)
        rel = out_dir.relative_to(workspace)
    else:
        workspace = Path(identity["project_path"])
        rel = Path(identity["out_dir"])
    entry = {
        "control_schema_version": 3,
        "ticket_id": meta.get("ticket_id"),
        "project_path": str(workspace),
        "out_dir": serialize_index_out_dir(rel),
        "status": meta.get("status"),
        "updated_at": now_iso(),
        "created_at": meta.get("created_at"),
        "has_00_init": (out_dir / "00_init.md").is_file(),
        "has_plan": (out_dir / "01_plan.md").is_file() or "lite_prepare" in (meta.get("completed_steps") or []),
        "requirement_summary": meta.get("requirement_summary") or (meta.get("requirement") or "")[:80],
        "requirement_points": meta.get("requirement_points"),
        "keywords": meta.get("keywords"),
        "verdict": meta.get("verdict"),
        "delivery_verdict": meta.get("delivery_verdict"),
        "workload_estimate": meta.get("workload_estimate"),
        "workload_reason": meta.get("workload_reason"),
        "archive_path": meta.get("archive_path"),
        "backup_path": meta.get("backup_path"),
        "tb_source": ({key: meta.get("tb_source", {}).get(key)
                       for key in ("lib", "num", "pid", "label")
                       if meta.get("tb_source", {}).get(key) is not None}
                      if isinstance(meta.get("tb_source"), dict) else None),
    }
    if meta.get("mode") == "lite":
        entry.update(mode="lite", workflow_version=meta["workflow_version"])
    return {k: v for k, v in entry.items() if v is not None or k in
            ("ticket_id", "project_path", "out_dir", "status")}


def canonical_index_entry_path(item):
    """返回 vNext 精确相对工单目录；legacy 粗粒度/绝对 out_dir 返回 None。"""
    if not isinstance(item, dict):
        return None
    project_path = item.get("project_path")
    out_dir = item.get("out_dir")
    if not isinstance(project_path, str) or not isinstance(out_dir, str):
        return None
    if re.fullmatch(r"\.icode_output/(?:\.debug/)?\.icode_output_[0-9]+", out_dir) is None:
        return None
    return (Path(project_path) / out_dir).resolve()


def index_identity_violations(index):
    """检查 ticket_id 与精确工单目录唯一性；legacy 粗粒度目录不冒充精确身份。"""
    problems = []
    ticket_positions = {}
    directory_positions = {}
    for pos, item in enumerate(index.get("tickets", [])):
        if not isinstance(item, dict):
            continue
        ticket_id = item.get("ticket_id")
        if isinstance(ticket_id, str):
            ticket_positions.setdefault(ticket_id, []).append(pos)
        # 历史索引曾复用编号目录；只对控制面接管后的精确身份做目录唯一性约束。
        # legacy 仍按 ticket_id 保证可解析，不能让既有重复路径阻断整个索引升级。
        identity_path = (canonical_index_entry_path(item)
                         if item.get("control_schema_version") == 3 else None)
        if identity_path is not None:
            key = str(identity_path)
            directory_positions.setdefault(key, []).append(pos)
    for ticket_id, positions in ticket_positions.items():
        if len(positions) > 1:
            problems.append(f"ticket_id={ticket_id!r} 重复于索引位置 {positions}")
    for path, positions in directory_positions.items():
        if len(positions) > 1:
            problems.append(f"工单目录 {path!r} 重复于索引位置 {positions}")
    return problems


def maintain_index_tickets(tickets):
    """执行无外部 I/O 的确定性索引维护：僵尸超时、价值排序、LRU 目标容量。"""
    now = datetime.now()
    in_progress = {
        "init_in_progress", "log_in_progress", "review_in_progress",
        "deepcheck_in_progress", "code_in_progress", "debug_in_progress",
    }
    normal_evictable = {
        "completed", "code_done", "review_done", "deepcheck_done",
        "plan_done", "plan_finalized", "log_done", "debug_done",
    }
    maintained = []
    for source in tickets:
        item = dict(source)
        stamp = item.get("last_used_at") or item.get("updated_at") or item.get("created_at")
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            parsed = None
        if item.get("status") in in_progress and parsed and now - parsed > timedelta(days=30):
            item.update({"stale": True, "stale_reason": "timeout"})
        maintained.append(item)

    verdict_priority = {"verified": 3, "unknown": 2, "superseded": 1, "disproved": 0}
    maintained.sort(
        key=lambda item: (
            verdict_priority.get(item.get("verdict") or "unknown", 2),
            int(item.get("hit_count") or 0),
            str(item.get("last_used_at") or item.get("updated_at") or ""),
        ), reverse=True)
    while len(maintained) > 200:
        candidates = [
            (idx, item) for idx, item in enumerate(maintained)
            if not (int(item.get("hit_count") or 0) >= 20
                    and item.get("verdict") != "disproved")
            and ((not item.get("stale") and item.get("status") in normal_evictable)
                 or (item.get("stale") and item.get("stale_reason") == "timeout"))
        ]
        if not candidates:
            break
        idx, _ = min(candidates, key=lambda pair: str(
            pair[1].get("last_used_at") or pair[1].get("updated_at") or ""))
        maintained.pop(idx)
    return maintained


def cmd_index_write(args):
    out_dir = Path(args.ticket_dir).resolve()
    index_path = Path(args.index) if args.index else INDEX_PATH
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_lock = (contextlib.nullcontext() if getattr(args, "_index_lock_held", False)
                  else FileLock(index_path))
    with index_lock:
        with DirLock(out_dir):
            recover_pending_transaction(out_dir)
            meta = load_metadata(out_dir)
            require_vnext(meta, out_dir)
            require_valid_metadata(meta)
            _stage_retirement_guard(out_dir)
            if meta.get("debug"):
                raise ControlError("debug 孪生工单走 .debug 隔离域，禁止写入全局索引", exit_code=1,
                                   gate_id="debug_isolation")

            # 索引锁内重读合并，保留顶层扩展字段和同 ticket 历史字段。
            index_existed = index_path.is_file()
            index = (load_json(index_path, "全局索引") if index_existed
                     else {"version": 1, "updated_at": now_iso(), "tickets": []})
            index_violations = validate_index(index)
            identity_violations = index_identity_violations(index)
            if index_violations or identity_violations:
                raise ControlError(
                    "现有索引不合法，拒绝在损坏基线上合并",
                    exit_code=1, gate_id="index_precheck",
                    violations=index_violations + [
                        {"gate_id": "index_identity", "detail": item}
                        for item in identity_violations
                    ])
            archive_path = Path(meta["archive_path"]).resolve() \
                if meta.get("archive_path") else None
            archive_mode = archive_path == out_dir
            identity = None
            if archive_mode:
                identity_matches = [item for item in index.get("tickets", [])
                                    if item.get("ticket_id") == meta.get("ticket_id")]
                if len(identity_matches) != 1:
                    raise ControlError(
                        "归档控制根写索引前，原工单必须已有唯一索引身份条目",
                        exit_code=1, gate_id="archive_index_identity",
                        hint="在 archived 交接前先对源工单运行 index-write")
                identity = identity_matches[0]
            entry = build_index_entry(out_dir, meta, identity=identity)
            existing_matches = [item for item in index.get("tickets", [])
                                if item.get("ticket_id") == entry["ticket_id"]]
            existing = existing_matches[0] if existing_matches else None
            if existing:
                existing_dir = canonical_index_entry_path(existing)
                legacy_same_project = (
                    existing.get("control_schema_version") is None
                    and isinstance(existing.get("project_path"), str)
                    and Path(existing["project_path"]).resolve() == containing_workspace(out_dir))
                if (existing_dir != out_dir and not archive_mode and not legacy_same_project):
                    raise ControlError(
                        f"ticket_id={entry['ticket_id']!r} 已归属另一目录 {existing_dir}，"
                        "拒绝覆盖；请按 project_path 短 hash 规则生成唯一 ID",
                        exit_code=1, gate_id="ticket_id_ownership",
                        existing=existing, attempted_out_dir=str(out_dir))
            # legacy 同 ticket 首次由 vNext 接管时，只保留未知扩展字段；已登记字段由
            # metadata/受控默认值重新投影，避免历史错误类型被静默带入严格条目。
            strict_fields = set(load_json(
                SCHEMA_INDEX, "索引 schema")["definitions"]["vnextEntry"]["properties"])
            merged_entry = {key: value for key, value in (existing or {}).items()
                            if key not in strict_fields}
            merged_entry.update(entry)
            prior_last_used = (existing or {}).get("last_used_at")
            prior_hit_count = (existing or {}).get("hit_count")
            prior_stale = (existing or {}).get("stale")
            prior_stale_reason = (existing or {}).get("stale_reason")
            prior_checked_commit = (existing or {}).get("stale_checked_commit")
            merged_entry["last_used_at"] = (prior_last_used if isinstance(prior_last_used, str)
                                             else meta.get("created_at") or now_iso())
            merged_entry["hit_count"] = (prior_hit_count
                                         if isinstance(prior_hit_count, int)
                                         and not isinstance(prior_hit_count, bool)
                                         and prior_hit_count >= 0 else 0)
            merged_entry["stale"] = prior_stale if isinstance(prior_stale, bool) else False
            merged_entry["stale_reason"] = (prior_stale_reason
                                            if isinstance(prior_stale_reason, str) else None)
            merged_entry["stale_checked_commit"] = (prior_checked_commit
                                                     if isinstance(prior_checked_commit, str) else None)
            tickets = [item for item in index.get("tickets", [])
                       if item.get("ticket_id") != entry["ticket_id"]]

            clash = [item for item in tickets
                     if item.get("control_schema_version") == 3
                     and canonical_index_entry_path(item) == out_dir
                     and item.get("ticket_id") != entry["ticket_id"]]
            if clash:
                raise ControlError(
                    f"out_dir {out_dir} 已被 ticket_id={clash[0].get('ticket_id')!r} "
                    "占用（目录误复用），拒绝写入",
                    exit_code=1, gate_id="out_dir_ownership", existing=clash[0])
            tickets.append(merged_entry)
            tickets = maintain_index_tickets(tickets)
            new_index = dict(index)
            new_index.update({"version": 1,
                              "updated_at": now_iso(), "tickets": tickets})
            violations = validate_index(new_index)
            identity_violations = index_identity_violations(new_index)
            if violations or identity_violations:
                raise ControlError("索引合并结果未通过 schema 校验，拒绝落盘",
                                   exit_code=1, violations=violations + [
                                       {"gate_id": "index_identity", "detail": item}
                                       for item in identity_violations
                                   ])
            atomic_write_json(index_path, new_index)

            reread = load_json(index_path, "全局索引（写后）")
            mine = [item for item in reread.get("tickets", [])
                    if item.get("ticket_id") == entry["ticket_id"]]
            if len(mine) != 1 or mine[0].get("out_dir") != entry["out_dir"]:
                raise ControlError("写后验证失败：条目未唯一落盘或内容不符", exit_code=1,
                                   gate_id="index_postcheck", written=mine)

            before_meta = dict(meta)
            meta["indexed"] = True
            try:
                commit_metadata_and_event(
                    out_dir, before_meta, meta, "index_updated",
                    {"out_dir": entry["out_dir"], "status": entry["status"],
                     "index": str(index_path)})
            except BaseException:
                if index_existed:
                    atomic_write_json(index_path, index)
                else:
                    index_path.unlink(missing_ok=True)
                raise
            if archive_mode and meta.get("close_state") is not None:
                refresh_archive_manifest_control_files(out_dir, meta.get("ticket_id"))
    print(json.dumps({"ok": True, "index": str(index_path), "entry": entry},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_index_update(args):
    """更新索引独有的检索/LRU 字段；禁止改写工单身份三元组。"""
    index_path = Path(args.index) if args.index else INDEX_PATH
    try:
        updates = strict_json_loads(args.set_json) if args.set_json else {}
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError(f"--set-json 不是合法 JSON: {exc}", exit_code=2)
    if not isinstance(updates, dict):
        raise ControlError("--set-json 必须是 JSON 对象", exit_code=2)
    protected = {"ticket_id", "project_path", "out_dir"}.intersection(updates)
    if protected:
        raise ControlError(f"索引身份字段禁止通过 index-update 修改: {sorted(protected)}",
                           exit_code=2, gate_id="index_identity_immutable")
    allowed = {"stale", "stale_reason", "stale_checked_commit",
               "verdict_at", "verdict_review_needed"}
    unknown = set(updates) - allowed
    if unknown:
        raise ControlError(
            f"index-update 含未登记或非索引独有字段: {sorted(unknown)}",
            exit_code=2, gate_id="index_update_field_allowlist",
            allowed=sorted(allowed))
    if not updates and not args.increment_hit:
        raise ControlError("index-update 需 --set-json 或 --increment-hit", exit_code=2)
    with FileLock(index_path):
        index = load_json(index_path, "全局索引")
        index_violations = validate_index(index)
        identity_violations = index_identity_violations(index)
        if index_violations or identity_violations:
            raise ControlError(
                "现有索引不合法，拒绝更新",
                exit_code=1, gate_id="index_precheck",
                violations=index_violations + [
                    {"gate_id": "index_identity", "detail": item}
                    for item in identity_violations
                ])
        positions = [i for i, item in enumerate(index.get("tickets", []))
                     if item.get("ticket_id") == args.ticket_id]
        if len(positions) != 1:
            raise ControlError(
                f"ticket_id={args.ticket_id!r} 在索引中命中 {len(positions)} 条，拒绝更新",
                exit_code=1, gate_id="index_ticket_ambiguity")
        new_index = dict(index)
        tickets = list(index["tickets"])
        entry = dict(tickets[positions[0]])
        entry.update(updates)
        if args.increment_hit:
            entry["hit_count"] = int(entry.get("hit_count") or 0) + 1
            entry["last_used_at"] = now_iso()
        entry["updated_at"] = now_iso()
        tickets[positions[0]] = entry
        tickets = maintain_index_tickets(tickets)
        new_index.update({"updated_at": now_iso(), "tickets": tickets})
        violations = validate_index(new_index)
        identity_violations = index_identity_violations(new_index)
        if violations or identity_violations:
            raise ControlError("索引更新未通过 schema，拒绝落盘", exit_code=1,
                               violations=violations + [
                                   {"gate_id": "index_identity", "detail": item}
                                   for item in identity_violations
                               ])
        atomic_write_json(index_path, new_index)
    print(json.dumps({"ok": True, "index": str(index_path), "entry": entry},
                     ensure_ascii=False, indent=2))
    return 0


def required_archive_files(meta, events=()):
    required = {METADATA_NAME, EVENTS_NAME}
    if meta.get("mode") == "lite":
        required.add("lite.md")
        for review in lite_review_records(events)[1]:
            if review.get("report_path"):
                required.add(review["report_path"])
        return required
    step_files = {
        "0": ["00_init.md"],
        "log": ["00_init.md", "log_analysis.md"],
        "1": ["01_plan.md"],
        "2": ["02_review.md"],
        "3": ["03_plan_final.md"],
        "4": ["04_code_review_fix.md"],
        "5": ["05_deepcheck.md"],
        "6": ["06_audit.md"],
    }
    for step in meta.get("completed_steps") or []:
        required.update(step_files.get(step, []))
    if set(meta.get("completed_steps") or []) - {"0"}:
        required.update({".thinking_gate_trace.jsonl", ".mcp_gate_trace.jsonl"})
    if meta.get("anchors_enabled"):
        required.add(".decision_anchors.json")
    # Persist new execution evidence even if someone removed the sidecar before
    # archive. Historical tickets without these receipts are not retrofitted.
    for event in events:
        payload = event.get("payload") or {}
        if event.get("event_type") == "artifact_written" and payload.get("output") == "inspection_worklist" \
                and payload.get("inspection_declaration_hash") and payload.get("step") in ("code", "deepcheck", "audit"):
            required.add(payload["step"] + "_worklist.json")
    return required


def definition_store_entries(root):
    """Relative POSIX paths of every content-addressed definition side file."""
    store = Path(root) / DEFINITION_STORE_DIR
    if not store.is_dir():
        return set()
    return {item.relative_to(root).as_posix() for item in store.rglob("*.json")
            if item.is_file() and not item.is_symlink()}


def mirror_definition_store(source_dir, archive_dir):
    """Copy definitions the archive root is missing; files are immutable.

    Content addressing makes the copy idempotent: an existing file is never
    rewritten, so a torn or tampered copy cannot be silently replaced here.
    """
    source_root = Path(source_dir) / DEFINITION_STORE_DIR / "definitions"
    if not source_root.is_dir():
        return
    for item in sorted(source_root.rglob("*.json")):
        if not item.is_file() or item.is_symlink():
            continue
        target = Path(archive_dir) / DEFINITION_STORE_DIR / "definitions" / item.relative_to(source_root)
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, item.read_bytes())


def verify_archive_manifest(archive_dir, ticket_id, source_dir=None):
    archive_dir = Path(archive_dir).resolve()
    manifest_path = archive_dir / ARCHIVE_MANIFEST_NAME
    manifest = load_json(manifest_path, "归档 manifest")
    problems = []
    if manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
        problems.append("manifest 未标记 schema_version=1 + complete=true")
    if manifest.get("ticket_id") != ticket_id:
        problems.append(
            f"manifest.ticket_id={manifest.get('ticket_id')!r} 与工单 {ticket_id!r} 不一致")
    if manifest.get("archive_dir"):
        if Path(manifest["archive_dir"]).resolve() != archive_dir:
            problems.append("manifest.archive_dir 与当前归档目录不一致")
    else:
        problems.append("manifest 缺 archive_dir")
    if source_dir is not None:
        source_dir = Path(source_dir).resolve()
        if not manifest.get("source_dir") or Path(manifest["source_dir"]).resolve() != source_dir:
            problems.append("manifest.source_dir 与当前源工单目录不一致")

    linter_rows = manifest.get("lint_roundtrip")
    skipped_for_test = manifest.get("linters_skipped_for_test") is True
    if skipped_for_test:
        if os.environ.get("ICODE_CONTROL_TEST_MODE") != "1":
            problems.append("manifest 标记跳过 linter，但当前不是测试夹具")
    else:
        expected_gates = ({"lite_contract"} if load_metadata(archive_dir).get("mode") == "lite" else
                          set(load_state_machine()["gate_policy"]["gate_linters"]))
        if not isinstance(linter_rows, list):
            problems.append("manifest.lint_roundtrip 类型错误")
        else:
            row_gates = {row.get("gate_id") for row in linter_rows
                         if isinstance(row, dict)}
            if row_gates != expected_gates:
                problems.append(
                    f"manifest.lint_roundtrip 门禁集合不完整: {sorted(row_gates)}")
            for row in linter_rows:
                if (not isinstance(row, dict) or row.get("source_ok") is not True
                        or row.get("archive_ok") is not True
                        or row.get("equivalent") is not True):
                    problems.append("manifest.lint_roundtrip 含未等价通过项")
                    break
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        problems.append("manifest.files 为空或类型错误")
        files = []
    manifest_paths = []
    for item in files:
        rel = item.get("path") if isinstance(item, dict) else None
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            problems.append(f"manifest 含非法相对路径: {rel!r}")
            continue
        manifest_paths.append(rel)
        archived = archive_dir / rel
        if not archived.is_file():
            problems.append(f"归档缺文件: {rel}")
            continue
        actual = file_sha256(archived)
        if actual != item.get("sha256"):
            problems.append(f"归档 hash 不符: {rel}")
        if archived.stat().st_size != item.get("size"):
            problems.append(f"归档 size 不符: {rel}")
        if source_dir is not None:
            source = source_dir / rel
            if not source.is_file() or file_sha256(source) != item.get("sha256"):
                problems.append(f"归档与源工单不一致: {rel}")

    external_refs = manifest.get("external_refs")
    if not isinstance(external_refs, list):
        problems.append("manifest.external_refs 类型错误")
        external_refs = []
    external_paths = set()
    for item in external_refs:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            problems.append("manifest.external_refs 含非法条目")
            continue
        ref_path = Path(item["path"]).resolve()
        external_paths.add(str(ref_path))
        if source_dir is not None:
            if ref_path.parent != source_dir:
                problems.append(f"外部引用不属于源工单顶层: {ref_path}")
            elif (not ref_path.is_file() or ref_path.stat().st_size != item.get("size")
                  or file_sha256(ref_path) != item.get("sha256")):
                problems.append(f"外部引用已变化或不可读: {ref_path}")
    if source_dir is not None:
        expected_external = {
            str(item.resolve()) for item in source_dir.iterdir()
            if item.is_file() and item.stat().st_size > 2 * 1024 * 1024
        }
        missing_external = sorted(expected_external - external_paths)
        if missing_external:
            problems.append(f"manifest 漏列大型外部文件: {missing_external}")

    duplicates = sorted({path for path in manifest_paths if manifest_paths.count(path) > 1})
    if duplicates:
        problems.append(f"manifest 含重复路径: {duplicates}")

    archived_meta = None
    try:
        archived_meta = load_json(archive_dir / METADATA_NAME, "归档 metadata")
        if archived_meta.get("ticket_id") != ticket_id:
            problems.append("归档 metadata.ticket_id 与待关闭工单不一致")
        violations = validate_against(archived_meta, SCHEMA_METADATA, "metadata_schema")
        problems.extend(f"归档 metadata: {item}" for item in violations)
        _, event_problems = verify_event_chain(archive_dir, archived_meta)
        problems.extend(f"归档事件链: {item}" for item in event_problems)
    except ControlError as exc:
        problems.append(str(exc))

    expected = set()
    if archived_meta is not None:
        archived_events, _ = verify_event_chain(archive_dir, archived_meta)
        expected.update(required_archive_files(archived_meta, archived_events))
    for root in [archive_dir] + ([Path(source_dir)] if source_dir is not None else []):
        if not root.is_dir():
            continue
        expected.update(
            item.name for item in root.iterdir()
            if item.is_file() and item.name not in {
                ".icontrol.lock", TXN_NAME, ARCHIVE_MANIFEST_NAME
            } and not item.name.endswith(".tmp") and item.stat().st_size <= 2 * 1024 * 1024
        )
        expected.update(definition_store_entries(root))
    missing_entries = sorted(expected - set(manifest_paths))
    if missing_entries:
        problems.append(f"manifest 漏列必需文件: {missing_entries}")
    return manifest, problems


def refresh_archive_manifest_control_files(archive_dir, ticket_id):
    """已交接归档根内的 metadata/事件发生受控变更后，刷新 manifest hash。

    调用方必须持有 archive_dir 的 DirLock。linter roundtrip 保留交接时结果；
    关闭阶段只改 schema 受控的 close/index 字段与事件链。
    """
    archive_dir = Path(archive_dir).resolve()
    manifest_path = archive_dir / ARCHIVE_MANIFEST_NAME
    manifest = load_json(manifest_path, "归档 manifest")
    if manifest.get("ticket_id") != ticket_id:
        raise ControlError("manifest.ticket_id 与归档控制根不一致",
                           exit_code=1, gate_id="archive_manifest")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise ControlError("manifest.files 不是数组，拒绝刷新",
                           exit_code=1, gate_id="archive_manifest")
    by_path = {row.get("path"): row for row in rows if isinstance(row, dict)}
    for name in (METADATA_NAME, EVENTS_NAME):
        path = archive_dir / name
        row = by_path.get(name)
        if row is None or not path.is_file():
            raise ControlError(f"manifest 缺受控文件条目: {name}",
                               exit_code=1, gate_id="archive_manifest")
        row.update({"size": path.stat().st_size, "sha256": file_sha256(path)})
    # A close-time event may define a new content-addressed manifest. Existing
    # rows are immutable; only definitions the handoff copied need new rows.
    for rel in sorted(definition_store_entries(archive_dir)):
        if rel in by_path:
            continue
        path = archive_dir / rel
        rows.append({"path": rel, "role": "control",
                     "size": path.stat().st_size, "sha256": file_sha256(path)})
    manifest["control_state_refreshed_at"] = now_iso()
    atomic_write_json(manifest_path, manifest)
    _, problems = verify_archive_manifest(archive_dir, ticket_id, source_dir=None)
    if problems:
        raise ControlError("归档控制状态刷新后完整性校验失败",
                           exit_code=1, gate_id="archive_manifest", violations=problems)


def handoff_close_control_root(source_dir, meta):
    """把 archived 事件后的最新 metadata/事件链交接到保留根。"""
    source_dir = Path(source_dir).resolve()
    archive_value = meta.get("archive_path")
    if not archive_value:
        raise ControlError("archived 交接缺 metadata.archive_path", exit_code=1,
                           gate_id="archive_exists")
    archive_dir = Path(archive_value).resolve()
    if archive_dir == source_dir:
        refresh_archive_manifest_control_files(archive_dir, meta.get("ticket_id"))
        return archive_dir
    if not archive_dir.is_dir():
        raise ControlError(f"归档控制根不存在: {archive_dir}", exit_code=1,
                           gate_id="archive_exists")
    with DirLock(archive_dir):
        events_path = source_dir / EVENTS_NAME
        if not events_path.is_file():
            raise ControlError("源工单事件链不存在，无法交接",
                               exit_code=1, gate_id="event_chain")
        # Close-time events may reference definitions created after the first
        # archive copy; mirror them before the ledger/metadata become readable
        # only through the archive root.
        mirror_definition_store(source_dir, archive_dir)
        atomic_write_bytes(archive_dir / EVENTS_NAME, events_path.read_bytes())
        write_metadata(archive_dir, meta, enabled=_storage_enabled(source_dir))
        refresh_archive_manifest_control_files(archive_dir, meta.get("ticket_id"))
    return archive_dir


def cmd_archive_manifest(args):
    source = Path(args.dir).resolve()
    target = Path(args.archive_dir).resolve()
    # External history backups stay usable. Rebuilding inside the retired
    # source itself is a mutation, even though no ledger event is appended.
    if args.write and target.is_relative_to(source):
        with DirLock(source):
            _stage_retirement_guard(source)
            return _cmd_archive_manifest(args)
    return _cmd_archive_manifest(args)


def _cmd_archive_manifest(args):
    source_dir = Path(args.dir).resolve()
    archive_dir = Path(args.archive_dir).resolve()
    if not archive_dir.is_dir():
        raise ControlError(f"归档目录不存在: {archive_dir}", exit_code=1,
                           gate_id="archive_exists")
    source_meta = load_metadata(source_dir)
    require_vnext(source_meta, source_dir)
    require_valid_metadata(source_meta)
    if source_dir == archive_dir:
        canonical_archive = Path(source_meta["archive_path"]).resolve() \
            if source_meta.get("archive_path") else None
        if canonical_archive != source_dir:
            raise ControlError(
                "只有已交接的归档控制根允许就地重建 manifest",
                exit_code=2, gate_id="archive_identity")
    if args.skip_linters and os.environ.get("ICODE_CONTROL_TEST_MODE") != "1":
        raise ControlError("--skip-linters 仅限 ICODE_CONTROL_TEST_MODE=1 夹具", exit_code=1,
                           gate_id="production_gate_bypass")
    if not args.write:
        manifest, problems = verify_archive_manifest(
            archive_dir, source_meta.get("ticket_id"), source_dir=None)
        print(json.dumps({"ok": not problems, "manifest": str(archive_dir / ARCHIVE_MANIFEST_NAME),
                          "file_count": len(manifest.get("files") or []),
                          "problems": problems}, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    if (source_dir / TXN_NAME).exists():
        raise ControlError("源工单存在未完成事务，禁止生成归档 manifest",
                           exit_code=1, gate_id="pending_transaction")
    source_events, chain_problems = verify_event_chain(source_dir, source_meta)
    problems = [f"源事件链: {item}" for item in chain_problems]
    required = required_archive_files(source_meta, source_events)
    small_files = {
        item.name for item in source_dir.iterdir()
        if item.is_file() and item.name not in {
            ".icontrol.lock", TXN_NAME, ARCHIVE_MANIFEST_NAME
        } and not item.name.endswith(".tmp") and item.stat().st_size <= 2 * 1024 * 1024
    }
    required.update(small_files)
    # Content-addressed definitions are referenced by the compacted ledger and
    # metadata, so an archive without them cannot be decoded. They are copied
    # unconditionally (they are control files, not bulky attachments).
    required.update(definition_store_entries(source_dir))
    file_rows = []
    for rel in sorted(required):
        source = source_dir / rel
        archived = archive_dir / rel
        if not source.is_file():
            problems.append(f"源工单缺必需控制文件: {rel}")
            continue
        if not archived.is_file():
            problems.append(f"归档缺必需控制文件: {rel}")
            continue
        source_hash = file_sha256(source)
        archive_hash = file_sha256(archived)
        if source_hash != archive_hash:
            problems.append(f"归档与源文件 hash 不同: {rel}")
            continue
        file_rows.append({
            "path": rel,
            "role": "control" if rel.startswith(".") else "artifact",
            "size": source.stat().st_size,
            "sha256": source_hash,
        })
    external_refs = []
    for item in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if item.is_file() and item.stat().st_size > 2 * 1024 * 1024:
            external_refs.append({"path": str(item), "size": item.stat().st_size,
                                  "sha256": file_sha256(item), "archived": False})
    lint_summary = []
    if not args.skip_linters:
        source_lints = run_gate_linters(source_dir)
        archive_lints = run_gate_linters(archive_dir)
        for source_report, archive_report in zip(source_lints, archive_lints):
            same = source_report["gate_id"] == archive_report["gate_id"]
            equivalent = same and source_report["ok"] == archive_report["ok"]
            lint_summary.append({"gate_id": source_report["gate_id"],
                                 "source_ok": source_report["ok"],
                                 "archive_ok": archive_report["ok"],
                                 "equivalent": equivalent})
            if not source_report["ok"] or not archive_report["ok"] or not equivalent:
                problems.append(f"归档前后 linter 未等价通过: {source_report['gate_id']}")
    if problems:
        print(json.dumps({"ok": False, "complete": False, "problems": problems},
                         ensure_ascii=False, indent=2))
        return 1
    manifest = {
        "schema_version": 1,
        "complete": True,
        "ticket_id": source_meta.get("ticket_id"),
        "generated_at": now_iso(),
        "source_dir": str(source_dir),
        "archive_dir": str(archive_dir),
        "files": file_rows,
        "external_refs": external_refs,
        "lint_roundtrip": lint_summary,
        "linters_skipped_for_test": bool(args.skip_linters),
    }
    atomic_write_json(archive_dir / ARCHIVE_MANIFEST_NAME, manifest)
    _, verify_problems = verify_archive_manifest(
        archive_dir, source_meta.get("ticket_id"), source_dir=source_dir)
    print(json.dumps({"ok": not verify_problems,
                      "manifest": str(archive_dir / ARCHIVE_MANIFEST_NAME),
                      "file_count": len(file_rows), "problems": verify_problems},
                     ensure_ascii=False, indent=2))
    return 0 if not verify_problems else 1


KNOWN_FIELDS_CACHE = None


def known_metadata_fields():
    global KNOWN_FIELDS_CACHE
    if KNOWN_FIELDS_CACHE is None:
        schema = load_json(SCHEMA_METADATA, "ticket-metadata.schema.json")
        KNOWN_FIELDS_CACHE = set(schema["properties"].keys())
    return KNOWN_FIELDS_CACHE


METADATA_UPDATE_PROTECTED = {
    "candidate_tracking", "review_receipts", "excluded_side_effects",
    "execution_binding",
    "schema_version", "ticket_id", "created_at", "status", "completed_steps",
    "indexed", "delivery_verdict", "claims", "verification_runs", "close_state",
    "control_plane_degraded", "debug", "project_path",
    "workflow_gate_schema_version", "thinking_gate_schema_version",
    "mcp_gate_schema_version", "mcp_gate_contract_version",
    "mcp_gate_contract_adopted_at", "mcp_gate_contract_history_boundary",
}
CLOSE_BOOKKEEPING_FIELDS = {
    "artifact_root", "archive_path", "backup_path", "worktree_path", "worktree_branch",
    "wt_degraded", "sub_worktrees", "active_checkout", "checkout_history", "migration",
    "submitted_baseline", "submitted_baselines", "submission_contracts", "submission_audit",
}


_CANDIDATE_MODULE = None
_STAGE_MODULE = None


def stage_module():
    global _STAGE_MODULE
    if _STAGE_MODULE is None:
        spec = importlib.util.spec_from_file_location("icode_stage_acceptance", SKILL_ROOT / "tools/stage_acceptance.py")
        _STAGE_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_STAGE_MODULE)
    # Preserve this control module's exception identity and protected provider.
    _STAGE_MODULE._CONTROL = types.SimpleNamespace(**globals())
    return _STAGE_MODULE


def stage_projection(out_dir, *, git_provider=None, require_git_provider=False):
    return stage_module().projection(out_dir, git_provider=git_provider, require_git_provider=require_git_provider)


def require_stage_action(out_dir, action, *, expected_generation=None, git_provider=None, require_git_provider=False):
    state = stage_module().require_action(out_dir, action, expected_generation=expected_generation,
                                         git_provider=git_provider, require_git_provider=require_git_provider)
    if state.get("enabled") and action not in {"status", "trace", "check", "inspect"} \
            and (Path(out_dir) / METADATA_NAME).is_file() and load_metadata(out_dir).get("mode") == "lite":
        raise ControlError("lite 首版不支持阶段计划执行", gate_id="lite_stage_unsupported")
    return state


def register_stage_program(out_dir, plan, **kwargs):
    return stage_module().register_program(out_dir, plan, **kwargs)


def bind_stage(coordinator, stage_id, out_dir, **kwargs):
    return stage_module().bind_stage(coordinator, stage_id, out_dir, **kwargs)


def preview_stage_revision(out_dir, plan, **kwargs):
    return stage_module().preview_revision(out_dir, plan, **kwargs)


def revise_stage_program(out_dir, plan, request, *, trusted_confirmation=None, **kwargs):
    return stage_module().revise_program(out_dir, plan, request, trusted_confirmation=trusted_confirmation, **kwargs)


def review_stage(out_dir, **kwargs):
    return stage_module().review_stage(out_dir, **kwargs)


def confirm_stage(out_dir, request, *, trusted_confirmation=None, git_provider=None, require_git_provider=False):
    return stage_module().confirm_stage(out_dir, request, trusted_confirmation=trusted_confirmation,
                                        git_provider=git_provider, require_git_provider=require_git_provider)


def _stage_write_action(event_type, payload):
    if event_type == "agent_spawned":
        return payload.get("stage_action") or "agent"
    if event_type == "step_started":
        step = payload.get("step")
        return step if step in {"code", "patch", "fast", "verify", "deploy"} else "review"
    if event_type == "operation_started":
        return None if payload.get("class") == "read_only" else payload.get("stage_action", "operation")
    if event_type == "verification_recorded" and payload.get("outcome") == "pass":
        return "verify"
    if event_type == "state_changed":
        target = payload.get("to") or ""
        if target == "review_in_progress":
            return "review"
        if target in {"code_in_progress", "code_done"}:
            return "code"
        if target in {"deepcheck_in_progress", "deepcheck_done", "audit_in_progress", "verify_in_progress", "completed", "debug_done"}:
            return "transition"
    if event_type == "metadata_updated":
        if payload.get("stage_control") or payload.get("serial_claim_control") or payload.get("visual_review_control") or payload.get("evidence_review_control"):
            return None
        if payload.get("build_start_control"):
            return "build"
        if payload.get("candidate_control"):
            return "candidate"
        if payload.get("execution_root_binding"):
            return "execution_root"
        return "metadata"
    return None


def _stage_retirement_guard(out_dir, *, git_provider=None, require_git_provider=False):
    """Check authenticated retirement before neutral/draft write exemptions.

    A caller holding the child lock only reads coordinator state. Revision
    publication also needs that child lock; never acquire a reverse parent lock.
    Birth/coordinator and ordinary nonstage tickets retain their original path.
    """
    if not (Path(out_dir) / METADATA_NAME).is_file():
        return None
    meta = load_metadata(out_dir)
    if (meta.get("extensions") or {}).get("stage_binding") is None:
        return None
    view = stage_module()._retirement_projection(out_dir, git_provider=git_provider, require_git_provider=require_git_provider)
    # A damaged historical report can change presentation state to blocked;
    # the authenticated replacement identity must still prohibit every write.
    if not view.get("enabled") or not view.get("stage_id") or view["state"] == "superseded" \
            or view.get("replacement_stages") is not None:
        raise ControlError("stage: superseded or invalid bound ticket cannot mutate", gate_id="stage_gate", stage=view)
    token = _stage_token(out_dir, view, None, meta)
    token["retirement_only"] = True
    return token


def _stage_write_guard(out_dir, event_type, payload, *, git_provider=None, require_git_provider=False):
    retirement = _stage_retirement_guard(out_dir, git_provider=git_provider, require_git_provider=require_git_provider)
    if (Path(out_dir) / METADATA_NAME).is_file() and load_metadata(out_dir).get("mode") == "lite" \
            and stage_projection(out_dir, git_provider=git_provider, require_git_provider=require_git_provider).get("enabled"):
        raise ControlError("lite 首版不支持阶段计划写入", gate_id="lite_stage_unsupported")
    action = _stage_write_action(event_type, payload)
    if action is None:
        return retirement
    if event_type == "metadata_updated" and set(payload.get("set") or {}) | set(payload.get("append") or {}):
        fields = set(payload.get("set") or {}) | set(payload.get("append") or {})
        if fields.issubset({"requirement", "requirement_summary", "keywords"}):
            meta = load_metadata(out_dir)
            events, _ = read_events(out_dir)
            implemented = any(event["event_type"] == "verification_recorded" or
                (event["event_type"] == "step_started" and (event.get("payload") or {}).get("step") in {"code", "patch", "fast", "verify", "deploy", "deepcheck", "audit"}) or
                (event["event_type"] == "operation_started" and (event.get("payload") or {}).get("class") != "read_only") for event in events)
            # Only the completed boolean is needed by the following stage guard.
            del events
            if not implemented and meta.get("status") in {"init_in_progress", "log_in_progress", "log_done", "plan_done", "review_in_progress", "review_done", "plan_finalized"}:
                return retirement
    view = require_stage_action(out_dir, action, git_provider=git_provider, require_git_provider=require_git_provider)
    if not view["enabled"]:
        return None
    return _stage_token(out_dir, view, action)


def _stage_token(out_dir, view, action, meta=None):
    if not view["enabled"]:
        return None
    if not view.get("coordinator_root") or not view.get("stage_id"):
        raise ControlError("stage: writer requires an intact bound coordinator reference", gate_id="stage_gate", stage=view)
    return {"coordinator_root": view["coordinator_root"], "generation": view["generation"], "action": action,
            "child_root": str(Path(out_dir).resolve()), "metadata": copy.deepcopy(load_metadata(out_dir) if meta is None else meta),
            "stage_id": view["stage_id"], "program_id": view["program_id"]}


def _stage_publication_check(token, *, git_provider=None, require_git_provider=False):
    if token is None:
        return
    view = stage_projection(token["coordinator_root"], git_provider=git_provider, require_git_provider=require_git_provider)
    if view["blocked"] or view.get("generation") != token["generation"] or view.get("program_id") != token["program_id"]:
        raise ControlError("stage: generation changed before result publication", gate_id="stage_gate", stage=view)
    access = {"git_provider": git_provider, "require_git_provider": require_git_provider}
    if "proposed_contract" in token:
        state = stage_module()._resolve(token["child_root"], _metadata_override=token["metadata"], _allow_pending=True, **access)
        stage_module().require_inherited_contract(state, {"verification_contract": token["proposed_contract"]})
    if token.get("retirement_only"):
        child = stage_module()._retirement_projection(token["child_root"], _metadata_override=token["metadata"], _allow_pending=True, **access)
        if not child.get("stage_id") or child["state"] == "superseded" \
                or child.get("replacement_stages") is not None:
            raise ControlError("stage: retired binding before publication", gate_id="stage_gate", stage=child)
        return
    stage_module().require_action(token["child_root"], token["action"], expected_generation=token["generation"],
        git_provider=git_provider, require_git_provider=require_git_provider,
        _metadata_override=token["metadata"], _allow_pending=True)


def cmd_stage(args, *, git_provider=None, require_git_provider=False):
    access = {"git_provider": git_provider, "require_git_provider": require_git_provider}
    if args.phase in {"register", "preview-revision"} and not args.plan_json:
        raise ControlError("stage register/preview-revision requires --plan-json", exit_code=2, gate_id="stage_gate")
    if args.phase == "bind" and (not args.stage_id or not args.ticket_dir):
        raise ControlError("stage bind requires --stage-id/--ticket-dir", exit_code=2, gate_id="stage_gate")
    if args.phase in {"register", "bind", "review"} and not args.request_id:
        raise ControlError("stage writer requires --request-id", exit_code=2, gate_id="stage_gate")
    if args.phase == "register":
        result = register_stage_program(args.dir, verification_json(args.plan_json, "--plan-json"), request_id=args.request_id, **access)
    elif args.phase == "preview-revision":
        result = preview_stage_revision(args.dir, verification_json(args.plan_json, "--plan-json"),
            consolidation=verification_json(getattr(args, "consolidation_json", None), "--consolidation-json"), **access)
    elif args.phase == "bind":
        result = bind_stage(args.dir, args.stage_id, args.ticket_dir, request_id=args.request_id, **access)
    elif args.phase == "review":
        result = review_stage(args.dir, request_id=args.request_id, **access)
    elif args.phase == "check":
        result = stage_projection(args.dir, **access)
        result["ok"] = not result["blocked"]
    else:
        raise ControlError("stage: trusted host/UI confirmation required; CLI actor or JSON cannot attest a user", gate_id="stage_gate")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _stage_writer_locks(out_dir, view):
    """Return a context manager with the global coordinator -> child order."""
    stack = contextlib.ExitStack()
    try:
        if view.get("enabled") and view.get("coordinator_root") and Path(view["coordinator_root"]).resolve() != Path(out_dir).resolve():
            stack.enter_context(DirLock(view["coordinator_root"]))
        stack.enter_context(DirLock(out_dir))
        return stack
    except BaseException:
        stack.close()
        raise


def claim_serial_operation(proof, session_info, *, git_provider=None, require_git_provider=False):
    """Consume a real open external operation before any serial TX, once only."""
    required = {"dir", "attempt", "event_id", "ticket_id", "input_digest", "operation_class"}
    if not isinstance(proof, dict) or set(proof) != required or any(not isinstance(proof[k], str) or not proof[k] for k in required):
        raise ControlError("serial operation proof shape invalid", gate_id="serial_claim")
    if not isinstance(session_info, dict) or set(session_info) != {"session_id", "output_dir", "operation_input"} \
            or any(not isinstance(session_info[k], str) or not session_info[k] for k in session_info):
        raise ControlError("serial session info shape invalid", gate_id="serial_claim")
    out_dir = Path(proof["dir"]).resolve(strict=True)
    access = {"git_provider": git_provider, "require_git_provider": require_git_provider}
    view = require_stage_action(out_dir, "serial_exec", **access)
    try:
        operation_input = strict_json_loads(session_info["operation_input"])
    except (ValueError, TypeError) as exc:
        raise ControlError("serial canonical input invalid", gate_id="serial_claim") from exc
    if not isinstance(operation_input, dict) or operation_input.get("action") != "serial_exec" \
            or operation_input.get("operation_class") != "external_side_effect" \
            or any(not isinstance(operation_input.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", operation_input[key]) is None
                   for key in ("script_sha256", "config_sha256")) \
            or canonical_digest(session_info["operation_input"]) != proof["input_digest"]:
        raise ControlError("serial script/config input digest mismatch", gate_id="serial_claim")
    with _stage_writer_locks(out_dir, view):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        ensure_execution_writable(meta)
        events, issues = verify_event_chain(out_dir, meta, **access)
        if issues:
            raise ControlError("serial operation event chain invalid", gate_id="serial_claim", violations=issues)
        fresh = require_stage_action(out_dir, "serial_exec", expected_generation=view.get("generation"), **access)
        event = find_operation_start(events, proof["attempt"])
        payload = event.get("payload") or {}
        if str(out_dir) != proof["dir"] or meta["ticket_id"] != proof["ticket_id"] \
                or event["event_id"] != proof["event_id"] or payload.get("class") != "external_side_effect" \
                or proof["operation_class"] != "external_side_effect" or payload.get("input_digest") != proof["input_digest"] \
                or operation_finished(events, proof["attempt"]) is not None:
            raise ControlError("serial operation is not the referenced open authorized attempt", gate_id="serial_claim")
        if fresh["enabled"] and payload.get("stage_generation") != fresh["generation"]:
            raise ControlError("serial operation stage generation is stale; refusing TX", gate_id="serial_claim")
        after = copy.deepcopy(meta)
        verification = after.setdefault("extensions", {}).setdefault("verification", {})
        claims = verification.setdefault("serial_claims", [])
        if any(row.get("attempt") == proof["attempt"] or row.get("session_id") == session_info["session_id"] for row in claims):
            raise ControlError("serial operation was already claimed; unknown remote outcome cannot be replayed", gate_id="serial_claim")
        claim = {"claim_id": str(uuid.uuid4()), "attempt": proof["attempt"], "operation_event_id": event["event_id"],
                 "ticket_id": meta["ticket_id"], "input_digest": proof["input_digest"], "operation_class": "external_side_effect",
                 "session_id": session_info["session_id"], "output_dir": str(Path(session_info["output_dir"]).resolve()),
                 "claimed_at": now_iso(), "stage_generation": fresh.get("generation")}
        claims.append(claim)
        record = {"serial_claim_control": 1, "serial_claim": claim, "set": {"extensions": after["extensions"]}, "append": {}}
        publication = {}
        if _candidate_access_requested(git_provider, require_git_provider):
            publication = dict(access, before_publish=lambda: _candidate_context_guard(out_dir, meta, git_provider, require_git_provider))
        token = _stage_token(out_dir, fresh, "serial_exec", meta)
        committed = commit_metadata_and_event(out_dir, meta, after, "metadata_updated", record,
            request_id="serial-claim:" + claim["claim_id"], before_append=lambda: _stage_publication_check(token, **access), **publication)
    return {"ok": True, "claim_id": claim["claim_id"], "event_id": committed["event_id"], "stage_generation": fresh.get("generation")}


def _review_start(out_dir, run, action, access):
    view = require_stage_action(out_dir, action, **access)
    meta, events = stage_module()._verified(out_dir, **access)
    if not isinstance(run, dict) or not isinstance(run.get("run_id"), str) or not run["run_id"].strip():
        raise ControlError("review requires a stable run_id", gate_id="evidence_review")
    if (meta.get("candidate_tracking") or {}).get("mode") != "enabled":
        raise ControlError("review requires tracked current candidate identity", gate_id="evidence_review")
    current = candidate_snapshot(out_dir, meta, events=events, **access)
    if run.get("candidate_id") != current["candidate_id"]:
        raise ControlError("review run candidate differs from current source", gate_id="evidence_review")
    return meta, view, current


def _review_replay(out_dir, meta, namespace, request_id, request_digest):
    events, _ = read_events(out_dir)
    prior = next((event for event in events if event.get("request_id") == request_id), None)
    if prior is None:
        return None
    receipts = ((meta.get("extensions") or {}).get("verification") or {}).get(namespace) or []
    receipt = next((row for row in receipts if row.get("request_id") == request_id), None)
    if receipt is None or receipt.get("request_digest") != request_digest:
        raise ControlError("review request_id conflicts with another input or writer", gate_id="evidence_review")
    return {"ok": True, "already_applied": True, "review_id": receipt["review_id"], "outcome": receipt["outcome"],
            "event_id": prior["event_id"], "receipt": copy.deepcopy(receipt)}


def _finish_review_activity(out_dir, attempt, request_id, outcome, access):
    """Use the same native operation finish writer and failure policies as CLI."""
    argv = ["operation", "--dir", str(out_dir), "--phase", "finish", "--attempt", attempt,
            "--outcome", outcome, "--evidence", "review request: " + request_id,
            "--check", "protected review receipt published" if outcome == "success" else "review did not complete; inspect original failure before retry",
            "--request", "review-finish:" + request_id]
    if outcome != "success":
        argv += ["--failure", "deterministic_failure"]
    results = []
    result = cmd_operation(build_parser().parse_args(argv), _result_sink=results.append, **access)
    if result != 0:
        raise ControlError("review operation did not terminate", gate_id="evidence_review", attempt=attempt)


@contextlib.contextmanager
def _review_activity(out_dir, meta, view, current, namespace, request_id, request_digest, access):
    """Admit exactly one provider owner before releasing revision writer locks.

    The existing operation ledger blocks consolidation while the provider runs,
    and until its protected receipt is published. A crashed/open/replayed start
    is never permission to call a provider again. Ordinary nonstage calls retain
    their old path. Operation success means execution/publication, not image IQ.
    """
    if not view["enabled"]:
        yield
        return
    operation_request = "review-activity:" + request_id
    with _stage_writer_locks(out_dir, view):
        fresh, events = stage_module()._verified(out_dir, **access)
        if metadata_hash(fresh) != metadata_hash(meta):
            raise ControlError("review metadata changed before provider admission", gate_id="evidence_review")
        require_stage_action(out_dir, "record_visual_review", expected_generation=view["generation"], **access)
        candidate = candidate_snapshot(out_dir, fresh, events=events, **access)
        if candidate["candidate_id"] != current["candidate_id"]:
            raise ControlError("source changed before provider admission", gate_id="evidence_review")
        if _review_replay(out_dir, fresh, namespace, request_id, request_digest) is not None:
            raise ControlError("review completed concurrently; query its exact receipt without rerunning provider", gate_id="evidence_review")
        model = load_execution_model()
        if validate_execution_catalog(model):
            raise ControlError("invalid operation catalog", gate_id="execution_model_catalog")
        args = argparse.Namespace(name="review:" + request_id, opclass="read_only", attempt=None,
            request=operation_request, input=json.dumps({"action": "record_visual_review", "namespace": namespace,
                "request_digest": request_digest}, sort_keys=True))
        results = []
        _operation_start_locked(args, out_dir, fresh, events, model, access, _result_sink=results.append)
        started = results[0]
        if started.get("already_applied"):
            raise ControlError("existing review operation is not provider replay permission; inspect its outcome",
                               gate_id="evidence_review", attempt=started["attempt"])
        attempt = started["attempt"]
    try:
        yield
    except BaseException as original:
        try:
            _finish_review_activity(out_dir, attempt, request_id, "failure", access)
        except BaseException as terminal_error:
            raise ControlError("review failed and operation termination remains pending; do not replay provider",
                gate_id="evidence_review", attempt=attempt, failure=type(original).__name__,
                terminal_failure=type(terminal_error).__name__) from original
        raise
    else:
        _finish_review_activity(out_dir, attempt, request_id, "success", access)


def _publish_review(out_dir, meta, view, current, receipt, namespace, request_id, check_files, access):
    with _stage_writer_locks(out_dir, view):
        fresh_meta, events = stage_module()._verified(out_dir, **access)
        if metadata_hash(fresh_meta) != metadata_hash(meta):
            raise ControlError("review metadata changed during provider execution", gate_id="evidence_review")
        fresh_view = require_stage_action(out_dir, "record_visual_review", expected_generation=view.get("generation"), **access)
        fresh_candidate = candidate_snapshot(out_dir, fresh_meta, events=events, **access)
        if fresh_candidate["candidate_id"] != current["candidate_id"]:
            raise ControlError("source changed during review execution", gate_id="evidence_review")
        check_files()
        after = copy.deepcopy(fresh_meta)
        reviews = after.setdefault("extensions", {}).setdefault("verification", {}).setdefault(namespace, [])
        reviews.append(receipt)
        marker = "visual_review" if namespace == "visual_reviews" else "evidence_review"
        payload = {marker + "_control": 1, marker: receipt, "set": {"extensions": after["extensions"]}, "append": {}}
        token = _stage_token(out_dir, fresh_view, "record_visual_review", fresh_meta)
        def before_append():
            check_files()
            _stage_publication_check(token, **access)
        def check_candidate():
            actual = candidate_snapshot(out_dir, fresh_meta, events=events, **access)
            if actual["candidate_id"] != current["candidate_id"]:
                raise ControlError("source changed before review publication", gate_id="evidence_review")
            if access:
                _candidate_context_guard(out_dir, fresh_meta, access["git_provider"], access["require_git_provider"])
        original_before_append = before_append
        def final_check():
            check_candidate()
            original_before_append()
        publication = dict(access, before_publish=check_candidate) if access else {}
        event = commit_metadata_and_event(out_dir, fresh_meta, after, "metadata_updated", payload,
            request_id=request_id, before_append=final_check, **publication)
    return {"ok": True, "review_id": receipt["review_id"], "outcome": receipt["outcome"], "event_id": event["event_id"], "receipt": receipt}


def record_evidence_review(out_dir, *, item, run, requirement_id, method, trusted_reviewer=None,
                           request_id, git_provider=None, require_git_provider=False):
    """Read the actual item and invoke a trusted AI/observation adapter ourselves.

    A host callback is executable authority; ordinary CLI/JSON cannot install it.
    Human observation is a separate explicit receipt, never the final acceptance.
    """
    if not callable(trusted_reviewer) or method not in {"ai_review", "human_observation"}:
        raise ControlError("trusted evidence review/observation adapter required", gate_id="evidence_review")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ControlError("review requires request_id", gate_id="evidence_review")
    out_dir = Path(out_dir).resolve()
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    meta, view, current = _review_start(out_dir, run, "record_visual_review", access)
    contract = meta.get("verification_contract") or {}
    requirements = verification_evidence_module().bundle_module().validate_contract(contract)["required_items"]
    selected = next((row for row in requirements if row["id"] == requirement_id), None)
    if selected is None or selected["method"] != method or any(selected[key] != run.get(key) for key in ("layer", "consumer", "scenario")):
        raise ControlError("review method/cell differs from frozen requirement", gate_id="evidence_review")
    if not isinstance(item, dict) or not {"path", "sha256", "bytes"}.issubset(item):
        raise ControlError("review requires the bounded original item reference", gate_id="evidence_review")
    bundle_api = verification_evidence_module().bundle_module()
    request_digest = canonical_digest({"item": item, "run": run, "requirement_id": requirement_id, "method": method,
                                       "contract": bundle_api.contract_digest(contract)})
    replay = _review_replay(out_dir, meta, "evidence_reviews", request_id, request_digest)
    if replay is not None:
        return replay
    def read():
        reader = bundle_api.Reader(out_dir)
        raw = reader.read(item["path"], item["sha256"], item["bytes"])
        reader.finish()
        return raw
    raw = read()
    binding = {"method": method, "item_sha256": item["sha256"], "run_id": run["run_id"], "candidate_id": run["candidate_id"]}
    with _review_activity(out_dir, meta, view, current, "evidence_reviews", request_id, request_digest, access):
        try:
            response = trusted_reviewer({**binding, "item": copy.deepcopy(item), "content": raw,
                                         "requirement": copy.deepcopy(selected), "contract_digest": bundle_api.contract_digest(contract)})
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > bundle_api.DEFAULT_LIMITS["max_json_bytes"]:
                raise ValueError("review response exceeds JSON budget")
        except Exception as exc:
            raise ControlError("trusted reviewer failed: " + type(exc).__name__, gate_id="evidence_review") from exc
        if not isinstance(response, dict) or any(response.get(key) != value for key, value in binding.items()) \
                or response.get("outcome") not in {"pass", "fail", "inconclusive"} \
                or not isinstance(response.get("conclusion"), str) or not response["conclusion"].strip() \
                or not isinstance(response.get("limitations"), list) or any(not isinstance(v, str) for v in response["limitations"]) \
                or not isinstance(response.get("source_id"), str) or not response["source_id"].strip() \
                or (method == "ai_review" and any(not isinstance(response.get(k), str) or not response[k].strip() for k in ("provider", "model"))) \
                or (method == "human_observation" and response.get("source") != "host_observation"):
            raise ControlError("trusted evidence review returned incomplete or mismatched receipt", gate_id="evidence_review")
        receipt = {**copy.deepcopy(response), "review_id": str(uuid.uuid4()), "at": now_iso(), "requirement_id": requirement_id,
                   "cell": {key: run[key] for key in ("layer", "consumer", "scenario")},
                   "item": {key: item[key] for key in ("path", "sha256", "bytes")},
                   "contract_digest": bundle_api.contract_digest(contract), "request_id": request_id, "request_digest": request_digest}
        return _publish_review(out_dir, meta, view, current, receipt, "evidence_reviews", request_id, read, access)


def record_visual_review(out_dir, *, frames, route, run, prompt_version, adapters=None, prompt="", request_id,
                         git_provider=None, require_git_provider=False):
    """Execute the selected media providers, then atomically protect their report.

    Each frame additionally supplies source_path so this writer hashes the
    original source itself. Paths stay under this explicit ticket evidence root.
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise ControlError("visual review requires request_id", gate_id="visual_review")
    out_dir = Path(out_dir).resolve()
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    meta, view, current = _review_start(out_dir, run, "record_visual_review", access)
    bundle_api = verification_evidence_module().bundle_module()
    contract = bundle_api.validate_contract(meta.get("verification_contract"))
    if contract.get("visual") is None or contract["visual"].get("required") is not True:
        raise ControlError("visual review requires a frozen visual contract", gate_id="visual_review")
    if not isinstance(frames, list) or not frames:
        raise ControlError("visual review requires actual frame sources", gate_id="visual_review")
    normalized = copy.deepcopy(frames)
    request_digest = canonical_digest({"frames": normalized, "route": route, "run": run,
        "prompt_version": prompt_version, "prompt_digest": canonical_digest(prompt),
        "contract": bundle_api.contract_digest(meta["verification_contract"])})
    replay = _review_replay(out_dir, meta, "visual_reviews", request_id, request_digest)
    if replay is not None:
        return replay
    def check_files():
        reader = bundle_api.Reader(out_dir)
        for frame in normalized:
            reader.read(frame["source_path"], frame["source_sha256"])
            reader.read(frame["preview_path"], frame["input_sha256"])
        reader.finish()
    try:
        reader = bundle_api.Reader(out_dir)
        for frame in normalized:
            frame["source_path"] = str(reader.path(frame["source_path"]))
            frame["preview_path"] = str(reader.path(frame["preview_path"]))
        check_files()
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ControlError("visual source/preview evidence invalid: " + str(exc), gate_id="visual_review") from exc
    spec = importlib.util.spec_from_file_location("controlled_visual_evidence", SKILL_ROOT / "tools/visual_evidence.py")
    visual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(visual)
    with _review_activity(out_dir, meta, view, current, "visual_reviews", request_id, request_digest, access):
        report = visual.execute_visual_review(normalized, route, run=run, prompt_version=prompt_version,
                                              adapters=adapters, prompt=prompt)
        receipt = {**report, "review_id": str(uuid.uuid4()), "at": now_iso(), "request_id": request_id,
                   "cell": {key: run.get(key) for key in ("layer", "consumer", "scenario")},
                   "frames": normalized, "prompt_version": prompt_version, "prompt_sha256": canonical_digest(prompt),
                   "route_digest": canonical_digest(route), "contract_digest": bundle_api.contract_digest(meta["verification_contract"])}
        receipt["request_digest"] = request_digest
        return _publish_review(out_dir, meta, view, current, receipt, "visual_reviews", request_id, check_files, access)


def candidate_module():
    """Shared stdlib-only source identity backend; never persists on its own."""
    global _CANDIDATE_MODULE
    if _CANDIDATE_MODULE is None:
        spec = importlib.util.spec_from_file_location("icode_candidate", SKILL_ROOT / "tools/icode_candidate.py")
        _CANDIDATE_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CANDIDATE_MODULE)
    return _CANDIDATE_MODULE


def _candidate_access_requested(provider, required):
    return provider is not None or type(required) is not bool or required


def _candidate_context_guard(out_dir, meta, provider, required):
    module = candidate_module()
    try:
        if not isinstance(meta, dict):
            raise module.CandidateError("candidate metadata missing")
        module._candidate_provider(execution_workspace(out_dir, meta), provider, required)
    except (module.CandidateError, OSError, UnicodeError) as exc:
        raise ControlError(str(exc), gate_id="candidate_provider_context") from exc


_VERIFICATION_EVIDENCE_MODULE = None
_WORKFLOW_LINT_MODULE = None


def verification_evidence_module():
    global _VERIFICATION_EVIDENCE_MODULE
    if _VERIFICATION_EVIDENCE_MODULE is None:
        spec = importlib.util.spec_from_file_location("verification_evidence", SKILL_ROOT / "tools/verification_evidence.py")
        _VERIFICATION_EVIDENCE_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_VERIFICATION_EVIDENCE_MODULE)
    return _VERIFICATION_EVIDENCE_MODULE


def workflow_lint_module():
    """Reuse the read-only credential policy enforced by the workflow linter."""
    global _WORKFLOW_LINT_MODULE
    if _WORKFLOW_LINT_MODULE is None:
        spec = importlib.util.spec_from_file_location("icode_workflow_lint", SKILL_ROOT / "tools/lint_workflow_contract.py")
        _WORKFLOW_LINT_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_WORKFLOW_LINT_MODULE)
    return _WORKFLOW_LINT_MODULE


def verification_context(out_dir, meta=None, *, git_provider=None, require_git_provider=False):
    """Read-only live source/event context; never persists a refreshed candidate."""
    meta = load_metadata(out_dir) if meta is None else meta
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    context = verification_evidence_module().context(out_dir, meta, capture=candidate_snapshot,
                verify=verify_event_chain, candidate_api=candidate_module(), **access)
    return context


def verification_applicability(out_dir, meta=None, *, git_provider=None, require_git_provider=False):
    """Small JSON-ready evidence verdict; query success is separate from ok."""
    meta = load_metadata(out_dir) if meta is None else meta
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    return verification_evidence_module().assess(meta, verification_context(out_dir, meta, **access), **access)


def candidate_inspection_scope(out_dir, meta=None, *, effective_base=None, events=None):
    """Aggregate applicable declarations, without read progress or report text.

    Review diff baselines are independent of the controlled candidate base.
    Only genuine writer/start events identify a declaration's generation;
    Unreceipted legacy declarations can use the scope bound by the first
    controlled enable event; later scope additions remain conservative.
    """
    select_generations = ((meta or {}).get("candidate_tracking") or {}).get("mode") == "enabled"
    attempts, finished, writers = {}, set(), {}
    generation, controlled_base = 0, None
    legacy_scope, legacy_generation = None, None
    for event in events or []:
        payload = event.get("payload") or {}
        kind = event.get("event_type")
        tracking = None
        if kind == "ticket_created":
            tracking = payload.get("candidate_tracking")
        elif kind == "metadata_updated" and payload.get("candidate_control") == 1:
            tracking = (payload.get("set") or {}).get("candidate_tracking")
        if isinstance(tracking, dict) and tracking.get("mode") == "enabled":
            if tracking.get("base") != controlled_base:
                generation += 1
                if controlled_base is None and kind == "metadata_updated":
                    legacy_scope = tracking["current"]["inspection_scope"]
                    legacy_generation = generation
                controlled_base = tracking["base"]
        key = (payload.get("step"), payload.get("attempt"))
        if key[0] not in candidate_module().REVIEW_STEPS:
            continue
        if kind == "step_started" and payload.get("execution_model_version") == 1:
            attempts[key] = generation
        elif kind == "step_finished" and payload.get("execution_model_version") == 1:
            finished.add(key)
        elif kind == "artifact_written" and payload.get("output") == "inspection_worklist":
            writers.setdefault(key[0], []).append(payload)
    # Explicit capture B runs before metadata records B. It starts a new
    # generation even if older history previously used that same base.
    applicable_generation = generation + (effective_base != controlled_base)
    if select_generations and effective_base is not None:
        old_open = [attempt for (step, attempt), origin in attempts.items()
                    if origin != applicable_generation and (step, attempt) not in finished]
        if old_open:
            raise ControlError("旧 candidate 代际仍有未终结 review attempt，先 blocked 终结再重入",
                               gate_id="candidate_scope", open_attempts=old_open)
    scopes, related, baselines = set(), set(), {}
    for step in ("code", "deepcheck", "audit"):
        path = Path(out_dir) / f"{step}_worklist.json"
        if not path.exists():
            continue
        report = load_json(path, "candidate inspection declaration")
        if not isinstance(report, dict):
            raise ControlError("inspection declaration 必须是对象", gate_id="candidate_scope")
        receipts = writers.get(step) or []
        declaration_receipts = []
        if receipts:
            attempt = receipts[-1].get("attempt")
            declaration_receipts = [receipt for receipt in receipts
                                    if receipt.get("attempt") == attempt and receipt.get("inspection_declaration_hash")]
            if declaration_receipts:
                try:
                    digest = inspection_declaration_hash(report)
                except (ValueError, TypeError) as exc:
                    raise ControlError(f"inspection declaration/receipt drift: {exc}", gate_id="candidate_scope") from exc
                key = (step, attempt)
                if report.get("attempt") != attempt or report.get("step") != step \
                        or meta is not None and report.get("ticket_id") != meta.get("ticket_id") \
                        or key not in attempts \
                        or any(receipt["inspection_declaration_hash"] != digest for receipt in declaration_receipts):
                    raise ControlError("inspection declaration/receipt drift", gate_id="candidate_scope")
                if select_generations and effective_base is not None and attempts[key] != applicable_generation:
                    continue  # Keep completed old-generation reports/receipts as history.
        declaration = {}
        for field in ("scopes", "related"):
            values = report.get(field) or []
            if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
                raise ControlError("inspection 范围必须是字符串数组", gate_id="candidate_scope")
            declaration[field] = set(values)
        values = report.get("resolved_baselines") or report.get("baselines") or {}
        if not isinstance(values, dict):
            raise ControlError("inspection baselines 必须是对象", gate_id="candidate_scope")
        if select_generations and not declaration_receipts and legacy_scope is not None \
                and legacy_generation != applicable_generation \
                and declaration["scopes"].issubset(legacy_scope["scopes"]) \
                and declaration["related"].issubset(legacy_scope["related"]) \
                and all(key in legacy_scope["baselines"] and legacy_scope["baselines"][key] == value
                        for key, value in values.items()):
            # The genuine enable snapshot bounds this old declaration. New
            # paths/repositories cannot self-identify as legacy to disappear.
            continue
        scopes.update(declaration["scopes"])
        related.update(declaration["related"])
        for key, value in values.items():
            if key in baselines and baselines[key] != value:
                raise ControlError("inspection baseline 合同不一致", gate_id="candidate_scope")
            baselines[key] = value
    return {"scopes": sorted(scopes), "related": sorted(related), "baselines": baselines}


def candidate_limits(raw=None):
    module = candidate_module()
    try:
        return module.normalize_limits(strict_json_loads(raw) if raw is not None else None)
    except (ValueError, TypeError) as exc:
        raise ControlError(f"候选预算无效: {exc}", exit_code=2, gate_id="candidate_limits") from exc


def candidate_snapshot(out_dir, meta, *, base=None, excluded_side_effects=None, limits=None, events=None,
                       git_provider=None, require_git_provider=False):
    """Capture once; callers may reuse events verified against the controlled metadata."""
    module = candidate_module()
    tracking = meta.get("candidate_tracking") or {}
    effective_base = base or tracking.get("base")
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if access:
        _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
    if events is None:
        events, problems = verify_event_chain(out_dir, meta, **access)
        if problems:
            raise ControlError("事件链不完整，拒绝建立候选声明", gate_id="event_chain", violations=problems)
    try:
        candidate = module.capture_candidate(
            execution_workspace(out_dir, meta), base=effective_base,
            code_files=meta.get("code_files") if access else meta.get("code_files") or [], scope_contract=meta.get("scope_contract"),
            inspection_scope=candidate_inspection_scope(out_dir, meta, effective_base=effective_base, events=events),
            limits=tracking.get("limits") if limits is None else limits,
            excluded_side_effects=(meta.get("excluded_side_effects") if access else meta.get("excluded_side_effects") or [])
                                  if excluded_side_effects is None else excluded_side_effects, **access)
        if access:
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        return candidate
    except (module.CandidateError, OSError, UnicodeError) as exc:
        raise ControlError(f"候选身份无法完整建立: {exc}", gate_id="candidate_capture") from exc


def delivery_freshness(out_dir, meta=None, *, required_steps=None,
                       git_provider=None, require_git_provider=False):
    """Read-only query; freshness acceptance remains distinct from query success."""
    meta = load_metadata(out_dir) if meta is None else meta
    required_steps = tuple(required_steps or workflow_contract(meta)["required_review_steps"])
    module = candidate_module()
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if access and meta.get("mode") == "lite":
        return {"ok": False, "state": "blocked", "reasons": ["lite_provider_unsupported"],
                "candidate_id": None, "applicable_receipts": [], "required_steps": list(required_steps)}
    if not access:
        if (meta.get("candidate_tracking") or {}).get("mode") != "enabled":
            return module.delivery_freshness(meta, None, required_steps=required_steps)
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            return {"ok": False, "state": "blocked", "reasons": ["event_chain"],
                    "violations": problems, "candidate_id": None, "applicable_receipts": [],
                    "required_steps": list(required_steps)}
        try:
            current = candidate_snapshot(out_dir, meta, events=events)
        except ControlError as exc:
            return {"ok": False, "state": "blocked", "reasons": ["candidate_capture"],
                    "detail": exc.message, "candidate_id": None, "applicable_receipts": [],
                    "required_steps": list(required_steps)}
        result = module.delivery_freshness(meta, current, events, required_steps)
        if meta.get("mode") == "lite":
            try:
                lite_review_fact(out_dir, meta, events, candidate=current)
            except ControlError as exc:
                result = dict(result, ok=False, state="review_required", applicable_receipts=[],
                              reasons=result["reasons"] + [exc.extra.get("gate_id", "lite_review_required")],
                              detail=exc.message)
        return result
    try:
        if access:
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        if (meta.get("candidate_tracking") or {}).get("mode") != "enabled":
            if access:
                raise ControlError("candidate tracking required", gate_id="candidate_provider_context")
            return module.delivery_freshness(meta, None, required_steps=required_steps)
        events, problems = verify_event_chain(out_dir, meta, **access)
        if problems:
            return {"ok": False, "state": "blocked", "reasons": ["event_chain"],
                    "violations": problems, "candidate_id": None, "applicable_receipts": [],
                    "required_steps": list(required_steps)}
        current = candidate_snapshot(out_dir, meta, events=events, **access)
        result = module.delivery_freshness(meta, current, events, required_steps, **access)
        if access:
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        return result
    except ControlError as exc:
        return {"ok": False, "state": "blocked",
                "reasons": ["candidate_provider_context" if access else "candidate_capture"],
                "detail": exc.message, "candidate_id": None, "applicable_receipts": [],
                "required_steps": list(required_steps)}


def candidate_tracking_shape_ok(tracking, exclusions):
    if not isinstance(tracking, dict) or set(tracking) - {"version", "mode", "base", "current", "limits"} \
            or type(tracking.get("version")) is not int or tracking["version"] != 1 \
            or tracking.get("mode") != "enabled":
        return False
    candidate = tracking.get("current")
    if not candidate_module().snapshot_identity_ok(candidate):
        return False
    if tracking.get("base") != candidate["base"] or exclusions != candidate["excluded_side_effects"]:
        return False
    try:
        candidate_module().normalize_limits(tracking.get("limits"))
    except candidate_module().CandidateError:
        return False
    return True


def candidate_event_issues(events, meta):
    """Mirror controlled candidate writes; old event chains remain unchanged."""
    problems, tracking, exclusions, receipts = [], None, None, []
    for event in events:
        payload = event.get("payload") or {}
        kind = event.get("event_type")
        # Free events may use the same payload names without acquiring the
        # semantics of a controlled step or candidate writer.
        if kind == "step_started" and "candidate_input" in payload and \
                not candidate_module().snapshot_identity_ok(payload["candidate_input"]):
            problems.append("candidate_input_event_shape")
        if kind == "ticket_created" and "candidate_tracking" in payload:
            tracking = payload["candidate_tracking"]
            exclusions = payload.get("excluded_side_effects")
            if not candidate_tracking_shape_ok(tracking, exclusions):
                problems.append("candidate_snapshot_identity")
        if kind == "metadata_updated":
            updates, appends = payload.get("set") or {}, payload.get("append") or {}
            controlled = {"candidate_tracking", "review_receipts", "excluded_side_effects"}
            if controlled.intersection(set(updates) | set(appends)) or "candidate_control" in payload:
                if type(payload.get("candidate_control")) is not int or payload["candidate_control"] != 1 \
                        or set(updates) != {"candidate_tracking", "excluded_side_effects"} or appends \
                        or event.get("actor") != "icode" or not event.get("request_id"):
                    problems.append("candidate_control_event_shape")
                else:
                    tracking = updates["candidate_tracking"]
                    exclusions = updates["excluded_side_effects"]
                    if not candidate_tracking_shape_ok(tracking, exclusions):
                        problems.append("candidate_snapshot_identity")
        receipt = payload.get("candidate_receipt")
        if kind == "step_finished" and "candidate_receipt" in payload:
            if payload.get("outcome") != "success" \
                    or payload.get("step") not in workflow_contract(meta)["required_review_steps"] \
                    or not isinstance(receipt, dict) or receipt.get("event_id") != event.get("event_id") \
                    or receipt.get("step") != payload.get("step") or receipt.get("attempt") != payload.get("attempt") \
                    or not isinstance(tracking, dict):
                problems.append("candidate_receipt_event_shape")
                continue
            candidate = receipt.get("candidate") or {}
            if receipt.get("candidate_id") != candidate.get("candidate_id") \
                    or not candidate_module().snapshot_identity_ok(candidate):
                problems.append("candidate_receipt_identity")
            receipts.append(receipt)
            tracking = dict(tracking, current=candidate)
    if tracking != meta.get("candidate_tracking") or exclusions != meta.get("excluded_side_effects"):
        problems.append("candidate_tracking_event_mismatch")
    if receipts != (meta.get("review_receipts") or []):
        problems.append("candidate_receipt_event_mismatch")
    return problems


def cmd_candidate(args, *, git_provider=None, require_git_provider=False):
    out_dir = Path(args.dir).resolve()
    if args.phase != "inspect":
        require_stage_action(out_dir, "candidate", git_provider=git_provider, require_git_provider=require_git_provider)
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if access and args.phase not in {"inspect", "capture"}:
        raise ControlError("candidate phase is not enabled", gate_id="candidate_business_propagation_pending")
    if args.phase == "inspect":
        result = delivery_freshness(out_dir, required_steps=tuple(args.require_step or workflow_contract(load_metadata(out_dir))["required_review_steps"]), **access)
        result["freshness_ok"] = result["ok"]
        if not args.require_step:
            result["ok"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # Inspect is a query. --require-step turns it into an acceptance check.
        return 0 if not args.require_step or result["ok"] else 1
    if not isinstance(args.request_id, str) or not args.request_id.strip():
        raise ControlError("candidate capture 需要 request-id", exit_code=2, gate_id="candidate_request")
    with DirLock(out_dir):
        if access:
            _candidate_context_guard(out_dir, load_metadata(out_dir), git_provider, require_git_provider)
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider transaction recovery is not enabled", gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, problems = verify_event_chain(out_dir, meta, **access)
        if problems:
            raise ControlError("事件链不完整，拒绝绑定候选", gate_id="event_chain", violations=problems)
        existing = meta.get("candidate_tracking") or {}
        try:
            base = args.base or existing.get("base") or candidate_module().repository_head(execution_workspace(out_dir, meta), **access)
        except (candidate_module().CandidateError, OSError, UnicodeError) as exc:
            if not access:
                raise
            raise ControlError("candidate base unavailable: " + str(exc), gate_id="candidate_capture") from exc
        exclusions = args.exclude if args.exclude is not None else meta.get("excluded_side_effects") or []
        limits = candidate_limits(args.limits_json) if getattr(args, "limits_json", None) is not None \
                 else candidate_module().normalize_limits(existing.get("limits"))
        # Explicit enable/capture proposes this generation before the atomic
        # metadata event exists. Do not promote legacy attempt declarations.
        prospective = dict(meta, candidate_tracking=dict(existing, mode="enabled", base=base))
        candidate = candidate_snapshot(out_dir, prospective, base=base, excluded_side_effects=exclusions, limits=limits, events=events, **access)
        tracking = {"version": 1, "mode": "enabled", "base": base, "current": candidate, "limits": limits}
        updates = {"candidate_tracking": tracking, "excluded_side_effects": candidate["excluded_side_effects"]}
        payload = {"candidate_control": 1, "set": updates, "append": {}}
        prior = find_idempotent_event(events, args.request_id, "metadata_updated", payload,
                                     actor="icode", payload_keys=("candidate_control", "set", "append"))
        if access:
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        if prior:
            print(json.dumps({"ok": True, "already_applied": True, "event_id": prior["event_id"],
                              "candidate_id": candidate["candidate_id"]}, ensure_ascii=False, indent=2))
            return 0
        updated = dict(meta, **updates)
        def before_publish():
            # A bounded software checkpoint, not an atomic snapshot/epoch proof.
            fresh = candidate_snapshot(out_dir, prospective, base=base, excluded_side_effects=exclusions,
                                       limits=limits, events=events, **access)
            if fresh["candidate_id"] != candidate["candidate_id"]:
                raise ControlError("candidate changed before publication", gate_id="candidate_capture")
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)

        publication = dict(before_publish=before_publish, **access) if access else {}
        event = commit_metadata_and_event(out_dir, meta, updated, "metadata_updated", payload,
                                          request_id=args.request_id, **publication)
    print(json.dumps({"ok": True, "already_applied": False, "event_id": event["event_id"],
                      "candidate_id": candidate["candidate_id"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_bind_execution_root(args):
    out_dir = Path(args.dir).resolve()
    require_stage_action(out_dir, "execution_root")
    if not isinstance(args.request_id, str) or not args.request_id.strip():
        raise ControlError("绑定需要非空 request-id", exit_code=2,
                           gate_id="execution_binding_request")
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("ticket_id") != args.ticket_id:
            raise ControlError("绑定 ticket-id 不匹配", gate_id="execution_binding_ticket")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝绑定", gate_id="event_chain",
                               violations=problems)
        binding = capture_execution_binding(args.execution_root)
        proposed = dict(meta, execution_binding=binding)
        bound_execution_workspace(out_dir, proposed)
        payload = {"execution_root_binding": 1, "set": {"execution_binding": binding},
                   "append": {}}
        prior = find_idempotent_event(events, args.request_id, "metadata_updated", payload,
                                     actor="icode",
                                     payload_keys=("execution_root_binding", "set", "append"))
        if prior:
            if meta.get("execution_binding") != binding:
                raise ControlError("绑定重放与当前对象不一致", gate_id="execution_binding_identity")
            print(json.dumps({"ok": True, "already_applied": True,
                              "event_id": prior["event_id"], "ticket_id": args.ticket_id,
                              "request_id": args.request_id}, ensure_ascii=False, indent=2))
            return 0
        if "execution_binding" in meta:
            raise ControlError("执行根已绑定，禁止替换或使用新请求重绑",
                               gate_id="execution_binding_immutable")
        if meta.get("close_state") is not None:
            raise ControlError("关闭流程中禁止绑定", gate_id="execution_binding_quiescence")
        mirror = ExecutionBindingMirror(meta)
        for event in events:
            mirror.consume(event)
        if mirror.open_steps or mirror.open_operations or mirror.open_agents or mirror.closed:
            raise ControlError("存在未结执行或关闭状态，禁止绑定",
                               gate_id="execution_binding_quiescence")
        require_valid_metadata(proposed)
        event = commit_metadata_and_event(out_dir, meta, proposed, "metadata_updated", payload,
                                          request_id=args.request_id, actor="icode")
    print(json.dumps({"ok": True, "event_id": event["event_id"],
                      "ticket_id": args.ticket_id, "request_id": args.request_id},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_metadata_update(args):
    """原子更新已登记业务字段；生命周期控制字段只允许专用命令维护。"""
    out_dir = Path(args.dir).resolve()
    try:
        updates = _read_json_argument(getattr(args, "set_json", None),
                                       getattr(args, "set_json_file", None), "--set-json")
        appends = _read_json_argument(getattr(args, "append_json", None),
                                      getattr(args, "append_json_file", None), "--append-json")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError(f"metadata-update 参数不是合法 JSON: {exc}", exit_code=2)
    if not isinstance(updates, dict) or not isinstance(appends, dict):
        raise ControlError("--set-json/--append-json 必须是 JSON 对象", exit_code=2)
    if not updates and not appends:
        raise ControlError("metadata-update 需 --set-json 或 --append-json", exit_code=2)
    overlap = sorted(set(updates).intersection(appends))
    if overlap:
        raise ControlError(f"同一字段不能同时 set 与 append: {overlap}", exit_code=2,
                           gate_id="metadata_update_overlap")
    touched = set(updates).union(appends)
    unknown = sorted(touched - known_metadata_fields())
    if unknown:
        raise ControlError(
            f"metadata-update 含未登记顶层字段: {unknown}", exit_code=2,
            gate_id="metadata_update_field_allowlist",
            hint="实验字段放入 extensions.<namespace>，并通过 --set-json 更新 extensions")
    protected = sorted(touched.intersection(METADATA_UPDATE_PROTECTED))
    if protected:
        raise ControlError(
            f"字段由专用控制面命令维护，metadata-update 禁止改写: {protected}",
            exit_code=2, gate_id="metadata_update_protected",
            hint="status/completed_steps/delivery_verdict 用 transition；证据结论用 "
                 "record-claim；验证记录用 record-verification；close_state 用 close-phase；"
                 "indexed 用 index-write")
    invalid_append = sorted(key for key, value in appends.items()
                            if not isinstance(value, list))
    if invalid_append:
        raise ControlError(
            f"--append-json 的每个值必须是待追加元素数组: {invalid_append}",
            exit_code=2, gate_id="metadata_append_shape")
    payload = {"set": updates, "append": appends}

    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("mode") == "lite":
            protected_lite = {"mode", "workflow_version", "requirement"}
            if touched & protected_lite or "requirement_deltas" in updates:
                raise ControlError("lite 出生身份与原始需求不可覆盖；需求变更只允许追加", gate_id="lite_identity_frozen")
        close_state = meta.get("close_state")
        if close_state is not None:
            phases = load_state_machine()["close_phases"]
            if phases.index(close_state) >= phases.index("archived"):
                archive_root = Path(meta["archive_path"]).resolve() \
                    if meta.get("archive_path") else None
                if archive_root != out_dir:
                    raise ControlError(
                        f"archived 后 metadata 控制根已交接到 {archive_root}，"
                        f"禁止继续写源目录 {out_dir}",
                        exit_code=1, gate_id="close_control_handoff")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝更新 metadata", exit_code=1,
                               gate_id="event_chain", violations=problems)
        replay = next((event for event in events if args.request_id and event.get("request_id") == args.request_id), None)
        replay_payload = replay.get("payload") or {} if replay else {}
        comparable_payload = {"metadata_update_request": payload} if "metadata_update_request" in replay_payload else payload
        prior = find_idempotent_event(
            events, args.request_id, "metadata_updated", comparable_payload,
            actor=args.actor, payload_keys=("metadata_update_request",) if "metadata_update_request" in replay_payload else ("set", "append"))
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id,
                              "event_id": prior["event_id"],
                              "changed_fields": sorted(touched)}, ensure_ascii=False, indent=2))
            return 0
        if close_state == "closed":
            raise ControlError(
                "closed 工单禁止更新 metadata；需要修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        if close_state is not None:
            disallowed = sorted(touched - CLOSE_BOOKKEEPING_FIELDS)
            if disallowed:
                raise ControlError(
                    f"关闭流程中只允许更新拓扑/提交/归档账本字段，收到: {disallowed}",
                    exit_code=1, gate_id="close_metadata_frozen")
        if meta.get("mode") == "lite" and touched & {
                "requirement_deltas", "scope_contract", "semantic_decisions", "acceptance_contract",
                "verification_contract", "impact_contract"} and meta["status"] != "lite_prepare_in_progress":
            # 精确历史回放已在上方处理；新语义只能在撤销下游投影后写入。
            raise ControlError("语义合同变化先受控回退 Prepare", gate_id="lite_prepare_required")
        updated = dict(meta)
        updated.update(updates)
        for key, values in appends.items():
            current = updated.get(key)
            if current is None:
                current = []
            if not isinstance(current, list):
                raise ControlError(
                    f"metadata.{key} 当前不是数组，不能 append",
                    exit_code=1, gate_id="metadata_append_target")
            updated[key] = list(current) + values
        evidence_api = verification_evidence_module()
        if evidence_api.build_starts(updated) != evidence_api.build_starts(meta):
            raise ControlError("extensions.verification.build_starts 只能由 build-start 维护", gate_id="metadata_update_protected")
        if "risk_profile" in touched and not evidence_api.real_env_risk(meta) and evidence_api.real_env_risk(updated) \
                and updated.get("verification_contract") is None:
            updated["verification_contract"] = evidence_api.pending_contract()
            payload = {"set": dict(updates, verification_contract=updated["verification_contract"]), "append": appends,
                       "metadata_update_request": {"set": updates, "append": appends}}
        current_skill_runs = ((((meta.get("extensions") or {}).get("skills") or {})
                               .get("runs")) or [])
        updated_skill_runs = ((((updated.get("extensions") or {}).get("skills") or {})
                               .get("runs")) or [])
        if updated_skill_runs != current_skill_runs:
            raise ControlError(
                "extensions.skills.runs 由 record-skill-run 专用命令维护，"
                "metadata-update 禁止改写",
                exit_code=2, gate_id="metadata_update_protected")
        current_agent_spawns = ((((meta.get("extensions") or {}).get("agent") or {})
                                  .get("spawns")) or [])
        updated_agent_spawns = ((((updated.get("extensions") or {}).get("agent") or {})
                                  .get("spawns")) or [])
        if updated_agent_spawns != current_agent_spawns:
            raise ControlError(
                "extensions.agent.spawns 由 record-agent-spawn/record-agent-result "
                "专用命令维护，metadata-update 禁止改写",
                exit_code=2, gate_id="metadata_update_protected")
        # 冻结的宿主适配器身份只能由受控阶段转换写入；否则调用方可在实施后
        # 用普通 metadata 更新把冻结值改成自己回显适配器的摘要，再顺利签收。
        current_pinned = ((((meta.get("extensions") or {}).get("lite") or {})
                           .get("host_adapter")) or {})
        updated_pinned = ((((updated.get("extensions") or {}).get("lite") or {})
                           .get("host_adapter")) or {})
        if updated_pinned != current_pinned:
            raise ControlError(
                "extensions.lite.host_adapter 由受控阶段转换维护，metadata-update 禁止改写",
                exit_code=2, gate_id="metadata_update_protected")
        if updated == meta:
            print(json.dumps({"ok": True, "no_change": True,
                              "changed_fields": []}, ensure_ascii=False, indent=2))
            return 0
        require_valid_metadata(updated)
        event = commit_metadata_and_event(
            out_dir, meta, updated, "metadata_updated", payload,
            request_id=args.request_id, actor=args.actor)
        archive_root = Path(updated["archive_path"]).resolve() \
            if updated.get("archive_path") else None
        if (updated.get("close_state") is not None and archive_root == out_dir
                and updated.get("close_state") != "close_planned"):
            refresh_archive_manifest_control_files(out_dir, updated.get("ticket_id"))
    print(json.dumps({"ok": True, "event_id": event["event_id"],
                      "changed_fields": sorted(touched)}, ensure_ascii=False, indent=2))
    return 0


def cmd_record_claim(args):
    """原子追加结构化证据 claim，并用事件链约束其来源与边界。"""
    out_dir = Path(args.dir).resolve()
    statement = (args.statement or "").strip()
    source = (args.source or "").strip()
    boundary = (args.boundary or "").strip()
    evidence = [item.strip() for item in (args.evidence or []) if item.strip()]
    contradicted_by = [item.strip() for item in (args.contradicted_by or []) if item.strip()]
    next_action = args.next_action.strip() if args.next_action and args.next_action.strip() else None
    if not statement:
        raise ControlError("claim statement 不能为空", exit_code=2,
                           gate_id="claim_statement")
    if not source or not boundary:
        raise ControlError("claim 必须提供非空 --source 与 --boundary", exit_code=2,
                           gate_id="claim_source_boundary")
    if args.kind in {"fact", "refuted"} and not evidence:
        raise ControlError(
            f"{args.kind} claim 必须至少提供一条 --evidence",
            exit_code=2, gate_id="claim_evidence_required")
    status_by_kind = {
        "fact": "supported",
        "inference": "open",
        "unobserved": "open",
        "refuted": "refuted",
    }
    input_payload = {
        "statement": statement,
        "kind": args.kind,
        "source": source,
        "boundary": boundary,
        "evidence": evidence,
        "contradicted_by": contradicted_by,
        "next_action": next_action,
        "status": status_by_kind[args.kind],
    }
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止继续追加 claim；需修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录 claim", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(
            events, args.request_id, "claim_recorded", input_payload,
            actor=args.actor, payload_keys=tuple(input_payload))
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id,
                              "claim_id": prior["payload"].get("claim_id"),
                              "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
            return 0
        claim = {"claim_id": str(uuid.uuid4()), "at": now_iso(), **input_payload}
        before_meta = dict(meta)
        claims = list(meta.get("claims") or [])
        claims.append(claim)
        meta["claims"] = claims
        require_valid_metadata(meta)
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "claim_recorded", claim,
            request_id=args.request_id, actor=args.actor)
    print(json.dumps({"ok": True, "claim": claim, "event_id": event["event_id"]},
                     ensure_ascii=False, indent=2))
    return 0


def verification_json(raw, label):
    if raw is None:
        return None
    try:
        value = strict_json_loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ControlError(f"{label} 不是严格 JSON: {exc}", exit_code=2, gate_id="verification_evidence") from exc
    if not isinstance(value, dict):
        raise ControlError(f"{label} 必须是 JSON 对象", exit_code=2, gate_id="verification_evidence")
    return value


def cmd_build_start(args, *, git_provider=None, require_git_provider=False):
    """Observe a dedicated build directory before execution, without clearing it."""
    if not isinstance(args.request_id, str) or not args.request_id.strip():
        raise ControlError("build-start requires a nonempty --request-id", exit_code=2)
    out_dir = Path(args.dir).resolve()
    require_stage_action(out_dir, "build", git_provider=git_provider, require_git_provider=require_git_provider)
    parameters = verification_json(args.parameters_json, "--parameters-json")
    if not isinstance(parameters, dict) or set(parameters) != {"configure_command", "build_command"}:
        raise ControlError("build-start 参数必须且只能包含 configure_command/build_command argv 数组", exit_code=2)
    for command in parameters.values():
        if not isinstance(command, list) or not command or len(command) > 128 or \
                any(not isinstance(arg, str) or not arg or len(arg) > 256 or "\n" in arg for arg in command):
            raise ControlError("build-start commands must be bounded argv arrays, not complete shell text", exit_code=2)
        if any(any(word in arg.lower() for word in ("password", "passwd", "api_key", "apikey", "secret", "token", "cookie", "authorization", "bearer")) for arg in command):
            raise ControlError("build-start parameters must not contain credentials", exit_code=2, gate_id="verification_sensitive")
        if command[0] in {"sh", "bash", "zsh", "cmd", "powershell"} and any(arg in {"-c", "/c", "-Command"} for arg in command[1:]):
            raise ControlError("build-start does not store shell execution strings", exit_code=2)
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    with DirLock(out_dir):
        if access:
            _candidate_context_guard(out_dir, load_metadata(out_dir), git_provider, require_git_provider)
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider transaction recovery is not enabled", gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, problems = verify_event_chain(out_dir, meta, **access)
        if problems:
            raise ControlError("现有事件链不完整", gate_id="event_chain", violations=problems)
        input_data = {"build_dir": str(Path(args.build_dir).absolute()), "candidate_id": args.candidate_id, "parameters": parameters}
        prior = find_idempotent_event(events, args.request_id, "metadata_updated", {"build_start_input": input_data}, payload_keys=("build_start_input",))
        if prior:
            if access:
                live = candidate_snapshot(out_dir, meta, events=events, **access)
                if live["candidate_id"] != args.candidate_id:
                    raise ControlError("build-start source changed before replay", gate_id="build_start_receipt")
                _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
            print(json.dumps({"ok": True, "already_applied": True, "event_id": prior["event_id"]}, ensure_ascii=False))
            return 0
        evidence_api = verification_evidence_module()
        try:
            candidate = candidate_snapshot(out_dir, meta, events=events, **access)
            if args.candidate_id != candidate["candidate_id"]:
                raise evidence_api.EvidenceError("build-start must explicitly bind the live source candidate")
            directory, info = evidence_api.safe_directory(args.build_dir, execution_workspace(out_dir, meta))
            with os.scandir(directory) as entries:
                empty = next(entries, None) is None
        except (evidence_api.EvidenceError, OSError, ValueError) as exc:
            raise ControlError(str(exc), gate_id="build_start_receipt") from exc
        event_id = str(uuid.uuid4())
        receipt = {"version": 1, "event_id": event_id, "observed_at": now_iso(), "observed_ns": time.time_ns(),
                   "build_dir": str(directory), "directory": {"device": info.st_dev, "inode": info.st_ino,
                       "mode": stat.S_IMODE(info.st_mode), "empty": empty}, "source_candidate": candidate,
                   "candidate_id": candidate["candidate_id"], "parameters": parameters}
        extensions = dict(meta.get("extensions") or {})
        namespace = dict(extensions.get("verification") or {})
        namespace["build_starts"] = [*evidence_api.build_starts(meta), receipt]
        extensions["verification"] = namespace
        updated = dict(meta, extensions=extensions)
        payload = {"set": {"extensions": extensions}, "append": {}, "build_start_control": 1,
                   "build_start_receipt": receipt, "build_start_input": input_data}
        def before_publish():
            # Recheck the live source at this software checkpoint, not an atomic epoch.
            fresh = candidate_snapshot(out_dir, meta, events=events, **access)
            if fresh["candidate_id"] != candidate["candidate_id"]:
                raise ControlError("build-start source changed before publication", gate_id="build_start_receipt")
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)

        publication = dict(before_publish=before_publish, **access) if access else {}
        event = commit_metadata_and_event(out_dir, meta, updated, "metadata_updated", payload,
                    request_id=args.request_id, event_id=event_id, **publication)
    print(json.dumps({"ok": True, "event_id": event["event_id"], "candidate_id": receipt["candidate_id"],
                      "directory_empty": empty, "build_dir": str(directory)}, ensure_ascii=False, indent=2))
    return 0


def cmd_record_verification(args, *, git_provider=None, require_git_provider=False):
    out_dir = Path(args.dir).resolve()
    # Reject before acquiring a lock or recovering/publishing any transaction.
    # Parse JSON options per leaf so container size is not mistaken for a long
    # evidence value. Existing validators retain responsibility for their shape.
    scanner = workflow_lint_module()
    supplied = {key: value for key, value in vars(args).items() if key not in {"dir", "cmd", "func"}}
    for key, value in supplied.items():
        if key.endswith("_json") and isinstance(value, str):
            try:
                supplied[key] = strict_json_loads(value)
            except (ValueError, RecursionError):
                pass
    if scanner.scan_sensitive(supplied, "$.verification_input"):
        # Keys and values are untrusted too: never echo scanner paths or input.
        raise ControlError("verification input contains credential data or unsafe text",
                           exit_code=1, gate_id="verification_sensitive_data")
    if args.outcome == "pass":
        require_stage_action(out_dir, "verify", git_provider=git_provider, require_git_provider=require_git_provider)
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if args.close_repair and not args.request_id:
        raise ControlError(
            "--close-repair 必须提供 --request-id，避免关闭恢复记录被重复追加",
            exit_code=2, gate_id="close_repair_idempotency")
    if not args.evidence or not args.evidence.strip():
        raise ControlError("verification run 必须提供可审计 --evidence", exit_code=2,
                           gate_id="verification_evidence")
    if args.build_source == "reused" and not args.artifact_identity:
        raise ControlError("--build-source reused 必须提供 --artifact-identity 核对产物",
                           exit_code=2, gate_id="reused_artifact_identity")
    meta_probe = load_metadata(out_dir)
    candidate_enabled = verification_evidence_module().enabled(meta_probe)
    if args.kind == "build":
        # 构建不能借用 deploy/device_test 层级，也不能冒充复用产物后的重编。
        if args.device is not None or args.layer not in {None, "build"} \
                or args.build_source == "reused" or (args.build_source == "existing" and not candidate_enabled):
            raise ControlError(
                "kind=build 只能记录 build 层级，不带 device，不复用既有构建",
                exit_code=2, gate_id="build_verification_scope")
        if args.outcome == "pass" and (
                args.build_source not in ({"fresh", "existing"} if candidate_enabled else {"fresh"})
                or not (args.artifact_identity or "").strip()
                or not (args.baseline or "").strip()):
            raise ControlError(
                "构建 pass 必须提供 fresh 来源、产物身份及实际源码 baseline",
                exit_code=2, gate_id="build_verification_identity")
        args.layer = "build"
    metrics = None
    if args.metrics_json is not None:
        try:
            metrics = strict_json_loads(args.metrics_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ControlError(f"--metrics-json 必须是严格 JSON 对象: {exc}", exit_code=2,
                               gate_id="verification_metrics")
        if not isinstance(metrics, dict):
            raise ControlError("--metrics-json 必须是 JSON 对象", exit_code=2,
                               gate_id="verification_metrics")
        name_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
        for name, value in metrics.items():
            if not name_pattern.fullmatch(name):
                raise ControlError(f"非法指标名: {name!r}", exit_code=2,
                                   gate_id="verification_metrics")
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                raise ControlError(f"指标 {name!r} 必须是有限数值", exit_code=2,
                                   gate_id="verification_metrics")
    input_payload = {
        "kind": args.kind,
        "build_source": args.build_source,
        "device": args.device,
        "artifact_identity": args.artifact_identity,
        "window": args.window,
        "layer": args.layer,
        "consumer": args.consumer,
        "scenario": args.scenario,
        "profile": args.profile,
        "baseline_ref": args.baseline_ref,
        "metrics": metrics,
        "baseline": args.baseline,
        "outcome": args.outcome,
        "evidence": args.evidence,
        "note": args.note,
    }
    optional_fields = {"candidate_id": args.candidate_id, "environment": args.environment,
                       "supersedes_run_id": args.supersedes_run_id}
    for field in ("reuse", "build_provenance", "deploy_provenance", "runtime_provenance", "direct_test"):
        optional_fields[field] = verification_json(getattr(args, field + "_json"), "--" + field.replace("_", "-") + "-json")
    input_payload.update({key: value for key, value in optional_fields.items() if value is not None})
    with DirLock(out_dir):
        if access:
            probe = load_metadata(out_dir)
            _candidate_context_guard(out_dir, probe, git_provider, require_git_provider)
            if not verification_evidence_module().enabled(probe):
                raise ControlError("candidate tracking required", gate_id="candidate_provider_context")
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider transaction recovery is not enabled",
                                   gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        close_state = meta.get("close_state")
        if close_state is not None:
            repair_allowed = close_state == "close_planned" and args.close_repair
            if not repair_allowed:
                raise ControlError(
                    "工单已进入关闭流程，禁止继续追加验证记录；仅 close_planned 且归档前"
                    "可用 --close-repair 补录已取得的验证证据，其余情况需先 reopen",
                    exit_code=1, gate_id="closed_ticket_mutation_frozen")
        elif args.close_repair:
            raise ControlError(
                "--close-repair 仅用于 close_state=close_planned 的归档前证据恢复",
                exit_code=1, gate_id="close_repair_state")
        events, problems = verify_event_chain(out_dir, meta, **access)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录验证", exit_code=1,
                               gate_id="event_chain", violations=problems)
        evidence_api = verification_evidence_module()
        try:
            if evidence_api.enabled(meta):
                if args.environment is None:
                    raise evidence_api.EvidenceError("enabled verification requires explicit --environment")
                live = candidate_snapshot(out_dir, meta, events=events, **access)
                input_payload["candidate"] = evidence_api.resolve_candidate(args.candidate_id, meta, events, live, candidate_module())
            elif args.candidate_id is not None:
                raise evidence_api.EvidenceError("candidate-id requires an explicitly enabled candidate contract")
            raw_bundle = getattr(args, "evidence_bundle_json", None)
            bundle_path = getattr(args, "evidence_bundle", None)
            if raw_bundle is not None or bundle_path is not None:
                contract = meta.get("verification_contract")
                if not isinstance(contract, dict) or "evidence_contract" not in contract:
                    raise evidence_api.EvidenceError("evidence_bundle requires an explicit evidence_contract")
                input_payload["evidence_bundle"] = (
                    verification_json(raw_bundle, "--evidence-bundle-json") if raw_bundle is not None else
                    evidence_api.bundle_module().make_reference(bundle_path, out_dir))
            validated_bundle = evidence_api.validate_bundle(input_payload, meta, out_dir,
                                                           review_validator=stage_module().review_validator(meta))
            if validated_bundle is not None:
                # A bundle is captured before registration; preserve its verified
                # run identity rather than generating an unrelated run UUID.
                input_payload["run_id"] = validated_bundle["bundle"]["run_id"]
                existing = next((event for event in events if event.get("event_type") == "verification_recorded"
                                 and (event.get("payload") or {}).get("run_id") == input_payload["run_id"]), None)
                if existing is not None and (not args.request_id or existing.get("request_id") != args.request_id):
                    raise evidence_api.EvidenceError("duplicate evidence bundle run_id")
            if getattr(args, "test_report", None) is not None:
                report_api = evidence_api.test_result_module()
                workspace = execution_workspace(out_dir, meta)
                report = report_api.report_path(args.test_report, workspace)
                previous = next((event for event in events if args.request_id and event.get("request_id") == args.request_id
                                 and event.get("event_type") == "verification_recorded"), None)
                stored = (previous.get("payload") or {}).get("test_summary") if previous else None
                # An exact request replay returns the immutable observation;
                # every new record reads its explicitly selected report bytes.
                if isinstance(stored, dict) and (stored.get("report") or {}).get("path") == str(report):
                    input_payload["test_summary"] = stored
                else:
                    input_payload["test_summary"] = report_api.summarize_report(report, workspace)
            prior_runs = evidence_api.event_runs(events)
            evidence_api.validate_supersedes(input_payload, prior_runs)
            evidence_api.validate_reuse(input_payload, prior_runs, candidate_module(), **access)
            evidence_api.validate_direct(input_payload)
            evidence_api.validate_test_summary(input_payload)
            # Idempotent replay checks stored inputs before re-reading outputs:
            # a historic receipt remains a receipt after artifacts move away.
            prior = find_idempotent_event(events, args.request_id, "verification_recorded", input_payload,
                                          payload_keys=(*input_payload, "test_summary"))
            if prior is None:
                # Exact replay is a historical receipt, not a new observation.
                evidence_api.validate_direct(input_payload, strict_outcome=True)
                evidence_api.validate_provenance(input_payload, meta, events, execution_workspace(out_dir, meta), candidate_module(),
                                                live_candidate=live if evidence_api.enabled(meta) else None, **access)
                if evidence_api.enabled(meta) and args.kind == "build" and args.build_source == "fresh" and args.outcome == "pass":
                    if access:
                        proof = evidence_api._compare_candidates(
                            input_payload["candidate"], candidate_snapshot(out_dir, meta, events=events, **access),
                            candidate_module(), **access)
                    else:
                        proof = candidate_module().compare_candidates(
                            input_payload["candidate"], candidate_snapshot(out_dir, meta, events=events))
                    if not proof["equivalent"]:
                        raise evidence_api.EvidenceError("source candidate changed while verifying build artifacts")
        except (evidence_api.EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
            raise ControlError(str(exc), exit_code=1, gate_id="verification_evidence") from exc
        prior = find_idempotent_event(
            events, args.request_id, "verification_recorded", input_payload,
            payload_keys=(*input_payload, "test_summary"))
        if scanner.scan_sensitive(input_payload, "$.verification_runs", direct_verification_run=True):
            raise ControlError("verification record contains credential data or unsafe text",
                               exit_code=1, gate_id="verification_sensitive_data")
        def before_publish():
            # Bind this invocation's live source, not a permitted historical origin.
            current = candidate_snapshot(out_dir, meta, events=events, **access)
            if current["candidate_id"] != live["candidate_id"]:
                raise ControlError("source candidate changed before verification publication",
                                   gate_id="verification_evidence")
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        if prior:
            if access:
                before_publish()
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id,
                              "run_id": prior["payload"].get("run_id"),
                              "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
            return 0
        run = {"run_id": str(uuid.uuid4()), "at": now_iso(), **input_payload}
        before_meta = dict(meta)
        runs = list(meta.get("verification_runs") or [])
        runs.append(run)
        meta["verification_runs"] = runs
        event_payload = run
        if meta.get("mode") == "lite" and meta.get("status") == "completed":
            cells = (meta.get("verification_contract") or {}).get("required_cells") or []
            key = lambda row: tuple(row.get(k) for k in ("layer", "consumer", "scenario"))
            if any(key(cell) == key(run) for cell in cells):
                issues, _ = lite_evidence_issues(out_dir, meta, events)
                if issues:
                    execution = execution_trace(events)
                    if execution["open_steps"] or execution["open_operations"] or execution["open_agents"]:
                        # 显式 verify 可仍有自己的开放 attempt；先撤销 verified，终结后按标准回退。
                        meta["delivery_verdict"] = "blocked"
                    else:
                        meta.update(status="lite_code_in_progress", delivery_verdict="verification_pending",
                                    completed_steps=["lite_prepare"])
                        event_payload = dict(run, lite_invalidation={"from": "completed", "to": "lite_code_in_progress",
                                             "delivery_verdict": None, "reason": "latest_required_verification_invalid"})
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "verification_recorded", event_payload,
            request_id=args.request_id,
            **(dict(access, before_publish=before_publish) if access else {}))
    print(json.dumps({"ok": True, "run": {key: value for key, value in run.items() if key != "candidate"}, "event_id": event["event_id"]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_record_skill_run(args):
    """原子记录技能路由的采用、成本与独有发现，不改变工单 verdict。"""
    out_dir = Path(args.dir).resolve()
    skill = (args.skill or "").strip()
    trigger = (args.trigger or "").strip()
    evidence_refs = [item.strip() for item in (args.evidence_ref or []) if item.strip()]
    unique_findings = [item.strip() for item in (args.unique_finding or []) if item.strip()]
    agent_id = args.agent_id.strip() if args.agent_id and args.agent_id.strip() else None
    if not skill or not trigger:
        raise ControlError("skill run 必须提供非空 --skill 与 --trigger", exit_code=2,
                           gate_id="skill_run_identity")
    if not evidence_refs:
        raise ControlError("skill run 至少需要一条 --evidence-ref", exit_code=2,
                           gate_id="skill_run_evidence")
    if args.elapsed_ms < 0 or args.estimated_tokens < 0:
        raise ControlError("--elapsed-ms/--estimated-tokens 不能为负数", exit_code=2,
                           gate_id="skill_run_cost")
    input_payload = {
        "skill": skill,
        "trigger": trigger,
        "result": args.result,
        "adopted": args.adopted == "true",
        "evidence_refs": evidence_refs,
        "elapsed_ms": args.elapsed_ms,
        "estimated_tokens": args.estimated_tokens,
        "unique_findings": unique_findings,
        "agent_id": agent_id,
    }
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止继续追加 skill run；需修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录 skill run", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(
            events, args.request_id, "skill_run_recorded", input_payload,
            actor=args.actor, payload_keys=tuple(input_payload))
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id,
                              "run_id": prior["payload"].get("run_id"),
                              "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
            return 0
        run = {"run_id": str(uuid.uuid4()), "at": now_iso(), **input_payload}
        before_meta = dict(meta)
        extensions = dict(meta.get("extensions") or {})
        skills_ns = dict(extensions.get("skills") or {})
        runs = list(skills_ns.get("runs") or [])
        runs.append(run)
        skills_ns["runs"] = runs
        extensions["skills"] = skills_ns
        meta["extensions"] = extensions
        require_valid_metadata(meta)
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "skill_run_recorded", run,
            request_id=args.request_id, actor=args.actor)
    print(json.dumps({"ok": True, "run": run, "event_id": event["event_id"]},
                     ensure_ascii=False, indent=2))
    return 0


def _agent_spawns(meta):
    return list((((meta.get("extensions") or {}).get("agent") or {}).get("spawns")) or [])


def _lite_agent_receipt(out_dir, meta, events, spawn, host_session_id, report, *,
                        host_receipt):
    """校验回执绑定并生成待落盘报告；不写入磁盘，可信核验通过后再提交。"""
    reviewed = _active_lite_review(meta, events, spawn["lite_review_attempt"])
    binding = spawn["lite_review_binding"]
    current = candidate_snapshot(out_dir, meta, events=events)
    if (reviewed or {}).get("candidate_id") != binding["candidate_id"] \
            or not candidate_module().compare_candidates(reviewed, current)["equivalent"] \
            or lite_module().contract_digest(meta) != binding["contract_digest"]:
        raise ControlError("审核返回已失效", gate_id="lite_review_stale")
    if any(fact.get("host_session_id") == host_session_id for fact in lite_review_records(events)[1]):
        raise ControlError("审核宿主复用了已有上下文", gate_id="lite_review_host")
    # P1：回执须绑定本次登记——回传 spawn 下发的 challenge，且身份/spawn 一致。
    challenge = spawn.get("host_receipt_challenge")
    if not isinstance(challenge, str) or not challenge:
        raise ControlError("该审核登记缺少宿主挑战，无法核验回执来源", gate_id="lite_review_host_receipt")
    if not isinstance(host_receipt, dict):
        raise ControlError("缺少宿主回执，无法核验结果来自本次启动", gate_id="lite_review_host_receipt")
    if host_receipt.get("challenge") != challenge \
            or host_receipt.get("spawn_id") != spawn.get("spawn_id") \
            or host_receipt.get("host_session_id") != host_session_id:
        raise ControlError("宿主回执与本次登记的 challenge/spawn/身份不一致",
                           gate_id="lite_review_host_receipt")
    if any(fact.get("host_receipt", {}).get("challenge") == challenge
           for fact in lite_review_records(events)[1] if fact.get("host_receipt")):
        raise ControlError("宿主回执 challenge 已被使用，禁止重放", gate_id="lite_review_host_receipt")
    report = dict(report, schema_version=1, attempt=spawn["lite_review_attempt"], **binding)
    violations = validate_against(report, SCHEMA_DIR / "lite-review.schema.json", "lite_review_schema")
    if violations:
        raise ControlError("审核报告不满足 schema", gate_id="lite_review_report", violations=violations)
    name = "lite_review_" + spawn["spawn_id"] + ".json"
    try:
        data = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, RecursionError) as exc:
        raise ControlError("审核报告包含无效 JSON 值", gate_id="lite_review_report") from exc
    return name, data


def _commit_lite_agent_report(out_dir, name, data, attestation, spawn, host_session_id,
                              degraded=False):
    """仅在可信宿主确认后落盘；失败尝试不占用报告路径。

    degraded=True 表示本工单已记录宿主适配器不可用：报告照常留痕供追溯，
    但不构成可信确认，交付结论只能是 verification_pending。
    """
    if degraded:
        # 降级路径：没有可信确认，报告只作留痕；调用方保证工单记录了不可用事实。
        if attestation is not None:
            raise ControlError("降级落盘不应携带可信确认",
                               gate_id="lite_review_host_unattested")
    else:
        challenge = spawn.get("host_receipt_challenge")
        if not isinstance(attestation, dict) or attestation.get("launched") is not True \
                or attestation.get("challenge") != challenge \
                or attestation.get("spawn_id") != spawn.get("spawn_id") \
                or attestation.get("host_session_id") != host_session_id \
                or attestation.get("stored_report_sha256") != hashlib.sha256(data).hexdigest():
            raise ControlError("缺少可信宿主确认，结果不能作为审核依据",
                               gate_id="lite_review_host_unattested")
    path = out_dir / name
    # 中断可能在事件提交前留下报告，只允许原字节的精确重试。
    if path.is_symlink() or (path.exists() and (
            not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data)):
        raise ControlError("审核报告路径已被占用", gate_id="lite_review_report")
    if not path.exists():
        atomic_write_bytes(path, data)
    return {"report_path": name, "report_sha256": hashlib.sha256(data).hexdigest()}


def _read_lite_agent_result(args):
    limit = 256 * 1024
    try:
        if args.lite_review_report == "-":
            data = sys.stdin.buffer.read(limit + 1)
        else:
            path = Path(args.lite_review_report)
            if not path.is_file():
                raise ValueError("报告输入须为常规文件或 stdin")
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("审核报告超过 256 KiB")
        report = strict_json_loads(data.decode("utf-8"))
        if not isinstance(report, dict):
            raise ValueError("审核报告应为 JSON 对象")
    except (OSError, ValueError, RecursionError) as exc:
        raise ControlError("无法读取审核报告", gate_id="lite_review_report") from exc
    return report, hashlib.sha256(data).hexdigest()


def _read_host_receipt(args):
    """读取宿主提供的启动/返回回执；宿主中立：仅校验与本次登记绑定的字段。"""
    limit = 64 * 1024
    path_arg = getattr(args, "host_receipt", None)
    try:
        if path_arg == "-":
            data = sys.stdin.buffer.read(limit + 1)
        else:
            path = Path(path_arg)
            if not path.is_file():
                raise ValueError("宿主回执须为常规文件或 stdin")
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("宿主回执超过 64 KiB")
        receipt = strict_json_loads(data.decode("utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("宿主回执应为 JSON 对象")
    except (OSError, ValueError, RecursionError) as exc:
        raise ControlError("无法读取宿主回执", gate_id="lite_review_host_receipt") from exc
    return receipt, hashlib.sha256(data).hexdigest()


# 宿主适配器只从宿主侧配置读取，调用方不能按次指定可执行文件。
# 配置固定位于 <宿主真实主目录>/.icode/host_adapter.json，部署时由宿主写入并锁定权限。
# 不接受任何环境变量重定向：调用方本就能设置环境变量，按次/按进程指向自建配置
# 会让"摘要与配置一致"退化为"摘要与调用方给的配置一致"，从而绕过来源信任。
# 主目录同样不能取自 HOME（见 _host_home）：设置 HOME 就能换掉整个信任根。
# 测试只用显式进程内钩子 _HOST_CONFIG_ROOT_OVERRIDE 隔离信任根。
_HOST_CONFIG_ROOT_OVERRIDE = None


def _host_path_is_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _host_home():
    """返回宿主真实主目录，不信任 HOME 等调用方可设置的环境变量。

    HOME 只是进程环境的约定值，调用方设置后 Path.home() 会跟随改变，等于
    把信任根交给调用方。这里改从系统账户数据库取值；取不到一律 fail-closed，
    绝不回退到环境变量。
    """
    if os.name == "nt":
        return _windows_host_home()
    if os.name != "posix":
        raise ControlError("宿主信任根无法在该平台安全确定（缺少账户数据库）",
                           gate_id="lite_host_adapter_unavailable")
    import pwd
    try:
        entry = pwd.getpwuid(os.getuid())
    except KeyError as exc:
        raise ControlError("无法从系统账户数据库确定宿主主目录",
                           gate_id="lite_host_adapter_unavailable") from exc
    home = (getattr(entry, "pw_dir", "") or "").strip()
    if not home or not os.path.isabs(home):
        raise ControlError("系统账户数据库返回的宿主主目录不可信",
                           gate_id="lite_host_adapter_unavailable")
    resolved = Path(home)
    if _host_path_is_link(resolved):
        raise ControlError("宿主主目录是符号链接，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    if not resolved.is_dir():
        raise ControlError("宿主主目录不存在，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    if _path_is_others_writable(resolved):
        raise ControlError("宿主主目录可被其他用户改写，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    return resolved


def _windows_host_home():
    r"""Windows：经 Known Folder API 取用户主目录，不使用 USERPROFILE/HOME。

    USERPROFILE 与 HOME 一样是进程环境值，调用方能随手改掉。这里用
    SHGetKnownFolderPath(FOLDERID_Profile)：它由 Shell 根据当前登录令牌的
    SID 解析，不读可被伪造的环境变量，也天然处理了 ProfileImagePath 里
    `%SystemDrive%` 之类的可展开形式。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError as exc:
        raise ControlError("宿主信任根无法在 Windows 上安全确定",
                           gate_id="lite_host_adapter_unavailable") from exc

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    # {5E6C858F-0E22-4760-9AFE-EA3317B67173} = FOLDERID_Profile
    folder_id_profile = GUID(0x5E6C858F, 0x0E22, 0x4760,
                             (ctypes.c_ubyte * 8)(0x9A, 0xFE, 0xEA, 0x33,
                                                  0x17, 0xB6, 0x71, 0x73))
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    # HRESULT 是有符号 32 位。c_long 在 Windows(LLP64) 上正是 32 位，
    # 而在 POSIX(LP64) 上是 64 位；本函数只在 Windows 运行，故用 c_long 安全。
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p)]
    # CoTaskMemFree 必须显式声明：默认返回值被当作 c_int，指针参数需 c_void_p。
    ole32.CoTaskMemFree.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out = ctypes.c_wchar_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id_profile), 0, None,
                                      ctypes.byref(out))
    if hr != 0:
        raise ControlError("Known Folder API 无法解析用户主目录（HRESULT 0x%08X）" % (hr & 0xFFFFFFFF),
                           gate_id="lite_host_adapter_unavailable")
    try:
        home = (out.value or "").strip()
    finally:
        if out:
            ole32.CoTaskMemFree(out)
    if not home or not os.path.isabs(home):
        raise ControlError("Windows 账户 API 返回的宿主主目录不可信",
                           gate_id="lite_host_adapter_unavailable")
    resolved = Path(home)
    if _host_path_is_link(resolved):
        raise ControlError("宿主主目录是符号链接，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    if not resolved.is_dir():
        raise ControlError("宿主主目录不存在，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    if _path_is_others_writable(resolved):
        raise ControlError("宿主主目录可被其他用户改写，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    return resolved


def _host_config_root():
    if _HOST_CONFIG_ROOT_OVERRIDE is not None:
        return Path(_HOST_CONFIG_ROOT_OVERRIDE)
    # 信任根不接受任何环境变量重定向。测试若需要隔离信任根，必须在本进程内
    # 直接赋值 _HOST_CONFIG_ROOT_OVERRIDE（例如 mock.patch.object），而 CLI
    # 子进程拿不到这个入口，生产调用永远走 _host_home()。
    root = _host_home() / ".icode"
    # 信任根本身也必须不可被旁人改写（对伪造主目录的纵深防御）。
    if _host_path_is_link(root) or (root.exists() and _path_is_others_writable(root)):
        raise ControlError("宿主信任根可被其他用户改写，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    return root


def _default_host_config():
    return _host_config_root() / "host_adapter.json"


def _host_adapter_config():
    """读取并验证宿主适配器配置：固定命令、固定摘要、不可被旁人改写。"""
    config_path = _default_host_config()
    if _host_path_is_link(config_path) or not config_path.is_file():
        raise ControlError("未配置可信宿主适配器（缺少宿主配置 %s）" % config_path,
                           gate_id="lite_host_adapter_unavailable")
    if _path_is_others_writable(config_path):
        raise ControlError("宿主适配器配置可被其他用户改写，拒绝信任",
                           gate_id="lite_host_adapter_unavailable")
    try:
        config = strict_json_loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ControlError("宿主适配器配置无法解析", gate_id="lite_host_adapter_unavailable") from exc
    if not isinstance(config, dict):
        raise ControlError("宿主适配器配置应为 JSON 对象", gate_id="lite_host_adapter_unavailable")
    command, pin = config.get("command"), config.get("sha256")
    if not isinstance(command, str) or not os.path.isabs(command) or not Path(command).is_file():
        raise ControlError("宿主适配器命令必须是已存在的绝对路径", gate_id="lite_host_adapter_unavailable")
    adapter = Path(command)
    if _host_path_is_link(adapter) or _path_is_others_writable(adapter):
        raise ControlError("宿主适配器可被其他用户改写，拒绝信任", gate_id="lite_host_adapter_unavailable")
    if not os.access(adapter, os.X_OK):
        raise ControlError("宿主适配器不可执行", gate_id="lite_host_adapter_unavailable")
    _require_host_owned_location(adapter)
    actual = file_sha256(adapter)
    if not isinstance(pin, str) or pin != actual:
        raise ControlError("宿主适配器摘要与可信配置不一致", gate_id="lite_host_adapter_unavailable")
    return {"path": adapter, "sha256": actual, "config_path": config_path}


def _observe_host_adapter():
    """预检和进入 Code 使用同一套宿主适配器可用性规则。"""
    try:
        trusted = _host_adapter_config()
    except ControlError as exc:
        return {"status": "unavailable", "reason": exc.extra.get("gate_id", "lite_host_adapter_unavailable"),
                "detail": exc.message}
    except (OSError, RuntimeError, ValueError) as exc:
        return {"status": "unavailable", "reason": "lite_host_adapter_unavailable",
                "detail": f"无法核验宿主适配器路径：{exc}"}
    return {"status": "pinned", "sha256": trusted["sha256"], "path": str(trusted["path"]),
            "config_path": str(trusted["config_path"])}


# 其他用户/组可写位。POSIX 上 st_mode 能证明这一点；Windows 的 st_mode 只
# 反映只读属性，无法说明 NTFS ACL 是否向他人授予写权限，必须另读 ACL。
_OTHERS_WRITE_BITS = 0o022
_MACOS_EVERYONE_ACL_UUID = "ABCDEFAB-CDEF-ABCD-EFAB-CDEF0000000C"


def _macos_path_has_foreign_write_acl(path, *, ancestor=False):
    """保守检查 macOS 扩展 ACL 中授予其他主体的写权限。"""
    try:
        result = subprocess.run(
            ["/bin/ls", "-lde", str(path)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
            env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlError("无法读取 macOS ACL：%s" % type(exc).__name__,
                           gate_id="lite_host_adapter_unavailable") from exc
    if result.returncode:
        raise ControlError("无法读取 macOS ACL（退出码 %d）" % result.returncode,
                           gate_id="lite_host_adapter_unavailable")

    import pwd
    current_user = pwd.getpwuid(os.getuid()).pw_name
    write_permissions = {"write", "append", "add_file", "add_subdirectory",
                         "delete_child", "delete", "writeattr", "writeextattr",
                         "writesecurity"}
    for line in result.stdout.splitlines()[1:]:
        entry = re.match(r"^\s*\d+:\s+(.+?)\s+(allow|deny)\s+(.+)$", line)
        if not entry or entry.group(2) != "allow":
            continue
        principal, permissions = entry.groups()[0], entry.group(3)
        granted = {permission.strip() for permission in permissions.split(",")}
        if not granted.intersection(write_permissions):
            continue
        if principal == current_user or principal == "user:" + current_user:
            continue
        # macOS ls may render the Everyone principal as its UUID instead of a name.
        if principal.casefold() == _MACOS_EVERYONE_ACL_UUID.casefold():
            return True
        # Group and unresolved principals may include another account. Reject
        # their write grants instead of assuming the ACL is private.
        if principal.startswith("group:") or principal.startswith("user:"):
            return True
        return True
    return False


def _path_is_others_writable(path, *, ancestor=False):
    """目标是否允许当前用户之外的主体写入；无法判定时抛错（fail-closed）。

    macOS 同时读取扩展 ACL；其它 POSIX 读 st_mode。Windows 读实际 DACL，
    逐个 ACE 判断是否对非当前用户的主体授予写类权限。
    """
    # 检查解析后的目标本身：lstat 只看符号链接自身的权限位，
    # 会让一个指向 0777 目录的 0755 链接被误判为安全。
    resolved = Path(path).resolve()
    if os.name == "posix":
        if os.uname().sysname == "Darwin" and _macos_path_has_foreign_write_acl(
                resolved, ancestor=ancestor):
            return True
        return bool(resolved.stat().st_mode & _OTHERS_WRITE_BITS)
    try:
        return _windows_path_has_foreign_write_ace(resolved, ancestor=ancestor)
    except ControlError:
        raise
    except (AttributeError, OSError, ValueError) as exc:
        # 缺失的 API、畸形 ACE 等原生失败必须转成受控的 ControlError，
        # 否则调用方的降级处理接不住，Windows 上会直接崩而不是按预期降级。
        raise ControlError("Windows ACL 校验失败：%s" % type(exc).__name__,
                           gate_id="lite_host_adapter_unavailable") from exc


def _ace_sid_offset(base, ace_type, object_ace_types, flat_offset):
    """返回 ACE 内 SID 起始偏移。

    普通允许型 ACE 固定为 Mask(4) 之后；对象型 ACE 在 Mask 后还有 Flags(4)
    与按 Flags 位可选出现的两个 GUID，SID 不在固定位置。
    """
    if ace_type not in object_ace_types:
        return flat_offset
    import ctypes
    from ctypes import wintypes
    flags = ctypes.cast(base + flat_offset,
                        ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = flat_offset + 4                     # 越过 Flags
    if flags & 0x1:                              # ACE_OBJECT_TYPE_PRESENT
        offset += 16
    if flags & 0x2:                              # ACE_INHERITED_OBJECT_TYPE_PRESENT
        offset += 16
    return offset


def _windows_path_has_foreign_write_ace(path, *, ancestor=False):
    r"""读 Windows DACL，判断是否存在授予"他人可写"的 ACE。

    只用 GetNamedSecurityInfo + 逐 ACE 解析。真实 ACE 形态多变（OBJECT_INHERIT、
    INHERIT_ONLY、容器快速 ACL 等），逐位解析容易漏判；这里采取保守策略：
    任何无法确定为"仅当前用户/系统/内置管理员可写"的写类 ACE 都视为风险。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError as exc:
        raise ControlError("无法在 Windows 上校验 ACL", gate_id="lite_host_adapter_unavailable") from exc

    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 0x00000004
    OWNER_SECURITY_INFORMATION = 0x00000001
    ERROR_SUCCESS = 0
    # 祖先目录仅允许创建新目录的 ACE（例如盘符根目录）不允许替换现有组件。
    write_mask = 0x00000100 | 0x00000040 | 0x00010000 | 0x00040000 | 0x00080000 | 0x50000000
    if not ancestor:
        write_mask |= 0x00000002 | 0x00000004 | 0x00000010

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 64 位下指针必须显式声明为 c_void_p：ctypes 默认按 c_int 解释返回值，
    # 会把指针高位截断，导致 GetAce/ConvertSidToStringSidW 解引用非法地址。
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    # Win32 没有 GetAceCount：ACE 数量只能由
    # GetAclInformation(AclSizeInformation) 取 ACL_SIZE_INFORMATION.AceCount。
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, wintypes.DWORD]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_wchar_p)]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
                    ("AceSize", wintypes.WORD)]

    class ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD),
                    ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD)]

    AclSizeInformation = 2
    # 允许型 ACE：ACCESS_ALLOWED、_OBJECT、_CALLBACK、_CALLBACK_OBJECT。
    # 0x2 是 SYSTEM_AUDIT、0xa 是 ACCESS_DENIED_CALLBACK，都不是允许型。
    ALLOWED_ACE_TYPES = (0x0, 0x5, 0x9, 0xB)
    OBJECT_ACE_TYPES = (0x5, 0xB)
    ACE_MASK_OFFSET = 4
    ACE_SID_OFFSET_FLAT = 8

    target = str(path)
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        target, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc != ERROR_SUCCESS:
        raise ControlError("无法读取 Windows ACL（错误 %d）" % rc,
                           gate_id="lite_host_adapter_unavailable")
    try:
        if not dacl:
            # 无 DACL 表示所有人完全访问，等同于他人可写。
            return True
        current_sid = _windows_current_sid_string_cached()
        allowed_sids = _windows_privileged_sid_strings()
        if not current_sid:
            # 无法确定"自己"是谁时，任何写类 ACE 都不能判定为安全。
            raise ControlError("无法确定当前用户 SID，拒绝信任",
                               gate_id="lite_host_adapter_unavailable")
        size_info = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(dacl, ctypes.byref(size_info),
                                          ctypes.sizeof(ACL_SIZE_INFORMATION),
                                          AclSizeInformation):
            raise ControlError("无法读取 ACL 的 ACE 数量（错误 %d）" % ctypes.get_last_error(),
                               gate_id="lite_host_adapter_unavailable")
        for index in range(size_info.AceCount):
            ace_ptr = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_ptr)) or not ace_ptr.value:
                raise ControlError("读取 ACE 失败", gate_id="lite_host_adapter_unavailable")
            base = ace_ptr.value
            header = ctypes.cast(ace_ptr, ctypes.POINTER(ACE_HEADER)).contents
            ace_type = header.AceType & 0xFF
            if header.AceFlags & 0x08 or ace_type not in ALLOWED_ACE_TYPES:  # INHERIT_ONLY_ACE
                continue
            mask = ctypes.cast(base + ACE_MASK_OFFSET,
                               ctypes.POINTER(wintypes.DWORD)).contents.value
            if not (mask & write_mask):
                continue
            sid_any = ctypes.c_void_p(base + _ace_sid_offset(base, ace_type,
                                                             OBJECT_ACE_TYPES,
                                                             ACE_SID_OFFSET_FLAT))
            sid_str_ptr = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(sid_any, ctypes.byref(sid_str_ptr)):
                return True
            try:
                sid_str = sid_str_ptr.value or ""
            finally:
                kernel32.LocalFree(sid_str_ptr)
            if sid_str and sid_str != current_sid and sid_str not in allowed_sids:
                return True
        return False
    finally:
        if sd:
            kernel32.LocalFree(sd)


def _windows_privileged_sid_strings():
    """内置的、可安全授予写权限的 SID（本机系统本体）。"""
    return {"S-1-5-18",   # LOCAL SYSTEM
            "S-1-5-32-544"}  # BUILTIN\Administrators


_WINDOWS_CURRENT_SID = None
_WINDOWS_CURRENT_SID_RESOLVED = False


def _windows_current_sid_string_cached():
    global _WINDOWS_CURRENT_SID, _WINDOWS_CURRENT_SID_RESOLVED
    if _WINDOWS_CURRENT_SID_RESOLVED:
        return _WINDOWS_CURRENT_SID
    result = None
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_wchar_p)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        TOKEN_QUERY = 0x0008
        TokenUser = 1
        token = wintypes.HANDLE()
        if advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                     ctypes.byref(token)):
            try:
                size = wintypes.DWORD(0)
                advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
                buf = ctypes.create_string_buffer(size.value)
                if advapi32.GetTokenInformation(token, TokenUser, buf, size,
                                                ctypes.byref(size)):
                    sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents
                    str_sid = ctypes.c_wchar_p()
                    if advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(str_sid)):
                        try:
                            result = str_sid.value
                        finally:
                            kernel32.LocalFree(str_sid)
            finally:
                kernel32.CloseHandle(token)
    except Exception:
        result = None
    _WINDOWS_CURRENT_SID = result
    _WINDOWS_CURRENT_SID_RESOLVED = True
    return result


def _require_host_owned_location(adapter):
    """适配器必须位于不可被组/其他人改写、且不是符号链接的目录链中。

    摘要只能证明文件未被替换，不能证明它来自宿主。这里要求从适配器到根的
    每一级父目录都不可被旁人写入：调用方私有的可写目录、工程工作区中由
    调用方创建的可写目录都会被拒绝。
    """
    resolved = adapter.resolve()
    for parent in adapter.parents:
        try:
            linked = _host_path_is_link(parent)
        except OSError as exc:
            raise ControlError("宿主适配器所在目录不可核验，拒绝信任",
                               gate_id="lite_host_adapter_unavailable") from exc
        if linked:
            raise ControlError("宿主适配器所在目录是符号链接，拒绝信任",
                               gate_id="lite_host_adapter_unavailable")
        if _path_is_others_writable(parent, ancestor=True):
            raise ControlError("宿主适配器所在目录可被其他用户改写，拒绝信任",
                               gate_id="lite_host_adapter_unavailable")
    # 调用方的工作区与临时目录即使权限收紧也属调用方，不能作为信任来源。
    # 只有"进程内注入了信任根"的测试才能落在临时目录：该信号不走环境变量，
    # 生产 CLI 拿不到，因此这条豁免无法被调用方利用。
    if _HOST_CONFIG_ROOT_OVERRIDE is not None:
        return
    denied = [Path.cwd(), Path("/tmp"), Path("/var/tmp")]
    for raw in (os.environ.get("TMPDIR"), os.environ.get("TEMP")):
        if raw:
            denied.append(Path(raw))
    for root in denied:
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if root_resolved == resolved or root_resolved in resolved.parents:
            raise ControlError(
                "宿主适配器不能位于调用方工作区或临时目录，拒绝信任",
                gate_id="lite_host_adapter_unavailable")


def _adapter_suffix(payload):
    """按入口文件类型选择暂存后缀。"""
    if payload.startswith(b"#!"):
        first = payload.split(b"\n", 1)[0].decode("utf-8", "replace")
        if "python" in first:
            return ".py"
        if "/sh" in first or "/bash" in first:
            return ".sh"
        return ".bin"
    if payload.startswith(b"MZ"):
        return ".exe"
    if payload.startswith(b"\x7fELF"):
        return ".bin"
    return ".py" if os.name == "nt" else ".bin"


def _stage_adapter_payload(payload):
    """适配器为自包含入口，私有目录只写入已校验字节。"""
    workspace = Path(tempfile.mkdtemp(prefix="icode-host-adapter-"))
    staged = workspace / ("adapter" + _adapter_suffix(payload))
    try:
        staged.write_bytes(payload)
        staged.chmod(0o500 if os.name == "posix" else stat.S_IREAD)
    except OSError:
        _remove_staged_adapter(staged)
        raise
    return staged


def _run_staged_adapter(staged, request):
    """按平台正确方式启动暂存适配器，始终把请求 JSON 送到 stdin。"""
    payload_in = json.dumps(request, ensure_ascii=False)
    suffix = staged.suffix.lower()
    if os.name == "nt" and suffix not in {".py", ".exe"}:
        raise ControlError("Windows 宿主适配器须使用 Python 或 EXE",
                           gate_id="lite_review_host_unattested")
    command = [sys.executable, "-I", "-X", "utf8", str(staged)] if suffix == ".py" else [str(staged)]
    return subprocess.run(command, input=payload_in, capture_output=True,
                          text=True, timeout=60, encoding="utf-8")


def _remove_staged_adapter(staged):
    """删除整个暂存工作目录；Windows 上只读位会阻止删除，先恢复可写。"""
    if staged is None:
        return
    workspace = staged.parent
    leftover = False
    try:
        if os.name == "nt":
            try:
                staged.chmod(stat.S_IWRITE)
            except OSError:
                pass
        shutil.rmtree(workspace, ignore_errors=True)
        leftover = workspace.exists()
    except OSError:
        leftover = True
    if leftover:
        # 清理失败不应掩盖真正的审核结论，但要留下可追踪的痕迹。
        try:
            sys.stderr.write(f"warning: 未能清理暂存适配器目录 {workspace}\n")
        except Exception:
            pass


def _host_attest(spawn, host_session_id, report_sha256, stored_report_sha256,
                 expected_sha256):
    """执行宿主配置指定的适配器，核验本次启动/返回身份与报告摘要。

    适配器由宿主配置选择并锁定摘要，调用方不能传入自己的程序；控制面只把
    challenge/身份/摘要交给它，要求它查询实际宿主会话后回传 launched=true。

    expected_sha256 是进入 Code 时冻结的身份。必须先比对再执行：未受信任的
    程序一旦跑起来，它造成的副作用无法靠事后拒绝签收撤销。
    """
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise ControlError("缺少 Prepare 阶段确认的宿主适配器身份，不能作为审核依据",
                           gate_id="lite_host_adapter_unavailable")
    trusted = _host_adapter_config()
    adapter = trusted["path"]
    # 执行前：配置摘要与冻结身份必须一致。
    if trusted["sha256"] != expected_sha256:
        raise ControlError("签收所用宿主适配器与 Prepare 阶段确认的身份不一致，拒绝执行",
                           gate_id="lite_host_adapter_unavailable")
    request = {"step": "lite_review", "spawn_id": spawn.get("spawn_id"),
               "host_session_id": host_session_id,
               "challenge": spawn.get("host_receipt_challenge"),
               "report_sha256": report_sha256,
               "stored_report_sha256": stored_report_sha256}
    # 把已核验的字节固定下来再执行，消除"校验之后、执行之前"被替换的窗口：
    # 交给子进程的执行体就是刚刚算过摘要的那份内容。
    try:
        payload = adapter.read_bytes()
    except OSError as exc:
        raise ControlError("宿主适配器不可读", gate_id="lite_host_adapter_unavailable") from exc
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ControlError("宿主适配器内容与确认身份不一致，拒绝执行",
                           gate_id="lite_host_adapter_unavailable")
    # 先落一份私有临时副本再执行：路径与内容都来自刚刚校验过的同一份字节。
    staged = None
    try:
        staged = _stage_adapter_payload(payload)
        proc = _run_staged_adapter(staged, request)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise ControlError("宿主适配器调用失败", gate_id="lite_review_host_unattested",
                           detail=type(exc).__name__) from exc
    finally:
        _remove_staged_adapter(staged)
    # 执行后再次核对磁盘内容，检出执行期间的改写。
    try:
        if file_sha256(adapter) != expected_sha256:
            raise ControlError("宿主适配器在执行期间被改写，拒绝信任",
                               gate_id="lite_host_adapter_unavailable")
    except OSError as exc:
        raise ControlError("宿主适配器在执行后不可读", gate_id="lite_host_adapter_unavailable") from exc
    if proc.returncode != 0:
        raise ControlError("宿主适配器拒绝确认本次启动", gate_id="lite_review_host_unattested",
                           detail=(proc.stderr or proc.stdout or "").strip()[:400])
    try:
        reply = strict_json_loads(proc.stdout)
    except (ValueError, RecursionError) as exc:
        raise ControlError("宿主适配器未返回有效 JSON", gate_id="lite_review_host_unattested") from exc
    if not isinstance(reply, dict) or reply.get("launched") is not True:
        raise ControlError("宿主适配器未确认 launched=true", gate_id="lite_review_host_unattested")
    for key in ("spawn_id", "host_session_id", "challenge", "report_sha256",
                "stored_report_sha256"):
        if reply.get(key) != request[key]:
            raise ControlError(f"宿主适配器回显 {key} 与本次登记不一致",
                               gate_id="lite_review_host_unattested")
    return {"adapter_sha256": expected_sha256, "launched": True,
            "spawn_id": request["spawn_id"], "host_session_id": request["host_session_id"],
            "challenge": request["challenge"], "report_sha256": request["report_sha256"],
            "stored_report_sha256": request["stored_report_sha256"],
            "attested_at": now_iso()}


def _require_agent_text(value, option):
    text = (value or "").strip()
    if not text:
        raise ControlError(f"{option} 必须是非空文本", exit_code=2,
                           gate_id="agent_spawn_contract")
    return text


def cmd_record_agent_spawn(args):
    """在调用模型/子代理前原子记录边界，避免事后补写或伪造。"""
    out_dir = Path(args.dir).resolve()
    capabilities = list(dict.fromkeys(args.capability or []))
    invalid_capabilities = sorted(set(capabilities) - AGENT_CAPABILITIES)
    if invalid_capabilities or "text" not in capabilities:
        raise ControlError(
            "Agent capabilities 必须包含 text，且只能使用 text/image/tools/reasoning",
            exit_code=2, gate_id="agent_capability_contract",
            invalid_capabilities=invalid_capabilities)
    input_payload = {
        "task_scope": _require_agent_text(args.task_scope, "--task-scope"),
        "expected_artifact": _require_agent_text(
            args.expected_artifact, "--expected-artifact"),
        "evidence_boundary": _require_agent_text(
            args.evidence_boundary, "--evidence-boundary"),
        "join_condition": _require_agent_text(args.join_condition, "--join-condition"),
        "backend": _require_agent_text(args.backend, "--backend"),
        "model": _require_agent_text(args.model, "--model"),
        "capabilities": capabilities,
    }
    if getattr(args, "stage_action", None) is not None:
        input_payload["stage_action"] = _require_agent_text(args.stage_action, "--stage-action")
    review_attempt = getattr(args, "lite_review_attempt", None)
    if review_attempt is not None:
        input_payload["lite_review_attempt"] = review_attempt
    require_stage_action(out_dir, input_payload.get("stage_action", "agent"))
    exclusive_key = (args.exclusive_key or "").strip()
    if exclusive_key:
        if len(exclusive_key) > 80:
            raise ControlError(
                "--exclusive-key 最多 80 字符", exit_code=2,
                gate_id="agent_spawn_contract")
        input_payload["exclusive_key"] = exclusive_key
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止 spawn Agent；需修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝 spawn Agent", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(
            events, args.request_id, "agent_spawned", input_payload,
            actor=args.actor, payload_keys=(*input_payload, "lite_review_attempt"))
        if prior:
            prior_id = prior["payload"].get("spawn_id")
            prior_spawn = next(
                (item for item in _agent_spawns(meta) if item.get("spawn_id") == prior_id),
                {key: value for key, value in prior["payload"].items()
                 if key != "metadata_hash_after"})
            response = {"ok": True, "already_applied": True,
                        "request_id": args.request_id, "spawn": prior_spawn,
                        "event_id": prior["event_id"]}
            # 重放时补回同一 challenge 的只读输入，避免中断后拿不到审核输入；
            # challenge 不变，安全语义不受影响。
            if prior_spawn.get("lite_review_attempt") and "result" not in prior_spawn:
                try:
                    response["review_input"] = _lite_review_input(meta, events, prior_spawn, out_dir=out_dir)
                except ControlError:
                    pass
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0
        spawns = _agent_spawns(meta)
        open_spawns = [item for item in spawns if "result" not in item]
        if exclusive_key:
            exclusive_open = [item for item in open_spawns
                              if item.get("exclusive_key") == exclusive_key]
            if exclusive_open:
                raise ControlError(
                    f"Agent 独占域 {exclusive_key!r} 已有开放调用",
                    exit_code=1, gate_id="agent_exclusive_lock",
                    open_spawn_ids=[item.get("spawn_id") for item in exclusive_open])
        if len(open_spawns) >= MAX_OPEN_AGENT_SPAWNS:
            raise ControlError(
                f"同工单开放 Agent spawn 已达上限 {MAX_OPEN_AGENT_SPAWNS}",
                exit_code=1, gate_id="agent_concurrency_limit",
                open_spawn_ids=[item.get("spawn_id") for item in open_spawns])
        spawn = {"spawn_id": str(uuid.uuid4()), "at": now_iso(), **input_payload}
        review_input = None
        if review_attempt is not None:
            reviewed = _active_lite_review(meta, events, review_attempt)
            if any(fact.get("attempt") == review_attempt for fact in lite_review_records(events)[0]):
                raise ControlError("本 attempt 已登记审核；核对结果后新建 attempt",
                                   gate_id="lite_review_replay")
            candidate = candidate_snapshot(out_dir, meta, events=events)
            if candidate["candidate_id"] != (reviewed or {}).get("candidate_id"):
                raise ControlError("审核输入已变化，请新建 attempt", gate_id="lite_review_stale")
            spawn["lite_review_binding"] = {"candidate_id": candidate["candidate_id"],
                                            "contract_digest": lite_module().contract_digest(meta)}
            # 宿主中立挑战：签收回执时必须回传本 challenge，证明结果绑定本次登记。
            challenge = uuid.uuid4().hex + uuid.uuid4().hex
            spawn["host_receipt_challenge"] = challenge
            review_input = _lite_review_input(meta, None, spawn, out_dir=out_dir,
                                              candidate=candidate)
        del events
        before_meta = dict(meta)
        extensions = dict(meta.get("extensions") or {})
        agent_ns = dict(extensions.get("agent") or {})
        agent_ns["spawns"] = spawns + [spawn]
        extensions["agent"] = agent_ns
        meta["extensions"] = extensions
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "agent_spawned", spawn,
            request_id=args.request_id, actor=args.actor)
    response = {"ok": True, "spawn": spawn, "event_id": event["event_id"]}
    if review_input is not None:
        response["review_input"] = review_input
    print(json.dumps(response,
                     ensure_ascii=False, indent=2))
    return 0


def cmd_record_agent_result(args):
    """终结一个已记录 Agent spawn；正文仅记摘要，避免事件链膨胀/泄密。"""
    out_dir = Path(args.dir).resolve()
    spawn_id = _require_agent_text(args.spawn_id, "--spawn-id")
    adoption_reason = _require_agent_text(args.adoption_reason, "--adoption-reason")
    summary = _require_agent_text(args.summary, "--summary")
    evidence_refs = [item.strip() for item in (args.evidence_ref or []) if item.strip()]
    if not evidence_refs:
        raise ControlError("Agent result 至少需要一条 --evidence-ref", exit_code=2,
                           gate_id="agent_result_evidence")
    if args.result != "joined" and args.adopted != "no":
        raise ControlError(
            "timed_out/stopped/failed 只能 adopted=no",
            exit_code=2, gate_id="agent_result_adoption")
    input_payload = {
        "spawn_id": spawn_id,
        "result": args.result,
        "adopted": args.adopted,
        "adoption_reason": adoption_reason,
        "evidence_refs": evidence_refs,
        "summary_digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
    }
    report = None
    host_receipt = None
    report_input = getattr(args, "lite_review_report", None)
    host_session_id = getattr(args, "host_session_id", None)
    if report_input is not None or host_session_id is not None:
        host_session_id = _require_agent_text(host_session_id, "--host-session-id")
        if args.result != "joined" or not report_input or len(host_session_id) > 256:
            raise ControlError("独立审核报告须匹配 joined 及有效宿主子代理身份",
                               gate_id="lite_review_host")
        if getattr(args, "lite_review_report", None) == "-" and \
                getattr(args, "host_receipt", None) == "-":
            raise ControlError("--lite-review-report 与 --host-receipt 不能同时使用 stdin",
                               gate_id="lite_review_host_receipt")
        report, report_digest = _read_lite_agent_result(args)
        input_payload["host_session_id"] = host_session_id
        host_receipt_arg = getattr(args, "host_receipt", None)
        if not host_receipt_arg:
            raise ControlError("joined 的独立审核须提供宿主回执（--host-receipt）以证明结果来自本次启动",
                               gate_id="lite_review_host_receipt")
        host_receipt, receipt_sha256 = _read_host_receipt(args)
        stored_receipt = {
            "spawn_id": host_receipt.get("spawn_id"),
            "host_session_id": host_receipt.get("host_session_id"),
            "challenge": host_receipt.get("challenge"),
            "receipt_sha256": receipt_sha256,
        }
        if host_receipt.get("returned_at"):
            stored_receipt["returned_at"] = host_receipt["returned_at"]
        input_payload["host_receipt"] = stored_receipt
    elif getattr(args, "host_receipt", None):
        raise ControlError("宿主回执仅用于 joined 的 Lite 独立审核结果",
                           gate_id="lite_review_host_receipt")
    error_class = (args.error_class or "").strip()
    if error_class:
        input_payload["error_class"] = error_class
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止终结 Agent；需修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录 Agent 结果", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(
            events, args.request_id, "agent_result", input_payload,
            actor=args.actor, payload_keys=(*input_payload, "host_session_id"))
        if prior:
            if report is not None:
                receipt = prior["payload"]["lite_review_report"]
                digest = receipt.get("input_sha256") or (
                    prior["payload"].get("host_attestation") or {}).get("report_sha256")
                if digest:
                    same_report = digest == report_digest
                else:
                    # 旧降级回执只保存了规范化正文。比较可恢复的内容，兼容原请求重试。
                    stored = _load_lite_review_report(out_dir, receipt)
                    binding = {key: stored[key] for key in (
                        "schema_version", "attempt", "candidate_id", "contract_digest")}
                    same_report = canonical_digest({**report, **binding}) == canonical_digest(stored)
                if not same_report:
                    raise ControlError("同一 request_id 提交了不同的 Lite 审核报告",
                                       gate_id="idempotency_conflict")
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "spawn_id": spawn_id,
                              "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
            return 0
        spawns = [dict(item) for item in _agent_spawns(meta)]
        target = next((item for item in spawns if item.get("spawn_id") == spawn_id), None)
        if target is None:
            raise ControlError(
                f"找不到 Agent spawn_id={spawn_id!r}", exit_code=1,
                gate_id="agent_spawn_not_found")
        if "result" in target:
            raise ControlError(
                f"Agent spawn_id={spawn_id!r} 已终结，拒绝不同请求重复改写",
                exit_code=1, gate_id="agent_spawn_already_terminal",
                existing_result=target.get("result"))
        result_at = now_iso()
        result_record = {key: value for key, value in input_payload.items()
                         if key != "spawn_id"}
        if target.get("lite_review_attempt"):
            if args.result == "joined":
                if report is None:
                    raise ControlError("独立审核须提供宿主身份与实际返回的结构化报告",
                                       gate_id="lite_review_host")
                # 先校验回执绑定（不落盘），再由可信适配器确认，最后才写报告并提交。
                name, data = _lite_agent_receipt(
                    out_dir, meta, events, target, host_session_id, report,
                    host_receipt=host_receipt)
                # 冻结身份在执行前读定并传入：_host_attest 先比对再决定是否
                # 启动子进程，未受信任的程序不会被执行。
                degradation = _host_adapter_degradation(meta)
                if degradation is not None:
                    # 适配器不可用：不执行适配器，也不写 host_attestation
                    # （该字段在 schema 中只表示"已获可信确认"）。降级事实本身
                    # 记在 extensions.lite.host_adapter，审核报告仅作留痕，
                    # 交付结论固定 verification_pending，不得升为 verified。
                    result_record["lite_review_report"] = _commit_lite_agent_report(
                        out_dir, name, data, None, target, host_session_id,
                        degraded=True)
                    result_record["host_attestation_degraded"] = {
                        "status": "unavailable",
                        "reason": degradation.get("reason") or "lite_host_adapter_unavailable",
                        "observed_at": result_at}
                else:
                    attestation = _host_attest(target, host_session_id, report_digest,
                                               hashlib.sha256(data).hexdigest(),
                                               _pinned_adapter_sha256(meta))
                    _require_pinned_adapter(meta, attestation)
                    result_record["host_attestation"] = attestation
                    result_record["lite_review_report"] = _commit_lite_agent_report(
                        out_dir, name, data, attestation, target, host_session_id)
                result_record["lite_review_report"]["input_sha256"] = report_digest
        elif report is not None:
            raise ControlError("普通 Agent 结果不能签收 Lite 审核报告", gate_id="lite_review_entry")
        del events
        result_record["result_at"] = result_at
        target.update(result_record)
        before_meta = dict(meta)
        extensions = dict(meta.get("extensions") or {})
        agent_ns = dict(extensions.get("agent") or {})
        agent_ns["spawns"] = spawns
        extensions["agent"] = agent_ns
        meta["extensions"] = extensions
        event_payload = {"spawn_id": spawn_id, **result_record}
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "agent_result", event_payload,
            request_id=args.request_id, actor=args.actor)
    print(json.dumps({"ok": True, "spawn": target, "event_id": event["event_id"]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_migration(args):
    out_dir = Path(args.dir).resolve()
    meta_path = Path(out_dir) / METADATA_NAME
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_json(meta_path, "工单 metadata")
        if is_vnext(meta):
            print(json.dumps({"ok": True, "already_v3": True, "out_dir": str(out_dir),
                              "ticket_id": meta.get("ticket_id")}, ensure_ascii=False, indent=2))
            return 0

        known = known_metadata_fields()
        auto_changes = []
        needs_human = []
        migrated = dict(meta)
        migrated["schema_version"] = 3
        auto_changes.append("schema_version: 3")

        legacy_ext = {}
        for key, value in meta.items():
            if key in known:
                continue
            legacy_ext[key] = value
            del migrated[key]
        if legacy_ext:
            extensions = dict(migrated.get("extensions") or {})
            if "legacy" in extensions and extensions["legacy"] != legacy_ext:
                needs_human.append("$.extensions.legacy: 已存在，无法无损合并未登记顶层字段")
            else:
                extensions["legacy"] = legacy_ext
                migrated["extensions"] = extensions
            auto_changes.append(
                f"{len(legacy_ext)} 个未登记顶层字段移入 extensions.legacy: "
                + ", ".join(sorted(legacy_ext)))

        needs_human.extend(check_schema(
            migrated, load_json(SCHEMA_METADATA, "schema")))
        report = {
            "ok": True, "out_dir": str(out_dir), "ticket_id": meta.get("ticket_id"),
            "classification": "auto" if not needs_human else "needs_human",
            "auto_changes": auto_changes,
            "needs_human": needs_human,
            "unmigratable": [],
            "hint": "needs_human 非空时 --apply 拒绝执行；先人工修正 metadata 再迁移。",
        }
        if not args.apply:
            report["mode"] = "dry_run"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if needs_human:
            report["mode"] = "apply_refused"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        backup = meta_path.with_suffix(".json.pre-v3.bak")
        suffix = 2
        while backup.exists():
            backup = meta_path.with_suffix(f".json.pre-v3.bak.{suffix}")
            suffix += 1
        with open(meta_path, "r", encoding="utf-8") as source:
            backup.write_text(source.read(), encoding="utf-8")
        event = commit_metadata_and_event(
            out_dir, meta, migrated, "migration_applied",
            {"backup": str(backup), "auto_changes": auto_changes})
    report["mode"] = "applied"
    report["backup"] = str(backup)
    report["event_id"] = event["event_id"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_reopen(args):
    """在永久归档控制根上解冻 closed 工单，保留原事件链和 ticket_id。"""
    out_dir = Path(args.dir).resolve()
    try:
        updates = strict_json_loads(args.metadata_json)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError(f"--metadata-json 不是合法 JSON: {exc}", exit_code=2)
    if not isinstance(updates, dict):
        raise ControlError("--metadata-json 必须是 JSON 对象", exit_code=2)
    allowed = {
        "active_checkout", "checkout_history", "sub_worktrees", "submission_contracts",
        "worktree_path", "worktree_branch",
    }
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise ControlError(f"reopen 含未允许字段: {unknown}", exit_code=2,
                           gate_id="reopen_field_allowlist", allowed=sorted(allowed))
    active = updates.get("active_checkout")
    if not isinstance(active, dict):
        raise ControlError("reopen 必须提供 active_checkout 对象", exit_code=2,
                           gate_id="reopen_active_checkout")
    active = dict(active)
    active.setdefault("activated_at", now_iso())
    if active.get("state") != "active":
        raise ControlError("active_checkout.state 必须为 active", exit_code=2,
                           gate_id="reopen_active_checkout")
    checkout_path = Path(str(active.get("path") or "")).resolve()
    expected_branch = active.get("branch")
    expected_commit = active.get("base_commit")
    if not checkout_path.is_dir() or not expected_branch or not expected_commit:
        raise ControlError(
            "active_checkout 必须含可读 path、非空 branch 和 base_commit",
            exit_code=2, gate_id="reopen_active_checkout")
    try:
        actual_branch = subprocess.run(
            ["git", "-C", str(checkout_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        actual_commit = subprocess.run(
            ["git", "-C", str(checkout_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        actual_root = subprocess.run(
            ["git", "-C", str(checkout_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        resolved_expected = subprocess.run(
            ["git", "-C", str(checkout_path), "rev-parse", str(expected_commit)],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlError(f"新 checkout Git 身份校验失败: {exc}", exit_code=1,
                           gate_id="reopen_checkout_identity")
    if (Path(actual_root).resolve() != checkout_path or actual_branch != expected_branch
            or actual_commit != resolved_expected):
        raise ControlError(
            "新 checkout 根/分支/HEAD 与 active_checkout 声明不一致",
            exit_code=1, gate_id="reopen_checkout_identity",
            expected={"root": str(checkout_path), "branch": expected_branch,
                      "commit": resolved_expected},
            actual={"root": actual_root, "branch": actual_branch, "commit": actual_commit})
    active["path"] = str(checkout_path)
    active["base_commit"] = actual_commit
    updates["active_checkout"] = active
    updates["worktree_path"] = str(checkout_path)
    updates["worktree_branch"] = expected_branch
    pointer_dir = checkout_path / ".icode_output"
    pointer_path = pointer_dir / ".active_ticket.json"
    payload = {
        "from": "closed", "to": None,
        "active_checkout": {
            "path": str(checkout_path), "branch": expected_branch,
            "base_commit": actual_commit,
        },
        "reason": args.reason,
    }

    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        _stage_retirement_guard(out_dir)
        archive_root = Path(meta["archive_path"]).resolve() \
            if meta.get("archive_path") else None
        if archive_root != out_dir:
            raise ControlError("reopen 只能在 archived 交接后的永久控制根执行",
                               exit_code=1, gate_id="reopen_control_root",
                               expected_archive=str(archive_root), actual=str(out_dir))
        if pointer_path.exists():
            existing_pointer = load_json(pointer_path, "active ticket pointer")
            if (existing_pointer.get("ticket_id") != meta.get("ticket_id")
                    or Path(str(existing_pointer.get("control_root") or "")).resolve()
                    != out_dir):
                raise ControlError(
                    f"新 checkout 已被另一工单指针占用: {pointer_path}",
                    exit_code=1, gate_id="reopen_pointer_ownership",
                    existing=existing_pointer)
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("事件链不完整，reopen 拒绝执行", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(
            events, args.request_id, "ticket_reopened", payload,
            payload_keys=("active_checkout", "reason"))
        current_active = meta.get("active_checkout") or {}
        same_active = all(current_active.get(key) == active.get(key)
                          for key in ("path", "branch", "base_commit", "state"))
        if prior or (meta.get("close_state") is None and same_active):
            refresh_archive_manifest_control_files(out_dir, meta.get("ticket_id"))
            pointer_dir.mkdir(exist_ok=True)
            atomic_write_json(pointer_path, {
                "schema_version": 1, "ticket_id": meta.get("ticket_id"),
                "control_root": str(out_dir), "active_checkout": str(checkout_path),
            })
            print(json.dumps({"ok": True, "already_applied": True,
                              "ticket_id": meta.get("ticket_id"),
                              "control_root": str(out_dir),
                              "active_checkout": active}, ensure_ascii=False, indent=2))
            return 0
        if meta.get("close_state") != "closed":
            raise ControlError(
                f"只有 close_state=closed 的工单可 reopen；当前={meta.get('close_state')!r}",
                exit_code=1, gate_id="reopen_requires_closed")
        if meta.get("active_checkout") is not None:
            raise ControlError("closed 工单仍有 active_checkout，先修复拓扑冲突",
                               exit_code=1, gate_id="reopen_topology_conflict")
        _, manifest_problems = verify_archive_manifest(
            out_dir, meta.get("ticket_id"), source_dir=None)
        if manifest_problems:
            raise ControlError("归档完整性未通过，禁止 reopen", exit_code=1,
                               gate_id="archive_manifest", violations=manifest_problems)
        before_meta = dict(meta)
        meta.update(updates)
        meta["migration"] = None
        meta["close_state"] = None
        meta["delivery_verdict"] = "verification_pending"
        meta["artifact_root"] = str(out_dir)
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "ticket_reopened", payload,
            request_id=args.request_id)
        refresh_archive_manifest_control_files(out_dir, meta.get("ticket_id"))
        pointer_dir.mkdir(exist_ok=True)
        atomic_write_json(pointer_path, {
            "schema_version": 1, "ticket_id": meta.get("ticket_id"),
            "control_root": str(out_dir), "active_checkout": str(checkout_path),
        })
    print(json.dumps({"ok": True, "ticket_id": meta.get("ticket_id"),
                      "control_root": str(out_dir), "active_checkout": active,
                      "delivery_verdict": meta.get("delivery_verdict"),
                      "event_id": event["event_id"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_close_phase(args):
    out_dir = Path(args.dir).resolve()
    sm = load_state_machine()
    phases = sm["close_phases"]
    phase = args.phase
    if phase not in phases:
        raise ControlError(f"未知关闭阶段 {phase!r}，合法阶段: {phases}", exit_code=2)
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("status") != "completed":
            raise ControlError(
                f"未完成工单禁止 close：当前 status={meta.get('status')!r}",
                exit_code=1, gate_id="close_requires_completed")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，关闭阶段禁止前移", exit_code=1,
                               gate_id="event_chain", violations=problems)
        completed_events = [item for item in events
                            if item.get("event_type") == "state_changed"
                            and item.get("payload", {}).get("to") == "completed"]
        migrated = any(item.get("event_type") == "migration_applied" for item in events)
        if not completed_events and not migrated:
            raise ControlError(
                "metadata 虽为 completed，但事件链无 completed 流转证据，拒绝 close",
                exit_code=1, gate_id="close_completed_evidence")
        current = meta.get("close_state")
        archive_root = Path(meta["archive_path"]).resolve() \
            if meta.get("archive_path") else None
        is_archive_root = archive_root == out_dir
        control_root = out_dir
        if current in phases[phases.index("archived"):]:
            if not is_archive_root:
                # archived 已落在源目录但上次交接中断时，同阶段重放可自愈。
                if current == "archived" and phase == "archived":
                    control_root = handoff_close_control_root(out_dir, meta)
                else:
                    raise ControlError(
                        f"archived 后控制根已交接到 {archive_root}；"
                        f"禁止继续在可删除源目录 {out_dir} 推进",
                        exit_code=1, gate_id="close_control_handoff",
                        hint=f"改用 close-phase --dir {archive_root} --phase {phase}")
            else:
                refresh_archive_manifest_control_files(out_dir, meta.get("ticket_id"))
        prior = find_idempotent_event(
            events, args.request_id, "close_phase", {"to": phase},
            payload_keys=("to",))
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "close_state": phase,
                              "event_id": prior["event_id"], "out_dir": str(out_dir),
                              "control_root": str(control_root)},
                             ensure_ascii=False, indent=2))
            return 0
        if current == "closed":
            print(json.dumps({"ok": True, "already_closed": True, "close_state": "closed",
                              "out_dir": str(out_dir), "ticket_id": meta.get("ticket_id"),
                              "control_root": str(control_root),
                              "note": "closed 为终态：重复 close 只回放摘要，不重复执行清理动作"},
                             ensure_ascii=False, indent=2))
            return 0
        if phase == current:
            print(json.dumps({"ok": True, "already_applied": True, "close_state": current,
                              "out_dir": str(out_dir), "ticket_id": meta.get("ticket_id"),
                              "control_root": str(control_root)},
                             ensure_ascii=False, indent=2))
            return 0
        expected_idx = 0 if current is None else phases.index(current) + 1
        if phases.index(phase) != expected_idx:
            raise ControlError(
                f"禁止跨阶段跳转：当前 close_state={current!r}，下一合法阶段为 "
                f"{phases[expected_idx]!r}，收到 {phase!r}",
                exit_code=1, gate_id="close_phase_order", close_phases=phases)
        if phase == "archived":
            archive_path = meta.get("archive_path")
            if not archive_path or not Path(archive_path).is_dir():
                raise ControlError(
                    "archived 阶段要求 metadata.archive_path 指向已存在归档目录",
                    exit_code=1, gate_id="archive_exists", archive_path=archive_path)
            _, archive_problems = verify_archive_manifest(
                archive_path, meta.get("ticket_id"), source_dir=out_dir)
            if archive_problems:
                raise ControlError(
                    "归档 manifest/hash 未通过，禁止标记 archived",
                    exit_code=1, gate_id="archive_manifest",
                    violations=archive_problems,
                    hint=f"先复制必需产物，再运行 archive-manifest --dir {out_dir} "
                         f"--archive-dir {archive_path} --write")
        before_meta = dict(meta)
        meta["close_state"] = phase
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "close_phase",
            {"from": current, "to": phase}, request_id=args.request_id)
        if phase == "archived":
            control_root = handoff_close_control_root(out_dir, meta)
        elif is_archive_root and phases.index(phase) > phases.index("archived"):
            refresh_archive_manifest_control_files(out_dir, meta.get("ticket_id"))
    print(json.dumps({"ok": True, "close_state": phase, "from": current,
                      "event_id": event["event_id"], "out_dir": str(out_dir),
                      "control_root": str(control_root)},
                     ensure_ascii=False, indent=2))
    return 0


def ensure_execution_writable(meta):
    if meta.get("close_state") is not None:
        raise ControlError(
            "工单已进入关闭流程，禁止追加执行轨迹；关闭阶段继续使用 close-phase/reopen 专用事件",
            exit_code=1, gate_id="closed_ticket_mutation_frozen")


def find_step_start(events, attempt):
    starts = [event for event in events
              if event.get("event_type") == "step_started"
              and (event.get("payload") or {}).get("execution_model_version") == 1
              and (event.get("payload") or {}).get("attempt") == attempt]
    if len(starts) != 1:
        raise ControlError(f"step attempt={attempt!r} 未唯一启动", exit_code=1,
                           gate_id="step_attempt", matches=len(starts))
    return starts[0]


def step_attempt_finished(events, attempt):
    return next((event for event in events
                 if event.get("event_type") == "step_finished"
                 and (event.get("payload") or {}).get("execution_model_version") == 1
                 and (event.get("payload") or {}).get("attempt") == attempt), None)


def derived_attempt(prefix, request_id):
    """有幂等键时稳定派生 attempt；无幂等键才使用随机 ID。"""
    if request_id:
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-req-{digest}"
    return str(uuid.uuid4())


def effective_workflow_mode(meta):
    """请求模式用于追溯，执行模式优先使用已声明的有效模式。"""
    profile = meta.get("risk_profile")
    effective = profile.get("effective_mode") if isinstance(profile, dict) else None
    if effective in ("fast", "full"):
        return effective
    return "fast" if meta.get("mode") == "fast" else "full"


def step_mode_changed(start, meta, events):
    """新 attempt 冻结有效模式；旧 attempt 仅在后续模式更新时保守回流。"""
    if (start.get("payload") or {}).get("step") not in ("review", "deepcheck", "audit"):
        return False
    initial = (start.get("payload") or {}).get("effective_mode")
    if initial not in ("fast", "full"):
        return any(event.get("event_type") == "metadata_updated" and
                   bool({"mode", "risk_profile"} & set(((event.get("payload") or {}).get("set") or {})))
                   for event in events[events.index(start) + 1:])
    if effective_workflow_mode(meta) != initial:
        return True
    subsequent = events[events.index(start) + 1:]
    for index, event in enumerate(subsequent):
        payload = event.get("payload") or {}
        if event.get("event_type") != "metadata_updated":
            continue
        updates = payload.get("set") or {}
        if "risk_profile" in updates:
            candidate = dict(meta, risk_profile=updates["risk_profile"])
            if effective_workflow_mode(candidate) != initial:
                return True
            profile = updates["risk_profile"]
            if (not isinstance(profile, dict) or profile.get("effective_mode") not in ("fast", "full")) and any(
                    later.get("event_type") == "metadata_updated" and
                    "mode" in ((later.get("payload") or {}).get("set") or {})
                    for later in subsequent[index + 1:]):
                return True
        if "mode" in updates:
            candidate = dict(meta, mode=updates["mode"])
            if effective_workflow_mode(candidate) != initial:
                return True
            # Earlier risk_profile values are not stored in old events. A later
            # profile update can hide a temporary mode change, so fail closed.
            if updates["mode"] != initial and any(
                    later.get("event_type") == "metadata_updated" and
                    "risk_profile" in ((later.get("payload") or {}).get("set") or {})
                    for later in subsequent[index + 1:]):
                return True
    return False


def cmd_step(args, *, git_provider=None, require_git_provider=False, inspection_provider_factory=None):
    """步骤端口快照、Reactive 边界复检和终结回执。"""
    out_dir = Path(args.dir).resolve()
    if args.phase == "start" or args.phase == "check" or (args.phase == "finish" and args.outcome == "success"):
        expected = None
        if args.phase != "start" and args.attempt:
            start = find_step_start(read_events(out_dir)[0], args.attempt)
            expected = (start.get("payload") or {}).get("stage_generation")
        require_stage_action(out_dir, args.step if args.step in {"code", "patch", "fast", "verify", "deploy"} else "review",
                             expected_generation=expected,
                             git_provider=git_provider, require_git_provider=require_git_provider)
    access = _code_access(args.step, git_provider, require_git_provider, inspection_provider_factory)
    candidate_access = _code_candidate_access(access)
    if access and args.phase not in {"start", "check", "finish"}:
        raise ControlError("code step phase is not enabled", gate_id="candidate_business_propagation_pending")
    model = load_execution_model(load_metadata(out_dir))
    contract = step_contract(model, args.step)
    with DirLock(out_dir):
        if access:
            _code_inspection_provider(out_dir, load_metadata(out_dir), **access)
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider recovery is not enabled", gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, problems = verify_event_chain(out_dir, meta, **candidate_access)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录步骤", exit_code=1,
                               gate_id="event_chain", violations=problems)

        # 续跑入口先固定资格合同边界（幂等）：step start/check/finish 走 append_event，
        # 不经过 metadata 提交；若不在生成新 trace 之前通过受控事务采纳合同，本次
        # attempt 写出的未标版本记录会在 lint 时按 v1 解释，且其时间早于之后才发生的
        # 采纳时刻而被永久当作历史行。边界必须早于本次续跑产生的任何新 trace。
        if meta.get("mcp_gate_contract_version") is None:
            before_meta = copy.deepcopy(meta)
            adopt_gate_contract(meta, events=events)
            commit_metadata_and_event(out_dir, before_meta, meta, "metadata_updated",
                {"gate_contract_adoption": {
                    "version": meta["mcp_gate_contract_version"],
                    "adopted_at": meta["mcp_gate_contract_adopted_at"],
                    "history_boundary": meta["mcp_gate_contract_history_boundary"]}})

        if meta.get("mode") == "lite" and args.phase == "finish" and args.request:
            finished_replay = step_attempt_finished(events, args.attempt)
            if finished_replay and finished_replay.get("request_id") == args.request:
                old = finished_replay["payload"]
                if old.get("step") != args.step or old.get("outcome") != args.outcome or old.get("evidence") != args.evidence:
                    raise ControlError("相同请求的终结内容不一致", gate_id="idempotency_conflict")
                print(json.dumps({"ok": True, "already_applied": True, "event_id": finished_replay["event_id"]}))
                return 0
        if meta.get("mode") == "lite" and args.step != "verify":
            if args.step not in workflow_contract(meta)["step_order"] or meta["status"] != args.step + "_in_progress":
                raise ControlError("lite step 与当前状态不匹配", gate_id="lite_step_state")
            if args.phase == "finish" and args.outcome in {"success", "degraded"}:
                if args.outcome != "success":
                    raise ControlError("lite 必需步骤不接受 degraded", gate_id="lite_quality_gate")
                lite_step_gate(out_dir, meta, events, args.step, args.attempt)
        if args.phase != "finish" or args.outcome in {"success", "degraded"}:
            admission = fast_policy_admission(out_dir, meta, args.step)
            if not admission["ok"]:
                raise ControlError("fast scope/risk admission failed", gate_id="fast_risk_admission",
                                   assessment=admission)
        if args.phase == "start":
            attempt = args.attempt or derived_attempt("step", args.request)
            snapshot, missing = capture_step_inputs(out_dir, meta, model, args.step, **candidate_access)
            if missing:
                raise ControlError(
                    f"步骤 {args.step!r} 必填输入端口缺失: {missing}", exit_code=1,
                    gate_id="step_ports", step=args.step, missing=missing,
                    hint="先补齐上游产物/metadata，再重新 start；不得用空占位跳过。")
            payload = {
                "execution_model_version": model["schema_version"],
                "step": args.step,
                "attempt": attempt,
                "contract_digest": snapshot["contract_digest"],
                "input_digest": snapshot["input_digest"],
                "inputs": snapshot["inputs"],
                "protected": snapshot["protected"],
            }
            if args.step in ("review", "deepcheck", "audit"):
                # 旧执行合同摘要不变，新增 attempt 单独冻结有效模式。
                payload["effective_mode"] = effective_workflow_mode(meta)
            if args.step in ("deepcheck", "audit"):
                payload["dedup_evidence_version"] = 1
            if args.step in ("deepcheck", "audit", "lite_review") and \
                    (meta.get("candidate_tracking") or {}).get("mode") == "enabled":
                payload["candidate_input"] = candidate_snapshot(out_dir, meta, events=events, **candidate_access)
            prior_start = next((e for e in events if args.request and
                                e.get("request_id") == args.request and
                                e.get("event_type") == "step_started"), None)
            if prior_start and "effective_mode" not in (prior_start.get("payload") or {}):
                # 旧 attempt 重放不因新增可选冻结字段产生伪冲突。
                payload.pop("effective_mode", None)
            if prior_start and "dedup_evidence_version" not in (prior_start.get("payload") or {}):
                payload.pop("dedup_evidence_version", None)
            prior_payload = prior_start.get("payload") or {} if prior_start else {}
            # 跨版本幂等重放必须核对完整请求身份：同 request 但换了 attempt
            # 是不同执行，不得重放旧事件（否则调用方按新 attempt 找不到启动事件）。
            same_attempt = bool(prior_start) and prior_payload.get("attempt") == attempt
            same_step = prior_payload.get("step") == args.step if prior_start else False
            if same_attempt and same_step and attempt_upgrade_compatible(
                    prior_payload, contract, captured_protected=snapshot["protected"]):
                # 工具升级为步骤合同新增端口后，同请求重试的 contract_digest 与受保护
                # 输入集合都会变化；旧记录自洽且是当前捕获输入的子集 → 重放而非
                # 输入漂移/伪冲突。返回原事件中的 attempt，绝不虚构新 attempt。
                require_stage_action(out_dir, args.step if args.step in {"code", "patch", "fast", "verify", "deploy"} else "review",
                    expected_generation=prior_payload.get("stage_generation"), **candidate_access)
                result = {"ok": True, "already_applied": True, "step": args.step,
                          "attempt": prior_payload.get("attempt"),
                          "event_id": prior_start["event_id"],
                          "input_digest": prior_payload.get("input_digest"),
                          "contract_digest": prior_payload.get("contract_digest"),
                          "contract_drift_replayed": True,
                          "execution_profile": step_execution_profile(meta, args.step)}
                _code_output(json.dumps(result, ensure_ascii=False, indent=2), out_dir, meta, access)
                return 0
            prior = find_idempotent_event(events, args.request, "step_started", payload)
            if prior:
                require_stage_action(out_dir, args.step if args.step in {"code", "patch", "fast", "verify", "deploy"} else "review",
                    expected_generation=(prior.get("payload") or {}).get("stage_generation"), **candidate_access)
                result = {"ok": True, "already_applied": True, "step": args.step,
                          "attempt": attempt, "event_id": prior["event_id"],
                          "input_digest": payload["input_digest"],
                          "execution_profile": step_execution_profile(meta, args.step)}
                _code_output(json.dumps(result, ensure_ascii=False, indent=2), out_dir, meta, access)
                return 0
            # Exact request replay above returns its old receipt without
            # granting permission to create a new execution in this state.
            if args.step == "review" and (legal_transition(load_state_machine(),
                    meta.get("status"), "review_in_progress") or {}).get("review_return"):
                raise ControlError("先通过受控 transition 返回审查，再启动新审查", gate_id="review_return_required")
            if args.step == "review" and meta.get("status") != "review_in_progress":
                raise ControlError("先原生进入 review_in_progress，再启动真实审查", gate_id="review_entry_required")
            if (any(e.get("event_type") == "state_changed" and
                    (e.get("payload") or {}).get("review_return") == 1 for e in events)
                    and args.step in {"review", "merge", "code", "deepcheck", "audit"}):
                policy = load_state_machine()["action_policy"]["actions"][args.step]
                if meta.get("status") not in policy["statuses"]:
                    raise ControlError("审查恢复后的步骤须按状态顺序执行", gate_id="action_policy")
            open_same_step = []
            for event in events:
                payload0 = event.get("payload") or {}
                if event.get("event_type") == "step_started" \
                        and payload0.get("execution_model_version") == 1 \
                        and payload0.get("step") == args.step \
                        and step_attempt_finished(events, payload0.get("attempt")) is None:
                    open_same_step.append(payload0.get("attempt"))
            if open_same_step:
                raise ControlError(
                    f"步骤 {args.step!r} 已有未终结 attempt，禁止并行重入", exit_code=1,
                    gate_id="step_attempt_open", open_attempts=open_same_step,
                    hint="先 check 并 finish 旧 attempt；输入漂移时以 blocked 终结后再 start。")
            if any((event.get("payload") or {}).get("attempt") == attempt
                   for event in events):
                raise ControlError(f"attempt={attempt!r} 已被使用", exit_code=1,
                                   gate_id="attempt_conflict")
            event = append_event(out_dir, meta, "step_started", payload,
                                 request_id=args.request, **access)
            _code_output(json.dumps({"ok": True, "step": args.step, "phase": "start",
                              "attempt": attempt, "event_id": event["event_id"],
                              "input_digest": payload["input_digest"],
                              "required_checks": contract["required_checks"],
                              "execution_profile": step_execution_profile(meta, args.step)},
                             ensure_ascii=False, indent=2), out_dir, meta, access)
            return 0

        if not args.attempt:
            raise ControlError(f"step --phase {args.phase} 必须提供 --attempt", exit_code=2)
        start = find_step_start(events, args.attempt)
        start_payload = start["payload"]
        if start_payload.get("step") != args.step:
            raise ControlError("--step 与 attempt 的启动步骤不一致", exit_code=1,
                               gate_id="step_attempt", expected=start_payload.get("step"),
                               actual=args.step)
        finished = step_attempt_finished(events, args.attempt)
        if args.phase == "check":
            if finished:
                raise ControlError("已终结 step attempt 禁止继续 gate check", exit_code=1,
                                   gate_id="step_attempt_finished")
            if args.boundary not in model["boundaries"]:
                raise ControlError(f"--boundary 非法: {args.boundary!r}", exit_code=2,
                                   allowed=model["boundaries"])
            current, missing = capture_step_inputs(out_dir, meta, model, args.step, **candidate_access)
            old_protected = start_payload.get("protected") or {}
            # 工具升级只新增端口时，旧 attempt 记录的 contract_digest 与受保护输入
            # 集合都会变化；这是升级而非真实漂移，兼容时按启动时合同继续本
            # attempt 的边界检查（新增端口只约束升级后启动的新 attempt）。
            upgrade_compatible = attempt_upgrade_compatible(
                start_payload, contract, captured_protected=current["protected"])
            if upgrade_compatible:
                changed = []
            else:
                changed = sorted(
                    key for key in set(old_protected) | set(current["protected"])
                    if old_protected.get(key) != current["protected"].get(key)
                )
                if start_payload.get("contract_digest") != current["contract_digest"]:
                    changed.insert(0, "$contract")
            if step_mode_changed(start, meta, events):
                changed.append("effective_mode")
            routes = contract["drift_routes"]
            route = "review" if "effective_mode" in changed else next((routes[item] for item in changed if item in routes),
                         routes["default"])
            result_name = "blocked" if changed or missing else "pass"
            payload = {
                "execution_model_version": model["schema_version"],
                "step": args.step,
                "attempt": args.attempt,
                "boundary": args.boundary,
                "result": result_name,
                "changed_inputs": changed,
                "missing_inputs": missing,
                "captured_digest": start_payload.get("input_digest"),
                "current_digest": current["input_digest"],
                "route": route if result_name == "blocked" else "continue",
                "elapsed_ms": elapsed_ms(start.get("timestamp")),
            }
            prior = find_idempotent_event(
                events, args.request, "gate_checked", payload,
                payload_keys=["step", "attempt", "boundary", "result", "changed_inputs",
                              "missing_inputs", "captured_digest", "current_digest", "route"])
            # This branch needs only start/prior, not the complete history list.
            del events
            event = prior or append_event(out_dir, meta, "gate_checked", payload,
                                          request_id=args.request, **access)
            result = {"ok": result_name == "pass", "step": args.step,
                      "phase": "check", "attempt": args.attempt,
                      "boundary": args.boundary, "result": result_name,
                      "changed_inputs": changed, "missing_inputs": missing,
                      "route": payload["route"], "event_id": event["event_id"],
                      "already_applied": bool(prior)}
            _code_output(json.dumps(result, ensure_ascii=False, indent=2), out_dir, meta, access)
            return 0 if result["ok"] else 1

        if args.phase != "finish":
            raise ControlError(f"未知 step phase: {args.phase!r}", exit_code=2)
        if args.outcome not in model["step_outcomes"]:
            raise ControlError("step --phase finish 必须提供合法 --outcome", exit_code=2,
                               allowed=model["step_outcomes"])
        if finished:
            expected = finished.get("payload") or {}
            if args.request and finished.get("request_id") == args.request \
                    and expected.get("outcome") == args.outcome \
                    and expected.get("evidence") == args.evidence:
                _code_output(json.dumps({"ok": True, "already_applied": True,
                                  "step": args.step, "attempt": args.attempt,
                                  "outcome": expected.get("outcome"),
                                  "event_id": finished["event_id"]},
                                 ensure_ascii=False, indent=2), out_dir, meta, access)
                return 0
            if args.request and finished.get("request_id") == args.request:
                raise ControlError(
                    "相同 request 的 step finish payload 不一致", exit_code=1,
                    gate_id="idempotency_conflict", existing=expected,
                    attempted={"outcome": args.outcome, "evidence": args.evidence})
            raise ControlError("step attempt 已终结，拒绝第二个终结结果", exit_code=1,
                               gate_id="step_attempt_finished",
                               existing=finished.get("payload"))
        if args.outcome in {"success", "degraded"} and step_mode_changed(start, meta, events):
            raise ControlError("步骤执行期间有效模式变化；请 blocked 终结后按新模式重新审查",
                               gate_id="effective_mode_drift", route="review")
        if args.outcome in {"success", "degraded"} and start_payload.get("contract_digest") != canonical_digest(contract):
            current, _ = capture_step_inputs(out_dir, meta, model, args.step, **candidate_access)
            if not attempt_upgrade_compatible(start_payload, contract, captured_protected=current["protected"]):
                raise ControlError("步骤合同漂移；请 blocked 终结后重新进入步骤",
                                   gate_id="step_contract_drift")
        gate_events = [event for event in attempt_events(events, args.attempt)
                       if event.get("event_type") == "gate_checked"]
        latest_checks = {}
        for gate_event in gate_events:
            gate_payload = gate_event.get("payload") or {}
            latest_checks[gate_payload.get("boundary")] = gate_payload.get("result")
        missing_checks = [boundary for boundary in contract["required_checks"]
                          if latest_checks.get(boundary) != "pass"]
        outputs, missing_outputs = validate_step_outputs(out_dir, meta, model, args.step, **access)
        missing_receipts = missing_output_receipts(
            out_dir, meta, contract, events, start)
        if args.step == "review":
            missing_receipts.extend(review_evidence_issues(out_dir, meta, events, start))
        if args.step in ("code", "deepcheck", "audit"):
            worklist_issues = inspection_worklist_issues(out_dir, meta, args.step, args.attempt,
                                                        allow_incomplete=args.outcome == "degraded", **access)
            missing_receipts.extend(worklist_issues)
            if args.outcome == "degraded" and (worklist_issues or any(
                    item.startswith("inspection_worklist:") for item in missing_receipts)):
                raise ControlError("降级自查必须有真实工作清单回执与明确债务", gate_id="inspection_worklist",
                                   violations=missing_receipts)
        if args.step in ("deepcheck", "audit"):
            coverage_issues = inspection_coverage_issues(out_dir, meta, args.step, args.attempt,
                                                         allow_incomplete=args.outcome == "degraded", **access)
            missing_receipts.extend(coverage_issues)
            if args.outcome == "degraded" and (coverage_issues or any(
                    item.startswith("inspection_coverage:") for item in missing_receipts)):
                raise ControlError("降级复检必须有真实覆盖声明回执与未完成原因", exit_code=1,
                                   gate_id="inspection_coverage", violations=missing_receipts)
        if not args.evidence:
            raise ControlError("step finish 必须至少提供一个 --evidence 简短引用", exit_code=2,
                               gate_id="step_receipt")
        if args.outcome in {"success", "degraded"} and missing_checks:
            raise ControlError(
                "步骤可推进回执缺必需边界检查", exit_code=1, gate_id="step_receipt",
                missing_checks=missing_checks)
        if args.outcome == "success" and (missing_outputs or missing_receipts):
            raise ControlError(
                "步骤成功回执条件不完整", exit_code=1, gate_id="step_receipt",
                missing_checks=missing_checks, missing_outputs=missing_outputs,
                missing_output_receipts=missing_receipts,
                hint="补齐边界 check 与输出产物后再 finish；若确实无法完成，显式记录 blocked/degraded。")
        if args.outcome in {"success", "degraded"} and requires_execution_receipts(events) \
                and args.step in model.get("finish_gate_steps", []):
            # Standalone steps have no completed-state transition to run these
            # checks. Keep one authoritative completion boundary for them too.
            if access:
                raise ControlError("code final linter is not enabled", gate_id="candidate_business_propagation_pending")
            failed = [g for g in run_gate_linters(out_dir, step=args.step, metadata=meta) if not g["ok"]]
            if failed:
                raise ControlError("独立步骤完成门禁未通过", gate_id="step_gates",
                                   failed_gates=failed)
        payload = {
            "execution_model_version": model["schema_version"],
            "step": args.step,
            "attempt": args.attempt,
            "outcome": args.outcome,
            "duration_ms": elapsed_ms(start.get("timestamp")),
            "checks": latest_checks,
            "outputs": outputs,
            "evidence": args.evidence,
        }
        prior = find_idempotent_event(
            events, args.request, "step_finished", payload,
            payload_keys=["step", "attempt", "outcome", "checks", "outputs", "evidence"])
        if not prior and args.outcome == "success" and args.step in candidate_module().REVIEW_STEPS \
                and (meta.get("candidate_tracking") or {}).get("mode") == "enabled":
            candidate = candidate_snapshot(out_dir, meta, events=events, **candidate_access)
            if args.step in ("deepcheck", "audit", "lite_review"):
                proof = candidate_module().review_source_equivalence(start_payload.get("candidate_input"), candidate)
                if not proof["equivalent"]:
                    raise ControlError("复检期间候选源码漂移，先blocked终结再重新start",
                                       gate_id="candidate_review_drift", proof=proof)
            finish_id = str(uuid.uuid4())
            receipt = {"version": 1, "step": args.step, "attempt": args.attempt,
                       "event_id": finish_id, "candidate_id": candidate["candidate_id"],
                       "candidate": candidate, "evidence": list(args.evidence)}
            payload["candidate_receipt"] = receipt
            updated = dict(meta, candidate_tracking=dict(meta["candidate_tracking"], current=candidate),
                           review_receipts=[*(meta.get("review_receipts") or []), receipt])
            def before_publish():
                fresh = candidate_snapshot(out_dir, meta, events=events, **candidate_access)
                if fresh["candidate_id"] != candidate["candidate_id"]:
                    raise ControlError("code source changed before publication", gate_id="candidate_capture")
                _, issues = validate_step_outputs(out_dir, meta, model, args.step, **access)
                issues += missing_output_receipts(out_dir, meta, contract, events, start)
                if issues:
                    raise ControlError("code outputs changed before publication", gate_id="step_receipt", violations=issues)
                _code_guard(out_dir, meta, **access)
            publication = dict(before_publish=before_publish,
                               before_append=lambda: _code_guard(out_dir, meta, **access),
                               **candidate_access) if access else {}
            event = commit_metadata_and_event(out_dir, meta, updated, "step_finished", payload,
                                              request_id=args.request, event_id=finish_id, **publication)
        else:
            event = prior or append_event(out_dir, meta, "step_finished", payload,
                                          request_id=args.request, **access)
        _code_output(json.dumps({"ok": True, "step": args.step, "phase": "finish",
                          "attempt": args.attempt, "outcome": args.outcome,
                          "duration_ms": payload["duration_ms"],
                          "event_id": event["event_id"],
                          "already_applied": bool(prior)}, ensure_ascii=False, indent=2), out_dir, meta, access)
        return 0


def cmd_inspection(args, *, git_provider=None, require_git_provider=False, inspection_provider_factory=None):
    """Prepare/read/assess through the same frozen declaration and artifact transaction."""
    batch_paths = None
    raw_paths = getattr(args, "paths_json", None)
    if raw_paths is not None:
        try:
            if args.phase != "read" or getattr(args, "path", None) is not None:
                raise ValueError("--paths-json 仅允许 read，且不能同时指定 --path")
            if type(raw_paths) is not str or len(raw_paths.encode("utf-8")) > 64 * 1024:
                raise ValueError("批量路径 JSON 必须是至多64KiB的字符串")
            paths = strict_json_loads(raw_paths)
            if type(paths) is not list or not 1 <= len(paths) <= 256:
                raise ValueError("批量路径必须包含1至256个唯一成员")
            for path in paths:
                if type(path) is not str or not path or "\0" in path or "\\" in path \
                        or path.startswith("/") or PureWindowsPath(path).drive \
                        or any(part in {"", ".", "..", ".git"} for part in path.split("/")):
                    raise ValueError("批量路径必须是规范的项目相对POSIX文件")
            if len(set(paths)) != len(paths):
                raise ValueError("批量路径不得重复")
            batch_paths = sorted(paths)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ControlError(f"批量Read参数无效: {exc}", gate_id="inspection_worklist") from exc
    max_files = getattr(args, "max_files", None)
    if max_files is not None and (args.phase != "prepare" or type(max_files) is not int or max_files <= 0):
        raise ControlError("显式文件预算仅允许 prepare 使用正整数", gate_id="inspection_budget")
    out_dir = Path(args.dir).resolve()
    access = _code_access(args.step, git_provider, require_git_provider, inspection_provider_factory)
    candidate_access = _code_candidate_access(access)
    if access and args.phase not in {"prepare", "read", "assess", "check"}:
        raise ControlError("code inspection phase is not enabled", gate_id="candidate_business_propagation_pending")
    model = load_execution_model()
    contract = step_contract(model, args.step)
    target = out_dir / f"{args.step}_worklist.json"
    if target.is_symlink():
        raise ControlError("工作清单不得是符号链接", gate_id="inspection_worklist")
    if args.phase == "check":
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if access:
            _code_inspection_provider(out_dir, meta, **access)
        events, problems = verify_event_chain(out_dir, meta, **candidate_access)
        if problems:
            raise ControlError("事件链不完整", gate_id="event_chain", violations=problems)
        start = find_step_start(events, args.attempt)
        if start["payload"].get("step") != args.step:
            raise ControlError("工作清单与执行步骤不一致", gate_id="inspection_worklist")
        issues = inspection_worklist_issues(out_dir, meta, args.step, args.attempt,
                                            allow_incomplete=args.allow_incomplete, **access)
        _code_output(json.dumps(dict(ok=not issues, read_only=True, path=str(target), violations=issues), ensure_ascii=False), out_dir, meta, access)
        return 0 if not issues else 1
    with DirLock(out_dir):
        # A new spelling or replay exemption cannot change a frozen budget.
        # Reject before transaction recovery as well as the first report write.
        if max_files is not None and target.exists():
            frozen = load_json(target, "inspection worklist")
            if isinstance(frozen, dict) and frozen.get("attempt") == args.attempt and frozen.get("max_files") != max_files:
                raise ControlError("同 attempt 不得改变审查文件预算；先终结并按原流程新建", gate_id="inspection_budget")
        if access:
            _code_inspection_provider(out_dir, load_metadata(out_dir), **access)
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider recovery is not enabled", gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        _stage_retirement_guard(out_dir, **candidate_access)
        events, problems = verify_event_chain(out_dir, meta, **candidate_access)
        if problems:
            raise ControlError("事件链不完整", gate_id="event_chain", violations=problems)
        start = find_step_start(events, args.attempt)
        if start["payload"].get("step") != args.step or step_attempt_finished(events, args.attempt):
            raise ControlError("工作清单须关联同一步骤的未终结 attempt", gate_id="inspection_worklist")
        current, missing = capture_step_inputs(out_dir, meta, model, args.step, **candidate_access)
        upgrade_only_drift = attempt_upgrade_compatible(
            start["payload"], contract, captured_protected=current.get("protected"))
        if missing or (not upgrade_only_drift and (current["protected"] != start["payload"].get("protected")
                or current["contract_digest"] != start["payload"].get("contract_digest"))):
            raise ControlError("执行合同或受保护输入漂移，先 blocked 重入", gate_id="inspection_worklist")
        if not any(e.get("event_type") == "gate_checked" and (e.get("payload") or {}).get("attempt") == args.attempt
                   and (e.get("payload") or {}).get("boundary") == "before_write"
                   and (e.get("payload") or {}).get("result") == "pass" for e in events):
            raise ControlError("写工作清单前须真实 before_write check", gate_id="inspection_worklist")
        helper, provider = _code_inspection_provider(out_dir, meta, **access) if access else (inspection_module(), None)
        inspection_access = dict(git_provider=provider) if access else {}
        workspace = execution_workspace(out_dir, meta)
        resumed = False
        if target.exists():
            existing = load_json(target, "inspection worklist")
            if isinstance(existing, dict) and existing.get("attempt") == args.attempt:
                report, resumed = existing, True
            else:
                report = None
        else:
            report = None
        try:
            assessment = strict_json_loads(args.assessment_json) if args.phase == "assess" and args.assessment_json else None
            request_identity = dict(phase=args.phase,
                read_phase=args.read_phase if args.phase == "read" else None,
                source_path=args.path if args.phase == "read" else None,
                related=sorted(set(args.related)) if args.related else None,
                scopes=sorted(set(args.scope)) if args.scope else None,
                baselines=strict_json_loads(args.baselines_json) if args.baselines_json else None)
            if batch_paths is not None:
                request_identity["source_paths"] = batch_paths
            # Omitting the new option retains the exact legacy request shape.
            if max_files is not None:
                request_identity["max_files"] = max_files
            if args.phase == "assess":
                request_identity.update(check_id=args.check_id, assessment_phase=args.read_phase,
                    assessment_sha256=canonical_digest(assessment))
            if args.phase == "prepare" and not resumed:
                baselines = strict_json_loads(args.baselines_json) if args.baselines_json else None
                if baselines is None and (meta.get("candidate_tracking") or {}).get("mode") == "enabled":
                    baselines = {".": meta["candidate_tracking"]["base"]}
                report = helper.build_worklist(workspace, meta.get("code_files") or [], step=args.step,
                    ticket_id=meta["ticket_id"], attempt=args.attempt,
                    mode=workflow_effective_mode(meta),
                    related=args.related, scopes=args.scope, baselines=baselines,
                    **({"max_files": max_files} if max_files is not None else {}), **inspection_access)
                if (meta.get("candidate_tracking") or {}).get("mode") == "enabled":
                    canonical_scope = candidate_inspection_scope(out_dir, meta,
                        effective_base=meta["candidate_tracking"]["base"], events=events)
                    report = helper._reuse_equivalent_related(report, workspace, canonical_scope, **inspection_access)
                report.setdefault("findings", [])
            elif args.phase == "prepare" and resumed:
                options = (("related", sorted(set(args.related)) if args.related else None),
                           ("scopes", sorted(set(args.scope)) if args.scope else None),
                           ("baselines", strict_json_loads(args.baselines_json) if args.baselines_json else None))
                # A canonical declaration can differ from the original CLI
                # spelling. Only its exact receipted request may replay that
                # mapping; all normal source/declaration checks still follow.
                exact_replay = any(e.get("event_type") == "artifact_written" and e.get("request_id") == args.request
                    and (e.get("payload") or {}).get("step") == args.step
                    and (e.get("payload") or {}).get("attempt") == args.attempt
                    and (e.get("payload") or {}).get("inspection_request") == request_identity
                    and (e.get("payload") or {}).get("inspection_declaration_hash") == inspection_declaration_hash(report)
                    for e in events)
                if not exact_replay and any(value is not None and value != report.get(key) for key, value in options):
                    raise ValueError("恢复轮不得改变审查范围；先 blocked 重入")
            if report is None:
                raise ValueError("先 prepare 本 attempt 的工作清单")
            prior_declarations = [e["payload"]["inspection_declaration_hash"] for e in events
                if e.get("event_type") == "artifact_written" and (e.get("payload") or {}).get("attempt") == args.attempt
                and (e.get("payload") or {}).get("inspection_declaration_hash")]
            if any(h != inspection_declaration_hash(report) for h in prior_declarations):
                raise ValueError("审查边界声明变化，先 blocked 重入")
            # Pending Read is expected during preparation. It must not relax source/identity checks.
            pending = dict(report, coverage_status="partial", debt_reason="pending Read")
            issues = helper.validate_worklist(pending, workspace, code_files=meta.get("code_files") or [],
                step=args.step, ticket_id=meta["ticket_id"], attempt=args.attempt, allow_incomplete=True,
                required_checks_version=1, **inspection_access)
            if issues:
                raise ValueError(str(issues))
            stable_payload = dict(execution_model_version=model["schema_version"], step=args.step,
                attempt=args.attempt, output="inspection_worklist", scope="ticket", path=str(target),
                inspection_declaration_hash=inspection_declaration_hash(report), inspection_checks_version=1,
                inspection_request=request_identity)
            prior = find_idempotent_event(events, args.request, "artifact_written", stable_payload,
                payload_keys=list(stable_payload))
            if prior:
                # A later Read may have expanded the artifact. Replaying this
                # operation must return its original receipt, not erase progress.
                _code_output(json.dumps(dict(ok=True, output="inspection_worklist", path=str(target), resumed=True,
                    event_id=prior["event_id"], sha256=prior["payload"]["sha256"],
                    current_sha256=file_sha256(target), already_applied=True), ensure_ascii=False), out_dir, meta, access)
                return 0
            if args.phase == "read":
                if args.read_phase not in report["required_phases"] or (batch_paths is None and not args.path):
                    raise ValueError("read 必须指定合法 --read-phase 和 --path")
                selected = []
                for path in batch_paths if batch_paths is not None else [args.path]:
                    members = [f for unit in report["units"] for f in unit["files"] if f["path"] == path]
                    if len(members) != 1:
                        raise ValueError("路径不在唯一自查单元中")
                    selected.append(members[0])
                # Resolve every member before changing the in-memory report.
                # The original whole-worklist SHA/provider/stage checks above
                # and original publication checks below remain mandatory.
                for member in selected:
                    member["reads"][args.read_phase] = member["sha256"]
            elif args.phase == "assess":
                if not args.check_id or not args.assessment_json or not args.read_phase:
                    raise ValueError("assess 必须指定 --check-id/--read-phase/--assessment-json")
                helper.apply_assessment(report, workspace, args.check_id, args.read_phase, assessment, **inspection_access)
            if args.phase != "prepare" or not resumed:
                # Reject a conflicting request before replacing any valid worklist.
                encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                prospective = dict(stable_payload, sha256=hashlib.sha256(encoded).hexdigest(), size=len(encoded))
                find_idempotent_event(events, args.request, "artifact_written", prospective)
                if access:
                    _code_guard(out_dir, meta, **access)
                atomic_write_json(target, report)
                if access:
                    _code_guard(out_dir, meta, **access)
        except (ValueError, TypeError, OSError) as exc:
            raise ControlError(f"工作清单准备/版本校验失败: {exc}", gate_id="inspection_worklist")
        payload = dict(stable_payload, sha256=file_sha256(target), size=target.stat().st_size)
        prior = find_idempotent_event(events, args.request, "artifact_written", payload)
        event = prior or append_event(out_dir, meta, "artifact_written", payload, request_id=args.request, **access)
    _code_output(json.dumps(dict(ok=True, output="inspection_worklist", path=str(target), resumed=resumed,
                          event_id=event["event_id"], sha256=payload["sha256"], already_applied=bool(prior)), ensure_ascii=False), out_dir, meta, access)
    return 0


def manual_dedup_verification_record(out_dir, meta, attempt, target, digest):
    """受控登记人工去重证据时，当场对当前源码完成完整校验并返回验证回执。

    报告摘要只能证明文件未变，不能证明报告曾经有效；因此完整校验（结构、
    函数目录、家族/去重结论、范围覆盖）必须在源码仍与报告一致的登记时刻完成，
    并把结果写入受事件链保护的 artifact 回执。后续 code 阶段修改源码后，
    lint 以该回执维持 review 时的清偿，无需也无法用新源码重验旧报告。
    校验不通过 → 拒绝登记（不完整报告不得作为受控证据）。
    """
    spec = importlib.util.spec_from_file_location("control_manual_dedup",
                                                  SKILL_ROOT / "tools/manual_dedup_evidence.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    report = load_json(target, "review dedup evidence")
    if not isinstance(report, dict):
        raise ControlError("人工去重证据必须是 JSON 对象", gate_id="review_dedup_evidence")
    workspace_value = meta.get("project_path")
    if not isinstance(workspace_value, str) or not workspace_value:
        raise ControlError("人工去重证据缺少工单工程根", gate_id="review_dedup_evidence")
    workspace = Path(workspace_value).resolve()
    entries = report.get("repositories")
    if entries is None:
        # 文档单仓格式：报告本身即 manual_dedup 报告，或包一层 {"manual_dedup": 报告}。
        repositories = [{"repo_root": ".",
                         "manual_dedup": report.get("manual_dedup", report)}]
    elif isinstance(entries, list) and entries             and all(isinstance(e, dict) and isinstance(e.get("repo_root"), str) for e in entries):
        repositories = entries
    else:
        raise ControlError("人工去重证据 repositories 结构非法", gate_id="review_dedup_evidence")
    record = {"version": 1, "ticket_id": meta.get("ticket_id"), "attempt": attempt,
              "report_sha256": digest, "repositories": []}
    for entry in repositories:
        report_part = entry.get("manual_dedup", entry)
        # 文档格式把 review_scope 放在内层报告；外层同名键仅作兼容旧写法。
        scope = entry.get("review_scope")
        if scope is None and isinstance(report_part, dict):
            scope = report_part.get("review_scope")
        root_raw = entry["repo_root"] if "repo_root" in entry else "."
        repo = (Path(root_raw) if Path(root_raw).is_absolute() else workspace / Path(root_raw)).resolve()
        if not repo.is_dir():
            raise ControlError(f"人工去重证据仓库根不存在: {root_raw}", gate_id="review_dedup_evidence")
        if not isinstance(scope, list) or not scope or any(not isinstance(s, str) or not s.strip() for s in scope):
            raise ControlError("人工去重证据缺有效 review_scope", gate_id="review_dedup_evidence")
        try:
            sources = helper.collect_sources(repo, scope)
        except (helper.EvidenceError, OSError, ValueError, UnicodeError) as exc:
            raise ControlError(f"人工去重证据源码枚举失败: {exc}", gate_id="review_dedup_evidence") from exc
        errors = helper.validate_report(report_part, repo, meta.get("ticket_id"), attempt, scope, sources)
        if errors:
            raise ControlError("人工去重证据完整校验未通过", gate_id="review_dedup_evidence",
                               violations=errors[:8])
        record["repositories"].append({
            "repo_root": str(repo), "review_scope": list(scope),
            "function_count": sum(len(source["functions"]) for source in report_part["sources"]),
            "sources": dict(sources),
        })
    return record


def declared_output_path(out_dir, meta, contract, target):
    workspace = execution_workspace(out_dir, meta)
    target = Path(target).resolve()
    for port in contract["outputs"]:
        kind = port["kind"]
        value = port_value(out_dir, meta, port)
        if kind == "ticket_file" and port_path(Path(out_dir), value) == target:
            return port["id"]
        if kind == "ticket_glob":
            try:
                relative = str(target.relative_to(Path(out_dir)))
            except ValueError:
                continue
            if fnmatch.fnmatch(relative, value):
                return port["id"]
        if kind == "metadata_files":
            exists, raw_paths = json_pointer_get(meta, value)
            base = workspace if port.get("base", "ticket") == "workspace" else Path(out_dir)
            if exists and isinstance(raw_paths, list) and any(
                    isinstance(raw, str) and port_path(base, raw) == target
                    for raw in raw_paths):
                return port["id"]
        if kind == "external_artifact":
            rendered = str(value).replace("<workspace>", str(workspace))
            rendered = str(Path(rendered).expanduser())
            if any(Path(path).resolve() == target for path in globlib.glob(rendered)):
                return port["id"]
    return None


def _artifact_batch_members(args):
    """Parse ``--paths-json`` into ordered canonical members, or None for single-path.

    Order is preserved (unlike the inspection read set) because the batch must
    emit receipts in the same order as the equivalent single calls.
    """
    raw = getattr(args, "paths_json", None)
    if raw is None:
        return None
    try:
        if getattr(args, "path", None) is not None:
            raise ValueError("--paths-json 与 --path 互斥")
        if getattr(args, "scope", "ticket") == "external":
            raise ValueError("批量产物仅允许 ticket/workspace scope")
        if type(raw) is not str or len(raw.encode("utf-8")) > 64 * 1024:
            raise ValueError("批量路径 JSON 必须是至多64KiB的字符串")
        members = strict_json_loads(raw)
        if type(members) is not list or not 1 <= len(members) <= 256:
            raise ValueError("批量路径必须包含1至256个唯一成员")
        for member in members:
            if type(member) is not str or not member or "\0" in member or "\\" in member \
                    or member.startswith("/") or PureWindowsPath(member).drive \
                    or any(part in {"", ".", "..", ".git"} for part in member.split("/")):
                raise ValueError("批量路径必须是规范的项目相对POSIX文件")
        if len(set(members)) != len(members):
            raise ValueError("批量路径不得重复")
        return list(members)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ControlError(f"批量产物参数无效: {exc}", gate_id="artifact_batch") from exc


def _publish_artifact_batch(out_dir, meta, events, prepared, base_request, access):
    """Publish one artifact event per member against one verified ledger.

    Chain construction and idempotency checks run for every member before the
    first append, so a conflict or a refused replay never leaves a partial
    batch behind.
    """
    event_schema = _event_schema_for("artifact_written")
    chain = list(events)
    planned, results = [], []
    for index, (target, output_id, payload) in enumerate(prepared):
        request = None if base_request is None else f"{base_request}#{index}"
        prior = find_idempotent_event(chain, request, "artifact_written", payload)
        if prior is None:
            prepared_event = build_prepared_event(chain, meta, "artifact_written", payload,
                                                  request, "icode", event_schema=event_schema)
            chain.append(prepared_event)
            planned.append((prepared_event, payload, request))
            event_id, already_applied = prepared_event["event_id"], False
        else:
            event_id, already_applied = prior["event_id"], True
        results.append({"output": output_id, "path": str(target), "sha256": payload["sha256"],
                        "event_id": event_id, "already_applied": already_applied})
    ledger = _committed_ledger(out_dir) if _storage_enabled(out_dir) else None
    for prepared_event, payload, request in planned:
        append_event(out_dir, meta, "artifact_written", payload, request_id=request,
                     prepared=prepared_event, storage_ledger=ledger, **access)
    return results


def cmd_artifact(args, *, git_provider=None, require_git_provider=False, inspection_provider_factory=None):
    out_dir = Path(args.dir).resolve()
    access = _code_access(args.step, git_provider, require_git_provider, inspection_provider_factory)
    candidate_access = _code_candidate_access(access)
    # Provider admission stays ahead of argument validation, as before.
    batch_members = _artifact_batch_members(args)
    if batch_members is None and getattr(args, "path", None) is None:
        raise ControlError("必须提供 --path 或 --paths-json", exit_code=2, gate_id="artifact_batch")
    with DirLock(out_dir):
        if access:
            _code_inspection_provider(out_dir, load_metadata(out_dir), **access)
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider recovery is not enabled", gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        model = load_execution_model(meta)
        contract = step_contract(model, args.step)
        events, problems = verify_event_chain(out_dir, meta, **candidate_access)
        if problems:
            raise ControlError("事件链不完整，拒绝记录产物", exit_code=1,
                               gate_id="event_chain", violations=problems)
        start = find_step_start(events, args.attempt)
        if start["payload"].get("step") != args.step or step_attempt_finished(events, args.attempt):
            raise ControlError("产物必须关联同一步骤的未终结 attempt", exit_code=1,
                               gate_id="artifact_attempt")
        base = Path(out_dir) if args.scope == "ticket" else execution_workspace(out_dir, meta)
        prepared = []
        for member in (batch_members if batch_members is not None else [args.path]):
            target = port_path(base, member)
            if args.scope != "external" and not target.is_relative_to(base.resolve()):
                raise ControlError("产物路径逃逸出声明 scope", exit_code=1,
                                   gate_id="artifact_scope", path=str(target))
            if not target.is_file():
                raise ControlError(f"产物不存在或不是普通文件: {target}", exit_code=1,
                                   gate_id="artifact_exists")
            output_id = declared_output_path(out_dir, meta, contract, target)
            if output_id is None:
                raise ControlError("产物不在该步骤 outputs 端口合同内", exit_code=1,
                                   gate_id="step_ports", step=args.step, path=str(target))
            payload = {
                "execution_model_version": model["schema_version"],
                "step": args.step,
                "attempt": args.attempt,
                "output": output_id,
                "scope": args.scope,
                "path": str(target),
                "sha256": file_sha256(target),
                "size": target.stat().st_size,
            }
            if output_id in ("review_dedup_evidence", "deepcheck_dedup_evidence"):
                # 完整校验必须在源码仍与报告一致的登记时刻完成，结果进入受控回执；
                # 摘要只能证明文件未变，不能证明报告曾经有效。review 与 deepcheck
                # 的人工去重证据共用同一登记合同。
                payload["manual_dedup_verification"] = manual_dedup_verification_record(
                    out_dir, meta, args.attempt, target, payload["sha256"])
            if output_id == "review_manifest":
                issues = review_evidence_issues(out_dir, meta)
                if issues:
                    raise ControlError("审查manifest不满足结构/计数合同", exit_code=1,
                                       gate_id="review_manifest", violations=issues)
                data = load_json(target, "review manifest")
                # Preserve each completed round's identity in the original event,
                # including clean rounds that have no separate detail file.
                payload["round_digests"] = {str(row["round"]): canonical_digest(row) for row in data["rounds"]}
            prepared.append((target, output_id, payload))
        if batch_members is None:
            target, output_id, payload = prepared[0]
            prior = find_idempotent_event(events, args.request, "artifact_written", payload)
            event = prior or append_event(out_dir, meta, "artifact_written", payload,
                                          request_id=args.request, **access)
            results = [{"output": output_id, "path": str(target), "sha256": payload["sha256"],
                        "event_id": event["event_id"], "already_applied": bool(prior)}]
        else:
            results = _publish_artifact_batch(out_dir, meta, events, prepared, args.request, access)
    response = {"ok": True, "step": args.step, "attempt": args.attempt}
    if batch_members is None:
        response.update(results[0])
    else:
        response.update(count=len(results), artifacts=results)
    _code_output(json.dumps(response, ensure_ascii=False, indent=2), out_dir, meta, access)
    return 0


def _closeout_run(argv, access):
    """Run one existing subcommand in-process and return its parsed payload.

    Composition keeps every gate, port check, receipt and lock at its single
    source; closeout only removes repeated process starts.
    """
    parsed = build_parser().parse_args(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = parsed.func(parsed, **access) if access else parsed.func(parsed)
    payload = None
    text = buffer.getvalue().strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    if code not in (0, None):
        raise ControlError(f"closeout 子阶段退出码 {code}", exit_code=code)
    if isinstance(payload, dict) and payload.get("ok") is False:
        extra = {key: payload[key] for key in ("gate_id", "hint", "violations") if key in payload}
        raise ControlError(str(payload.get("error") or "closeout 子阶段失败"), **extra)
    return payload if payload is not None else {"ok": True}


def cmd_closeout(args, *, git_provider=None, require_git_provider=False, inspection_provider_factory=None):
    """机械收尾：可选产物登记 + before_transition 复检 + finish + 可选 transition。

    每个子阶段仍走各自的命令、锁与事务；closeout 只去掉重复的进程启动，不合并、
    不跳过任何门禁。状态目标因步骤而异，故 transition 只在显式给出时执行。
    """
    access = {}
    if _candidate_access_requested(git_provider, require_git_provider) or inspection_provider_factory is not None:
        access = dict(git_provider=git_provider, require_git_provider=require_git_provider,
                      inspection_provider_factory=inspection_provider_factory)
    out_dir = str(Path(args.dir).resolve())

    def phase_request(name):
        return None if args.request is None else f"{args.request}.{name}"

    results = {}
    if args.paths_json is not None or args.path is not None:
        argv = ["artifact", "--dir", out_dir, "--step", args.step, "--attempt", args.attempt,
                "--scope", args.scope]
        argv += (["--paths-json", args.paths_json] if args.paths_json is not None
                 else ["--path", args.path])
        if phase_request("artifact"):
            argv += ["--request", phase_request("artifact")]
        results["artifacts"] = _closeout_run(argv, access)

    argv = ["step", "--dir", out_dir, "--step", args.step, "--attempt", args.attempt,
            "--phase", "check", "--boundary", "before_transition"]
    if phase_request("check"):
        argv += ["--request", phase_request("check")]
    results["check"] = _closeout_run(argv, access)

    argv = ["step", "--dir", out_dir, "--step", args.step, "--attempt", args.attempt,
            "--phase", "finish", "--outcome", args.outcome]
    for evidence in args.evidence:
        argv += ["--evidence", evidence]
    if phase_request("finish"):
        argv += ["--request", phase_request("finish")]
    results["finish"] = _closeout_run(argv, access)

    if args.transition_to:
        argv = ["transition", "--dir", out_dir, "--to", args.transition_to]
        if args.delivery_verdict:
            argv += ["--delivery-verdict", args.delivery_verdict]
        if phase_request("transition"):
            argv += ["--request-id", phase_request("transition")]
        results["transition"] = _closeout_run(argv, {})

    print(json.dumps({"ok": True, "step": args.step, "attempt": args.attempt, **results},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_compact(args):
    """Physical storage maintenance: encode one legacy ticket as storage v2.

    Dry-run by default. Only the physical envelope changes; the logical object
    and its metadata hash are identical before and after, which the pre-write
    round trip and the post-write reload both assert before the command
    reports success.
    """
    out_dir = Path(args.dir).resolve()
    meta_path = out_dir / METADATA_NAME
    if meta_path.is_symlink():
        raise ControlError("metadata 不得是符号链接", exit_code=1, gate_id="compact_target")
    if not meta_path.is_file():
        raise ControlError(f"{METADATA_NAME} 不存在", exit_code=1, gate_id="compact_target",
                           path=str(meta_path))
    with DirLock(out_dir):
        raw = _load_raw_json(meta_path, "metadata")
        if storage_module().is_envelope(raw):
            print(json.dumps({"ok": True, "dir": str(out_dir), "already_compact": True,
                              "applied": False}, ensure_ascii=False, indent=2))
            return 0
        if (out_dir / TXN_NAME).exists():
            raise ControlError("存在未完成事务，先恢复再压缩", exit_code=1,
                               gate_id="compact_pending_transaction")
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        before_hash = metadata_hash(meta)
        before_bytes = meta_path.stat().st_size
        ledger = _committed_ledger(out_dir)
        encoded = storage_module().encode_document(meta, kind="metadata",
                                                   committed_registry=ledger.registry)
        del meta
        restored = storage_module().decode_document(encoded, kind="metadata",
                                                    registry=ledger.registry)
        same = metadata_hash(restored) == before_hash
        del restored
        if not same:
            raise ControlError("压缩后逻辑内容与原文不一致，拒绝写入", exit_code=1,
                               gate_id="compact_roundtrip")
        after_bytes = len(json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        events_path = out_dir / EVENTS_NAME
        # v2 reads decode the whole ledger, so report it: a ledger that dominates
        # the metadata can make the compacted form slower per command.
        event_bytes = events_path.stat().st_size if events_path.is_file() else 0
        report = {"ok": True, "dir": str(out_dir), "applied": False,
                  "before_bytes": before_bytes, "after_bytes": after_bytes,
                  "event_bytes": event_bytes,
                  "saved_ratio": round(1 - after_bytes / before_bytes, 4) if before_bytes else 0.0,
                  "metadata_hash": before_hash}
        if event_bytes >= before_bytes:
            report["note"] = "事件账本不小于 metadata：减重后每次读取需解码完整账本，可能反而更慢"
        if not args.apply:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        # Measured regression guard: when the ledger dominates, v2 reads get
        # slower, so refuse instead of trusting the operator to read the note.
        if event_bytes >= before_bytes and not getattr(args, "force", False):
            raise ControlError(
                "事件账本不小于 metadata，减重后读取会变慢；先实测再带 --force 执行",
                exit_code=1, gate_id="compact_ledger_dominates",
                before_bytes=before_bytes, event_bytes=event_bytes)
        backup = out_dir / (METADATA_NAME + ".pre-compact.bak")
        if not backup.exists():
            shutil.copy2(meta_path, backup)
        atomic_write_json(meta_path, encoded)
        try:
            reread = load_metadata(out_dir)
            if metadata_hash(reread) != before_hash:
                raise ControlError("重读逻辑内容与压缩前不一致", exit_code=1, gate_id="compact_verify")
            require_valid_metadata(reread)
        except Exception:
            shutil.copy2(backup, meta_path)
            raise
        report.update(applied=True, backup=str(backup))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0


def evaluate_execution_policy(model, op_class, failure, attempts):
    if op_class not in model["operation_classes"]:
        raise ControlError(f"未知 operation class: {op_class!r}", exit_code=2,
                           allowed=sorted(model["operation_classes"]))
    if failure not in model["failure_policies"]:
        raise ControlError(f"未知 failure class: {failure!r}", exit_code=2,
                           allowed=sorted(model["failure_policies"]))
    if attempts < 0:
        raise ControlError("--attempts 不能小于 0", exit_code=2)
    policy = dict(model["failure_policies"][failure])
    action = policy["action"]
    reason = "failure_policy"
    if op_class == "destructive_hardware":
        action, reason = "human_decision", "destructive_hardware_never_auto_retry"
    elif op_class == "external_side_effect" and action == "retry":
        action, reason = "verify_receipt", "external_side_effect_no_blind_retry"
    elif op_class == "managed_write" and action == "retry":
        action, reason = "verify_receipt", "managed_write_requires_idempotency_and_after_check"
    max_attempts = policy["max_attempts"]
    if action == "retry" and attempts >= max_attempts:
        action, reason = "block", "retry_budget_exhausted"
    backoff = None
    schedule = policy.get("backoff_seconds") or []
    if action == "retry" and schedule:
        backoff = schedule[min(attempts, len(schedule) - 1)]
    return {
        "operation_class": op_class,
        "failure_class": failure,
        "attempts": attempts,
        "action": action,
        "reason": reason,
        "max_attempts": max_attempts,
        "backoff_seconds": backoff,
        "conclusion_ceiling": policy["conclusion_ceiling"],
        "auto_retry": action == "retry" and model["operation_classes"][op_class]["auto_retry"],
    }


def cmd_policy(args):
    model = load_execution_model()
    problems = validate_execution_catalog(model)
    if problems:
        raise ControlError("execution_model 机器契约无效", exit_code=1,
                           gate_id="execution_model_catalog", violations=problems)
    result = evaluate_execution_policy(model, args.opclass, args.failure, args.attempts)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


def find_operation_start(events, attempt):
    starts = [event for event in events
              if event.get("event_type") == "operation_started"
              and (event.get("payload") or {}).get("execution_model_version") == 1
              and (event.get("payload") or {}).get("attempt") == attempt]
    if len(starts) != 1:
        raise ControlError(f"operation attempt={attempt!r} 未唯一启动", exit_code=1,
                           gate_id="operation_attempt", matches=len(starts))
    return starts[0]


def operation_finished(events, attempt):
    return next((event for event in events
                 if event.get("event_type") == "operation_finished"
                 and (event.get("payload") or {}).get("execution_model_version") == 1
                 and (event.get("payload") or {}).get("attempt") == attempt), None)


def _append_operation_event(out_dir, meta, event_type, payload, request_id, access):
    """Candidate-only review operations use the existing native transaction.

    append_event's optional inspection pair belongs to Code. Do not weaken that
    contract to admit a candidate-only operation; its same-value metadata event
    is supported by the regular transaction and the original operation ledger.
    """
    if not access:
        return append_event(out_dir, meta, event_type, payload, request_id=request_id)
    return commit_metadata_and_event(out_dir, meta, copy.deepcopy(meta), event_type, payload,
        request_id=request_id, before_publish=lambda: _candidate_context_guard(
            out_dir, meta, access["git_provider"], access["require_git_provider"]), **access)


def _operation_output(payload, sink):
    # A private result sink avoids redirecting process-global stdout while
    # other trusted providers may be running. Default CLI output is unchanged.
    if sink is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        sink(payload)


def _operation_start_locked(args, out_dir, meta, events, model, access, *, _result_sink=None):
    """Native operation start, shared by CLI and already-locked review admission."""
    if not isinstance(args.name, str) or not args.name.strip():
        raise ControlError("operation --phase start 必须提供非空 --name", exit_code=2)
    if args.opclass is None:
        raise ControlError("operation --phase start 必须提供 --opclass", exit_code=2)
    if args.opclass not in model["operation_classes"]:
        raise ControlError(f"--opclass 非法: {args.opclass!r}", exit_code=2,
                           allowed=sorted(model["operation_classes"]))
    class_cfg = model["operation_classes"][args.opclass]
    if class_cfg.get("requires_idempotency") and not args.request:
        raise ControlError(
            f"{args.opclass} 长动作必须提供 --request 幂等键", exit_code=1,
            gate_id="operation_idempotency")
    attempt = args.attempt or derived_attempt("operation", args.request)
    open_same_name = []
    for event in events:
        if event.get("event_type") != "operation_started":
            continue
        payload = event.get("payload") or {}
        prior_attempt = payload.get("attempt")
        if payload.get("execution_model_version") == 1 \
                and payload.get("name") == args.name \
                and operation_finished(events, prior_attempt) is None:
            open_same_name.append(prior_attempt)
    payload = {
        "execution_model_version": model["schema_version"],
        "name": args.name,
        "class": args.opclass,
        "attempt": attempt,
        "input_digest": canonical_digest(args.input or ""),
        "idempotency_provided": bool(args.request),
    }
    try:
        operation_input = strict_json_loads(args.input or "{}")
        if isinstance(operation_input, dict) and isinstance(operation_input.get("action"), str):
            payload["stage_action"] = operation_input["action"]
    except (ValueError, TypeError):
        pass
    prior = find_idempotent_event(events, args.request, "operation_started", payload)
    if prior:
        if args.opclass != "read_only":
            require_stage_action(out_dir, payload.get("stage_action", "operation"),
                expected_generation=(prior.get("payload") or {}).get("stage_generation"), **access)
        _operation_output({"ok": True, "already_applied": True,
                          "name": args.name, "attempt": attempt,
                          "event_id": prior["event_id"]}, _result_sink)
        return 0
    if any((event.get("payload") or {}).get("attempt") == attempt for event in events):
        raise ControlError(f"attempt={attempt!r} 已被使用", exit_code=1,
                           gate_id="attempt_conflict")
    if open_same_name and args.opclass != "read_only":
        action = class_cfg["ambiguous_start_action"]
        raise ControlError(
            "同名有副作用长动作存在无终结回执，禁止盲目重放", exit_code=1,
            gate_id="ambiguous_side_effect", operation=args.name,
            open_attempts=open_same_name, action=action,
            hint="先核对远端/设备/文件写后状态，再用原 attempt finish 记录真实结果。")
    event = _append_operation_event(out_dir, meta, "operation_started", payload, args.request, access)
    _operation_output({"ok": True, "phase": "start", "name": args.name,
                      "class": args.opclass, "attempt": attempt,
                      "event_id": event["event_id"]}, _result_sink)
    return 0


def cmd_operation(args, *, git_provider=None, require_git_provider=False, _result_sink=None):
    if _result_sink is not None and not callable(_result_sink):
        raise ControlError("operation result sink must be callable", gate_id="operation_receipt")
    out_dir = Path(args.dir).resolve()
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if args.phase == "start" and args.opclass != "read_only":
        try:
            declared = strict_json_loads(args.input or "{}")
        except (ValueError, TypeError):
            declared = {}
        require_stage_action(out_dir, declared.get("action", "operation") if isinstance(declared, dict) else "operation", **access)
    model = load_execution_model()
    problems = validate_execution_catalog(model)
    if problems:
        raise ControlError("execution_model 机器契约无效", exit_code=1,
                           gate_id="execution_model_catalog", violations=problems)
    with DirLock(out_dir):
        if access:
            _candidate_context_guard(out_dir, load_metadata(out_dir), git_provider, require_git_provider)
            if (out_dir / TXN_NAME).exists():
                raise ControlError("provider transaction recovery is not enabled", gate_id="candidate_transaction_pending")
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, chain_problems = verify_event_chain(out_dir, meta, **access)
        if chain_problems:
            raise ControlError("事件链不完整，拒绝记录长动作", exit_code=1,
                               gate_id="event_chain", violations=chain_problems)
        if args.phase == "start":
            return _operation_start_locked(args, out_dir, meta, events, model, access, _result_sink=_result_sink)

        if not args.attempt:
            raise ControlError("operation --phase finish 必须提供 --attempt", exit_code=2)
        start = find_operation_start(events, args.attempt)
        start_payload = start.get("payload") or {}
        finished = operation_finished(events, args.attempt)
        if finished:
            finish_payload = finished.get("payload") or {}
            if args.request and finished.get("request_id") == args.request \
                    and finish_payload.get("outcome") == args.outcome \
                    and finish_payload.get("failure") == args.failure \
                    and finish_payload.get("evidence") == args.evidence \
                    and finish_payload.get("after_check") == args.check:
                _operation_output({"ok": True, "already_applied": True,
                                  "name": finish_payload.get("name"),
                                  "attempt": args.attempt,
                                  "outcome": finish_payload.get("outcome"),
                                  "event_id": finished["event_id"]}, _result_sink)
                return 0
            if args.request and finished.get("request_id") == args.request:
                raise ControlError(
                    "相同 request 的 operation finish payload 不一致", exit_code=1,
                    gate_id="idempotency_conflict", existing=finish_payload,
                    attempted={"outcome": args.outcome, "failure": args.failure,
                               "evidence": args.evidence, "after_check": args.check})
            raise ControlError("operation attempt 已终结，拒绝第二个结果", exit_code=1,
                               gate_id="operation_attempt_finished")
        if args.outcome not in model["step_outcomes"]:
            raise ControlError("finish 必须提供合法 --outcome", exit_code=2,
                               allowed=model["step_outcomes"])
        if not args.evidence or not args.check:
            raise ControlError("长动作 finish 必须提供 --evidence 与 --check", exit_code=2,
                               gate_id="operation_receipt")
        if len(args.evidence) > 2048 or len(args.check) > 2048:
            raise ControlError("长动作回执只接受简短证据引用（每项 <= 2048 字符）", exit_code=2)
        if args.outcome == "success":
            if args.failure:
                raise ControlError("success 回执禁止携带 --failure", exit_code=2)
            decision = {"action": "complete", "reason": "operation_succeeded",
                        "conclusion_ceiling": "verified", "auto_retry": False}
        else:
            if not args.failure:
                raise ControlError("非 success 回执必须分类 --failure", exit_code=2)
            prior_attempts = 0
            for event in events:
                finish0 = event.get("payload") or {}
                if event.get("event_type") != "operation_finished" \
                        or finish0.get("name") != start_payload.get("name") \
                        or finish0.get("class") != start_payload.get("class") \
                        or finish0.get("failure") != args.failure:
                    continue
                prior_start = next((candidate for candidate in events
                                    if candidate.get("event_type") == "operation_started"
                                    and (candidate.get("payload") or {}).get("attempt")
                                    == finish0.get("attempt")), None)
                if prior_start and (prior_start.get("payload") or {}).get("input_digest") \
                        == start_payload.get("input_digest"):
                    prior_attempts += 1
            decision = evaluate_execution_policy(
                model, start_payload.get("class"), args.failure, prior_attempts + 1)
        payload = {
            "execution_model_version": model["schema_version"],
            "name": start_payload.get("name"),
            "class": start_payload.get("class"),
            "attempt": args.attempt,
            "outcome": args.outcome,
            "failure": args.failure,
            "duration_ms": elapsed_ms(start.get("timestamp")),
            "evidence": args.evidence,
            "after_check": args.check,
            "decision": decision,
        }
        prior = find_idempotent_event(
            events, args.request, "operation_finished", payload,
            payload_keys=["name", "class", "attempt", "outcome", "failure",
                          "evidence", "after_check", "decision"])
        event = prior or _append_operation_event(out_dir, meta, "operation_finished", payload, args.request, access)
    _operation_output({"ok": True, "phase": "finish", "name": payload["name"],
                      "class": payload["class"], "attempt": args.attempt,
                      "outcome": args.outcome, "duration_ms": payload["duration_ms"],
                      "decision": decision, "event_id": event["event_id"],
                      "already_applied": bool(prior)}, _result_sink)
    return 0


def execution_trace(events, limit=None):
    tracked = {"step_started", "gate_checked", "artifact_written", "step_finished",
               "operation_started", "operation_finished", "state_changed",
               "agent_spawned", "agent_result"}
    records = []
    open_steps = {}
    open_operations = {}
    open_agents = {}
    for event in events:
        if event.get("event_type") not in tracked:
            continue
        payload = event.get("payload") or {}
        record = {"at": event.get("timestamp"), "type": event.get("event_type"),
                  "event_id": event.get("event_id"), "request_id": event.get("request_id")}
        for key in ("step", "attempt", "boundary", "result", "route", "outcome",
                    "name", "class", "duration_ms", "path", "sha256", "from", "to",
                    "spawn_id", "task_scope", "backend", "model", "adopted",
                    "error_class", "exclusive_key"):
            if key in payload:
                record[key] = payload[key]
        records.append(record)
        attempt = payload.get("attempt")
        if event.get("event_type") == "step_started":
            open_steps[attempt] = {"step": payload.get("step"), "started_at": event.get("timestamp")}
        elif event.get("event_type") == "step_finished":
            open_steps.pop(attempt, None)
        elif event.get("event_type") == "operation_started":
            open_operations[attempt] = {"name": payload.get("name"),
                                        "class": payload.get("class"),
                                        "started_at": event.get("timestamp")}
        elif event.get("event_type") == "operation_finished":
            open_operations.pop(attempt, None)
        elif event.get("event_type") == "agent_spawned":
            spawn_id = payload.get("spawn_id")
            open_agents[spawn_id] = {
                "task_scope": payload.get("task_scope"),
                "backend": payload.get("backend"),
                "model": payload.get("model"),
                "started_at": event.get("timestamp"),
            }
        elif event.get("event_type") == "agent_result":
            open_agents.pop(payload.get("spawn_id"), None)
    if limit is not None:
        records = records[-limit:]
    return {"events": records, "open_steps": open_steps,
            "open_operations": open_operations, "open_agents": open_agents}


def cmd_trace(args):
    out_dir = Path(args.dir).resolve()
    if args.limit <= 0:
        raise ControlError("trace --limit 必须大于 0", exit_code=2)
    meta = load_metadata(out_dir)
    require_vnext(meta, out_dir)
    require_valid_metadata(meta)
    events, problems = verify_event_chain(out_dir, meta)
    if problems:
        raise ControlError("事件链不完整，拒绝生成可信轨迹", exit_code=1,
                           gate_id="event_chain", violations=problems)
    trace = execution_trace(events, args.limit)
    print(json.dumps({"ok": True, "ticket_id": meta.get("ticket_id"),
                      "status": meta.get("status"), "event_count": len(events),
                      "agent_spawns": _agent_spawns(meta),
                      **trace}, ensure_ascii=False, indent=2))
    return 0


def workflow_capability_preflight(model, policy, required_steps, *, protected=False,
                                  inspection_provider_factory=None):
    """Describe dispatch support, separately from authorization/runtime proof.

    Reuse the protected inspection admission set. A missing backend is an
    implementation limit: neither another confirmation nor raw Git can fix it.
    This read-only projection never activates an inspection transport.
    """
    known = set(model["step_contracts"]) | set(policy["actions"])
    required = list(dict.fromkeys(required_steps))
    unknown = sorted(set(required) - known)
    if unknown:
        raise ControlError("未知能力预检步骤", exit_code=2,
                           gate_id="workflow_capability_contract", unknown_steps=unknown)
    steps = {}
    for name in sorted(known):
        supported = not protected or name in policy.get("read_only_actions", []) or (
            name in PROTECTED_INSPECTION_STEPS and callable(inspection_provider_factory))
        reason = None if supported else (
            "inspection_provider_required" if name in PROTECTED_INSPECTION_STEPS
            else "provider_propagation_pending")
        steps[name] = {"supported": supported, "reason": reason}
    unsupported = [name for name in required if not steps[name]["supported"]]
    return {"schema_version": 1,
            "profile": "protected_provider" if protected else "local",
            "boundary": "control_plane_dispatch", "runtime_verified": False,
            "steps": steps, "required_steps": required, "unsupported_steps": unsupported,
            "task_supported": not unsupported if required else None,
            "recovery": {"automatic_fallback": False, "requires_user_confirmation": False,
                         "action": "complete_provider_propagation" if protected else None,
                         "message": "后端能力缺失时保留当前工单，先补齐受保护调用链；不改用裸 Git，也不重复索取授权。"
                                    if protected else None}}


def cmd_action_policy(args, *, git_provider=None, require_git_provider=False,
                      inspection_provider_factory=None):
    """生成轻量、一致的 UI/Agent 动作投影，并用 revision 防止过期执行。"""
    if getattr(args, "verify_action", None) and args.action != "verify":
        raise ControlError("--verify-action 只能与 --action verify 一起使用", exit_code=2)
    out_dir = Path(args.dir).resolve()
    access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
              if _candidate_access_requested(git_provider, require_git_provider) else {})
    if inspection_provider_factory is not None and not access:
        raise ControlError("能力预检缺少配对的受保护候选上下文", gate_id="candidate_provider_context")
    deadline = time.monotonic() + candidate_module().DEFAULT_LIMITS["timeout_seconds"] if access else None
    sm = load_state_machine(load_metadata(out_dir))
    policy = sm.get("action_policy")
    if not isinstance(policy, dict) or policy.get("schema_version") != 1 \
            or not isinstance(policy.get("actions"), dict):
        raise ControlError(
            "gates.json action_policy 机器契约无效",
            exit_code=1, gate_id="action_policy_catalog")

    meta = load_metadata(out_dir)
    require_vnext(meta, out_dir)
    require_valid_metadata(meta)
    if access:
        _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        try:
            candidate_module()._candidate_provider(execution_workspace(out_dir, meta), git_provider,
                require_git_provider, deadline=deadline)
        except (candidate_module().CandidateError, OSError, UnicodeError) as exc:
            raise ControlError(str(exc), gate_id="candidate_provider_context") from exc
    events, problems = verify_event_chain(out_dir, meta, **access)
    if problems:
        raise ControlError(
            "事件链不完整，拒绝生成动作策略",
            exit_code=1, gate_id="event_chain", violations=problems)
    model = load_execution_model(load_metadata(out_dir))
    required_steps = list(getattr(args, "required_step", None) or [])
    if args.action in policy["actions"]:
        required_steps.append(args.action)
    capabilities = workflow_capability_preflight(
        model, policy, required_steps, protected=bool(access) or inspection_provider_factory is not None,
        inspection_provider_factory=inspection_provider_factory)
    if capabilities["task_supported"] is False:
        raise ControlError("当前后端尚不支持请求的完整步骤组合", gate_id="workflow_capability",
                           capability_preflight=capabilities)
    execution = execution_trace(events)
    stage = stage_projection(out_dir, **access)
    requested_verify_variant = getattr(args, "verify_action", None) or "intent"
    if requested_verify_variant == "default":
        requested_verify_variant = "intent"
    if args.action == "verify":
        require_stage_action(out_dir, "plan" if requested_verify_variant in {"intent", "plan"} else requested_verify_variant, **access)
    revision = workflow_revision(meta, events, stage)

    if args.expected_revision and args.expected_revision != revision["token"]:
        raise ControlError(
            "工单状态已变化，拒绝基于过期 revision 执行动作",
            exit_code=1, gate_id="revision_mismatch",
            expected_revision=args.expected_revision,
            actual_revision=revision["token"])

    status = meta.get("status")
    is_debug = bool(meta.get("debug"))
    actions = policy["actions"]
    read_only = set(policy.get("read_only_actions") or [])
    allowed = []
    for name, contract in actions.items():
        if not isinstance(contract, dict):
            continue
        statuses = set(contract.get("statuses", []))
        for variant in contract.get("variants", {}).values():
            statuses.update(variant.get("statuses", []))
        if status not in statuses:
            continue
        debug_mode = contract.get("debug")
        if is_debug and debug_mode not in {"debug", "both"}:
            continue
        if not is_debug and debug_mode not in {"main", "both"}:
            continue
        allowed.append(name)

    blocked_reason = None
    if meta.get("close_state") is not None:
        allowed = [name for name in allowed if name in read_only]
        blocked_reason = "closed_ticket"
    elif execution["open_steps"] or execution["open_operations"] \
            or execution["open_agents"]:
        allowed = [name for name in allowed if name in read_only]
        blocked_reason = "open_execution"
    if stage["enabled"]:
        if stage["state"] == "superseded":
            allowed = [name for name in allowed if name in read_only]
            blocked_reason = "superseded_stage"
        elif stage["blocked"] or not stage.get("stage_id") or stage["state"] == "accepted":
            allowed = [name for name in allowed if name in read_only or name == "verify"]
            blocked_reason = "stage_gate"
        else:
            allowed = [name for name in allowed if name in read_only or name == "verify" or name in stage["available_actions"]
                       or name in {"deepcheck", "audit", "plan", "init", "log"}]
            if (meta.get("candidate_tracking") or {}).get("mode") != "enabled":
                allowed = [name for name in allowed if name not in {"code", "patch", "fast"}]
    return_transition = legal_transition(sm, status, "review_in_progress")
    return_required = bool(return_transition and (return_transition.get("review_return") or status == "completed"))
    if return_required and review_return_problem(meta, events, stage):
        allowed = [name for name in allowed if name != "review"]
    # State permission alone must not recommend a step whose backend is absent.
    executable = sorted(name for name in allowed if capabilities["steps"][name]["supported"])
    if len(executable) != len(allowed) and blocked_reason is None:
        blocked_reason = "workflow_capability"
    allowed = executable
    recommended = (policy.get("recommended_by_status") or {}).get(status)
    if recommended not in allowed:
        recommended = None

    if args.action:
        if args.action not in actions:
            raise ControlError(
                f"未知 action {args.action!r}", exit_code=2,
                gate_id="action_policy_catalog", allowed=sorted(actions))
        if args.action not in allowed:
            raise ControlError(
                f"动作 {args.action!r} 在当前工单状态不可执行",
                exit_code=1, gate_id="action_policy", status=status,
                blocked_reason=blocked_reason, allowed_actions=allowed)
        if args.action == "verify":
            variant = getattr(args, "verify_action", None) or "intent"
            if variant == "default":
                variant = "intent"  # 旧内部调用只解释意图，绝不默认部署。
            contract = actions["verify"].get("variants", {}).get(variant, actions["verify"])
            if status not in contract["statuses"]:
                raise ControlError(f"verify {variant} 在当前状态不可执行；分析阶段须先确定合法动作",
                                   gate_id="action_policy", status=status, verify_action=variant)
        model = load_execution_model(load_metadata(out_dir))
        if args.action in model.get("step_contracts", {}):
            _snapshot, missing = capture_step_inputs(
                out_dir, meta, model, args.action, **access)
            if missing:
                raise ControlError(
                    f"动作 {args.action!r} 缺少必填输入",
                    exit_code=1, gate_id="step_ports", missing=missing)

    result = {
        "ok": True,
        "schema_version": 1,
        "ticket_id": meta.get("ticket_id"),
        "status": status,
        "debug": is_debug,
        "close_state": meta.get("close_state"),
        "allowed_actions": allowed,
        "recommended_action": recommended,
        "blocked_reason": blocked_reason,
        "revision": revision,
        "review_return_required": return_required,
        "execution_root": str(trusted_execution_workspace(out_dir, meta)),
        "open_steps": execution["open_steps"],
        "open_operations": execution["open_operations"],
        "open_agents": execution["open_agents"],
        "stage": stage,
        "capability_preflight": capabilities,
    }
    if args.action:
        result.update({"action": args.action, "action_allowed": True})
        if args.action == "verify":
            result.update(verify_action=variant, side_effect=contract["side_effect"])
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if access:
        # Check after real reads and serialization, not a host epoch/rollback proof.
        _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
        try:
            candidate_module()._candidate_provider(execution_workspace(out_dir, meta), git_provider,
                require_git_provider, deadline=deadline)
        except (candidate_module().CandidateError, OSError, UnicodeError) as exc:
            raise ControlError(str(exc), gate_id="candidate_provider_context") from exc
    print(encoded)
    return 0


def cmd_snapshot(args):
    out_dir = Path(args.dir).resolve()
    sm = load_state_machine(load_metadata(out_dir))
    snap_path = Path(out_dir) / SNAPSHOT_NAME
    view = stage_projection(out_dir) if not args.verify else {"enabled": False}
    with _stage_writer_locks(out_dir, view):
        if not args.verify:
            recover_pending_transaction(out_dir)
        elif (out_dir / TXN_NAME).exists():
            raise ControlError("snapshot 校验时发现未完成事务，拒绝自动修改",
                               exit_code=1, gate_id="pending_transaction")
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        token = _stage_retirement_guard(out_dir) if not args.verify else None
        if not args.verify and meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止生成会改写事件链的新快照；"
                "仍可用 snapshot --verify 只读校验",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, chain_problems = verify_event_chain(out_dir, meta)
        if chain_problems:
            raise ControlError("事件链不完整，snapshot 拒绝写入", exit_code=1,
                               gate_id="event_chain", violations=chain_problems)
        last_hash = events[-1]["event_hash"] if events else GENESIS_HASH
        if args.verify:
            problems = []
            if not snap_path.is_file():
                raise ControlError(f"snapshot 不存在: {snap_path}", exit_code=1)
            snap = load_json(snap_path, "ticket_snapshot.json")
            if snap.get("status") != meta.get("status"):
                problems.append(f"snapshot.status={snap.get('status')!r} 与 metadata.status="
                                f"{meta.get('status')!r} 不一致（snapshot 过期）")
            if snap.get("last_event_hash") != last_hash:
                problems.append("snapshot.last_event_hash 与事件链尾不一致（snapshot 过期或事件被篡改）")
            if snap.get("event_count") != len(events):
                problems.append("snapshot.event_count 与当前事件数不一致（snapshot 过期）")
            if snap.get("metadata_hash") != metadata_hash(meta):
                problems.append("snapshot.metadata_hash 与当前 metadata 不一致（snapshot 过期）")
            result = {"ok": not problems, "verify": True, "out_dir": str(out_dir),
                      "problems": problems}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1

        # 先构造但不落事件；快照写成功后才追加审计事实。
        event = prepare_event(out_dir, meta, "snapshot_written", {"path": SNAPSHOT_NAME})
        snapshot = {
            "schema_version": 1,
            "ticket_id": meta.get("ticket_id"),
            "generated_at": now_iso(),
            "status": meta.get("status"),
            "close_state": meta.get("close_state"),
            "delivery_verdict": meta.get("delivery_verdict"),
            "verdict": meta.get("verdict"),
            "mode": meta.get("mode"),
            "debug": meta.get("debug", False),
            "next_legal_transitions": next_transitions(sm, meta.get("status")),
            "unmet_gates_note": "门禁明细运行 validate 子命令获取。",
            "baselines": {
                "runtime": len(meta.get("runtime_code_baselines") or []),
                "analysis": len(meta.get("analysis_code_baselines") or []),
                "verification": len(meta.get("verification_code_baselines") or []),
            },
            "open_items": {
                "pending_verification": meta.get("pending_verification") or [],
                "unresolved_issues_at_cap": meta.get("unresolved_issues_at_cap"),
            },
            "execution": execution_trace(events, 10),
            "event_count": len(events) + 1,
            "last_event_hash": event["event_hash"],
            "metadata_hash": metadata_hash(meta),
        }
        previous_snapshot = load_json(snap_path, "旧 ticket_snapshot.json") \
            if snap_path.exists() else None
        atomic_write_json(snap_path, snapshot)
        try:
            _stage_publication_check(token)
            append_prepared_event(out_dir, event)
        except BaseException:
            event_presence = None
            try:
                current_events, _ = read_events(out_dir)
                event_presence = any(
                    item.get("event_hash") == event["event_hash"] for item in current_events)
            except BaseException:
                # 无法判定事件是否已落盘时不做破坏性回滚。
                pass
            if event_presence is False:
                if previous_snapshot is None:
                    snap_path.unlink(missing_ok=True)
                else:
                    atomic_write_json(snap_path, previous_snapshot)
                raise
            if event_presence is None:
                raise
            # append 已生效但 fsync/返回路径报错时，保留与事件一致的快照并视为成功。
    print(json.dumps({"ok": True, "path": str(snap_path), "status": snapshot["status"],
                      "next_legal_transitions": snapshot["next_legal_transitions"]},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(prog="icode_control.py",
                                 description="icode 工单控制面（vNext schema v3）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("lite-report", help="从单源合同及状态派生 lite.md")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_lite_report)

    p = sub.add_parser("lite-preflight", help="实施前确认宿主适配器已由宿主配置并锁定摘要")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_lite_preflight)

    p = sub.add_parser("lite-review", help="登记宿主独立子代理审核并返回只读输入引用")
    p.add_argument("--dir", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--request-id", required=True)
    p.set_defaults(func=cmd_lite_review)

    p = sub.add_parser("stage", help="受控顺序阶段计划、绑定、AI报告与验收投影")
    p.add_argument("--dir", required=True)
    p.add_argument("--phase", required=True, choices=["register", "bind", "review", "check", "confirm", "reject", "revoke", "preview-revision"])
    p.add_argument("--plan-json")
    p.add_argument("--consolidation-json", help="Explicit complete unaccepted-tail mapping; preview only")
    p.add_argument("--stage-id")
    p.add_argument("--ticket-dir")
    p.add_argument("--request-id")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("resolve-ticket", help="工单身份解析（--dir/--ticket/--latest，可按状态/产物过滤）")
    selector = p.add_mutually_exclusive_group(required=True)
    selector.add_argument("--dir")
    selector.add_argument("--ticket")
    selector.add_argument("--latest", action="store_true")
    p.add_argument("--workspace", help="工程根（--ticket/--latest 扫描范围）")
    p.add_argument("--require-status", action="append", default=[],
                   help="只接受指定 status；可重复，多个值按任一匹配")
    p.add_argument("--require-artifact", action="append", default=[],
                   help="只接受含指定工单内相对路径的目录；可重复，多个产物须全部存在")
    p.add_argument("--require-init-ready", action="store_true",
                   help="只接受结构完整且各必需章节有实质内容的 init 分析源")
    p.set_defaults(func=cmd_resolve_ticket)

    p = sub.add_parser("create", help="原子创建 vNext metadata + ticket_created 出生事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--ticket-id", required=True)
    p.add_argument("--requirement", help="原始需求文本；与 --requirement-file 二选一")
    p.add_argument("--requirement-file", help="UTF-8 需求文件；Windows 非 ASCII argv 的可靠入口")
    p.add_argument("--birth", required=True,
                   choices=["init", "plan", "log", "debug-init", "debug-log", "lite"])
    p.add_argument("--metadata-json", help="额外 metadata 字段 JSON 对象；受保护出生字段由工具覆盖")
    p.add_argument("--candidate-limits-json", help="Git候选扫描预算JSON；超预算fail-closed，不降级")
    p.add_argument("--exclude", action="append", default=None, help="用户明确排除且未参与本轮实现/构建的相对路径；写入出生候选合同")
    p.add_argument("--stage-id", help="已启用阶段项目必须显式指定计划内 stage id")
    p.add_argument("--request-id", help="创建幂等键")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("create-next", help="为 UI 原子分配编号、出生并写入索引")
    p.add_argument("--workspace", required=True, help="服务端解析后的可信工程根")
    p.add_argument("--requirement", required=True)
    p.add_argument("--request-id", required=True, help="UI 创建工单幂等键")
    p.add_argument("--index", help="索引路径覆盖（测试用）")
    p.set_defaults(func=cmd_create_next)

    p = sub.add_parser("candidate", help="候选绑定/只读交付新鲜度检查；历史工单须显式capture启用")
    p.add_argument("--dir", required=True)
    p.add_argument("--phase", required=True, choices=["capture", "inspect"])
    p.add_argument("--base", help="明确Git完整commit oid基线；省略沿用候选基线")
    p.add_argument("--exclude", action="append", default=None, help="用户明确排除的相对文件或目录")
    p.add_argument("--request-id", help="capture 必需幂等键")
    p.add_argument("--limits-json", help="显式扫描预算JSON；持久化并供后续只读检查复用，不改变源码身份")
    p.add_argument("--require-step", action="append", default=[], choices=["code", "deepcheck", "audit", "lite_review"],
                   help="inspect 作为验收检查时所需当前候选真实成功回执")
    p.set_defaults(func=cmd_candidate)

    p = sub.add_parser("check-outputs", help="只读检查步骤产物与条件审查证据")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True, choices=["review", "code", "deepcheck", "audit", "lite_review"])
    p.set_defaults(func=cmd_check_outputs)

    p = sub.add_parser("validate", help="工单整体校验（fail-closed 报告）")
    p.add_argument("--dir", required=True)
    p.add_argument("--index", help="索引路径覆盖（默认 ~/.claude/icode_data/index.json；测试/离线审计用）")
    p.add_argument("--skip-linters", action="store_true",
                   help="仅限 ICODE_CONTROL_TEST_MODE=1 夹具")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("event", help="追加事件（哈希链）")
    p.add_argument("--dir", required=True)
    p.add_argument("--type", required=True, help="事件类型（见 schemas/ticket-event.schema.json）")
    p.add_argument("--payload", help="JSON 对象字符串")
    p.add_argument("--request-id", help="幂等键")
    p.add_argument("--actor", default="icode", choices=["icode", "user", "watch", "system"])
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("step", help="步骤端口快照、Reactive 边界复检与终结回执")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--phase", required=True, choices=["start", "check", "finish"])
    p.add_argument("--boundary", help="check 边界（真源见 gates.json execution_model.boundaries）")
    p.add_argument("--attempt", help="执行尝试 ID；start 可省略并自动生成")
    p.add_argument("--outcome", help="finish 结果")
    p.add_argument("--evidence", action="append", default=[], help="finish 的简短证据引用，可重复")
    p.add_argument("--request", help="本条执行事件的幂等键")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("artifact", help="记录与 step outputs 合同匹配的产物 hash（单条或批量）")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--path", help="单个产物路径；与 --paths-json 互斥")
    p.add_argument("--paths-json",
                   help="仅批量：1至256个规范相对POSIX路径的JSON数组，最多64KiB；按输入顺序逐条登记")
    p.add_argument("--scope", default="ticket", choices=["ticket", "workspace", "external"])
    p.add_argument("--request", help="幂等键；批量时每个成员使用 request#序号")
    p.set_defaults(func=cmd_artifact)

    p = sub.add_parser("closeout", help="步骤机械收尾：产物登记 + before_transition 复检 + finish（可选 transition）")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--path", help="单个产物路径；与 --paths-json 互斥")
    p.add_argument("--paths-json", help="批量产物：1至256个规范相对POSIX路径的JSON数组，最多64KiB")
    p.add_argument("--scope", default="ticket", choices=["ticket", "workspace", "external"])
    p.add_argument("--outcome", required=True, help="finish 结果")
    p.add_argument("--evidence", action="append", required=True, help="finish 的简短证据引用，可重复")
    p.add_argument("--transition-to", help="可选：finish 成功后继续流转到该状态")
    p.add_argument("--delivery-verdict", choices=DELIVERY_VERDICTS,
                   help="仅 --transition-to completed 时使用")
    p.add_argument("--request", help="幂等键前缀；各阶段派生 <prefix>.artifact/.check/.finish/.transition")
    p.set_defaults(func=cmd_closeout)

    p = sub.add_parser("compact", help="存量工单物理减重：legacy 内联 metadata → storage v2（默认 dry-run）")
    p.add_argument("--dir", required=True)
    p.add_argument("--apply", action="store_true", help="真实写入；缺省只报告压缩后大小")
    p.add_argument("--force", action="store_true",
                   help="事件账本不小于 metadata 时仍写入（已实测确认收益后再用）")
    p.set_defaults(func=cmd_compact)

    p = sub.add_parser("inspection", help="原生自查清单：范围/关联单元/专项规则/源码定位（不调用模型）")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True, choices=["code", "deepcheck", "audit"])
    p.add_argument("--attempt", required=True)
    p.add_argument("--phase", required=True, choices=["prepare", "read", "assess", "check"])
    p.add_argument("--scope", action="append", default=None, help="有证据的项目内范围，重复增补")
    p.add_argument("--related", action="append", default=None, help="调用方/协议另一端/测试等关联路径")
    p.add_argument("--baselines-json", help="受影响仓库相对根到真实Git基线的映射")
    p.add_argument("--max-files", type=int, default=None, help="仅 prepare：显式文件枚举预算；省略保持原默认，同 attempt 冻结")
    p.add_argument("--read-phase", "--assessment-phase", dest="read_phase", help="真实Read/专项结论对应的必需阶段")
    p.add_argument("--path", help="read登记的项目相对文件")
    p.add_argument("--paths-json", help="仅read：1至256个唯一相对路径的JSON数组；与--path互斥，最多64KiB")
    p.add_argument("--check-id", help="assess 的清单派生专项检查 ID")
    p.add_argument("--assessment-json", help="assess 本阶段全部 required_items 的 cells、理由和真实证据")
    p.add_argument("--allow-incomplete", action="store_true", help="check明确债务，仍不允许身份/源码漂移")
    p.add_argument("--request", help="artifact回执幂等键")
    p.set_defaults(func=cmd_inspection)

    p = sub.add_parser("operation", help="长动作开始/终结回执（禁止盲目重放副作用）")
    p.add_argument("--dir", required=True)
    p.add_argument("--phase", required=True, choices=["start", "finish"])
    p.add_argument("--name", help="start 时的稳定动作名")
    p.add_argument("--opclass", choices=["read_only", "managed_write", "external_side_effect",
                                         "destructive_hardware"])
    p.add_argument("--attempt", help="动作尝试 ID；start 可省略并自动生成")
    p.add_argument("--input", help="输入身份描述；仅保存其 sha256")
    p.add_argument("--outcome", help="finish 结果")
    p.add_argument("--failure", help="非 success 的失败分类")
    p.add_argument("--evidence", help="finish 的简短证据引用")
    p.add_argument("--check", help="finish 的写后/远端/设备核对结果")
    p.add_argument("--request", help="本条动作事件幂等键；有副作用动作必填")
    p.set_defaults(func=cmd_operation)

    p = sub.add_parser("policy", help="查询 side-effect-aware Retry/Fallback 决策")
    p.add_argument("--opclass", required=True)
    p.add_argument("--failure", required=True)
    p.add_argument("--attempts", type=int, default=0)
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("trace", help="只读输出统一 step/gate/operation/state 轨迹")
    p.add_argument("--dir", required=True)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser("action-policy",
                       help="只读生成允许动作、可信执行根与 revision")
    p.add_argument("--dir", required=True)
    p.add_argument("--action", help="可选：启动前复检指定动作")
    p.add_argument("--required-step", action="append", default=[],
                   help="可重复：自动路由请求包含的全部步骤，提前检查后端支持；不执行步骤")
    p.add_argument("--verify-action", choices=["default", "intent", "build", "plan", "deploy", "listen", "device_test"],
                   help="verify 子动作；无选项仅解释意图，实际执行前须按解析后的动作复检")
    p.add_argument("--expected-revision",
                   help="可选：UI 刷新时取得的 revision token")
    p.set_defaults(func=cmd_action_policy)

    p = sub.add_parser("transition", help="状态流转（fail-closed + 门禁 linter + 事件）")
    p.add_argument("--dir", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--delivery-verdict", choices=DELIVERY_VERDICTS)
    p.add_argument("--request-id", help="幂等键（同键同目标重放=已应用摘要）")
    p.add_argument("--expected-revision", help="受控返回审查时绑定 action-policy 的当前 revision")
    p.add_argument("--reason", help="受控返回审查的明确原因；保留到原生事件")
    p.add_argument("--skip-gates", action="store_true",
                   help="仅限测试夹具；生产流程禁止跳门禁")
    p.set_defaults(func=cmd_transition)

    p = sub.add_parser("bind-execution-root", help="一次性绑定可信宿主的隔离代码根")
    p.add_argument("--dir", required=True)
    p.add_argument("--ticket-id", required=True)
    p.add_argument("--execution-root", required=True)
    p.add_argument("--request-id", required=True)
    p.set_defaults(func=cmd_bind_execution_root)

    p = sub.add_parser("metadata-update", help="原子更新已登记业务字段并追加 metadata_updated 事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--set-json", help="覆盖字段的 JSON 对象")
    p.add_argument("--set-json-file", help="UTF-8 JSON 文件；Windows 非 ASCII argv 的可靠入口")
    p.add_argument("--append-json", help="追加数组字段的 JSON 对象；每个值必须是元素数组")
    p.add_argument("--append-json-file", help="UTF-8 JSON 文件；与 --append-json 二选一")
    p.add_argument("--request-id", help="幂等键")
    p.add_argument("--actor", default="icode", choices=["icode", "user", "watch", "system"])
    p.set_defaults(func=cmd_metadata_update)

    p = sub.add_parser("index-write", help="全局索引单一 writer（合并+校验+原子写+写后验证）")
    p.add_argument("--ticket-dir", required=True)
    p.add_argument("--index", help="索引路径覆盖（默认 ~/.claude/icode_data/index.json；测试用）")
    p.set_defaults(func=cmd_index_write)

    p = sub.add_parser("index-update", help="更新索引独有字段（检索命中/stale/LRU）")
    p.add_argument("--ticket-id", required=True)
    p.add_argument("--set-json", help="要合并的条目字段 JSON（禁改身份三元组）")
    p.add_argument("--increment-hit", action="store_true")
    p.add_argument("--index", help="索引路径覆盖（测试用）")
    p.set_defaults(func=cmd_index_update)

    p = sub.add_parser("migration", help="legacy → vNext 迁移（dry-run 报告 / --apply 幂等）")
    p.add_argument("--dir", required=True)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_migration)

    p = sub.add_parser("build-start", help="构建前真实观察目录与候选；不执行、不清空构建目录")
    p.add_argument("--dir", required=True)
    p.add_argument("--build-dir", required=True)
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--parameters-json", required=True)
    p.add_argument("--request-id", required=True)
    p.set_defaults(func=cmd_build_start)

    p = sub.add_parser("record-verification",
                       help="原子记录 verification_runs + verification_recorded 事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--kind", required=True, choices=["deploy", "listen", "device_test", "build"])
    p.add_argument("--outcome", required=True, choices=["pass", "fail", "inconclusive"])
    p.add_argument("--candidate-id", help="enabled 工单显式绑定完整可核验源码候选，与 baseline_ref 独立")
    p.add_argument("--environment", choices=["host", "physical", "simulated", "unknown"])
    p.add_argument("--supersedes-run-id")
    p.add_argument("--reuse-json")
    p.add_argument("--build-provenance-json")
    p.add_argument("--deploy-provenance-json")
    p.add_argument("--runtime-provenance-json")
    p.add_argument("--direct-test-json")
    p.add_argument("--test-report", help="只读解析实际 JUnit XML，机器记录用例数和原始报告 path/SHA256；相对执行工程根")
    bundle_input = p.add_mutually_exclusive_group()
    bundle_input.add_argument("--evidence-bundle", help="证据包路径；相对工单控制目录，只读计算实际 SHA256")
    bundle_input.add_argument("--evidence-bundle-json", help="结构化证据引用 JSON {path,sha256}；写入前复核当前原始字节")
    p.add_argument("--build-source", default="unknown",
                   choices=["fresh", "reused", "existing", "unknown"])
    p.add_argument("--device")
    p.add_argument("--artifact-identity")
    p.add_argument("--window")
    p.add_argument("--layer", choices=[
        "static", "unit", "build", "host", "deploy", "delivery", "consumption",
        "settings_preview", "operation_view", "closed_client", "physical",
    ])
    p.add_argument("--consumer")
    p.add_argument("--scenario")
    p.add_argument("--profile", choices=["generic", "embedded", "camera"])
    p.add_argument(
        "--baseline-ref",
        help="与 verification_contract.baseline_ref 完全一致的 sha256:<64位小写十六进制> 摘要",
    )
    p.add_argument("--metrics-json", help="本次实测指标 JSON 对象，例如 {\"fps\":29.7}")
    p.add_argument("--baseline", help="本次验证绑定的代码/产物/设备基线")
    p.add_argument("--evidence", required=True)
    p.add_argument("--note")
    p.add_argument("--request-id", help="幂等键（同键同记录重放=已应用）")
    p.add_argument(
        "--close-repair", action="store_true",
        help="仅 close_state=close_planned 且归档前，受控补录已经取得的验证证据；必须带 --request-id",
    )
    p.set_defaults(func=cmd_record_verification)

    p = sub.add_parser("record-claim", help="原子记录 claims + claim_recorded 事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--kind", required=True,
                   choices=["fact", "inference", "unobserved", "refuted"])
    p.add_argument("--statement", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--boundary", required=True)
    p.add_argument("--evidence", action="append", default=[],
                   help="可重复；fact/refuted 至少一条")
    p.add_argument("--contradicted-by", action="append", default=[])
    p.add_argument("--next-action")
    p.add_argument("--request-id", help="幂等键（同键同记录重放=已应用）")
    p.add_argument("--actor", default="icode", choices=["icode", "user", "watch", "system"])
    p.set_defaults(func=cmd_record_claim)

    p = sub.add_parser("record-skill-run",
                       help="原子记录 extensions.skills.runs + skill_run_recorded 事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--skill", required=True)
    p.add_argument("--trigger", required=True)
    p.add_argument("--result", required=True,
                   choices=["success", "failure", "degraded", "skipped"])
    p.add_argument("--adopted", required=True, choices=["true", "false"])
    p.add_argument("--evidence-ref", action="append", default=[], required=True)
    p.add_argument("--elapsed-ms", type=int, required=True)
    p.add_argument("--estimated-tokens", type=int, required=True)
    p.add_argument("--unique-finding", action="append", default=[])
    p.add_argument("--agent-id")
    p.add_argument("--request-id", help="幂等键（同键同记录重放=已应用）")
    p.add_argument("--actor", default="icode", choices=["icode", "user", "watch", "system"])
    p.set_defaults(func=cmd_record_skill_run)

    p = sub.add_parser("record-agent-spawn",
                       help="模型/子代理调用前原子记录 extensions.agent.spawns + agent_spawned")
    p.add_argument("--dir", required=True)
    p.add_argument("--task-scope", required=True)
    p.add_argument("--expected-artifact", required=True)
    p.add_argument("--evidence-boundary", required=True)
    p.add_argument("--join-condition", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--capability", action="append", required=True,
                   choices=sorted(AGENT_CAPABILITIES))
    p.add_argument("--stage-action", help="显式宿主步骤；未声明的自由任务必须属于当前实施阶段")
    p.add_argument("--request-id", required=True, help="调用前幂等键")
    p.add_argument("--exclusive-key",
                   help="可选跨进程独占域；同工单同 key 仅允许一个开放 spawn")
    p.add_argument("--actor", default="icode", choices=["icode", "user", "watch", "system"])
    p.set_defaults(func=cmd_record_agent_spawn)

    p = sub.add_parser("record-agent-result",
                       help="原子终结 Agent spawn + agent_result；正文只记录 sha256")
    p.add_argument("--dir", required=True)
    p.add_argument("--spawn-id", required=True)
    p.add_argument("--result", required=True, choices=sorted(AGENT_RESULTS))
    p.add_argument("--adopted", required=True, choices=sorted(AGENT_ADOPTION))
    p.add_argument("--adoption-reason", required=True)
    p.add_argument("--evidence-ref", action="append", required=True)
    p.add_argument("--summary", required=True, help="仅计算 sha256，不写入事件链")
    p.add_argument("--error-class")
    p.add_argument("--host-session-id", help="Lite 审核的实际宿主子代理身份")
    p.add_argument("--lite-review-report", help="Lite 子代理返回的 JSON 文件或 -（stdin）")
    p.add_argument("--host-receipt", help="宿主启动/返回回执 JSON 文件或 -（绑定本次 spawn 的 challenge）")
    p.add_argument("--request-id", required=True, help="终结幂等键")
    p.add_argument("--actor", default="icode", choices=["icode", "user", "watch", "system"])
    p.set_defaults(func=cmd_record_agent_result)

    p = sub.add_parser("archive-manifest",
                       help="生成/校验归档清单、hash 与门禁 roundtrip")
    p.add_argument("--dir", required=True, help="源工单目录")
    p.add_argument("--archive-dir", required=True)
    p.add_argument("--write", action="store_true", help="校验通过后写 archive_manifest.json")
    p.add_argument("--skip-linters", action="store_true", help="仅限 ICODE_CONTROL_TEST_MODE=1 夹具")
    p.set_defaults(func=cmd_archive_manifest)

    p = sub.add_parser("close-phase", help="关闭分阶段状态（幂等 + 禁跨阶段）")
    p.add_argument("--dir", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--request-id")
    p.set_defaults(func=cmd_close_phase)

    p = sub.add_parser("reopen", help="在归档控制根受控解冻 closed 工单")
    p.add_argument("--dir", required=True, help="archived 交接后的 control_root")
    p.add_argument("--metadata-json", required=True,
                   help="active_checkout 及 checkout_history/sub_worktrees/submission_contracts")
    p.add_argument("--reason", required=True, help="本次恢复原因")
    p.add_argument("--request-id", help="幂等键")
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("snapshot", help="生成/校验 ticket_snapshot.json")
    p.add_argument("--dir", required=True)
    p.add_argument("--verify", action="store_true", help="校验既有 snapshot 与事件链一致性")
    p.set_defaults(func=cmd_snapshot)
    return ap


def refresh_ticket_overview(out_dir):
    """Refresh one derived Markdown at stable checkpoints, never workflow state.

    Delivery query failures remain visible but cannot undo a committed workflow
    result. The report writer owns path safety and pending-transaction checks.
    """
    path = Path(out_dir) / "delivery_validation_report.md"
    try:
        spec = importlib.util.spec_from_file_location("checkpoint_delivery", SKILL_ROOT / "tools/icode_delivery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stdout(io.StringIO()):
            code = module.main(["--dir", str(out_dir), "--markdown", str(path)])
        if code == 0:
            return {"state": "generated", "path": path.name}
        return {"state": "unavailable", "reason": "delivery_query_failed"}
    except Exception as exc:  # Derived output must not turn a committed action into a retry.
        return {"state": "unavailable", "reason": type(exc).__name__}


def main(*, git_provider=None, require_git_provider=False, inspection_provider_factory=None):
    import contextlib
    import io

    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    try:
        access = ({"git_provider": git_provider, "require_git_provider": require_git_provider}
                  if _candidate_access_requested(git_provider, require_git_provider) else {})
        if inspection_provider_factory is not None:
            access = dict(git_provider=git_provider, require_git_provider=require_git_provider,
                          inspection_provider_factory=inspection_provider_factory)
        policy_entry = args.cmd == "action-policy" and args.func is cmd_action_policy
        ordinary = policy_entry or inspection_provider_factory is None and (
            (args.cmd == "candidate" and args.func is cmd_candidate) or
            (args.cmd == "build-start" and args.func is cmd_build_start) or
            (args.cmd == "record-verification" and args.func is cmd_record_verification) or
            (args.cmd == "stage" and args.func is cmd_stage))
        code_entry = inspection_provider_factory is not None and getattr(args, "step", None) in PROTECTED_INSPECTION_STEPS and (
            (args.cmd == "step" and args.func is cmd_step and getattr(args, "phase", None) in {"start", "check", "finish"}) or
            (args.cmd == "inspection" and args.func is cmd_inspection and getattr(args, "phase", None) in {"prepare", "read", "assess", "check"}) or
            (args.cmd == "artifact" and args.func is cmd_artifact) or
            (args.cmd == "closeout" and args.func is cmd_closeout) or
            (args.cmd == "check-outputs" and args.func is cmd_check_outputs))
        if access and not (ordinary or code_entry):
            raise ControlError("B1 command dispatch is not enabled", gate_id="candidate_business_propagation_pending")
        policy_deadline = (time.monotonic() + candidate_module().DEFAULT_LIMITS["timeout_seconds"]
                           if access and (args.cmd == "action-policy" or code_entry) else None)
        # 捕获子命令输出：业务失败（ok=false）时子命令仍可能返回 0，
        # 会让 shell 的 `&&` / `set -e` 误判为成功。此处统一纠正为退出码 1。
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = args.func(args, **access) if access else args.func(args)
        out = buf.getvalue()
        if policy_deadline is not None:
            # Inner handler success is not yet acceptance by the outer caller.
            out_dir = Path(args.dir).resolve()
            meta = load_metadata(out_dir)
            _candidate_context_guard(out_dir, meta, git_provider, require_git_provider)
            try:
                candidate_module()._candidate_provider(execution_workspace(out_dir, meta), git_provider,
                    require_git_provider, deadline=policy_deadline)
            except (candidate_module().CandidateError, OSError, UnicodeError) as exc:
                raise ControlError(str(exc), gate_id="candidate_provider_context") from exc
        try:
            payload = json.loads(out)
        except (ValueError, TypeError):
            payload = None
        closeout_completed = (args.cmd == "closeout"
                              and getattr(args, "transition_to", None) == "completed")
        closeout_patch = (args.cmd == "closeout" and getattr(args, "step", None) == "patch"
                          and getattr(args, "outcome", None) in {"success", "degraded"})
        checkpoint = (closeout_completed or closeout_patch or
                      (args.cmd == "transition" and getattr(args, "to", None) == "completed") or
                      (args.cmd == "step" and getattr(args, "step", None) == "patch" and
                       getattr(args, "phase", None) == "finish" and
                       getattr(args, "outcome", None) in {"success", "degraded"}))
        if checkpoint and code == 0 and isinstance(payload, dict) and payload.get("ok") is True:
            payload["overview"] = ({"state": "deferred", "reason": "provider_context_required"} if access
                                   else refresh_ticket_overview(Path(args.dir).resolve()))
            out = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()
        if code == 0:
            if isinstance(payload, dict) and payload.get("ok") is False:
                return 1
        return code
    except ControlError as exc:
        print(json.dumps(exc.report(), ensure_ascii=False, indent=2))
        return exc.exit_code
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"文件系统操作失败: {exc}",
                          "gate_id": "filesystem_io"}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    # Native CLI stdout is a UTF-8 wire; importing/capturing main stays text-only.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
