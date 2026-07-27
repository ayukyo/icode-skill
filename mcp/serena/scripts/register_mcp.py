"""serena MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走 config.json(本工具无 KEY)。

用法:
    python3 scripts/register_mcp.py

serena 区别于 npm 类 MCP:
- 不安装 npm 包, 改用 uv tool install (持久化安装, 避免每次启动 git fetch)
- 首次 install 时 uv 从 git clone serena (~50MB) 装到 uv tool cache
- 后续启动用 serena start-mcp-server (秒启, 无 git fetch)
- 需要 Python 3.10+ 和 uv (由 install.sh 探测)

v2.1+ 优化 (vs 旧版 uvx --from git+...):
- 旧版: 每次启动都 git fetch + 装包 (~25-90s)
- 新版: 首次 install 一次, 后续启动秒级 (从 uv tool cache 读)
"""
import json
import shutil
import subprocess
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


def _serena_installed_via_uv_tool() -> bool:
    """检测 serena 是否已通过 uv tool install 持久化安装"""
    uv_path = _resolve_executable("uv")
    if not uv_path:
        return False
    try:
        r = subprocess.run(
            [uv_path, "tool", "list"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and "serena" in r.stdout
    except Exception:
        return False


def _install_serena_via_uv_tool() -> bool:
    """用 uv tool install 持久化安装 serena (避免每次启动 git fetch)

    注意: serena 仓库的 Python 包名是 'serena-agent' (不是 'serena'),
    但安装后会提供 'serena' CLI 入口。所以 --from 后面跟包名 'serena-agent',
    而 ENTRY_CMD 用 'serena' (CLI 名)。
    """
    uv_path = _resolve_executable("uv")
    if not uv_path:
        print("❌ uv 不可执行,无法 uv tool install")
        return False
    print("📦 首次安装: uv tool install --from git+... serena-agent")
    print("   (一次性 git clone ~50MB + 装依赖, 后续启动秒级)")
    try:
        # 包名 serena-agent (提供 serena CLI)
        r = subprocess.run(
            [uv_path, "tool", "install", "--from", SOURCE_URL, "serena-agent"],
            timeout=300,  # 5 分钟超时 (含 git clone)
        )
        return r.returncode == 0
    except Exception as e:
        print(f"❌ uv tool install 失败: {e}")
        return False


def main():
    _configure_utf8_stdout()

    # v2.1+: 优先用 uv tool install 持久化安装 (避免每次启动 git fetch)
    # 检测是否已装
    serena_bin = _resolve_executable("serena")
    if not serena_bin and not _serena_installed_via_uv_tool():
        # 首次安装
        if not _install_serena_via_uv_tool():
            print("⚠️  uv tool install 失败,回退到旧版 uvx --from 模式")
            print("   (每次启动会 git fetch ~25-90s, 但功能正常)")
            # 旧版 fallback: 用 uvx --from
            uvx_path = _resolve_executable("uvx")
            if not uvx_path:
                print("❌ uvx 也不可用。请先装 uv (https://docs.astral.sh/uv/)")
                sys.exit(1)
            _register_with_uvx_fallback(uvx_path)
            return
        # 装完重新探测 serena 路径
        serena_bin = _resolve_executable("serena")

    if CLAUDE_JSON.exists():
        cfg = json.loads(CLAUDE_JSON.read_text())
    else:
        cfg = {}
    cfg.setdefault("mcpServers", {})

    if serena_bin:
        # v2.1+ 新版: 直接用本地 serena 命令 (秒启, 无 git fetch)
        cfg["mcpServers"][SERVER_NAME] = {
            "command": serena_bin,
            "args": ["start-mcp-server"],
        }
        CLAUDE_JSON.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        print(f"✅ 已注册 {SERVER_NAME} 到 {CLAUDE_JSON}")
        print(f"   command: {serena_bin}")
        print(f"   args: start-mcp-server")
        print(f"   ⚠️ 重启 Claude Code 后生效")
        print(f"   ✅ v2.1+ 优化: 已持久化安装, 启动秒级 (无 git fetch)")
    else:
        # 不应到达此处 (uv tool install 成功但 serena 不在 PATH)
        print("⚠️  uv tool install 成功但 serena 不在 PATH")
        print("   回退到 uvx --from 模式")
        uvx_path = _resolve_executable("uvx")
        if not uvx_path:
            print("❌ uvx 不可用")
            sys.exit(1)
        _register_with_uvx_fallback(uvx_path)


def _register_with_uvx_fallback(uvx_path: str) -> None:
    """旧版 fallback: uvx --from git+... (每次启动 git fetch)"""
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
    print(f"✅ 已注册 {SERVER_NAME} 到 {CLAUDE_JSON} (uvx fallback 模式)")
    print(f"   uvx: {uvx_path}")
    print(f"   args: --from {SOURCE_URL} {' '.join(ENTRY_CMD)}")
    print(f"   ⚠️ 重启 Claude Code 后生效")
    print(f"   ⚠️ 每次启动会从 git fetch serena (~25-90s)")


if __name__ == "__main__":
    main()
