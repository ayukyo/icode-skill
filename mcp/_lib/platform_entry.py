"""MCP server 注册条目跨平台构造工具。

5 个 install.sh 工程 (context7 / git / memory / playwright / sequential-thinking)
用统一策略构造 mcpServers 条目,确保三平台 (Windows / Linux / macOS) 都能跑。

策略 (按优先级):
1. 主方案: command=npx, args=[-y, package]
   - Windows: npx -> npx.cmd (shutil.which 已解析)
   - Linux/macOS: npx -> /usr/bin/npx (shell 脚本,shebang #!/usr/bin/sh,可直接 spawn)
2. Fallback: command=node, args=[npx-cli.js, -y, package]
   - npx 在 PATH 找不到时 / 版本异常时走这条
   - node 自带 npm,npm 自带 npx-cli.js,无需额外安装
3. 标注 _fallback,Claude Code 启动器若主方案失败可选用 (实现未支持前不生效,但保留以备)

调用:
    from _platform import build_server_entry
    entry = build_server_entry(node_path, npx_path, package)
"""
import os
import shutil


def resolve_npx_cli(node_path: str) -> str | None:
    """推断 npx-cli.js 绝对路径。

    常见位置:
    - Windows: C:/Program Files/nodejs/node_modules/npm/bin/npx-cli.js
    - macOS brew: /usr/local/Cellar/node/<v>/bin -> ../../lib/node_modules/npm/bin/
    - Linux nvm: ~/.nvm/versions/node/<v>/bin/node -> ../lib/node_modules/npm/bin/
    """
    if not node_path:
        return None
    candidates = []
    node_dir = os.path.dirname(node_path)
    # 1) node 同目录的 node_modules (Windows 标准安装)
    candidates.append(os.path.join(node_dir, "node_modules", "npm", "bin", "npx-cli.js"))
    # 2) 父目录的 node_modules (Linux/macOS 单文件安装)
    candidates.append(os.path.join(node_dir, "..", "lib", "node_modules", "npm", "bin", "npx-cli.js"))
    # 3) 上一级 (nvm 风格)
    candidates.append(os.path.join(node_dir, "..", "node_modules", "npm", "bin", "npx-cli.js"))
    for c in candidates:
        c = os.path.normpath(c)
        if os.path.exists(c):
            return c
    return None


def build_server_entry(node_path: str, npx_path: str, package: str) -> dict:
    """构造 mcpServers 条目。三平台通用。

    Args:
        node_path: node 可执行路径 (可解析的)
        npx_path:  npx 可执行路径 (可解析的)
        package:   npm 包名 (e.g. @modelcontextprotocol/server-memory)

    Returns:
        dict: 包含 command/args,可选 _fallback 字段
    """
    entry = {
        "command": npx_path,
        "args": ["-y", package],
    }
    npx_cli = resolve_npx_cli(node_path)
    if npx_cli:
        entry["_fallback"] = {
            "command": node_path,
            "args": [npx_cli, "-y", package],
        }
    return entry
