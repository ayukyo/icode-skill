"""把 sequential-thinking MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走环境变量(本工具无 KEY)。

用法:
    python3 scripts/register_mcp.py <node> <npx>

示例:
    python3 scripts/register_mcp.py \\
        /usr/bin/node \\
        /usr/bin/npx

或 (Windows):
    python scripts/register_mcp.py "C:\\Program Files\\nodejs\\node.exe" "C:\\Program Files\\nodejs\\npx.cmd"

默认包名: @modelcontextprotocol/server-sequential-thinking
可通过第三个参数覆盖: <node> <npx> [package_name]
"""
import json
import shutil
import sys
from pathlib import Path

# 注入跨工程共享的 _lib 路径 (mcp/_lib/)。无论 register_mcp.py 从哪个
# 脚本目录被调用,都能找到 platform_entry.py。scripts/ 的父目录 = 子工程根,
# 父目录的父目录 = mcp/。
_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent.parent / "_lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from platform_entry import build_server_entry  # noqa: E402
from claude_registry import register as _claude_register  # noqa: E402

CLAUDE_JSON = Path.home() / ".claude.json"

DEFAULT_PACKAGE = "@modelcontextprotocol/server-sequential-thinking"
SERVER_NAME = "sequential-thinking"
# 默认禁用服务端 thought 的 stderr 输出（隐私基线）。只禁止 stderr 打印，
# 不保证宿主不保存工具调用；thought 本身仍不得含密钥/Cookie/设备凭据。
DEFAULT_ENV = {"DISABLE_THOUGHT_LOGGING": "true"}


def _existing_user_env() -> dict:
    """读取 ~/.claude.json 中 sequential-thinking 已有的 env，用于保留用户显式覆盖。

    用户主动把 DISABLE_THOUGHT_LOGGING 设为 false（开启日志）时，重复执行
    install/register 不得把它静默改回 true（优化文档 §10.4「保持用户显式覆盖能力」）。
    仅当用户从未设置过该键时才回落到默认 DISABLE_THOUGHT_LOGGING=true。
    """
    try:
        cfg = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers") or {}
        existing = servers.get(SERVER_NAME) or {}
        env = existing.get("env") or {}
        if isinstance(env, dict) and "DISABLE_THOUGHT_LOGGING" in env:
            return {"DISABLE_THOUGHT_LOGGING": str(env["DISABLE_THOUGHT_LOGGING"])}
    except (OSError, ValueError):
        # 文件不存在/损坏/非预期结构：走默认，后续由 claude_registry 兜底保护
        pass
    return dict(DEFAULT_ENV)


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


def _resolve_executable(name_or_path: str) -> str | None:
    """解析可执行文件为 native 绝对路径。

    用 shutil.which 而不是 Path.exists,因为:
    - Git Bash/MinGW 下 command -v 返回 MSYS 路径(/d/Program Files/...),
      Path.exists() 不识别;shutil.which 自动转 native Windows 路径。
    - shutil.which 还会按 PATHEXT 自动补 .exe/.cmd(Windows)。
    - Linux/macOS 下行为与 command -v 等价。
    """
    return shutil.which(name_or_path)


def main():
    # 先配置 UTF-8,确保错误分支也能在 Windows GBK 控制台正常打印。
    _configure_utf8_stdout()

    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print(f"用法: register_mcp.py <node> <npx> [{DEFAULT_PACKAGE}]")
        sys.exit(1)

    raw_node = sys.argv[1]
    raw_npx = sys.argv[2]
    # 兜底:第 4 个参数可能是空串(上游 ${1:-} 展开),避免覆盖默认包名。
    package = sys.argv[3] if len(sys.argv) == 4 and sys.argv[3] else DEFAULT_PACKAGE

    # 用 shutil.which 解析为 native 路径,避免 MSYS 路径不可执行问题
    node_path = _resolve_executable(raw_node)
    if not node_path:
        print(f"❌ node 不可执行: {raw_node}")
        print(f"   请确认 Node.js 已安装且 {raw_node} 在 PATH 上")
        sys.exit(1)
    npx_path = _resolve_executable(raw_npx)
    if not npx_path:
        print(f"❌ npx 不可执行: {raw_npx}")
        print(f"   请确认 npm 已安装(自带 npx)")
        sys.exit(1)

    # 共享模块: 原子写 + 损坏保护 + 回读校验 + 导出 entry 供 Codex 注册分支读取
    # 默认注入 DISABLE_THOUGHT_LOGGING=true（隐私基线）；用户显式开启日志时
    # （DISABLE_THOUGHT_LOGGING=false）保留用户值，不反复覆盖（优化文档 §10.4）。
    _claude_register(SERVER_NAME, build_server_entry(node_path, npx_path, package, _existing_user_env()))

    print(f"✅ 已注册 {SERVER_NAME} 到 {CLAUDE_JSON}")
    print(f"   node: {node_path}")
    print(f"   npx:  {npx_path}")
    print(f"   pkg:  {package}")
    print(f"   ⚠️ 重启 Claude Code 后生效")


if __name__ == "__main__":
    main()
