"""跨平台 node/npx 定位工具(供各 install.sh 在 PATH 探测失败时回退调用)。

背景: Windows 上 node 常装在非 PATH 位置(便携目录/nvm/scoop/choco/自动
provision 的运行时), `command -v node` 找不到就直接失败, 要求用户手工把
node 加进 PATH——这属于安装流程该自己解决的问题。

探测优先级:
1. 环境变量覆盖: ICODE_NODE_BIN / ICODE_NPX_BIN(绝对路径, 最高优先);
2. PATH: shutil.which(node/nodejs/npx)(自动按 PATHEXT 补 .exe/.cmd);
3. 常见安装根(Windows 为主, 兼顾 nvm/scoop/choco/便携安装):
   - ~/.workbuddy/binaries/node/versions/*/  (本机自动 provision 的 node)
   - ~/.icode/binaries/node/versions/*/
   - C:/Program Files/nodejs, C:/Program Files (x86)/nodejs
   - %LOCALAPPDATA%/Programs/nodejs
   - %APPDATA%/nvm/*/                        (nvm for Windows)
   - C:/ProgramData/chocolatey/bin
   - ~/scoop/shims

用法:
    python3 find_node.py     # 成功: 输出 "node路径\tnpx路径"; 失败: stderr 报错, 退出码 1
"""
import glob
import os
import shutil
import sys
from pathlib import Path


def _candidate_roots() -> list[str]:
    """常见安装根(含版本目录 glob 的父级)。"""
    roots: list[str] = []
    home = Path.home()
    # 自动 provision 的运行时(工具链约定位置, 版本目录取最新)
    roots.append(str(home / ".workbuddy" / "binaries" / "node" / "versions"))
    roots.append(str(home / ".icode" / "binaries" / "node" / "versions"))
    if os.name == "nt":
        for base in (
            "C:/Program Files/nodejs",
            "C:/Program Files (x86)/nodejs",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs"),
            str(Path(os.environ.get("APPDATA", "")) / "nvm"),
            "C:/ProgramData/chocolatey/bin",
            str(home / "scoop" / "shims"),
        ):
            if base and not base.endswith("nodejs"):
                roots.append(base)
            elif base:
                roots.append(base)
    return [r for r in roots if r and os.path.isdir(r)]


def find_node_npx() -> tuple[str | None, str | None]:
    """返回 (node, npx) 绝对路径; 找不到的项为 None。"""
    node = os.environ.get("ICODE_NODE_BIN") or shutil.which("node") or shutil.which("nodejs")
    npx = os.environ.get("ICODE_NPX_BIN") or shutil.which("npx")
    if node and npx:
        return node, npx

    exe = "node.exe" if os.name == "nt" else "node"
    npx_exe = "npx.cmd" if os.name == "nt" else "npx"
    for root in _candidate_roots():
        # 版本目录(降序, 取最新) + 根目录本身(标准安装 node.exe 就在根上)
        dirs = sorted(glob.glob(os.path.join(root, "*")), reverse=True)
        dirs.append(root)
        for d in dirs:
            if not os.path.isdir(d):
                continue
            if not node:
                candidate = os.path.join(d, exe)
                if os.path.isfile(candidate):
                    node = candidate
            if not npx:
                candidate = os.path.join(d, npx_exe)
                if os.path.isfile(candidate):
                    npx = candidate
            if node and npx:
                return node, npx
    return node, npx


def main() -> int:
    node, npx = find_node_npx()
    if node and npx:
        sys.stdout.write(f"{node}\t{npx}\n")
        return 0
    missing = []
    if not node:
        missing.append("node")
    if not npx:
        missing.append("npx")
    sys.stderr.write(
        f"未找到 {'/'.join(missing)}。请安装 Node.js (https://nodejs.org) "
        f"或用 ICODE_NODE_BIN/ICODE_NPX_BIN 指定绝对路径。\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
