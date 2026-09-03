#!/bin/bash
# tb_watch 守护控制脚本: start | stop | status
#
# 用法:
#   tb_watch_ctl.sh start  [--config <path>] [--project-dir <path>]   # 启动常驻（默认配置 ~/.claude/icode_data/tb_watch.json）
#   tb_watch_ctl.sh stop                                              # 优雅停止（SIGTERM，当前轮结束后停）
#   tb_watch_ctl.sh stop --force                                      # 强制停止（中断正在跑的分析，杀守护+子进程）
#   tb_watch_ctl.sh status [--config <path>]                          # 查看运行状态
#
# 工程路径：配置文件各 "projects[].project_dir" 字段（顶层 project_dir 可省作全局缺省；或 --project-dir 覆盖）。
# 产物落 <project_dir>/.icode_output/（报告 tb_watch_report.md + debug 工单 + tb_watch/ 运行目录）。
# start 会一并拉起网页只读查看服务 tb_web.py（配置 web 段，缺省启用 0.0.0.0:8000）；
# stop / stop --force 一并停止；status 一并显示。web.pid 落 <project_dir>/.icode_output/tb_watch/。
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/tb_watch.py"
# 多实例语义：默认 = 全量（对配置目录下所有 tb_watch*.json 生效）；--config / TB_WATCH_CONFIG = 单个。
CFG="${TB_WATCH_CONFIG:-$HOME/.claude/icode_data/tb_watch.json}"
MULTI=1
[ -n "${TB_WATCH_CONFIG:-}" ] && MULTI=0

# 平台判断：Git Bash / MSYS2 视作 Windows（跨平台分支）
IS_WINDOWS=""
case "$(uname -s)" in
  MINGW*|MSYS*) IS_WINDOWS=1;;
esac

# 跨平台 python 解释器：Windows 上 python3 常解析到商店占位 stub（静默退出无输出），
# 优先探测可用的 python3，否则退回 python；也允许 TB_WATCH_PYTHON 显式指定。
PY_BIN="${TB_WATCH_PYTHON:-}"
if [ -z "$PY_BIN" ]; then
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then
    PY_BIN=python3
  elif command -v python >/dev/null 2>&1 && python -c 'import sys' >/dev/null 2>&1; then
    PY_BIN=python
  else
    PY_BIN=python3
  fi
fi

usage() {
  echo "用法: $0 {start|stop|status} [--config <path>] [--project-dir <path>] [--force]"
  echo "  默认（不带 --config）对所有工程实例生效（配置目录下全部 tb_watch*.json，排除 example/bak）"
  echo "  --config <path>  只对指定单个实例生效"
  echo "  start            启动常驻守护（产物落配置 project_dir 工程的 .icode_output/）"
  echo "  stop             优雅停止（SIGTERM，当前轮结束后停）"
  echo "  stop --force     强制停止（中断正在跑的分析，杀守护+子进程）"
  echo "  status           查看运行状态"
  exit 1
}

CMD="${1:-}"
shift || true
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CFG="${2:-}"; MULTI=0; shift 2;;
    --project-dir) PDIR="${2:-}"; shift 2;;
    --force) FORCE=1; shift;;
    *) usage;;
  esac
done

# 枚举本次应操作的配置：MULTI=1 时 = 默认配置同目录下所有 tb_watch*.json（排除 example/备份），
# MULTI=0 时 = 仅 CFG。全量模式下 --project-dir 无意义（多实例），忽略。
list_configs() {
  if [ "$MULTI" = 0 ]; then
    printf '%s\n' "$CFG"
  else
    ls -1 "$(dirname "$CFG")"/tb_watch*.json 2>/dev/null | grep -viE 'example|\.bak'
  fi
}

# 全量分支：不带 --config 时对每个实例递归调用本脚本（带 --config 走单实例逻辑），复用成熟实现。
all_instances() {
  local extra="" rc=0
  [ "$FORCE" = 1 ] && extra="$extra --force"
  [ -n "${PDIR:-}" ] && echo "[tb_watch] 警告: 全量模式下忽略 --project-dir（多实例无法对应单工程）"
  for cfg in $(list_configs); do
    echo "---- [$CMD] $cfg ----"
    "$0" "$CMD" --config "$cfg" $extra || rc=1
  done
  return $rc
}

# 从配置读 project_dir（全局运行时锚点）：顶层 project_dir 优先，否则第一个 project 的
# project_dir，否则缺省 = 当前目录。规范写法是每 project 自带 project_dir，顶层可省。
project_dir_of() {
  "$PY_BIN" - "$CFG" <<'PY'
import json, sys, os
try:
    # Windows 下管道默认 CRLF 换行会让 bash 读到残留 \r，强制纯 LF
    sys.stdout.reconfigure(newline="\n")
except Exception:
    pass
try:
    cfg = json.load(open(sys.argv[1]))
    pd = cfg.get("project_dir")
    if not pd:
        for p in cfg.get("projects") or []:
            if p.get("project_dir"):
                pd = p["project_dir"]
                break
    print(pd or os.getcwd())
except Exception as e:
    print(os.getcwd(), file=sys.stderr)
    print(os.getcwd())
PY
}

# 从配置读网页服务段 web {enable, host, port, project_dir}（缺省 = 启用 0.0.0.0:8000）
web_info() {
  "$PY_BIN" - "$CFG" <<'PY'
import json, sys, os
try:
    # Windows 下管道默认 CRLF 换行会让 bash 读到残留 \r，强制纯 LF
    sys.stdout.reconfigure(newline="\n")
except Exception:
    pass
try:
    cfg = json.load(open(sys.argv[1]))
    web = cfg.get("web") or {}
    enable = web.get("enable", True)
    host = web.get("host") or "0.0.0.0"
    port = web.get("port") or 8000
    pdir = cfg.get("project_dir")
    if not pdir:
        for p in cfg.get("projects") or []:
            if p.get("project_dir"):
                pdir = p["project_dir"]
                break
    print(f"{enable} {host} {port} {pdir or os.getcwd()}")
except Exception:
    print("True 0.0.0.0 8000 " + os.getcwd())
PY
}

# 本机局域网 IPv4 列表（跨平台：Windows/Linux，与 tb_web.py 的 _local_ips 同逻辑；一行一个）
local_ips() {
  "$PY_BIN" - <<'PY'
import socket
try:
    # Windows 下管道默认 CRLF 换行会让 bash 读到残留 \r，强制纯 LF
    import sys
    sys.stdout.reconfigure(newline="\n")
except Exception:
    pass
try:
    ips = set()
    for info in socket.getaddrinfo(socket.gethostname(), None):
        ip = info[4][0]
        if not ip.startswith("127."):
            ips.add(ip)
    for ip in sorted(ips):
        print(ip)
except Exception:
    pass
PY
}

# 列出某 PID 的直接子进程 PID（跨平台：Linux 与 MSYS 的 ps -ef 第三列均为 PPID）
children_of() {
  ps -ef | awk -v p="$1" '$3==p {gsub(/[ \t]/, "", $2); printf "%s ", $2}'
}

# 停网页服务（读 <proj>/.icode_output/tb_watch/web.pid）
stop_web() {
  local wf="$1/.icode_output/tb_watch/web.pid"
  if [ -f "$wf" ]; then
    local wp="$(cat "$wf")"
    if kill -0 "$wp" 2>/dev/null; then
      kill -TERM "$wp" 2>/dev/null && echo "[tb_watch] 已停网页服务 $wp"
    fi
    rm -f "$wf"
  fi
}

# 网页服务可访问地址：host=0.0.0.0 时优先给 mDNS 稳定地址（IP 变化不影响），再枚举本机局域网 IPv4
# （否则按配置 host）。输出多行：第一行 .local（可用时），其后为当前各网段 IP。
access_url() {
  local host="$1" port="$2"
  if [ "$host" = "0.0.0.0" ]; then
    local hn ips=""
    hn=$(hostname 2>/dev/null)
    if [ -n "$hn" ]; then
      printf "http://%s.local:%s/  (mDNS 稳定地址, 本机 IP 变化不影响)\n" "$hn" "$port"
    fi
    ips=$(local_ips | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | tr '\n' ' ')
    [ -z "$ips" ] && ips="<本机IP>"
    for ip in $ips; do printf "http://%s:%s/ " "$ip" "$port"; done
    echo
  else
    echo "http://$host:$port/"
  fi
}

# IP 变更检测：对比 <proj>/.icode_output/tb_watch/last_ips 快照，本机 IP 变了打印提示并更新快照。
# 首次（无快照）只建基准不提示；返回 0=无变化/首次，1=已变更。
ip_change_note() {
  local proj="$1"
  local f="$proj/.icode_output/tb_watch/last_ips"
  local cur old
  cur=$(local_ips | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | tr '\n' ' ')
  cur="${cur% }"
  mkdir -p "$(dirname "$f")" 2>/dev/null
  if [ -f "$f" ]; then
    old=$(cat "$f" 2>/dev/null)
    if [ -n "$old" ] && [ "$old" != "$cur" ]; then
      echo "[tb_watch] ⚠ 本机 IP 已变更: ${old} → ${cur}"
      echo "[tb_watch]   网页旧地址已失效，请用上方 mDNS .local 稳定地址或新 IP 重新分享"
      echo "$cur" > "$f"
      return 1
    fi
  fi
  echo "$cur" > "$f"
  return 0
}

# 输出配置里所有 mount_required=true 的工程根（每 project 带自己的 project_dir + mount_required，
# 缺省继承顶层；去重，一行一个）。多工程单配置时 start 要对每个需挂载的工程做前置检查。
mount_required_dirs() {
  "$PY_BIN" - "$CFG" <<'PY'
import json, os, sys
try:
    # Windows 下管道默认 CRLF 换行会让 bash 读到残留 \r，强制纯 LF
    sys.stdout.reconfigure(newline="\n")
except Exception:
    pass
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
default_pd = cfg.get("project_dir")
default_mr = bool(cfg.get("mount_required"))
seen = []
for p in cfg.get("projects") or []:
    pd = p.get("project_dir") or default_pd or os.getcwd()
    mr = p.get("mount_required", default_mr)
    if mr and pd not in seen:
        seen.append(pd)
        print(pd)
PY
}

# 校验工程路径位于网络挂载（Linux: sshfs 或 gvfs SMB；Windows: 映射驱动器可达即可）。
# 就绪返回 0，否则返回 1 并打印原因。
# 用于 start 前置检查：任一 mount_required=true 的工程若挂载未恢复（路径退化成普通本地目录），
# 直接拒绝启动——防止守护在本地空目录上生成假的 .icode_output/（与 NAS 真实产物对不上）。
mount_ready() {
  local proj ft rc=0
  while read -r proj; do
    proj="${proj%$'\r'}"
    [ -n "$proj" ] || continue
    if [ -n "$IS_WINDOWS" ]; then
      # Windows 无 findmnt：目录存在且可读可写 即等价于“挂载/驱动器就绪”
      if [ -d "$proj" ] && [ -r "$proj" ] && [ -w "$proj" ]; then
        continue
      fi
      echo "[tb_watch] 启动中止：mount_required=true 但工程路径不可访问/不可写（当前: $proj）"
      echo "[tb_watch]   请先确认网络驱动器已映射且目录存在（例如 Z:\\rl2601\\tuya\\mowerware_rl2601）"
      rc=1
      continue
    fi
    ft=$(findmnt -T "$proj" -o FSTYPE -n 2>/dev/null | head -1)
    case "$ft" in
      fuse.sshfs) continue;;
      fuse.gvfsd-fuse)
        case "$proj" in *smb-share:*) continue;; esac
        ;;
    esac
    echo "[tb_watch] 启动中止：mount_required=true 但工程路径不在网络挂载上（当前 fstype=${ft:-无挂载}）"
    echo "[tb_watch]   工程路径: $proj"
    echo "[tb_watch]   请先恢复挂载再 start（例如: sshfs ... 或 systemctl --user start mnt-zilaiye）"
    rc=1
  done < <(mount_required_dirs)
  return $rc
}

case "$CMD" in
  start)
    if [ "$MULTI" = 1 ]; then
      all_instances
      exit $?
    fi
    if [ ! -f "$CFG" ]; then
      TPL="$SCRIPT_DIR/../tb_watch.config.example.json"
      echo "[tb_watch] 配置不存在: $CFG"
      if [ -f "$TPL" ]; then
        echo "[tb_watch] 提示: 首次使用请先复制模板并填写工程路径+项目 URL ——"
        echo "  mkdir -p \"$(dirname "$CFG")\""
        echo "  cp \"$TPL\" \"$CFG\""
        echo "  然后编辑: project_dir=监控工程绝对路径, projects[0].url=真实TB项目URL"
      else
        echo "[tb_watch] 提示: 模板也不在 $TPL，请参照仓库 tools/tb/tb_watch.config.example.json 手工创建配置"
      fi
      exit 1
    fi
    # 防多实例（python 层另有 flock 单实例兜底）：在跑则提示退出，残留 pid 自动清理
    if [ -n "${PDIR:-}" ]; then PROJ="$PDIR"; else PROJ="$(project_dir_of)"; fi
    # 挂载前置检查：mount_required=true 时工程路径必须在网络挂载上，否则拒绝启动（不建假数据）
    if ! mount_ready "$PROJ"; then
      exit 1
    fi
    PIDF="$PROJ/.icode_output/tb_watch/watch.pid"
    if [ -f "$PIDF" ]; then
      DPID=$(cat "$PIDF")
      if kill -0 "$DPID" 2>/dev/null; then
        echo "[tb_watch] 已在运行 PID=$DPID（工程: $PROJ），未重复启动（如需重启请先 stop / stop --force）"
        exit 0
      fi
      echo "[tb_watch] 清理残留 pid 文件（进程 $DPID 已死）"
      rm -f "$PIDF"
    fi
    ARGS="--config $CFG"
    if [ -n "${PDIR:-}" ]; then ARGS="$ARGS --project-dir $PDIR"; fi
    nohup "$PY_BIN" "$PY" $ARGS > /tmp/tb_watch_console.log 2>&1 &
    DPID=$!
    echo "[tb_watch] 已启动 PID=$DPID（日志 /tmp/tb_watch_console.log）"
    sleep 2
    tail -3 /tmp/tb_watch_console.log 2>/dev/null || true
    # Windows(MSYS)：python 写入 watch.pid 的是 Windows PID，与 ps/kill 用的 MSYS pid 不一致，
    # 导致 status/stop 的 kill -0 误判。用 bash 的 $!（MSYS pid）覆盖 watch.pid，保证后续可查活可杀。
    if [ -n "$IS_WINDOWS" ]; then
      for _ in 1 2 3 4 5; do
        [ -f "$PIDF" ] && break
        sleep 1
      done
      echo "$DPID" > "$PIDF"
    fi
    # 网页只读查看服务（配置 web.enable 缺省开启；失败不拖累守护）
    read -r WENABLE WHOST WPORT _ <<< "$(web_info)"
    if [ "$WENABLE" = "True" ]; then
      WEBPIDF="$PROJ/.icode_output/tb_watch/web.pid"
      if [ -f "$WEBPIDF" ] && kill -0 "$(cat "$WEBPIDF")" 2>/dev/null; then
        echo "[tb_watch] 网页服务已在运行 PID=$(cat "$WEBPIDF")"
      else
        WEBROOT=""
        [ -n "${PDIR:-}" ] && WEBROOT="--root $PDIR/.icode_output"
        nohup "$PY_BIN" "$SCRIPT_DIR/tb_web.py" --config "$CFG" $WEBROOT --quiet > /tmp/tb_web.log 2>&1 &
        WEBPID=$!
        echo "$WEBPID" > "$WEBPIDF"
        sleep 1
        if kill -0 "$WEBPID" 2>/dev/null; then
          echo "[tb_watch] 网页服务已启动 PID=$WEBPID：$(access_url "$WHOST" "$WPORT")  （日志 /tmp/tb_web.log）"
          ip_change_note "$PROJ" || true
        else
          echo "[tb_watch] 警告: 网页服务启动失败（端口被占? 看 /tmp/tb_web.log），守护不受影响"
          rm -f "$WEBPIDF"
        fi
      fi
    fi
    ;;
  stop)
    if [ "$MULTI" = 1 ]; then
      all_instances
      exit $?
    fi
    if [ "$FORCE" = 1 ]; then
      PROJ="$(project_dir_of)"
      PIDF="$PROJ/.icode_output/tb_watch/watch.pid"
      if [ -f "$PIDF" ]; then
        DPID=$(cat "$PIDF")
        # 先中断正在跑的分析子进程（claude），再杀守护；未退出则强杀
        CHILD=$(children_of "$DPID"); CHILD="${CHILD% }"
        [ -n "$CHILD" ] && kill -TERM $CHILD 2>/dev/null && echo "[tb_watch] 已中断分析子进程 $CHILD"
        kill -TERM "$DPID" 2>/dev/null && echo "[tb_watch] 已发 SIGTERM 给守护 $DPID"
        sleep 2
        if kill -0 "$DPID" 2>/dev/null; then
          kill -9 "$DPID" 2>/dev/null && echo "[tb_watch] 守护未退出，已强杀 $DPID"
        fi
        rm -f "$PIDF"
        stop_web "$PROJ"
        echo "[tb_watch] 已停止（pid 文件已清理）"
      else
        echo "[tb_watch] 无 pid 文件，未在跑（若确认仍有残留守护进程，可用 pgrep -af tb_watch.py 定位后 kill）"
      fi
    else
      "$PY_BIN" "$PY" --config "$CFG" --stop
      stop_web "$(project_dir_of)"
    fi
    ;;
  status)
    if [ "$MULTI" = 1 ]; then
      all_instances
      exit $?
    fi
    PROJ="$(project_dir_of)"
    PIDF="$PROJ/.icode_output/tb_watch/watch.pid"
    if [ -f "$PIDF" ]; then
      DPID=$(cat "$PIDF")
      if kill -0 "$DPID" 2>/dev/null; then
        CHILD=$(children_of "$DPID"); CHILD="${CHILD% }"
        echo "[tb_watch] 运行中 PID=$DPID（工程: $PROJ）${CHILD:+| 分析子进程: $CHILD}"
      else
        echo "[tb_watch] pid 文件存在但进程 $DPID 已死（可能异常退出，建议 stop --force 清理）"
      fi
    else
      echo "[tb_watch] 未运行（无 pid 文件，工程: $PROJ）"
    fi
    # 网页服务状态
    WEBPIDF="$PROJ/.icode_output/tb_watch/web.pid"
    if [ -f "$WEBPIDF" ]; then
      WPID=$(cat "$WEBPIDF")
      if kill -0 "$WPID" 2>/dev/null; then
        read -r _ WHOST WPORT _ <<< "$(web_info)"
        echo "[tb_watch] 网页服务 运行中 PID=$WPID：$(access_url "$WHOST" "$WPORT")"
        ip_change_note "$PROJ" || true
      else
        echo "[tb_watch] 网页服务 pid 存在但进程已死（残留，可 stop 清理）"
      fi
    else
      echo "[tb_watch] 网页服务 未运行"
    fi
    ;;
  *) usage;;
esac
