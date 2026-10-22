"""MCP server 注册条目跨平台构造工具。

5 个 install.sh 工程 (context7 / git / memory / playwright / sequential-thinking)
用统一策略构造 mcpServers 条目,确保三平台 (Windows / Linux / macOS) 都能跑。

策略 (按优先级):
1. 主方案:
   - Windows: command=node, args=[npx-cli.js, -y, package]
     (.exe + .js 组合可被各客户端直接 spawn;npx.cmd 在部分客户端
     需要 shell 才能启动, 不作主方案)
   - Linux/macOS: command=npx, args=[-y, package]
     (npx 为 shell 脚本, shebang #!/usr/bin/sh, 可直接 spawn)
2. Fallback: 另一方案(Windows 上为 npx.cmd, Linux/macOS 上为 node+npx-cli.js)
   - npx 在 PATH 找不到时 / 版本异常时走这条
   - node 自带 npm,npm 自带 npx-cli.js,无需额外安装
3. 标注 _fallback,Claude Code 启动器若主方案失败可选用 (实现未支持前不生效,但保留以备)
4. Windows 下额外注入 env.PATH: npx-cli.js 最终执行的是 _npx 缓存里包自带的
   .cmd 垫片, 垫片内部再调用一次 `node`, 只能靠 PATH 解析。安装期 node 常不在
   PATH(portable / nvm / scoop / 自动 provision 的运行时), 不注入会在运行期
   报 `'node' is not recognized as an internal or external command`。

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


def _windows_system_path() -> str:
    """Windows 系统目录兜底。

    主方案的 PATH 取「node 目录 + 系统目录」, 而不是继承安装期的 PATH:
    安装脚本常在 Git Bash / MSYS 下运行, 那里的 PATH 是 `/usr/bin:/c/...`
    形式, 原样写进配置会让客户端 spawn 出的进程拿到无效 PATH。
    """
    sysroot = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return os.pathsep.join(
        [
            os.path.join(sysroot, "system32"),
            sysroot,
            os.path.join(sysroot, "System32", "Wbem"),
            os.path.join(sysroot, "System32", "WindowsPowerShell", "v1.0"),
        ]
    )


def _windows_env_with_node_path(node_path: str, env: dict | None) -> dict:
    """返回带 PATH 的 env, 让 npx-cli.js 派生的 .cmd 垫片能解析到 node。

    调用方显式传入 PATH 时以其为准(尊重用户覆盖), 不再拼接。
    """
    merged = dict(env or {})
    if any(str(key).upper() == "PATH" for key in merged):
        return merged
    node_dir = os.path.dirname(os.path.abspath(node_path))
    merged["PATH"] = os.pathsep.join([node_dir, _windows_system_path()])
    return merged


def build_server_entry(node_path: str, npx_path: str, package: str, env: dict | None = None) -> dict:
    """构造 mcpServers 条目。三平台通用。

    Args:
        node_path: node 可执行路径 (可解析的)
        npx_path:  npx 可执行路径 (可解析的)
        package:   npm 包名 (e.g. @modelcontextprotocol/server-memory)
        env:       可选环境变量注入 (e.g. {"DISABLE_THOUGHT_LOGGING": "true"})；
                   仅在需要时传，不影响其他 npx 系工程 (context7/memory/playwright)。

    Returns:
        dict: 包含 command/args,可选 env 与 _fallback 字段
    """
    entry: dict = {}
    npx_cli = resolve_npx_cli(node_path)
    if os.name == "nt" and npx_cli:
        # Windows: node.exe + npx-cli.js 为主方案——.exe+.js 可被各客户端
        # 直接 spawn;npx.cmd 在部分客户端(如 Node 系 spawn 无 shell 时)不可
        # 直接执行, 降级为 _fallback。
        entry = {
            "command": node_path,
            "args": [npx_cli, "-y", package],
        }
        entry["_fallback"] = {
            "command": npx_path,
            "args": ["-y", package],
        }
        # 见模块 docstring 第 4 条:垫片内部要能解析到 node。
        entry["env"] = _windows_env_with_node_path(node_path, env)
    else:
        # Linux/macOS: npx(shell 脚本)可直接 spawn 作主方案。
        entry = {
            "command": npx_path,
            "args": ["-y", package],
        }
        if npx_cli:
            entry["_fallback"] = {
                "command": node_path,
                "args": [npx_cli, "-y", package],
            }
        if env:
            entry["env"] = dict(env)
    return entry
