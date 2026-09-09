"""注册一个无密钥的本地 Python MCP。"""

from __future__ import annotations

import sys
from pathlib import Path

from claude_registry import register


def main() -> int:
    if len(sys.argv) != 6:
        print("用法: register_python_mcp.py <name> <python> <server.py> <cwd> <config-env>")
        return 2
    name, python_path, server_path, cwd, config_env = sys.argv[1:]
    entry = {
        "command": python_path,
        "args": [server_path],
        "cwd": cwd,
        "env": {config_env: str(Path(cwd) / "config.json")},
    }
    register(name, entry)
    print(f"✅ 已注册 {name}（配置仅保存路径，不保存密钥）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
