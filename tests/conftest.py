"""pytest 全局配置：按本机能力跳过不可执行的用例。

背景：一部分 UI 用例会拉起本地 HTTP server 再连 `127.0.0.1`。某些 Windows 环境
（防火墙 / WFP 策略）会拦截 loopback TCP，表现为 connect 超时——此时用例失败与
被测代码无关。这里先探测本机 loopback 是否可用，不可用时把依赖它的模块整体标记
为 skip，并在 skip 原因里写清楚，避免给出与代码无关的假红。

探测放在 collection 阶段只做一次（socket 自连，1 秒超时），不引入任何依赖。
"""
import socket

import pytest

# 依赖本地 HTTP server（create_ui_server / dashboard 夹具）的模块。
_LOOPBACK_MODULES = (
    "test_agent_ui.py",
    "test_agent_ui_dashboard.py",
    "test_agent_ui_manager_v2.py",
    "test_doc_library.py",
    "test_doc_reader_assets.py",
    "test_stage_preview.py",
    "test_stage_ui.py",
    "test_ui_launcher_portability.py",
)


def _loopback_available() -> bool:
    """用一次真实的 listen+connect 判断 loopback TCP 是否可用。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(1.0)
        try:
            client.connect(("127.0.0.1", port))
            return True
        finally:
            client.close()
    except OSError:
        return False
    finally:
        server.close()


_LOOPBACK_OK = _loopback_available()


def pytest_collection_modifyitems(config, items):
    if _LOOPBACK_OK:
        return
    reason = "本机 loopback TCP 不可用（防火墙/策略拦截），无法拉起本地 HTTP server"
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        # item.fspath 在 pytest 8+ 已移除；优先用 path，再退回 fspath。
        path = getattr(item, "path", None)
        if path is None:
            path = getattr(item, "fspath", None)
        name = getattr(path, "name", None) or str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
        if name in _LOOPBACK_MODULES:
            item.add_marker(skip)