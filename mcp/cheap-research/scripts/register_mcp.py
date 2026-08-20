"""把 cheap-research MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走 config.json。

用法:
    python3 scripts/register_mcp.py <venv_python> <server_py> <cheap_research_dir>

示例:
    python3 scripts/register_mcp.py \\
        <your_install_path>/.venv/bin/python \\
        <your_install_path>/server.py \\
        <your_install_path>
"""
import sys
from pathlib import Path

# 注入跨工程共享的 _lib 路径 (mcp/_lib/)。无论 register_mcp.py 从哪个
# 脚本目录被调用,都能找到 claude_registry.py。scripts/ 的父目录 = 子工程根,
# 父目录的父目录 = mcp/。
_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent.parent / "_lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_registry import register as _claude_register  # noqa: E402

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
        print("用法: register_mcp.py <python> <server.py> <cheap_research_dir>")
        sys.exit(1)

    python_path, server_py, cr_dir = sys.argv[1], sys.argv[2], sys.argv[3]

    # 共享模块: 原子写 + 损坏保护 + 回读校验 + 导出 entry 供 Codex 注册分支读取
    _claude_register("cheap-research", {
        "command": python_path,
        "args": [server_py],
        "cwd": cr_dir,
        "env": {
            # 注意:这里只放 config.json 路径, 不放任何 KEY
            "CHEAP_RESEARCH_CONFIG": f"{cr_dir}/config.json",
        },
    })

    _configure_utf8_stdout()
    print(f"✅ 已注册 cheap-research 到 {CLAUDE_JSON}")
    print(f"   python: {python_path}")
    print(f"   server: {server_py}")
    print(f"   config: {cr_dir}/config.json  (⚠️ 不含 KEY 留痕)")
    print(f"   ⚠️ 重启 Claude Code 后生效")


if __name__ == "__main__":
    main()
