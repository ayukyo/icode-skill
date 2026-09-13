#!/usr/bin/env python3
"""ICODE Agent Runtime CLI；现有 /icode 主会话不会自动调用本入口。"""

import argparse
import errno
import fcntl
import http.client
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = ROOT / "agent_runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from icode_agent.backends.base import BackendError
from icode_agent.backends.fake import FakeBackend
from icode_agent.backends.openai_responses import OpenAIResponsesBackend
from icode_agent.coordinator import (
    AgentCoordinator,
    ControlPlaneError,
    LifecycleAmbiguityError,
)
from icode_agent.models import AgentRequest, CapabilityError


UI_INSTANCE_NAME = "ui_instance.json"
UI_START_LOCK_NAME = ".ui_start.lock"
UI_SERVICE_NAME = "icode-agent-ui"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icode_agent.py",
        description="ICODE 可选 Agent Runtime（单工单、单模型回合）",
    )
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="只读输出版本化 Agent 生命周期状态")
    status.add_argument("--dir", required=True, help="v3 工单目录")
    status.set_defaults(func=cmd_status)

    run = sub.add_parser("run", help="执行一个有界模型回合，不自动推进工单 status")
    run.add_argument("--dir", required=True, help="v3 工单目录")
    run.add_argument("--backend", required=True,
                     choices=["fake", "openai-responses"])
    run.add_argument("--model", required=True)
    run.add_argument("--capability", action="append", required=True,
                     choices=["text", "image", "tools", "reasoning"])
    run.add_argument("--prompt", required=True)
    run.add_argument("--instructions", default="")
    run.add_argument("--image", action="append", default=[])
    run.add_argument("--max-output-tokens", type=int, default=2048)
    run.add_argument("--task-scope", required=True)
    run.add_argument("--expected-artifact", required=True)
    run.add_argument("--evidence-boundary", required=True)
    run.add_argument("--join-condition", required=True)
    run.add_argument("--request-id", required=True,
                     help="模型回合稳定幂等键；重复调用不会重放模型请求")
    run.add_argument("--fake-response", default="fake response",
                     help="仅 --backend fake 使用的离线结果")
    run.set_defaults(func=cmd_run)

    ui = sub.add_parser("ui", help="启动仅监听本机的 ICODE 管理 UI")
    ui.add_argument("--dir", help="高级兼容：固定一个 v3 工单目录")
    ui.add_argument("--ticket", help="全局模式初选 ticket_id")
    ui.add_argument("--project", help="全局模式初选 project_id")
    ui.add_argument("--port", type=int,
                    help="显式本机端口；省略时优先设置值 8765，冲突自动换端口")
    ui.add_argument("--no-browser", action="store_true",
                    help="不自动打开默认浏览器")
    ui.set_defaults(func=cmd_ui)
    return parser


def coordinator() -> AgentCoordinator:
    return AgentCoordinator(control_path=ROOT / "tools" / "icode_control.py")


def cmd_status(args) -> int:
    status = coordinator().status(Path(args.dir).resolve())
    print(json.dumps({"ok": True, "status": status}, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    request = AgentRequest(
        ticket_dir=Path(args.dir).resolve(),
        task_scope=args.task_scope,
        expected_artifact=args.expected_artifact,
        evidence_boundary=args.evidence_boundary,
        join_condition=args.join_condition,
        backend=args.backend,
        model=args.model,
        capabilities=frozenset(args.capability),
        prompt=args.prompt,
        request_id=args.request_id,
        instructions=args.instructions,
        images=tuple(args.image),
        max_output_tokens=args.max_output_tokens,
    )
    backend = (FakeBackend(response=args.fake_response)
               if args.backend == "fake" else OpenAIResponsesBackend())
    result = coordinator().run(request, backend)
    print(json.dumps({
        "ok": True,
        "backend": args.backend,
        "model": args.model,
        "result": result.to_dict(),
    }, ensure_ascii=False, indent=2))
    return 0


def bind_ui_server(server_factory, create_kwargs, *, preferred_port: int,
                   explicit_port: bool):
    """绑定首选端口；只有非显式端口发生 EADDRINUSE 时回退到端口 0。"""
    try:
        return server_factory(port=preferred_port, **create_kwargs), False
    except OSError as exc:
        if explicit_port or preferred_port == 0 or exc.errno != errno.EADDRINUSE:
            raise
        return server_factory(port=0, **create_kwargs), True


@contextmanager
def ui_startup_lock(data_dir: Path):
    """串行化短暂启动窗口；锁不覆盖 UI 的整个运行期。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / UI_START_LOCK_NAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_ui_instance(path: Path):
    """读取派生实例登记；损坏/旧格式按 stale 处理，不把内容当可信地址。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) \
            or payload.get("schema_version") != 1 \
            or payload.get("service") != UI_SERVICE_NAME \
            or payload.get("host") != "127.0.0.1" \
            or payload.get("mode") != "global":
        return None
    instance_id = payload.get("instance_id")
    port = payload.get("port")
    pid = payload.get("pid")
    if not isinstance(instance_id, str) or not 16 <= len(instance_id) <= 128:
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return payload


def probe_ui_instance(instance, *, attempts: int = 5):
    """用实例 ID 绑定健康回执，避免复用同端口上的非 ICODE 服务。"""
    if not isinstance(instance, dict):
        return None
    port = instance["port"]
    for attempt in range(attempts):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
        try:
            connection.request("GET", "/api/v1/health", headers={
                "Host": f"127.0.0.1:{port}",
            })
            response = connection.getresponse()
            raw = response.read(4097)
            if response.status != 200 or len(raw) > 4096:
                continue
            health = json.loads(raw.decode("utf-8"))
            if health == {
                "ok": True,
                "schema_version": 1,
                "service": UI_SERVICE_NAME,
                "instance_id": instance["instance_id"],
                "mode": "global",
            }:
                return f"http://127.0.0.1:{port}/"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        finally:
            connection.close()
        if attempt + 1 < attempts:
            time.sleep(0.05)
    return None


def write_ui_instance(path: Path, payload) -> None:
    """原子登记非秘密实例身份；浏览器会话 token 不写磁盘。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def clear_ui_instance(path: Path, instance_id: str) -> None:
    """仅清理由本进程登记的实例，绝不删除后来者的登记。"""
    current = read_ui_instance(path)
    if current is None or current["instance_id"] != instance_id:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def announce_ui(url: str, *, preferred_port: int, fell_back: bool,
                reused: bool, open_browser: bool) -> None:
    import webbrowser

    print(json.dumps({
        "ok": True,
        "service": UI_SERVICE_NAME,
        "schema_version": 1,
        "url": url,
        "preferred_port": preferred_port,
        "port_fallback": fell_back,
        "reused": reused,
    }, ensure_ascii=False), flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            # 浏览器启动失败不影响已就绪的本地服务，用户可复制 URL。
            pass


def launch_project_root(cwd: Path) -> Path:
    """把 UI 启动目录收敛到当前 Git 根；非 Git 工程保留当前目录。"""
    current = Path(cwd).resolve()
    proc = subprocess.run(
        ["git", "-C", str(current), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        candidate = Path(proc.stdout.strip()).resolve()
        if candidate.is_dir():
            return candidate
    return current


def cmd_ui(args) -> int:
    from icode_agent.ui_server import create_ui_server
    from icode_agent.ui_settings import UISettingsStore

    data_dir = Path.home() / ".claude" / "icode_data"
    settings_path = data_dir / "ui_settings.json"
    instance_path = data_dir / UI_INSTANCE_NAME
    settings = UISettingsStore(settings_path).load()
    preferred_port = args.port if args.port is not None else settings["preferred_port"]
    seed_project = launch_project_root(Path.cwd())
    simple_global = (
        args.dir is None and args.ticket is None and args.project is None
        and args.port is None
    )
    registered = False
    with ui_startup_lock(data_dir):
        if simple_global:
            existing = read_ui_instance(instance_path)
            existing_url = probe_ui_instance(existing) if existing else None
            if existing_url:
                announce_ui(
                    existing_url,
                    preferred_port=preferred_port,
                    fell_back=existing["port"] != preferred_port,
                    reused=True,
                    open_browser=not args.no_browser,
                )
                return 0
        server, fell_back = bind_ui_server(
            create_ui_server,
            {
                "ticket_dir": Path(args.dir).resolve() if args.dir else None,
                "control_path": ROOT / "tools" / "icode_control.py",
                "settings_path": settings_path,
                "seed_project_paths": [seed_project],
                "initial_ticket_id": args.ticket,
                "initial_project_id": args.project,
            },
            preferred_port=preferred_port,
            explicit_port=args.port is not None,
        )
        host, port = server.server_address
        url = f"http://{host}:{port}/"
        if simple_global:
            write_ui_instance(instance_path, {
                "schema_version": 1,
                "service": UI_SERVICE_NAME,
                "instance_id": server.ui_instance_id,
                "host": host,
                "port": port,
                "pid": os.getpid(),
                "mode": "global",
            })
            registered = True
    announce_ui(
        url,
        preferred_port=preferred_port,
        fell_back=fell_back,
        reused=False,
        open_browser=not args.no_browser,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if registered:
            clear_ui_instance(instance_path, server.ui_instance_id)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (CapabilityError, BackendError, ControlPlaneError,
            LifecycleAmbiguityError, ValueError, OSError) as exc:
        print(json.dumps({
            "ok": False,
            "error_class": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
