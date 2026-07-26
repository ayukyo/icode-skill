"""把 vision-bridge MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走 config.json。

用法:
    python3 scripts/register_mcp.py <venv_python> <server_py> <vision_bridge_dir>

示例:
    python3 scripts/register_mcp.py \\
        <your_install_path>/.venv/bin/python \\
        <your_install_path>/server.py \\
        <your_install_path>
"""
import json
import sys
from pathlib import Path

CLAUDE_JSON = Path.home() / ".claude.json"


def _configure_utf8_stdout() -> None:
    """强制 stdout/stderr 用 UTF-8,兼容 Windows 默认 GBK 控制台。

    Linux/macOS 默认就是 UTF-8,reconfigure 是 no-op,不影响行为。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main():
    if len(sys.argv) != 4:
        print("用法: register_mcp.py <python> <server.py> <vision_bridge_dir>")
        sys.exit(1)

    python_path, server_py, vb_dir = sys.argv[1], sys.argv[2], sys.argv[3]

    if CLAUDE_JSON.exists():
        cfg = json.loads(CLAUDE_JSON.read_text())
    else:
        cfg = {}
    cfg.setdefault("mcpServers", {})

    cfg["mcpServers"]["vision-bridge"] = {
        "command": python_path,
        "args": [server_py],
        "cwd": vb_dir,
        "env": {
            # 注意:这里只放 config.json 路径, 不放任何 KEY
            "VISION_BRIDGE_CONFIG": f"{vb_dir}/config.json",
        },
    }
    CLAUDE_JSON.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

    _configure_utf8_stdout()
    print(f"✅ 已注册 vision-bridge 到 {CLAUDE_JSON}")
    print(f"   python: {python_path}")
    print(f"   server: {server_py}")
    print(f"   config: {vb_dir}/config.json  (⚠️ 不含 KEY 留痕)")
    print(f"   ⚠️ 重启 Claude Code 后生效")


if __name__ == "__main__":
    main()
