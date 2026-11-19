#!/usr/bin/env bash
# 契约测试的跨平台适配层（python3 解析 / 临时路径 / 符号链接探测 / rg 回退）
#
# 背景：Windows 上测试并非"跑不过"，而是**根本没跑**——
#   1. `python3` 常解析到 WindowsApps 的应用执行别名（假 stub）：无任何输出、退出码 49。
#      所有 `python3 ...` 调用整体失效，58 个契约测试在 rc=49 处直接中断。
#   2. 没有真实 ripgrep 二进制（交互 shell 里的 `rg` 可能只是宿主注入的函数）。
#   3. Git Bash 默认把 `ln -s` 退化成复制，依赖符号链接的断言会给出假红。
#   4. MSYS 只转换传给原生程序的 argv 里的 POSIX 路径；路径一旦写进 JSON 再由
#      原生 Python 读出来，`/tmp/...` 会变成 `C:\tmp\...` → ENOENT。
#
# 本文件逐条兜底，并且**只在能力缺失时生效**（Linux/macOS/CI 上零改动）。
# 用法（测试脚本头部，shebang 之后）：
#   . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/python3-shim.sh" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 1. Windows(MSYS)：把 TMPDIR 归一化为 Windows 形式
#
# 这样 mktemp 产出的路径在 bash 和原生 Python 两侧含义一致。Linux/macOS 无
# cygpath，此段自动跳过。
# ---------------------------------------------------------------------------
# 保留 POSIX 形式的临时目录：PATH 里只能放 POSIX 路径，Windows 形式（C:/...）
# 在 Git Bash 的命令查找里不生效。
_icode_tmpdir_posix="${TMPDIR:-/tmp}"
if command -v cygpath >/dev/null 2>&1; then
  _icode_tmpdir_win="$(cygpath -m "${TMPDIR:-/tmp}" 2>/dev/null)"
  if [ -n "$_icode_tmpdir_win" ]; then
    TMPDIR="$_icode_tmpdir_win"
    export TMPDIR
  fi
  unset _icode_tmpdir_win
fi

# ---------------------------------------------------------------------------
# 2. 符号链接能力探测
#
# 真实能力不可用时，调用方应显式 SKIP 并打印，而不是给出假红。
# ---------------------------------------------------------------------------
icode_symlink_ok() {
  _icode_link_probe="${TMPDIR:-/tmp}/icode-symlink-probe.$$"
  rm -rf "$_icode_link_probe" 2>/dev/null
  mkdir -p "$_icode_link_probe/target" 2>/dev/null || { rm -rf "$_icode_link_probe"; return 1; }
  ln -s "$_icode_link_probe/target" "$_icode_link_probe/link" 2>/dev/null
  if [ -L "$_icode_link_probe/link" ]; then
    rm -rf "$_icode_link_probe"
    return 0
  fi
  rm -rf "$_icode_link_probe"
  return 1
}

# ---------------------------------------------------------------------------
# 3. rg 回退（仅在没有真实 rg 时定义）
#
# 把 tests/ 实际用到的开关子集映射到 grep：
#   短选项：-q / -qF / -qi / -qiF / -n / -i / -F
#   长选项：--fixed-strings / --ignore-case / --quiet / --line-number / --
#   语义：非 -F 时补 -E（rg 默认正则为类 ERE）；有目录参数时补 -r（rg 默认递归）
#        无路径参数且 stdin 不是 TTY 时读 stdin（与 rg 一致）
# 已核对语料：模式中无 \d/\b/\w 等 rg 专属语法，均为 ERE 兼容（含 | ( ) ? [[:space:]]）。
# 出现未支持的开关时明确报错而不是猜——宁可让测试报错，也不要给出错误结论。
# ---------------------------------------------------------------------------
if ! command -v rg >/dev/null 2>&1; then
  rg() {
    _rg_flags=""
    _rg_fixed=0
    _rg_recursive=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --)              shift; break ;;
        --fixed-strings) _rg_flags="$_rg_flags -F"; _rg_fixed=1; shift ;;
        --ignore-case)   _rg_flags="$_rg_flags -i"; shift ;;
        --quiet)         _rg_flags="$_rg_flags -q"; shift ;;
        --line-number)   _rg_flags="$_rg_flags -n"; shift ;;
        --*)  printf 'rg-shim: 不支持的长选项 %s\n' "$1" >&2; return 2 ;;
        -qF|-Fq) _rg_flags="$_rg_flags -qF"; _rg_fixed=1; shift ;;
        -qiF|-qFi|-iFq|-iqF|-Fqi|-Fiq) _rg_flags="$_rg_flags -qiF"; _rg_fixed=1; shift ;;
        -qi|-iq) _rg_flags="$_rg_flags -qi"; shift ;;
        -q)   _rg_flags="$_rg_flags -q"; shift ;;
        -F)   _rg_flags="$_rg_flags -F"; _rg_fixed=1; shift ;;
        -i)   _rg_flags="$_rg_flags -i"; shift ;;
        -n)   _rg_flags="$_rg_flags -n"; shift ;;
        -*)   printf 'rg-shim: 不支持的开关 %s\n' "$1" >&2; return 2 ;;
        *)    break ;;
      esac
    done
    if [ $# -eq 0 ]; then
      printf 'rg-shim: 缺少模式参数（本回退不猜测 rg 默认行为）\n' >&2
      return 2
    fi
    _rg_pat="$1"
    shift
    _rg_stdin=0
    # rg 无路径参数时：stdin 是管道就读 stdin，否则递归当前目录
    if [ $# -eq 0 ]; then
      if [ -t 0 ]; then
        set -- .
      else
        _rg_stdin=1
      fi
    fi
    for _rg_path in "$@"; do
      if [ -d "$_rg_path" ]; then _rg_recursive=1; fi
    done
    if [ "$_rg_recursive" -eq 1 ]; then
      _rg_flags="$_rg_flags -r --exclude-dir=.git"
    fi
    if [ "$_rg_fixed" -eq 0 ]; then
      _rg_flags="$_rg_flags -E"
    fi
    # 用 -e 显式传模式：模式本身以 '-' 开头（如 '--pending'）时不会被当成开关
    if [ "$_rg_stdin" -eq 1 ]; then
      grep $_rg_flags -e "$_rg_pat"
    else
      grep $_rg_flags -e "$_rg_pat" "$@"
    fi
  }
fi

# ---------------------------------------------------------------------------
# 4. python3 解析 shim（本段必须放在最后：可用时会 return 掉整个文件）
#
# 用 PATH 而不是 shell 函数，是为了同时覆盖子 shell 与 `sh -c '...'`。
#   - python3 可用（Linux/macOS/CI）→ 立即返回，零开销
#   - python3 不可用且没有 `py` 启动器 → 什么都不做，行为与今天一致
# ---------------------------------------------------------------------------
# 用**真实输出**判定，不信任退出码或 --version：WindowsApps 假 stub 对 `python -c`
# 会返回 rc=0 但没有任何输出，只看退出码会把 stub 当成可用解释器。
#
# 快路径：已知解析到 WindowsApps 别名时不要真去执行它——该 stub 单次调用约 1.9s
# （失败路径的冷启动），70 个测试累计就是 2 分钟以上。
_icode_python3_path="$(command -v python3 2>/dev/null || true)"
case "$_icode_python3_path" in
  *[Ww]indows[Aa]pps*) : ;;
  *)
    if [ "$(python3 -c 'print(1)' 2>/dev/null)" = "1" ]; then
      return 0 2>/dev/null || exit 0
    fi
    ;;
esac

command -v py >/dev/null 2>&1 || { return 0 2>/dev/null || exit 0; }

# 共享一个 shim 目录（不按 PID 分裂，避免每次测试残留一个新目录）。
_icode_shim_dir="${_icode_tmpdir_posix}/icode-python3-shim"
mkdir -p "$_icode_shim_dir" 2>/dev/null || true

# 优先缓存真实解释器路径：Windows 的 `py` 启动器在 USERPROFILE 被重定向时会直接
# 退出（rc=109、无任何输出），而测试隔离 HOME 恰恰要改 USERPROFILE。
# 缓存同时省掉每个测试 1~2 次 `py` 启动（约 0.5s/次）。
_icode_exe_cache="$_icode_shim_dir/.python-exe"
_icode_py_exe=""
if [ -s "$_icode_exe_cache" ]; then
  _icode_py_exe="$(cat "$_icode_exe_cache" 2>/dev/null)"
  if [ -n "$_icode_py_exe" ] && [ ! -x "$_icode_py_exe" ]; then
    _icode_py_exe=""   # 缓存失效（解释器被卸载/换机）
  fi
fi
if [ -z "$_icode_py_exe" ]; then
  [ "$(py -3 -c 'print(1)' 2>/dev/null)" = "1" ] || { return 0 2>/dev/null || exit 0; }
  _icode_py_exe="$(py -3 -c 'import sys; print(sys.executable)' 2>/dev/null)"
  if [ -n "$_icode_py_exe" ]; then
    printf '%s' "$_icode_py_exe" > "$_icode_exe_cache.$$" 2>/dev/null \
      && mv -f "$_icode_exe_cache.$$" "$_icode_exe_cache" 2>/dev/null || true
    # 不删旧包装器：下面的 stamp 比对会触发原子重写。并发跑测试时删除会造成
    # 瞬时缺文件窗口（期间启动的测试 rc=127）。
  fi
fi
if [ -z "$_icode_py_exe" ]; then
  _icode_py_launch='py -3'
else
  _icode_py_launch="\"$_icode_py_exe\""
fi
# 只有解释器启动命令变了才重写包装器（用 .launch 里的字符串比对，read 是内建，
# 不产生进程）。稳态下省掉每次测试 3 次进程创建（printf/chmod/mv 各约 0.5s），
# 同时保证升级后旧的 `py -3` 包装器会被替换成本机真实解释器路径。
# -X utf8：Windows 的 Python 默认编码是 cp936，而测试里大量 open()/read_text 没有
# 显式指定 encoding，会抛 UnicodeDecodeError。强制 UTF-8 模式，与仓库其余入口
# （控制面调用用 `-B -X utf8`）保持一致。
_icode_stamp=""
[ -f "$_icode_shim_dir/.launch" ] && IFS= read -r _icode_stamp < "$_icode_shim_dir/.launch"
if [ "$_icode_stamp" != "$_icode_py_launch" ] || [ ! -f "$_icode_shim_dir/python3" ]; then
  printf '#!/bin/sh\nexec %s -X utf8 "$@"\n' "$_icode_py_launch" > "$_icode_shim_dir/python3.$$" 2>/dev/null \
    && chmod +x "$_icode_shim_dir/python3.$$" 2>/dev/null \
    && mv -f "$_icode_shim_dir/python3.$$" "$_icode_shim_dir/python3" 2>/dev/null || true
  printf '%s' "$_icode_py_launch" > "$_icode_shim_dir/.launch.$$" 2>/dev/null \
    && mv -f "$_icode_shim_dir/.launch.$$" "$_icode_shim_dir/.launch" 2>/dev/null || true
fi

if [ -x "$_icode_shim_dir/python3" ]; then
  case ":$PATH:" in
    *":$_icode_shim_dir:"*) : ;;
    *) PATH="$_icode_shim_dir:$PATH"; export PATH ;;
  esac
fi

# `python` 同样可能是 WindowsApps 假 stub（rc=0、无输出），补齐同名包装器。
# 只在包装器**还不存在**时探测：执行一次 python 约 0.5s，70 个测试都付一次不划算。
# 代价是若之后原生 python 恢复可用，仍会被这里的包装器覆盖——对测试环境可接受。
if [ ! -f "$_icode_shim_dir/python" ] && [ -x "$_icode_shim_dir/python3" ]; then
  if [ "$(python -c 'print(1)' 2>/dev/null)" != "1" ]; then
    cp -f "$_icode_shim_dir/python3" "$_icode_shim_dir/python" 2>/dev/null || true
    chmod +x "$_icode_shim_dir/python" 2>/dev/null || true
  fi
fi

unset _icode_shim_dir _icode_tmpdir_posix _icode_py_exe _icode_py_launch _icode_stamp
return 0 2>/dev/null || exit 0