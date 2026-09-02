"""把 context7 MCP 注册到 ~/.claude.json 的 mcpServers 段。

⚠️ 只放路径, 不放任何 KEY/URL/MODELNAME。配置全部走环境变量(本工具无 KEY)。

用法:
    python3 scripts/register_mcp.py <node> <npx>

示例:
    python3 scripts/register_mcp.py \\
        /usr/bin/node \\
        /usr/bin/npx

或 (Windows):
    python scripts/register_mcp.py "C:\\Program Files\\nodejs\\node.exe" "C:\\Program Files\\nodejs\\npx.cmd"

默认包名: @modelcontextprotocol/server-context7
可通过第三个参数覆盖: <node> <npx> [package_name]
"""
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

DEFAULT_PACKAGE = "@upstash/context7-mcp"
SERVER_NAME = "context7"


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
    resolved = shutil.which(name_or_path)
    if resolved:
        return resolved
    # Git Bash/MinGW 下 command -v 返回 MSYS 路径(/c/Program Files/...),
    # shutil.which 直接解析会失败;退化为按 basename 再查一次
    # (shutil.which 自动补 PATHEXT .exe/.cmd),兼容 Windows。
    return shutil.which(Path(name_or_path).name)


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
    _claude_register(SERVER_NAME, build_server_entry(node_path, npx_path, package))

    print(f"✅ 已注册 {SERVER_NAME} 到 {CLAUDE_JSON}")
    print(f"   node: {node_path}")
    print(f"   npx:  {npx_path}")
    print(f"   pkg:  {package}")
    print(f"   ⚠️ 重启 Claude Code 后生效")


if __name__ == "__main__":
    main()
