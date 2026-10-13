"""跨平台 ffmpeg/ffprobe 定位工具(供 install.sh 探测, 探测结果写入 config.json)。

背景: 与 node 同样的问题——ffmpeg 常装在非 PATH 位置(winget Links/Packages、
choco、scoop、便携目录、自动 provision 运行时), 只查 PATH 会误判"未安装"。
运行时(providers/openai_compat.py)用 config 的 ffmpeg_dir(或环境变量
VISION_BRIDGE_FFMPEG_DIR)定位, 因此安装期探测到的是"目录", 需同时含
ffmpeg 与 ffprobe 两个可执行文件。

探测优先级:
1. 环境变量覆盖: ICODE_FFMPEG_DIR(绝对目录);
2. PATH: shutil.which(ffmpeg) 与 shutil.which(ffprobe) 同目录则采用;
3. 常见安装根(Windows 为主, 兼顾 macOS Homebrew):
   - ~/.workbuddy/binaries/ffmpeg/versions/*/bin  (自动 provision 的运行时)
   - ~/.icode/binaries/ffmpeg/versions/*/bin
   - %LOCALAPPDATA%/Microsoft/WinGet/Links         (winget 链接目录)
   - %LOCALAPPDATA%/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin 等
   - C:/Program Files/ffmpeg/bin
   - C:/ProgramData/chocolatey/bin
   - ~/scoop/shims
   - /opt/homebrew/bin, /usr/local/bin             (macOS Homebrew)

用法:
    python3 find_ffmpeg.py     # 成功: 输出目录路径; 失败: stderr 报错, 退出码 1
"""
import glob
import os
import shutil
import sys
from pathlib import Path


def _candidate_dirs() -> list[str]:
    """常见安装目录(含版本 glob 的 bin 子目录)。"""
    dirs: list[str] = []
    home = Path.home()
    dirs.append(str(home / ".workbuddy" / "binaries" / "ffmpeg" / "versions"))
    dirs.append(str(home / ".icode" / "binaries" / "ffmpeg" / "versions"))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        dirs.append(str(Path(local) / "Microsoft" / "WinGet" / "Links"))
        dirs.append(str(Path(local) / "Microsoft" / "WinGet" / "Packages"))
        dirs += [
            "C:/Program Files/ffmpeg/bin",
            "C:/ProgramData/chocolatey/bin",
            str(home / "scoop" / "shims"),
        ]
    else:
        dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/opt/homebrew/opt/ffmpeg/bin"]
    # 版本/通配目录展开为具体 bin 目录
    expanded: list[str] = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        if glob.has_magic(d):
            expanded.extend(sorted(glob.glob(d), reverse=True))
        else:
            expanded.append(d)
    # WinGet/Packages 下的 Gyan.FFmpeg*/ffmpeg-*/bin 形态
    packages = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages")
    if os.path.isdir(packages):
        for pattern in ("Gyan.FFmpeg*/ffmpeg-*/bin", "*FFmpeg*/ffmpeg*/bin", "*ffmpeg*/**/bin"):
            expanded.extend(sorted(glob.glob(os.path.join(packages, pattern), recursive=True), reverse=True))
    # 版本目录(自动 provision)本身也是 bin 的父级, 补一层 */bin
    for root in (str(home / ".workbuddy" / "binaries" / "ffmpeg" / "versions"),
                 str(home / ".icode" / "binaries" / "ffmpeg" / "versions")):
        expanded.extend(sorted(glob.glob(os.path.join(root, "*", "bin")), reverse=True))
    return expanded


def _has_both(d: str) -> bool:
    exe = ".exe" if os.name == "nt" else ""
    return (os.path.isfile(os.path.join(d, "ffmpeg" + exe))
            and os.path.isfile(os.path.join(d, "ffprobe" + exe)))


def find_ffmpeg_dir() -> str | None:
    """返回同时含 ffmpeg/ffprobe 的目录; 找不到返回 None。"""
    override = os.environ.get("ICODE_FFMPEG_DIR")
    if override and _has_both(override):
        return override
    # PATH: 两个可执行同目录才采用(ffmpeg_dir 是单目录语义)
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    if ff and fp and Path(ff).resolve().parent == Path(fp).resolve().parent:
        return str(Path(ff).resolve().parent)
    for d in _candidate_dirs():
        if _has_both(d):
            return d
    return None


def main() -> int:
    d = find_ffmpeg_dir()
    if d:
        sys.stdout.write(d + "\n")
        return 0
    sys.stderr.write(
        "未找到 ffmpeg/ffprobe。安装方法: winget install Gyan.FFmpeg (Windows) / "
        "brew install ffmpeg (macOS) / sudo apt install ffmpeg (Debian/Ubuntu);"
        "或用 ICODE_FFMPEG_DIR 指定目录。\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
