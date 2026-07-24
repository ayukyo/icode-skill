"""把 vision-bridge MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走 config.json。

用法:
    python3 scripts/register_mcp.py <venv_python> <server_py> <vision_bridge_dir>

示例:
    python3 scripts/register_mcp.py \\
        /home/orbbec/skills/icode/mcp/vision-bridge/.venv/bin/python \\
        /home/orbbec/skills/icode/mcp/vision-bridge/server.py \\
        /home/orbbec/skills/icode/mcp/vision-bridge
"""
import json
import sys
from pathlib import Path

CLAUDE_JSON = Path.home() / ".claude.json"


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

    print(f"✅ 已注册 vision-bridge 到 {CLAUDE_JSON}")
    print(f"   python: {python_path}")
    print(f"   server: {server_py}")
    print(f"   config: {vb_dir}/config.json  (⚠️ 不含 KEY 留痕)")
    print(f"   ⚠️ 重启 Claude Code 后生效")


if __name__ == "__main__":
    main()
