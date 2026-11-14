"""启动实际 CLI 隔离审核；不接受调用者提供的 PASS 或 invocation 字符串。"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def run_review(backend, model, workspace, prompt, timeout=600):
    executable = shutil.which(backend)
    if backend not in {"codex", "claude"} or not executable:
        raise ValueError("独立审核 CLI 不可用：" + str(backend))
    with tempfile.TemporaryDirectory(prefix="icode-lite-review-") as temporary:
        output = Path(temporary) / "response.json"
        if backend == "codex":
            command = [executable, "exec", "--json", "--ephemeral", "--sandbox", "read-only",
                       "-C", str(workspace), "--output-last-message", str(output)]
            if model:
                command += ["-m", model]
            command += ["-"]
        else:
            command = [executable, "-p", "--output-format", "json", "--no-session-persistence",
                       "--permission-mode", "plan"]
            if model:
                command += ["--model", model]
        with (Path(temporary) / "stdout").open("w+b") as log:
            proc = subprocess.run(command, input=prompt.encode("utf-8"), stdout=log,
                                  stderr=subprocess.STDOUT, cwd=workspace, timeout=timeout)
            size = log.tell()
            if size > 2 * 1024 * 1024:
                raise ValueError("独立审核返回超过预算")
            log.seek(0)
            raw = log.read().decode("utf-8")
        if proc.returncode:
            raise ValueError("独立审核 CLI 非零退出：" + str(proc.returncode))
        if backend == "codex":
            messages = [json.loads(line) for line in raw.splitlines() if line.strip()]
            identity = next((m.get("thread_id") for m in messages if m.get("type") == "thread.started"), None)
            if not output.is_file() or output.stat().st_size > 256 * 1024:
                raise ValueError("独立审核未返回有效报告")
            report_text = output.read_text(encoding="utf-8")
        else:
            result = json.loads(raw)
            identity = result.get("session_id")
            report_text = result.get("result", "")
            if result.get("is_error"):
                raise ValueError("独立审核宿主报告错误")
        if not isinstance(identity, str) or not identity:
            raise ValueError("独立审核未返回宿主 session identity")
        report = json.loads(report_text)
        if not isinstance(report, dict):
            raise ValueError("独立审核报告不是 JSON 对象")
        return {"host_session_id": identity, "backend": backend, "fresh_context": True,
                "report": report, "returncode": proc.returncode}
