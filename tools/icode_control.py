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
import fcntl
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
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

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
CONTROLLED_BIRTH_FIELDS = {
    "schema_version", "ticket_id", "requirement", "created_at", "status",
    "completed_steps", "indexed", "debug", "project_path", "close_state",
    "delivery_verdict", "claims", "verification_runs", "control_plane_degraded",
    "workflow_gate_schema_version", "thinking_gate_schema_version",
    "mcp_gate_schema_version",
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


def load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f, parse_constant=reject_nonfinite_json)
    except FileNotFoundError:
        raise ControlError(f"{what} 不存在: {path}", exit_code=1, path=str(path))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError(f"{what} JSON 解析失败: {path}: {exc}", exit_code=1, path=str(path))


def atomic_write_json(path, obj):
    """临时文件 + fsync + 同目录原子 rename。"""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
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
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_append_line(path, line):
    """JSONL 追加：由上层目录锁串行化，追加后 flush + fsync。"""
    path = Path(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


class DirLock:
    """工单目录级排它锁（fcntl.flock，阻塞等待，防并发写撕裂）。"""

    def __init__(self, out_dir):
        self.path = Path(out_dir) / ".icontrol.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        self.fh.close()
        return False


class FileLock:
    """任意共享文件的旁路锁；锁文件固定存在，业务文件仍可原子 rename。"""

    def __init__(self, path):
        self.path = Path(str(path) + ".lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
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


def check_schema(value, schema, path="$", errors=None):
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


# ---------------------------------------------------------------- gates.json 状态机

def load_state_machine():
    gates = load_json(GATES_JSON, "gates.json")
    sm = gates.get("state_machine")
    if not sm:
        raise ControlError(f"gates.json 缺少 state_machine 段: {GATES_JSON}")
    return sm


def load_execution_model():
    gates = load_json(GATES_JSON, "gates.json")
    model = gates.get("execution_model")
    if not isinstance(model, dict):
        raise ControlError(f"gates.json 缺少 execution_model 段: {GATES_JSON}",
                           gate_id="execution_model_catalog")
    return model


def legal_transition(sm, from_status, to_status):
    for t in sm["transitions"]:
        if t["from"] == from_status and t["to"] == to_status:
            return t
    return None


def next_transitions(sm, from_status):
    return [t["to"] for t in sm["transitions"] if t["from"] == from_status]


# ---------------------------------------------------------------- 工单定位与元数据

def load_metadata(out_dir):
    out_dir = Path(out_dir)
    return load_json(out_dir / METADATA_NAME, "工单 metadata")


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


def read_events(out_dir):
    path = Path(out_dir) / EVENTS_NAME
    events = []
    if not path.exists():
        return events, path
    if not path.is_file():
        raise ControlError(f"事件日志路径存在但不是普通文件: {path}", exit_code=1,
                           gate_id="event_log_path")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(strict_json_loads(line))
                except (json.JSONDecodeError, ValueError):
                    raise ControlError(f"事件日志第 {lineno} 行 JSON 解析失败（可能被截断/篡改）: {path}",
                                       exit_code=1, event_chain_broken_at_line=lineno)
    except OSError as exc:
        raise ControlError(f"事件日志读取失败: {path}: {exc}", exit_code=1,
                           gate_id="event_log_io")
    return events, path


def prepare_event(out_dir, meta, event_type, payload, request_id=None, actor="icode"):
    """锁定内校验现有链并构造下一事件；不落盘。"""
    # event schema 真源校验 event_type 枚举
    event_schema = load_json(SCHEMA_EVENT, "ticket-event.schema.json")
    allowed_types = event_schema["properties"]["event_type"]["enum"]
    if event_type not in allowed_types:
        raise ControlError(f"未知事件类型 {event_type!r}，允许: {allowed_types}", exit_code=2)
    events, problems = verify_event_chain(out_dir, meta, semantic=False)
    if problems:
        raise ControlError("现有事件链不完整，拒绝追加事件", exit_code=1,
                           gate_id="event_chain", violations=problems)
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


def append_prepared_event(out_dir, event):
    path = Path(out_dir) / EVENTS_NAME
    try:
        atomic_append_line(path, json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        raise ControlError(f"事件日志追加失败: {path}: {exc}", exit_code=1,
                           gate_id="event_log_io")


def append_event(out_dir, meta, event_type, payload, request_id=None, actor="icode"):
    """兼容事件-only 写入；调用方必须已持有 DirLock。"""
    event = prepare_event(out_dir, meta, event_type, payload, request_id=request_id, actor=actor)
    append_prepared_event(out_dir, event)
    return event


def verify_event_chain(out_dir, meta=None, semantic=True):
    events, _ = read_events(out_dir)
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
    if semantic and meta is not None and is_vnext(meta):
        problems.extend(f"event_semantics: {item}"
                        for item in validate_event_semantics(events, meta))
    return events, problems


def validate_event_semantics(events, meta):
    """校验控制事件的顺序和 payload 语义，防止自洽 hash 掩盖伪造生命周期。"""
    problems = []
    sm = load_state_machine()
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
            "debug-log": "debug_in_progress",
        }
        current_state = initial_by_kind.get(birth_kind)
        if current_state is None:
            problems.append(f"ticket_created.birth_kind 非法或缺失: {birth_kind!r}")
        expected_debug = isinstance(birth_kind, str) and birth_kind.startswith("debug-")
        if bool(meta.get("debug")) != expected_debug:
            problems.append("ticket_created.birth_kind 与 metadata.debug 不一致")

    for idx, event in enumerate(events, 1):
        if event.get("event_type") != "state_changed":
            continue
        payload = event.get("payload") or {}
        source = payload.get("from")
        target = payload.get("to")
        if source not in sm["states"] or target not in sm["states"]:
            problems.append(f"第 {idx} 行 state_changed.from/to 不在状态词表")
            continue
        if current_state is not None and source != current_state:
            problems.append(
                f"第 {idx} 行 state_changed.from={source!r} 与事件序列当前状态 "
                f"{current_state!r} 不一致")
        if legal_transition(sm, source, target) is None:
            problems.append(f"第 {idx} 行记录非法状态流转 {source!r} → {target!r}")
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

    problems.extend(validate_execution_event_semantics(events))

    event_runs = [{key: value for key, value in (event.get("payload") or {}).items()
                   if key != "metadata_hash_after"}
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


def validate_execution_event_semantics(events):
    """校验 v1 执行事件配对；未标版本的旧 step 事件保持可读兼容。"""
    problems = []
    model = load_execution_model()
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
    active = meta.get("active_checkout")
    active_history = [item for item in (meta.get("checkout_history") or [])
                      if isinstance(item, dict) and item.get("state") == "active"]
    if active_history:
        raise ControlError(
            "checkout_history 与 active_checkout 出现双活动根",
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
    finish = step_attempt_finished(events, start["payload"]["attempt"])
    outcome = (finish.get("payload") or {}).get("outcome") if finish else None
    if outcome not in {"success", "degraded"}:
        return [f"{step}:missing_advancing_receipt"]
    if outcome == "degraded":
        return []  # Existing explicit debt contracts still govern degradation.
    model = load_execution_model()
    _, missing = validate_step_outputs(out_dir, meta, model, step)
    missing += missing_output_receipts(out_dir, meta, step_contract(model, step), events, start)
    return missing


def resolve_port(out_dir, meta, port):
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
        for target in candidates:
            try:
                root_proc = subprocess.run(
                    ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=10)
                head_proc = subprocess.run(
                    ["git", "-C", str(target), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if root_proc.returncode == 0 and head_proc.returncode == 0:
                roots[root_proc.stdout.strip()] = head_proc.stdout.strip()
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


def capture_step_inputs(out_dir, meta, model, step):
    contract = step_contract(model, step)
    facts = [resolve_port(out_dir, meta, port) for port in contract["inputs"]]
    by_id = {fact["id"]: fact for fact in facts}
    missing = [port["id"] for port in contract["inputs"]
               if port.get("required", False) and not by_id[port["id"]]["exists"]]
    protected_ids = [port["id"] for port in contract["inputs"]
                     if port.get("protected", False)]
    protected = {port_id: by_id[port_id] for port_id in protected_ids}
    contract_digest = canonical_digest(contract)
    return {
        "contract_digest": contract_digest,
        "inputs": facts,
        "protected": protected,
        "input_digest": canonical_digest(protected),
    }, missing


def validate_step_outputs(out_dir, meta, model, step, allow_incomplete=False):
    contract = step_contract(model, step)
    facts = [resolve_port(out_dir, meta, port) for port in contract["outputs"]]
    by_id = {fact["id"]: fact for fact in facts}
    missing = [port["id"] for port in contract["outputs"]
               if port.get("required", True) and not by_id[port["id"]]["exists"]]
    if step == "review" and by_id.get("review_manifest", {}).get("exists"):
        missing.extend(review_evidence_issues(out_dir, meta))
    if step in ("deepcheck", "audit") and by_id.get("inspection_coverage", {}).get("exists"):
        missing.extend(inspection_coverage_issues(out_dir, meta, step, allow_incomplete=allow_incomplete))
    if by_id.get("inspection_worklist", {}).get("exists"):
        missing.extend(inspection_worklist_issues(out_dir, meta, step, allow_incomplete=allow_incomplete))
    return facts, missing


def inspection_module():
    """Load the bundled stdlib helper without depending on the invocation cwd."""
    spec = importlib.util.spec_from_file_location("icode_inspection_worklist", SKILL_ROOT / "tools/inspection_worklist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspection_worklist_issues(out_dir, meta, step, attempt=None, allow_incomplete=False):
    path = Path(out_dir) / f"{step}_worklist.json"
    if path.is_symlink() or not path.is_file():
        return ["inspection_worklist:missing_or_symlink"]
    try:
        report = load_json(path, "inspection worklist")
        helper = inspection_module()
        workspace = execution_workspace(out_dir, meta)
        issues = helper.validate_worklist(report, workspace, code_files=meta.get("code_files") or [],
            step=step, ticket_id=meta.get("ticket_id"), attempt=attempt, allow_incomplete=allow_incomplete)
        profile = meta.get("risk_profile")
        effective = profile.get("effective_mode") if isinstance(profile, dict) else None
        effective = effective if effective in ("full", "fast") else meta.get("mode", "full")
        if report.get("mode") != effective:
            issues.append("effective_mode mismatch")
        if attempt is not None:
            events, chain_issues = verify_event_chain(out_dir, meta)
            receipts = [e for e in events if e.get("event_type") == "artifact_written"
                and (e.get("payload") or {}).get("attempt") == attempt
                and (e.get("payload") or {}).get("output") == "inspection_worklist"
                and (e.get("payload") or {}).get("inspection_declaration_hash")]
            if chain_issues or not receipts or any(e["payload"]["inspection_declaration_hash"] != inspection_declaration_hash(report) for e in receipts):
                issues.append("declaration/receipt drift")
        findings = report.get("findings") if isinstance(report, dict) else None
        issues.extend(helper.validate_findings(findings, report, workspace))
        return ["inspection_worklist:" + str(item) for item in issues]
    except (ControlError, ValueError, OSError, TypeError) as exc:
        return [f"inspection_worklist:invalid={exc}"]


def inspection_declaration_hash(report):
    keys = ("schema_version", "workspace", "step", "ticket_id", "attempt", "mode", "required_phases",
            "code_files", "related", "scopes", "baselines", "resolved_baselines", "max_files", "max_bytes")
    return hashlib.sha256(json.dumps({k: report.get(k) for k in keys}, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


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


def inspection_coverage_issues(out_dir, meta, step, attempt=None, allow_incomplete=False):
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
    effective_mode = profile.get("effective_mode") if isinstance(profile, dict) else None
    if effective_mode not in ("fast", "full"):
        effective_mode = meta.get("mode")
    phases = ["audit"] if step == "audit" else ["reverse"] if effective_mode == "fast" else ["reverse", "fixed", "free"]
    reads = report.get("read_phases")
    workspace = execution_workspace(out_dir, meta)
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


def cmd_check_outputs(args):
    """Single read-only final product check; historical receipts are not invented."""
    out_dir = Path(args.dir).resolve()
    meta = load_metadata(out_dir)
    require_vnext(meta, out_dir)
    require_valid_metadata(meta)
    events, problems = verify_event_chain(out_dir, meta)
    model = load_execution_model()
    steps = ["plan", "review", "merge", "code", "deepcheck", "audit"] if args.step == "audit" else [args.step]
    warnings = []
    for step in steps:
        contract = step_contract(model, step)
        starts = [e for e in events if e.get("event_type") == "step_started" and
                  (e.get("payload") or {}).get("step") == step]
        policy = load_state_machine()["gate_policy"]
        completion = {policy["step_by_target"][target]: number for target, number in
                      policy["completed_step_by_target"].items() if target in policy["step_by_target"]}
        old_contract = bool(starts) and (starts[-1].get("payload") or {}).get("contract_digest") != canonical_digest(contract)
        latest_start = starts[-1] if starts else None
        attempt = (latest_start.get("payload") or {}).get("attempt") if latest_start else None
        finished = step_attempt_finished(events, attempt) if attempt is not None else None
        historical = bool(old_contract and finished and (finished.get("payload") or {}).get("outcome") in ("success", "degraded")) or (
                      not starts and completion.get(step) in (meta.get("completed_steps") or []))
        if old_contract and not historical:
            problems.append(f"{step}:contract_drift_reenter")
        degraded = bool(finished and (finished.get("payload") or {}).get("outcome") == "degraded")
        facts, missing = validate_step_outputs(out_dir, meta, model, step, allow_incomplete=degraded)
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
            problems.extend(inspection_worklist_issues(out_dir, meta, step, attempt, allow_incomplete=degraded))
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
                problems.extend(inspection_coverage_issues(out_dir, meta, step, attempt, allow_incomplete=degraded))
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
    print(json.dumps(result, ensure_ascii=False, indent=2))
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
    txn = load_json(txn_path, "控制面事务日志")
    before = txn.get("before_metadata")
    after = txn.get("after_metadata")
    event = txn.get("event")
    if (before is not None and not isinstance(before, dict)) or not isinstance(after, dict) or not isinstance(event, dict):
        raise ControlError("控制面事务日志结构损坏，拒绝自动恢复", exit_code=1,
                           gate_id="transaction_recovery", transaction=str(txn_path))
    meta_path = out_dir / METADATA_NAME
    current = load_json(meta_path, "工单 metadata") if meta_path.is_file() else None
    events, _ = read_events(out_dir)
    event_present = any(e.get("event_hash") == event.get("event_hash") for e in events)
    current_hash = metadata_hash(current)
    if event_present:
        if current is None or current_hash == metadata_hash(before):
            atomic_write_json(meta_path, after)
        elif current_hash != metadata_hash(after):
            raise ControlError("事务事件已落盘但 metadata 已被并发改写，拒绝猜测恢复", exit_code=1,
                               gate_id="transaction_recovery")
        txn_path.unlink()
        return "completed"
    if current_hash == metadata_hash(after):
        if before is None:
            meta_path.unlink(missing_ok=True)
        else:
            atomic_write_json(meta_path, before)
    elif current_hash != metadata_hash(before):
        raise ControlError("事务事件未落盘且 metadata 已被并发改写，拒绝猜测恢复", exit_code=1,
                           gate_id="transaction_recovery")
    txn_path.unlink()
    return "rolled_back"


def commit_metadata_and_event(out_dir, before, after, event_type, payload,
                              request_id=None, actor="icode"):
    """调用方持锁。事务日志 + metadata 原子替换 + event 追加；普通 I/O 失败回滚旧 metadata。"""
    out_dir = Path(out_dir)
    require_valid_metadata(after)
    event_payload = dict(payload)
    event_payload["metadata_hash_after"] = metadata_hash(after)
    event = prepare_event(out_dir, after, event_type, event_payload,
                          request_id=request_id, actor=actor)
    txn_path = out_dir / TXN_NAME
    atomic_write_json(txn_path, {
        "schema_version": 1,
        "created_at": now_iso(),
        "before_metadata": before,
        "after_metadata": after,
        "event": event,
    })
    try:
        atomic_write_json(out_dir / METADATA_NAME, after)
        append_prepared_event(out_dir, event)
    except BaseException:
        # append 的 write 已成功但 fsync/返回失败时，事件可能已落盘。
        # 此时应以事件链为准完成 metadata，不能回滚成「新事件+旧状态」。
        event_absence_confirmed = False
        try:
            events, _ = read_events(out_dir)
            if any(item.get("event_hash") == event.get("event_hash") for item in events):
                atomic_write_json(out_dir / METADATA_NAME, after)
                txn_path.unlink(missing_ok=True)
                return event
            event_absence_confirmed = True
        except BaseException:
            pass
        try:
            if before is None:
                (out_dir / METADATA_NAME).unlink(missing_ok=True)
            else:
                atomic_write_json(out_dir / METADATA_NAME, before)
            if event_absence_confirmed:
                txn_path.unlink(missing_ok=True)
        except BaseException:
            # 保留事务日志，让下一次变更显式恢复或 validate 报 pending_transaction。
            pass
        raise
    txn_path.unlink(missing_ok=True)
    return event


# ---------------------------------------------------------------- 门禁 linter（复用三现有 linter）

def run_gate_linters(out_dir, to_status=None, delivery_verdict=None, legacy=False, step=None):
    sm = load_state_machine()
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
    results = []
    for gate_id, cmd in lint_cfg.items():
        full_cmd = list(cmd)
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
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=180,
                                  cwd=str(SKILL_ROOT))
        except subprocess.TimeoutExpired:
            results.append({"gate_id": gate_id, "ok": False, "fail_closed": True,
                            "detail": "linter 超时（180s）", "hint": f"手动运行: {' '.join(full_cmd)}"})
            continue
        except OSError as exc:
            results.append({"gate_id": gate_id, "ok": False, "fail_closed": True,
                            "detail": f"linter 启动失败: {exc}", "hint": f"手动运行: {' '.join(full_cmd)}"})
            continue
        report = None
        try:
            report = strict_json_loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            pass
        # 所有门禁命令都声明 --json；exit 0 但没有 JSON 报告不能当成可审计通过。
        ok = proc.returncode == 0 and isinstance(report, dict)
        entry = {"gate_id": gate_id, "ok": ok, "returncode": proc.returncode,
                 "cmd": full_cmd}
        if isinstance(report, dict):
            entry["report"] = report
        else:
            raw = (proc.stdout or proc.stderr or "")[-500:]
            entry["detail"] = "linter 未返回合法 JSON 对象" + (f": {raw}" if raw else "")
        if not ok:
            entry["fail_closed"] = True
            entry["hint"] = f"修复后重试: {' '.join(full_cmd)}"
        results.append(entry)
    return results


# ---------------------------------------------------------------- 子命令实现

def cmd_resolve_ticket(args):
    result = resolve_dir(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_create(args):
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
        "log": ("log_in_progress", [], False),
        "debug-init": ("debug_in_progress", ["0"], True),
        "debug-log": ("debug_in_progress", [], True),
    }
    status, completed_steps, debug = birth[args.birth]
    meta = dict(seed)
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
        "mcp_gate_schema_version": load_json(SKILL_ROOT / "mcp/cheap-research/gates.json", "cheap gate catalog")["trace_schema_version"],
    })
    if debug:
        meta["debug"] = True
    elif meta.get("debug"):
        raise ControlError("非 debug 出生类型禁止 metadata.debug=true", exit_code=2,
                           gate_id="birth_kind_mismatch")
    require_valid_metadata(meta)
    payload = {"birth_kind": args.birth,
               "execution_contract_version": 1,
               "requirement_summary": meta.get("requirement_summary") or args.requirement[:100]}
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
            if prior and current == comparable_meta:
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
        state_events = [e for e in events if e["event_type"] == "state_changed"]
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
        state_events = [e for e in events if e.get("event_type") == "state_changed"]
        birth_states = {"init_in_progress", "log_in_progress", "debug_in_progress"}
        if not state_events and not migration_events and meta.get("status") not in birth_states:
            violations.append({
                "gate_id": "status_event_consistency", "path": "$.status",
                "detail": f"metadata.status={meta.get('status')!r} 不是出生态但事件链无 state_changed；"
                          "状态疑似在创建时绕过 transition 直写。",
            })
        completed_by_target = load_state_machine()["gate_policy"].get(
            "completed_step_by_target", {})
        completed_steps = set(meta.get("completed_steps") or [])
        event_backed_steps = {
            completed_by_target.get(item.get("payload", {}).get("to"))
            for item in state_events
        }
        event_backed_steps.discard(None)
        for state_event in state_events:
            target = state_event.get("payload", {}).get("to")
            expected_step = completed_by_target.get(target)
            if expected_step and expected_step not in completed_steps:
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
        step = load_state_machine()["gate_policy"]["step_by_target"].get(meta.get("status"))
        state_events = [e for e in events if e.get("event_type") == "state_changed"]
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
        for report in run_gate_linters(out_dir, legacy=legacy):
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


def cmd_transition(args):
    out_dir = Path(args.dir).resolve()
    sm = load_state_machine()
    to_status = args.to
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
            {"to": to_status, "delivery_verdict": args.delivery_verdict},
            payload_keys=("to", "delivery_verdict"))
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "to": to_status,
                              "at": prior["timestamp"], "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
            return 0

        # Only historical births may be untracked. New births explicitly opt in
        # through the immutable event, so omitting start cannot bypass receipts.
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
    if args.skip_gates:
        transition_payload["gates_skipped_for_test"] = True

    # 门禁：完成态跑三 linter（debug 孪生跳过）；completed 强制显式 delivery_verdict
    gate_reports = []
    gates_skipped = bool(args.skip_gates)
    if to_status in sm["gate_policy"]["gated_targets"] and not meta.get("debug") and not gates_skipped:
        gate_reports = run_gate_linters(out_dir, to_status, args.delivery_verdict)
        failed = [g for g in gate_reports if not g["ok"]]
        if failed:
            print(json.dumps({
                "ok": False, "fail_closed": True, "status": current,
                "error": f"门禁未通过，状态不前移（{current!r} → {to_status!r} 被拒绝）",
                "failed_gates": [{"gate_id": g["gate_id"], "detail": g.get("detail") or
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
        if tracked_step and requires_execution_receipts(events2) and not args.skip_gates:
            receipt_issues = completion_receipt_issues(out_dir, meta2, events2, tracked_step)
            if receipt_issues:
                raise ControlError("门禁执行期间产物身份变化", gate_id="step_receipt",
                                   violations=receipt_issues)
        prior = find_idempotent_event(
            events2, args.request_id, "state_changed",
            {"to": to_status, "delivery_verdict": args.delivery_verdict},
            payload_keys=("to", "delivery_verdict"))
        if prior:
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "to": to_status,
                              "at": prior["timestamp"], "event_id": prior["event_id"]},
                                 ensure_ascii=False, indent=2))
            return 0
        before_meta = dict(meta2)
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
        "out_dir": str(rel),
        "status": meta.get("status"),
        "updated_at": now_iso(),
        "created_at": meta.get("created_at"),
        "has_00_init": (out_dir / "00_init.md").is_file(),
        "has_plan": (out_dir / "01_plan.md").is_file(),
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
        expected_gates = set(load_state_machine()["gate_policy"]["gate_linters"])
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
        atomic_write_json(archive_dir / METADATA_NAME, meta)
        events_path = source_dir / EVENTS_NAME
        if not events_path.is_file():
            raise ControlError("源工单事件链不存在，无法交接",
                               exit_code=1, gate_id="event_chain")
        atomic_write_bytes(archive_dir / EVENTS_NAME, events_path.read_bytes())
        refresh_archive_manifest_control_files(archive_dir, meta.get("ticket_id"))
    return archive_dir


def cmd_archive_manifest(args):
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
    "schema_version", "ticket_id", "created_at", "status", "completed_steps",
    "indexed", "delivery_verdict", "claims", "verification_runs", "close_state",
    "control_plane_degraded", "debug", "project_path",
    "workflow_gate_schema_version", "thinking_gate_schema_version",
    "mcp_gate_schema_version",
}
CLOSE_BOOKKEEPING_FIELDS = {
    "artifact_root", "archive_path", "backup_path", "worktree_path", "worktree_branch",
    "wt_degraded", "sub_worktrees", "active_checkout", "checkout_history", "migration",
    "submitted_baseline", "submitted_baselines", "submission_contracts", "submission_audit",
}


def cmd_metadata_update(args):
    """原子更新已登记业务字段；生命周期控制字段只允许专用命令维护。"""
    out_dir = Path(args.dir).resolve()
    try:
        updates = strict_json_loads(args.set_json) if args.set_json else {}
        appends = strict_json_loads(args.append_json) if args.append_json else {}
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
        prior = find_idempotent_event(
            events, args.request_id, "metadata_updated", payload,
            actor=args.actor, payload_keys=("set", "append"))
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


def cmd_record_verification(args):
    out_dir = Path(args.dir).resolve()
    if not args.evidence or not args.evidence.strip():
        raise ControlError("verification run 必须提供可审计 --evidence", exit_code=2,
                           gate_id="verification_evidence")
    if args.build_source == "reused" and not args.artifact_identity:
        raise ControlError("--build-source reused 必须提供 --artifact-identity 核对产物",
                           exit_code=2, gate_id="reused_artifact_identity")
    if args.kind == "build":
        # 构建不能借用 deploy/device_test 层级，也不能冒充复用产物后的重编。
        if args.device is not None or args.layer not in {None, "build"} \
                or args.build_source in {"reused", "existing"}:
            raise ControlError(
                "kind=build 只能记录 build 层级，不带 device，不复用既有构建",
                exit_code=2, gate_id="build_verification_scope")
        if args.outcome == "pass" and (
                args.build_source != "fresh"
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
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        if meta.get("close_state") is not None:
            raise ControlError(
                "工单已进入关闭流程，禁止继续追加验证记录；需修改请先 reopen",
                exit_code=1, gate_id="closed_ticket_mutation_frozen")
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录验证", exit_code=1,
                               gate_id="event_chain", violations=problems)
        prior = find_idempotent_event(
            events, args.request_id, "verification_recorded", input_payload,
            payload_keys=tuple(input_payload))
        if prior:
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
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "verification_recorded", run,
            request_id=args.request_id)
    print(json.dumps({"ok": True, "run": run, "event_id": event["event_id"]},
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
            actor=args.actor, payload_keys=tuple(input_payload))
        if prior:
            prior_id = prior["payload"].get("spawn_id")
            prior_spawn = next(
                (item for item in _agent_spawns(meta) if item.get("spawn_id") == prior_id),
                {key: value for key, value in prior["payload"].items()
                 if key != "metadata_hash_after"})
            print(json.dumps({"ok": True, "already_applied": True,
                              "request_id": args.request_id, "spawn": prior_spawn,
                              "event_id": prior["event_id"]},
                             ensure_ascii=False, indent=2))
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
        before_meta = dict(meta)
        extensions = dict(meta.get("extensions") or {})
        agent_ns = dict(extensions.get("agent") or {})
        agent_ns["spawns"] = spawns + [spawn]
        extensions["agent"] = agent_ns
        meta["extensions"] = extensions
        event = commit_metadata_and_event(
            out_dir, before_meta, meta, "agent_spawned", spawn,
            request_id=args.request_id, actor=args.actor)
    print(json.dumps({"ok": True, "spawn": spawn, "event_id": event["event_id"]},
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
            actor=args.actor, payload_keys=tuple(input_payload))
        if prior:
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


def cmd_step(args):
    """步骤端口快照、Reactive 边界复检和终结回执。"""
    out_dir = Path(args.dir).resolve()
    model = load_execution_model()
    contract = step_contract(model, args.step)
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("现有事件链不完整，拒绝记录步骤", exit_code=1,
                               gate_id="event_chain", violations=problems)

        if args.phase == "start":
            attempt = args.attempt or derived_attempt("step", args.request)
            snapshot, missing = capture_step_inputs(out_dir, meta, model, args.step)
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
            prior = find_idempotent_event(events, args.request, "step_started", payload)
            if prior:
                result = {"ok": True, "already_applied": True, "step": args.step,
                          "attempt": attempt, "event_id": prior["event_id"],
                          "input_digest": payload["input_digest"]}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
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
                                 request_id=args.request)
            print(json.dumps({"ok": True, "step": args.step, "phase": "start",
                              "attempt": attempt, "event_id": event["event_id"],
                              "input_digest": payload["input_digest"],
                              "required_checks": contract["required_checks"]},
                             ensure_ascii=False, indent=2))
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
            current, missing = capture_step_inputs(out_dir, meta, model, args.step)
            old_protected = start_payload.get("protected") or {}
            changed = sorted(
                key for key in set(old_protected) | set(current["protected"])
                if old_protected.get(key) != current["protected"].get(key)
            )
            if start_payload.get("contract_digest") != current["contract_digest"]:
                changed.insert(0, "$contract")
            routes = contract["drift_routes"]
            route = next((routes[item] for item in changed if item in routes),
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
            event = prior or append_event(out_dir, meta, "gate_checked", payload,
                                          request_id=args.request)
            result = {"ok": result_name == "pass", "step": args.step,
                      "phase": "check", "attempt": args.attempt,
                      "boundary": args.boundary, "result": result_name,
                      "changed_inputs": changed, "missing_inputs": missing,
                      "route": payload["route"], "event_id": event["event_id"],
                      "already_applied": bool(prior)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
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
                print(json.dumps({"ok": True, "already_applied": True,
                                  "step": args.step, "attempt": args.attempt,
                                  "outcome": expected.get("outcome"),
                                  "event_id": finished["event_id"]},
                                 ensure_ascii=False, indent=2))
                return 0
            if args.request and finished.get("request_id") == args.request:
                raise ControlError(
                    "相同 request 的 step finish payload 不一致", exit_code=1,
                    gate_id="idempotency_conflict", existing=expected,
                    attempted={"outcome": args.outcome, "evidence": args.evidence})
            raise ControlError("step attempt 已终结，拒绝第二个终结结果", exit_code=1,
                               gate_id="step_attempt_finished",
                               existing=finished.get("payload"))
        gate_events = [event for event in attempt_events(events, args.attempt)
                       if event.get("event_type") == "gate_checked"]
        latest_checks = {}
        for gate_event in gate_events:
            gate_payload = gate_event.get("payload") or {}
            latest_checks[gate_payload.get("boundary")] = gate_payload.get("result")
        missing_checks = [boundary for boundary in contract["required_checks"]
                          if latest_checks.get(boundary) != "pass"]
        outputs, missing_outputs = validate_step_outputs(out_dir, meta, model, args.step)
        missing_receipts = missing_output_receipts(
            out_dir, meta, contract, events, start)
        if args.step == "review":
            missing_receipts.extend(review_evidence_issues(out_dir, meta, events, start))
        if args.step in ("code", "deepcheck", "audit"):
            worklist_issues = inspection_worklist_issues(out_dir, meta, args.step, args.attempt,
                                                        allow_incomplete=args.outcome == "degraded")
            missing_receipts.extend(worklist_issues)
            if args.outcome == "degraded" and (worklist_issues or any(
                    item.startswith("inspection_worklist:") for item in missing_receipts)):
                raise ControlError("降级自查必须有真实工作清单回执与明确债务", gate_id="inspection_worklist",
                                   violations=missing_receipts)
        if args.step in ("deepcheck", "audit"):
            coverage_issues = inspection_coverage_issues(out_dir, meta, args.step, args.attempt,
                                                         allow_incomplete=args.outcome == "degraded")
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
            failed = [g for g in run_gate_linters(out_dir, step=args.step) if not g["ok"]]
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
        event = prior or append_event(out_dir, meta, "step_finished", payload,
                                      request_id=args.request)
        print(json.dumps({"ok": True, "step": args.step, "phase": "finish",
                          "attempt": args.attempt, "outcome": args.outcome,
                          "duration_ms": payload["duration_ms"],
                          "event_id": event["event_id"],
                          "already_applied": bool(prior)}, ensure_ascii=False, indent=2))
        return 0


def cmd_inspection(args):
    """Prepare/reuse a bounded worklist; register reads only against its frozen version."""
    out_dir = Path(args.dir).resolve()
    model = load_execution_model()
    contract = step_contract(model, args.step)
    target = out_dir / f"{args.step}_worklist.json"
    if target.is_symlink():
        raise ControlError("工作清单不得是符号链接", gate_id="inspection_worklist")
    if args.phase == "check":
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("事件链不完整", gate_id="event_chain", violations=problems)
        start = find_step_start(events, args.attempt)
        if start["payload"].get("step") != args.step:
            raise ControlError("工作清单与执行步骤不一致", gate_id="inspection_worklist")
        issues = inspection_worklist_issues(out_dir, meta, args.step, args.attempt,
                                            allow_incomplete=args.allow_incomplete)
        print(json.dumps(dict(ok=not issues, read_only=True, path=str(target), violations=issues), ensure_ascii=False))
        return 0 if not issues else 1
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("事件链不完整", gate_id="event_chain", violations=problems)
        start = find_step_start(events, args.attempt)
        if start["payload"].get("step") != args.step or step_attempt_finished(events, args.attempt):
            raise ControlError("工作清单须关联同一步骤的未终结 attempt", gate_id="inspection_worklist")
        current, missing = capture_step_inputs(out_dir, meta, model, args.step)
        if missing or current["protected"] != start["payload"].get("protected") or current["contract_digest"] != start["payload"].get("contract_digest"):
            raise ControlError("执行合同或受保护输入漂移，先 blocked 重入", gate_id="inspection_worklist")
        if not any(e.get("event_type") == "gate_checked" and (e.get("payload") or {}).get("attempt") == args.attempt
                   and (e.get("payload") or {}).get("boundary") == "before_write"
                   and (e.get("payload") or {}).get("result") == "pass" for e in events):
            raise ControlError("写工作清单前须真实 before_write check", gate_id="inspection_worklist")
        helper = inspection_module()
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
            if args.phase == "prepare" and not resumed:
                baselines = strict_json_loads(args.baselines_json) if args.baselines_json else None
                report = helper.build_worklist(workspace, meta.get("code_files") or [], step=args.step,
                    ticket_id=meta["ticket_id"], attempt=args.attempt,
                    mode=(meta.get("risk_profile") or {}).get("effective_mode") or meta.get("mode", "full"),
                    related=args.related, scopes=args.scope, baselines=baselines)
                report.setdefault("findings", [])
            elif args.phase == "prepare" and resumed:
                options = (("related", sorted(set(args.related)) if args.related else None),
                           ("scopes", sorted(set(args.scope)) if args.scope else None),
                           ("baselines", strict_json_loads(args.baselines_json) if args.baselines_json else None))
                if any(value is not None and value != report.get(key) for key, value in options):
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
                step=args.step, ticket_id=meta["ticket_id"], attempt=args.attempt, allow_incomplete=True)
            if issues:
                raise ValueError(str(issues))
            request_identity = dict(phase=args.phase,
                read_phase=args.read_phase if args.phase == "read" else None,
                source_path=args.path if args.phase == "read" else None,
                related=sorted(set(args.related)) if args.related else None,
                scopes=sorted(set(args.scope)) if args.scope else None,
                baselines=strict_json_loads(args.baselines_json) if args.baselines_json else None)
            stable_payload = dict(execution_model_version=model["schema_version"], step=args.step,
                attempt=args.attempt, output="inspection_worklist", scope="ticket", path=str(target),
                inspection_declaration_hash=inspection_declaration_hash(report), inspection_request=request_identity)
            prior = find_idempotent_event(events, args.request, "artifact_written", stable_payload,
                payload_keys=list(stable_payload))
            if prior:
                # A later Read may have expanded the artifact. Replaying this
                # operation must return its original receipt, not erase progress.
                print(json.dumps(dict(ok=True, output="inspection_worklist", path=str(target), resumed=True,
                    event_id=prior["event_id"], sha256=prior["payload"]["sha256"],
                    current_sha256=file_sha256(target), already_applied=True), ensure_ascii=False))
                return 0
            if args.phase == "read":
                if args.read_phase not in report["required_phases"] or not args.path:
                    raise ValueError("read 必须指定合法 --read-phase 和 --path")
                selected = [f for unit in report["units"] for f in unit["files"] if f["path"] == args.path]
                if len(selected) != 1:
                    raise ValueError("路径不在唯一自查单元中")
                selected[0]["reads"][args.read_phase] = selected[0]["sha256"]
            if args.phase != "prepare" or not resumed:
                # Reject a conflicting request before replacing any valid worklist.
                encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                prospective = dict(stable_payload, sha256=hashlib.sha256(encoded).hexdigest(), size=len(encoded))
                find_idempotent_event(events, args.request, "artifact_written", prospective)
                atomic_write_json(target, report)
        except (ValueError, TypeError, OSError) as exc:
            raise ControlError(f"工作清单准备/版本校验失败: {exc}", gate_id="inspection_worklist")
        payload = dict(stable_payload, sha256=file_sha256(target), size=target.stat().st_size)
        prior = find_idempotent_event(events, args.request, "artifact_written", payload)
        event = prior or append_event(out_dir, meta, "artifact_written", payload, request_id=args.request)
    print(json.dumps(dict(ok=True, output="inspection_worklist", path=str(target), resumed=resumed,
                          event_id=event["event_id"], sha256=payload["sha256"], already_applied=bool(prior)), ensure_ascii=False))
    return 0


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


def cmd_artifact(args):
    out_dir = Path(args.dir).resolve()
    model = load_execution_model()
    contract = step_contract(model, args.step)
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, problems = verify_event_chain(out_dir, meta)
        if problems:
            raise ControlError("事件链不完整，拒绝记录产物", exit_code=1,
                               gate_id="event_chain", violations=problems)
        start = find_step_start(events, args.attempt)
        if start["payload"].get("step") != args.step or step_attempt_finished(events, args.attempt):
            raise ControlError("产物必须关联同一步骤的未终结 attempt", exit_code=1,
                               gate_id="artifact_attempt")
        base = Path(out_dir) if args.scope == "ticket" else execution_workspace(out_dir, meta)
        target = port_path(base, args.path)
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
        if output_id == "review_manifest":
            issues = review_evidence_issues(out_dir, meta)
            if issues:
                raise ControlError("审查manifest不满足结构/计数合同", exit_code=1,
                                   gate_id="review_manifest", violations=issues)
            data = load_json(target, "review manifest")
            # Preserve each completed round's identity in the original event,
            # including clean rounds that have no separate detail file.
            payload["round_digests"] = {str(row["round"]): canonical_digest(row) for row in data["rounds"]}
        prior = find_idempotent_event(events, args.request, "artifact_written", payload)
        event = prior or append_event(out_dir, meta, "artifact_written", payload,
                                      request_id=args.request)
    print(json.dumps({"ok": True, "step": args.step, "attempt": args.attempt,
                      "output": output_id, "path": str(target),
                      "sha256": payload["sha256"], "event_id": event["event_id"],
                      "already_applied": bool(prior)}, ensure_ascii=False, indent=2))
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


def cmd_operation(args):
    out_dir = Path(args.dir).resolve()
    model = load_execution_model()
    problems = validate_execution_catalog(model)
    if problems:
        raise ControlError("execution_model 机器契约无效", exit_code=1,
                           gate_id="execution_model_catalog", violations=problems)
    with DirLock(out_dir):
        recover_pending_transaction(out_dir)
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
        ensure_execution_writable(meta)
        events, chain_problems = verify_event_chain(out_dir, meta)
        if chain_problems:
            raise ControlError("事件链不完整，拒绝记录长动作", exit_code=1,
                               gate_id="event_chain", violations=chain_problems)
        if args.phase == "start":
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
            prior = find_idempotent_event(events, args.request, "operation_started", payload)
            if prior:
                print(json.dumps({"ok": True, "already_applied": True,
                                  "name": args.name, "attempt": attempt,
                                  "event_id": prior["event_id"]},
                                 ensure_ascii=False, indent=2))
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
            event = append_event(out_dir, meta, "operation_started", payload,
                                 request_id=args.request)
            print(json.dumps({"ok": True, "phase": "start", "name": args.name,
                              "class": args.opclass, "attempt": attempt,
                              "event_id": event["event_id"]}, ensure_ascii=False, indent=2))
            return 0

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
                print(json.dumps({"ok": True, "already_applied": True,
                                  "name": finish_payload.get("name"),
                                  "attempt": args.attempt,
                                  "outcome": finish_payload.get("outcome"),
                                  "event_id": finished["event_id"]},
                                 ensure_ascii=False, indent=2))
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
        event = prior or append_event(out_dir, meta, "operation_finished", payload,
                                      request_id=args.request)
    print(json.dumps({"ok": True, "phase": "finish", "name": payload["name"],
                      "class": payload["class"], "attempt": args.attempt,
                      "outcome": args.outcome, "duration_ms": payload["duration_ms"],
                      "decision": decision, "event_id": event["event_id"],
                      "already_applied": bool(prior)}, ensure_ascii=False, indent=2))
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


def cmd_action_policy(args):
    """生成轻量、一致的 UI/Agent 动作投影，并用 revision 防止过期执行。"""
    if getattr(args, "verify_action", None) and args.action != "verify":
        raise ControlError("--verify-action 只能与 --action verify 一起使用", exit_code=2)
    out_dir = Path(args.dir).resolve()
    sm = load_state_machine()
    policy = sm.get("action_policy")
    if not isinstance(policy, dict) or policy.get("schema_version") != 1 \
            or not isinstance(policy.get("actions"), dict):
        raise ControlError(
            "gates.json action_policy 机器契约无效",
            exit_code=1, gate_id="action_policy_catalog")

    meta = load_metadata(out_dir)
    require_vnext(meta, out_dir)
    require_valid_metadata(meta)
    events, problems = verify_event_chain(out_dir, meta)
    if problems:
        raise ControlError(
            "事件链不完整，拒绝生成动作策略",
            exit_code=1, gate_id="event_chain", violations=problems)
    execution = execution_trace(events)
    last_event_hash = events[-1]["event_hash"] if events else GENESIS_HASH
    meta_digest = metadata_hash(meta)
    revision_base = {
        "ticket_id": meta.get("ticket_id"),
        "metadata_hash": meta_digest,
        "last_event_hash": last_event_hash,
        "event_count": len(events),
    }
    revision = {**revision_base, "token": canonical_digest(revision_base)}

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
    allowed = sorted(allowed)
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
            variant = getattr(args, "verify_action", None) or "default"
            contract = actions["verify"].get("variants", {}).get(variant, actions["verify"])
            if status not in contract["statuses"]:
                raise ControlError(f"verify {variant} 在当前状态不可执行；分析阶段只允许 --build/--plan",
                                   gate_id="action_policy", status=status, verify_action=variant)
        model = load_execution_model()
        if args.action in model.get("step_contracts", {}):
            _snapshot, missing = capture_step_inputs(
                out_dir, meta, model, args.action)
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
        "execution_root": str(trusted_execution_workspace(out_dir, meta)),
        "open_steps": execution["open_steps"],
        "open_operations": execution["open_operations"],
        "open_agents": execution["open_agents"],
    }
    if args.action:
        result.update({"action": args.action, "action_allowed": True})
        if args.action == "verify":
            result.update(verify_action=variant, side_effect=contract["side_effect"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_snapshot(args):
    out_dir = Path(args.dir).resolve()
    sm = load_state_machine()
    snap_path = Path(out_dir) / SNAPSHOT_NAME
    with DirLock(out_dir):
        if not args.verify:
            recover_pending_transaction(out_dir)
        elif (out_dir / TXN_NAME).exists():
            raise ControlError("snapshot 校验时发现未完成事务，拒绝自动修改",
                               exit_code=1, gate_id="pending_transaction")
        meta = load_metadata(out_dir)
        require_vnext(meta, out_dir)
        require_valid_metadata(meta)
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
    p.add_argument("--requirement", required=True)
    p.add_argument("--birth", required=True,
                   choices=["init", "plan", "log", "debug-init", "debug-log"])
    p.add_argument("--metadata-json", help="额外 metadata 字段 JSON 对象；受保护出生字段由工具覆盖")
    p.add_argument("--request-id", help="创建幂等键")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("create-next", help="为 UI 原子分配编号、出生并写入索引")
    p.add_argument("--workspace", required=True, help="服务端解析后的可信工程根")
    p.add_argument("--requirement", required=True)
    p.add_argument("--request-id", required=True, help="UI 创建工单幂等键")
    p.add_argument("--index", help="索引路径覆盖（测试用）")
    p.set_defaults(func=cmd_create_next)

    p = sub.add_parser("check-outputs", help="只读检查步骤产物与条件审查证据")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True, choices=["review", "code", "deepcheck", "audit"])
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

    p = sub.add_parser("artifact", help="记录与 step outputs 合同匹配的产物 hash")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--scope", default="ticket", choices=["ticket", "workspace", "external"])
    p.add_argument("--request", help="幂等键")
    p.set_defaults(func=cmd_artifact)

    p = sub.add_parser("inspection", help="原生自查清单：范围/关联单元/专项规则/源码定位（不调用模型）")
    p.add_argument("--dir", required=True)
    p.add_argument("--step", required=True, choices=["code", "deepcheck", "audit"])
    p.add_argument("--attempt", required=True)
    p.add_argument("--phase", required=True, choices=["prepare", "read", "check"])
    p.add_argument("--scope", action="append", default=None, help="有证据的项目内范围，重复增补")
    p.add_argument("--related", action="append", default=None, help="调用方/协议另一端/测试等关联路径")
    p.add_argument("--baselines-json", help="受影响仓库相对根到真实Git基线的映射")
    p.add_argument("--read-phase", help="真实Read之后登记对应阶段")
    p.add_argument("--path", help="read登记的项目相对文件")
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
    p.add_argument("--verify-action", choices=["default", "build", "plan", "deploy", "listen", "device_test"],
                   help="verify 子动作；默认仍按部署权限校验")
    p.add_argument("--expected-revision",
                   help="可选：UI 刷新时取得的 revision token")
    p.set_defaults(func=cmd_action_policy)

    p = sub.add_parser("transition", help="状态流转（fail-closed + 门禁 linter + 事件）")
    p.add_argument("--dir", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--delivery-verdict", choices=DELIVERY_VERDICTS)
    p.add_argument("--request-id", help="幂等键（同键同目标重放=已应用摘要）")
    p.add_argument("--skip-gates", action="store_true",
                   help="仅限测试夹具；生产流程禁止跳门禁")
    p.set_defaults(func=cmd_transition)

    p = sub.add_parser("metadata-update", help="原子更新已登记业务字段并追加 metadata_updated 事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--set-json", help="覆盖字段的 JSON 对象")
    p.add_argument("--append-json", help="追加数组字段的 JSON 对象；每个值必须是元素数组")
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

    p = sub.add_parser("record-verification",
                       help="原子记录 verification_runs + verification_recorded 事件")
    p.add_argument("--dir", required=True)
    p.add_argument("--kind", required=True, choices=["deploy", "listen", "device_test", "build"])
    p.add_argument("--outcome", required=True, choices=["pass", "fail", "inconclusive"])
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


def main():
    import contextlib
    import io

    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    try:
        # 捕获子命令输出：业务失败（ok=false）时子命令仍可能返回 0，
        # 会让 shell 的 `&&` / `set -e` 误判为成功。此处统一纠正为退出码 1。
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = args.func(args)
        out = buf.getvalue()
        sys.stdout.write(out)
        sys.stdout.flush()
        if code == 0:
            try:
                payload = json.loads(out)
            except (ValueError, TypeError):
                payload = None
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
    sys.exit(main())
