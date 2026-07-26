"""serena MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走 config.json(本工具无 KEY)。

用法:
    python3 scripts/register_mcp.py

serena 区别于 npm 类 MCP:
- 不安装 npm 包, 改用 uvx --from git+https://github.com/oraios/serena
- 首次启动时 uvx 自动从 git clone serena (~50MB)
- 启动命令是 serena start-mcp-server (而非节点 app)
- 需要 Python 3.10+ 和 uv (由 install.sh 探测)
"""
import json
import shutil
import sys
from pathlib import Path

CLAUDE_JSON = Path.home() / ".claude.json"
SERVER_NAME = "serena"

# 官方仓库 URL 和启动命令(serena-mcp-server 内嵌在 serena CLI)
SOURCE_URL = "git+https://github.com/oraios/serena"
ENTRY_CMD = ["serena", "start-mcp-server"]


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


def _resolve_executable(name: str) -> str | None:
    """用 shutil.which 解析可执行文件, 自动处理 MSYS 路径和 PATHEXT。

    uv 装到 ~/.local/bin 后通常不在 PATH (PowerShell 装的不加 PATH),
    所以用 fallback 显式探测 ~/.local/bin / ~/.cargo/bin。
    """
    # 1. PATH 探测
    path = shutil.which(name)
    if path:
        return path
    # 2. fallback 路径
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / f"{name}.exe",
        home / ".local" / "bin" / name,
        home / ".cargo" / "bin" / f"{name}.exe",
        home / ".cargo" / "bin" / name,
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def main():
    _configure_utf8_stdout()

    # serena register_mcp.py 无外部参数(uvx 路径由内部 fallback 探测)
    uvx_path = _resolve_executable("uvx")
    if not uvx_path:
        print("❌ uvx 不可执行。请先装 uv (https://docs.astral.sh/uv/)")
        print("   常见原因: uv 装到 ~/.local/bin/ 但该路径不在 PATH")
        print("   手动修复: export PATH=\"$HOME/.local/bin:$PATH\"")
        sys.exit(1)

    if CLAUDE_JSON.exists():
        cfg = json.loads(CLAUDE_JSON.read_text())
    else:
        cfg = {}
    cfg.setdefault("mcpServers", {})

    cfg["mcpServers"][SERVER_NAME] = {
        "command": uvx_path,
        "args": ["--from", SOURCE_URL] + ENTRY_CMD,
    }
    CLAUDE_JSON.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

    print(f"✅ 已注册 {SERVER_NAME} 到 {CLAUDE_JSON}")
    print(f"   uvx: {uvx_path}")
    print(f"   args: --from {SOURCE_URL} {' '.join(ENTRY_CMD)}")
    print(f"   ⚠️ 重启 Claude Code 后生效")
    print(f"   ⚠️ 首次启动会从 git clone serena (~50MB)")


if __name__ == "__main__":
    main()
