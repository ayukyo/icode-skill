"""通过本机 Codex/Claude Code CLI 运行受限 ICODE 步骤。"""

from __future__ import annotations

import json
import importlib.util
import io
import os
import re
import signal
import shutil
import shlex
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List

from .ticket_catalog import ResolvedTicket

_verify_spec = importlib.util.spec_from_file_location(
    "icode_verify_request", Path(__file__).resolve().parents[2] / "tools/verify_request.py")
_verify_module = importlib.util.module_from_spec(_verify_spec)
_verify_spec.loader.exec_module(_verify_module)


def parse_verify_note(note, ticket_id=None):
    try:
        parsed = _verify_module.parse_request(note)
    except ValueError as exc:
        raise HostRunnerError(str(exc), code="invalid_verify_request") from exc
    if ticket_id and parsed["ticket"] and parsed["ticket"] != ticket_id:
        raise HostRunnerError("verify 参数工单与 UI 锁定工单不一致", code="ticket_mismatch")
    return parsed


ALLOWED_STEPS = frozenset({
    "init", "start", "fast", "log", "plan", "review", "merge", "code",
    "deepcheck", "audit", "readme", "patch", "verify", "status", "doc",
    "docx", "ppt", "limit", "learn", "bak",
})
MAX_NOTE_CHARS = 8000
DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
TERMINAL_STATES = frozenset({
    "succeeded", "failed", "cancelled", "outcome_unknown",
})
RECOVERY_SCHEMA_VERSION = 1
MAX_PUBLIC_EVENTS = 1000
PERSISTED_JOB_FIELDS = frozenset({
    "job_id", "request_id", "ticket_id", "step", "host", "model", "state",
    "created_at", "finished_at", "returncode", "truncated",
    "cancel_requested", "spawn_id", "recovery_reason", "last_event_message",
    "event_count", "lifecycle_error",
})
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)

class HostRunnerError(RuntimeError):
    """主机选择、任务约束或进程生命周期错误。"""

    def __init__(self, message: str, *, code: str = "host_runner_error"):
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_icode_prompt(step: str, note: str = "", ticket_id: str | None = None) -> str:
    if step not in ALLOWED_STEPS:
        raise HostRunnerError(f"未登记步骤: {step!r}", code="step_not_allowed")
    if not isinstance(note, str):
        raise HostRunnerError("步骤备注必须是文本", code="invalid_note")
    note = note.strip()
    if len(note) > MAX_NOTE_CHARS:
        raise HostRunnerError("步骤备注过长", code="note_too_long")
    prompt = f"/icode {step}"
    if step == "verify":
        parsed = parse_verify_note(note, ticket_id)
        action = parsed["action"]
        if action != "default":
            prompt += " --" + ("test" if action == "device_test" else action)
            if action == "device_test":
                prompt += " " + shlex.quote(parsed["target"])
        for option in ("ticket", "reuse"):
            if parsed[option]:
                prompt += " --" + option + " " + shlex.quote(parsed[option])
    if ticket_id is not None:
        if not isinstance(ticket_id, str) or not ticket_id \
                or len(ticket_id) > 240 or any(ord(char) < 32 for char in ticket_id):
            raise HostRunnerError("ticket_id 格式非法", code="invalid_ticket_id")
        prompt += (
            "\n\nUI 已锁定对象工单 ticket_id = "
            f"{json.dumps(ticket_id, ensure_ascii=False)}。"
            "必须用控制面按此 ticket_id 唯一解析，禁止改用 latest 或新建工单。"
        )
    if note:
        prompt += f"\n\n用户补充说明：\n{note}"
    return prompt


class ControlLifecycle:
    """Host CLI 调用前后唯一通过 ICODE 控制面写 Agent 生命周期。"""

    def __init__(self, control_path):
        self.control_path = str(control_path)

    def _control(self, args: List[str]) -> Dict:
        proc = subprocess.run(
            [sys.executable, self.control_path, *args],
            text=True,
            capture_output=True,
            check=False,
        )
        raw = proc.stdout.strip() or proc.stderr.strip()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise HostRunnerError(
                "控制面未返回合法 JSON", code="control_plane_error") from exc
        if proc.returncode != 0 or not payload.get("ok"):
            gate = payload.get("gate_id", "control_plane")
            raise HostRunnerError(
                f"控制面拒绝请求（{gate}）", code=str(gate))
        return payload

    def projection(self, ticket):
        return self._control([
            "action-policy", "--dir", str(ticket.out_dir),
        ])

    def action_policy(self, ticket, action, expected_revision, verify_action=None):
        args = [
            "action-policy", "--dir", str(ticket.out_dir),
            "--action", action,
            "--expected-revision", expected_revision,
        ]
        if verify_action is not None:
            args += ["--verify-action", verify_action]
        return self._control(args)

    def record_spawn(self, ticket, *, step, host, model, request_id):
        return self._control([
            "record-agent-spawn", "--dir", str(ticket.out_dir),
            "--task-scope", f"UI /icode {step}",
            "--expected-artifact", "ICODE host step terminal receipt",
            "--evidence-boundary", "selected ticket and validated execution root",
            "--join-condition", "host process terminal state recorded",
            "--backend", f"{host}-cli",
            "--model", model or "host-default",
            "--capability", "text", "--capability", "tools",
            "--exclusive-key", "ui-step-run",
            "--request-id", f"{request_id}:spawn",
            "--actor", "system",
        ])

    def record_result(self, ticket, *, spawn_id, result, adopted,
                      reason, evidence_ref, summary, request_id,
                      error_class=None):
        args = [
            "record-agent-result", "--dir", str(ticket.out_dir),
            "--spawn-id", spawn_id,
            "--result", result,
            "--adopted", adopted,
            "--adoption-reason", reason,
            "--evidence-ref", evidence_ref,
            "--summary", summary,
            "--request-id", f"{request_id}:result",
            "--actor", "system",
        ]
        if error_class:
            args += ["--error-class", error_class]
        return self._control(args)

    def create_next(self, *, workspace, requirement, request_id, index_path):
        return self._control([
            "create-next", "--workspace", str(workspace),
            "--requirement", requirement,
            "--request-id", request_id,
            "--index", str(index_path),
        ])


class HostJobRunner:
    """在内存中管理有界宿主进程；不持久化 prompt 或输出。"""

    def __init__(self, *, lifecycle,
                 executable_resolver: Callable[[str], str | None] | None = None,
                 process_factory: Callable | None = None,
                 max_concurrent: int = 3,
                 max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                 max_jobs: int = 100,
                 recovery_path: Path | str | None = None):
        if max_concurrent < 1 or max_jobs < 1 or max_output_bytes < 1:
            raise ValueError("主机任务上限必须是正整数")
        self.executable_resolver = executable_resolver or shutil.which
        self.process_factory = process_factory or subprocess.Popen
        self.lifecycle = lifecycle
        self.max_concurrent = max_concurrent
        self.max_output_bytes = max_output_bytes
        self.max_jobs = max_jobs
        self.recovery_path = Path(recovery_path).expanduser() \
            if recovery_path is not None else None
        self.recovery_available = self.recovery_path is not None
        self._jobs: Dict[str, Dict] = {}
        self._request_ids: set[str] = set()
        self._events: List[Dict] = []
        self._event_seq = 0
        self._lock = threading.RLock()
        self._load_recovery()

    @staticmethod
    def _redact(value: str, *, limit: int | None = 2000) -> str:
        text = value.replace("\x00", "").strip()
        for pattern in SECRET_PATTERNS:
            if pattern.groups:
                text = pattern.sub(r"\1[已隐藏]", text)
            else:
                text = pattern.sub("[已隐藏]", text)
        return text if limit is None else text[:limit]

    @classmethod
    def _progress_message(cls, raw_line: str) -> str:
        """把不同宿主的 JSONL 收敛为短文本，不向 UI 倒出原始对象。"""
        line = raw_line.strip()
        if not line:
            return ""
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            return cls._redact(line)

        def find_text(value):
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                for item in value:
                    found = find_text(item)
                    if found:
                        return found
            if isinstance(value, dict):
                for key in ("text", "message", "summary", "result", "content"):
                    if key in value:
                        found = find_text(value[key])
                        if found:
                            return found
            return None

        message = find_text(payload)
        if not message and isinstance(payload, dict):
            message = payload.get("type")
        return cls._redact(message or "宿主返回进度事件")

    def _emit_locked(self, job: Dict, kind: str, message: str) -> None:
        safe_message = self._redact(message)
        if not safe_message:
            return
        self._event_seq += 1
        event = {
            "seq": self._event_seq,
            "at": _now_iso(),
            "job_id": job["job_id"],
            "ticket_id": job["ticket_id"],
            "kind": kind,
            "message": safe_message,
            "state": job["state"],
        }
        self._events.append(event)
        if len(self._events) > MAX_PUBLIC_EVENTS:
            del self._events[:-MAX_PUBLIC_EVENTS]
        job["last_event_message"] = safe_message
        job["event_count"] = int(job.get("event_count", 0)) + 1

    def _persist_locked(self) -> None:
        if self.recovery_path is None:
            return
        path = self.recovery_path
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": RECOVERY_SCHEMA_VERSION,
                "updated_at": _now_iso(),
                "jobs": [
                    {key: value for key, value in job.items()
                     if key in PERSISTED_JOB_FIELDS}
                    for job in sorted(
                        self._jobs.values(), key=lambda item: item["created_at"],
                        reverse=True)[:self.max_jobs]
                ],
            }
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
            self.recovery_available = True
        except OSError:
            # 恢复投影是观测增强，失败不得把已经启动的宿主进程变成孤儿。
            self.recovery_available = False
            try:
                if temp.is_file():
                    temp.unlink()
            except OSError:
                pass

    def _load_recovery(self) -> None:
        if self.recovery_path is None or not self.recovery_path.is_file():
            return
        try:
            payload = json.loads(self.recovery_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if payload.get("schema_version") != RECOVERY_SCHEMA_VERSION \
                or not isinstance(payload.get("jobs"), list):
            return
        changed = False
        for candidate in payload["jobs"][:self.max_jobs]:
            if not isinstance(candidate, dict):
                continue
            job_id = candidate.get("job_id")
            request_id = candidate.get("request_id")
            required_text = (job_id, request_id, candidate.get("ticket_id"),
                             candidate.get("step"), candidate.get("host"),
                             candidate.get("created_at"))
            if any(not isinstance(value, str) or not value for value in required_text):
                continue
            job = {key: candidate[key] for key in PERSISTED_JOB_FIELDS
                   if key in candidate}
            job.pop("output", None)
            if job.get("state") not in TERMINAL_STATES:
                job["state"] = "outcome_unknown"
                job["recovery_reason"] = "runtime_restarted"
                job["finished_at"] = _now_iso()
                job["returncode"] = None
                job["cancel_requested"] = False
                changed = True
            self._jobs[job_id] = job
            self._request_ids.add(request_id)
            if job.get("recovery_reason") == "runtime_restarted":
                self._emit_locked(job, "recovered", "Runtime 已重启，原任务结果待人工核实")
        if changed:
            self._persist_locked()

    def capabilities(self) -> Dict:
        return {
            "codex": bool(self.executable_resolver("codex")),
            "claude": bool(self.executable_resolver("claude")),
        }

    def _choose_host(self, settings: Dict) -> tuple[str, str]:
        requested = settings.get("host", "auto")
        fallback = settings.get("fallback", True)
        if requested not in {"auto", "codex", "claude"}:
            raise HostRunnerError("host 设置非法", code="invalid_host")
        if not isinstance(fallback, bool):
            raise HostRunnerError("fallback 设置非法", code="invalid_host")

        order = ["codex", "claude"] if requested == "auto" else [requested]
        if requested != "auto" and fallback:
            order.append("claude" if requested == "codex" else "codex")
        for host in order:
            executable = self.executable_resolver(host)
            if executable:
                return host, executable
        raise HostRunnerError("Codex 和 Claude Code 均不可用", code="host_unavailable")

    @staticmethod
    def _command(host: str, executable: str, model: str,
                 project_path: str) -> List[str]:
        if host == "codex":
            argv = [
                executable, "exec", "--json", "-C", project_path,
                "--sandbox", "workspace-write", "--ephemeral",
            ]
            if model:
                argv += ["-m", model]
            return argv + ["-"]
        argv = [
            executable, "-p", "--verbose", "--output-format", "stream-json",
            "--input-format", "text", "--permission-mode", "acceptEdits",
            "--no-session-persistence",
        ]
        if model:
            argv += ["--model", model]
        return argv

    def _active_jobs(self) -> List[Dict]:
        return [job for job in self._jobs.values() if job["state"] not in TERMINAL_STATES]

    def _prune(self) -> None:
        if len(self._jobs) < self.max_jobs:
            return
        terminal = sorted(
            (job for job in self._jobs.values() if job["state"] in TERMINAL_STATES),
            key=lambda job: job["created_at"],
        )
        while len(self._jobs) >= self.max_jobs and terminal:
            expired = terminal.pop(0)
            self._jobs.pop(expired["job_id"], None)

    def start(self, ticket: ResolvedTicket, *, step: str, note: str,
              settings: Dict, request_id: str, expected_revision: str) -> Dict:
        prompt = build_icode_prompt(step, note, ticket.ticket_id)
        if not ticket.executable:
            raise HostRunnerError("该工单当前只读，不允许执行步骤", code="ticket_read_only")
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            raise HostRunnerError("request_id 格式非法", code="invalid_request_id")
        if not isinstance(expected_revision, str) \
                or re.fullmatch(r"[0-9a-f]{64}", expected_revision) is None:
            raise HostRunnerError("expected_revision 格式非法", code="invalid_revision")
        model = settings.get("model", "")
        if not isinstance(model, str) or len(model) > 160 or model.startswith("-") \
                or any(ord(char) < 32 for char in model):
            raise HostRunnerError("model 设置非法", code="invalid_model")

        with self._lock:
            if request_id in self._request_ids:
                raise HostRunnerError("request_id 已使用，拒绝重放", code="duplicate_request")
            active = self._active_jobs()
            if any(job["ticket_id"] == ticket.ticket_id for job in active):
                raise HostRunnerError("该工单已有活动任务", code="ticket_busy")
            if len(active) >= self.max_concurrent:
                raise HostRunnerError("UI 主机任务已达到并发上限", code="global_busy")
            self._prune()
            if len(self._jobs) >= self.max_jobs:
                raise HostRunnerError("任务历史已满且仍在运行", code="job_capacity")
            host, executable = self._choose_host(settings)
            policy_options = {"verify_action": parse_verify_note(note, ticket.ticket_id)["action"]} \
                if step == "verify" else {}
            policy = self.lifecycle.action_policy(ticket, step, expected_revision, **policy_options)
            execution_root = policy.get("execution_root")
            if not isinstance(execution_root, str) or not execution_root:
                raise HostRunnerError(
                    "控制面未返回可信执行根", code="execution_root_topology")
            job_id = f"job-{uuid.uuid4()}"
            spawn_payload = self.lifecycle.record_spawn(
                ticket, step=step, host=host, model=model, request_id=request_id)
            if spawn_payload.get("already_applied"):
                raise HostRunnerError(
                    "控制面已记录相同调用，拒绝重放模型请求",
                    code="duplicate_request")
            spawn = spawn_payload.get("spawn") or {}
            spawn_id = spawn.get("spawn_id")
            if not isinstance(spawn_id, str) or not spawn_id:
                raise HostRunnerError(
                    "控制面 spawn 回执缺身份", code="control_plane_error")
            argv = self._command(host, executable, model, execution_root)
            try:
                process = self.process_factory(
                    argv,
                    cwd=execution_root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    shell=False,
                    start_new_session=True,
                )
            except OSError as exc:
                # 进入 process_factory 后结果可能不明确，绝不尝试另一宿主重放。
                try:
                    self.lifecycle.record_result(
                        ticket, spawn_id=spawn_id, result="failed", adopted="no",
                        reason="Host process spawn failed; no replay attempted",
                        evidence_ref=f"ui-job:{job_id}",
                        summary=f"spawn failed for {job_id}",
                        request_id=request_id, error_class="spawn_failed")
                except HostRunnerError as receipt_error:
                    raise HostRunnerError(
                        "主机进程启动失败，且控制面终态回执失败；未自动重放",
                        code="ambiguous_spawn_failure") from receipt_error
                raise HostRunnerError("主机进程启动失败；未自动重放", code="spawn_failed") from exc

            job = {
                "job_id": job_id,
                "request_id": request_id,
                "ticket_id": ticket.ticket_id,
                "step": step,
                "host": host,
                "model": model,
                "state": "running",
                "created_at": _now_iso(),
                "finished_at": None,
                "returncode": None,
                "output": "",
                "truncated": False,
                "cancel_requested": False,
                "spawn_id": spawn_id,
                "last_event_message": "任务已提交给宿主",
                "event_count": 0,
                "_process": process,
                "_prompt": prompt,
                "_ticket": ticket,
            }
            self._jobs[job_id] = job
            self._request_ids.add(request_id)
            self._emit_locked(job, "started", "任务已提交给宿主")
            self._persist_locked()
            thread = threading.Thread(
                target=self._collect,
                args=(job_id,),
                name=f"icode-ui-{job_id}",
                daemon=True,
            )
            thread.start()
            return self._public(job)

    def _bounded_output(self, value) -> tuple[str, bool]:
        text = self._redact(value, limit=None) if isinstance(value, str) else ""
        raw = text.encode("utf-8", errors="replace")
        if len(raw) <= self.max_output_bytes:
            return text, False
        return raw[:self.max_output_bytes].decode("utf-8", errors="ignore"), True

    def _collect(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            process = job["_process"]
            prompt = job["_prompt"]
            ticket = job["_ticket"]
        collection_error = None
        try:
            stdin = getattr(process, "stdin", None)
            stdout = getattr(process, "stdout", None)
            if stdin is not None and stdout is not None:
                stdin.write(prompt)
                stdin.flush()
                # 真正的 Popen pipe 必须关闭 stdin 才能让宿主收到 EOF；
                # StringIO 仅用于可重复读取的单测，不关闭。
                if not isinstance(stdin, io.StringIO):
                    stdin.close()
                chunks = []
                used = 0
                truncated = False
                while True:
                    line = stdout.readline()
                    if line == "":
                        break
                    message = self._progress_message(line)
                    if message:
                        with self._lock:
                            current = self._jobs[job_id]
                            self._emit_locked(current, "progress", message)
                    raw = line.encode("utf-8", errors="replace")
                    remaining = self.max_output_bytes - used
                    if remaining > 0:
                        part = raw[:remaining]
                        chunks.append(part)
                        used += len(part)
                    if len(raw) > max(remaining, 0):
                        truncated = True
                returncode = process.wait()
                collected = b"".join(chunks).decode("utf-8", errors="ignore")
                bounded, redacted_truncated = self._bounded_output(collected)
                truncated = truncated or redacted_truncated
            else:
                output, _ = process.communicate(input=prompt)
                returncode = process.poll()
                if returncode is None:
                    returncode = getattr(process, "returncode", 1)
                bounded, truncated = self._bounded_output(output)
                message = self._progress_message(bounded)
                if message:
                    with self._lock:
                        current = self._jobs[job_id]
                        self._emit_locked(current, "progress", message)
        except Exception as exc:
            collection_error = type(exc).__name__
            bounded = f"主机输出收集失败（{collection_error}）"
            truncated = False
            returncode = getattr(process, "returncode", None)

        with self._lock:
            job = self._jobs[job_id]
            job["output"] = bounded
            job["truncated"] = truncated
            job["returncode"] = returncode
            if job["cancel_requested"]:
                terminal_state = "cancelled"
            elif collection_error or returncode != 0:
                terminal_state = "failed"
            else:
                terminal_state = "succeeded"
            # 只有控制面 agent_result 已持久化后，才向 UI 暴露终态。
            job["state"] = "finalizing"
            self._emit_locked(job, "finalizing", "宿主已结束，正在写入控制面回执")
            job.pop("_prompt", None)
            spawn_id = job["spawn_id"]
            request_id = job["request_id"]
            self._persist_locked()

        result = "joined" if terminal_state == "succeeded" else (
            "stopped" if terminal_state == "cancelled" else "failed")
        adopted = "yes" if result == "joined" else "no"
        reason = {
            "joined": "Host process completed and returned a terminal receipt",
            "stopped": "User cancelled the UI host process",
            "failed": "Host process failed or output collection was incomplete",
        }[result]
        error_class = collection_error or (
            "host_nonzero_exit" if result == "failed" else None)
        try:
            self.lifecycle.record_result(
                ticket, spawn_id=spawn_id, result=result, adopted=adopted,
                reason=reason, evidence_ref=f"ui-job:{job_id}",
                summary=f"{terminal_state} job={job_id} output_bytes={len(bounded.encode('utf-8'))}",
                request_id=request_id, error_class=error_class)
            with self._lock:
                job = self._jobs[job_id]
                job["state"] = terminal_state
                job["finished_at"] = _now_iso()
                self._emit_locked(
                    job, "terminal",
                    "任务执行成功" if terminal_state == "succeeded" else (
                        "任务已取消" if terminal_state == "cancelled" else "任务执行失败"))
                self._persist_locked()
        except Exception:
            with self._lock:
                job = self._jobs[job_id]
                job["state"] = "failed"
                job["finished_at"] = _now_iso()
                job["lifecycle_error"] = True
                job["output"] = "主机已结束，但控制面终态回执失败；禁止重放。"
                self._emit_locked(job, "terminal", "终态回执失败，禁止自动重放")
                self._persist_locked()

    @staticmethod
    def _public(job: Dict) -> Dict:
        return {key: value for key, value in job.items() if not key.startswith("_")}

    def get(self, job_id: str) -> Dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise HostRunnerError("任务不存在", code="job_not_found")
            return self._public(job)

    def list(self) -> List[Dict]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda job: job["created_at"], reverse=True)
            return [self._public(job) for job in jobs]

    def events(self, *, after: int = 0,
               ticket_id: str | None = None) -> Dict:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise HostRunnerError("事件游标非法", code="invalid_event_cursor")
        with self._lock:
            events = [dict(event) for event in self._events
                      if event["seq"] > after
                      and (ticket_id is None or event["ticket_id"] == ticket_id)]
            return {"cursor": self._event_seq, "events": events}

    def cancel(self, job_id: str) -> Dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise HostRunnerError("任务不存在", code="job_not_found")
            if job["state"] in TERMINAL_STATES:
                return self._public(job)
            job["cancel_requested"] = True
            self._emit_locked(job, "cancelling", "正在请求宿主停止任务")
            self._persist_locked()
            process = job["_process"]
        try:
            pid = getattr(process, "pid", None)
            if isinstance(pid, int) and pid > 0:
                os.killpg(pid, signal.SIGTERM)
            else:
                process.terminate()
        except OSError as exc:
            raise HostRunnerError("任务终止请求失败", code="cancel_failed") from exc
        return self.get(job_id)
