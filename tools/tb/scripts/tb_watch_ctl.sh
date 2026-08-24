#!/bin/bash
# tb_watch 守护控制脚本: start | stop | status
#
# 用法:
#   tb_watch_ctl.sh start  [--config <path>] [--project-dir <path>]   # 启动常驻（默认配置 ~/.claude/icode_data/tb_watch.json）
#   tb_watch_ctl.sh stop                                              # 优雅停止（SIGTERM，当前轮结束后停）
#   tb_watch_ctl.sh stop --force                                      # 强制停止（中断正在跑的分析，杀守护+子进程）
#   tb_watch_ctl.sh status [--config <path>]                          # 查看运行状态
#
# 工程路径：配置文件 JSON 顶层 "project_dir" 字段（或 --project-dir 覆盖）。
# 产物落 <project_dir>/.icode_output/（报告 tb_watch_report.md + debug 工单 + tb_watch/ 运行目录）。
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/tb_watch.py"
CFG="${TB_WATCH_CONFIG:-$HOME/.claude/icode_data/tb_watch.json}"

usage() {
  echo "用法: $0 {start|stop|status} [--config <path>] [--project-dir <path>] [--force]"
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
    --config) CFG="${2:-}"; shift 2;;
    --project-dir) PDIR="${2:-}"; shift 2;;
    --force) FORCE=1; shift;;
    *) usage;;
  esac
done

# 从配置读 project_dir（缺省 = 当前目录）
project_dir_of() {
  python3 - "$CFG" <<'PY'
import json, sys, os
try:
    cfg = json.load(open(sys.argv[1]))
    print(cfg.get("project_dir") or os.getcwd())
except Exception as e:
    print(os.getcwd(), file=sys.stderr)
    print(os.getcwd())
PY
}

case "$CMD" in
  start)
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
    nohup python3 "$PY" $ARGS > /tmp/tb_watch_console.log 2>&1 &
    echo "[tb_watch] 已启动 PID=$!（日志 /tmp/tb_watch_console.log）"
    sleep 2
    tail -3 /tmp/tb_watch_console.log 2>/dev/null || true
    ;;
  stop)
    if [ "$FORCE" = 1 ]; then
      PROJ="$(project_dir_of)"
      PIDF="$PROJ/.icode_output/tb_watch/watch.pid"
      if [ -f "$PIDF" ]; then
        DPID=$(cat "$PIDF")
        # 先中断正在跑的分析子进程（claude），再杀守护；未退出则强杀
        CHILD=$(ps --ppid "$DPID" -o pid= 2>/dev/null | tr -d ' ')
        [ -n "$CHILD" ] && kill -TERM $CHILD 2>/dev/null && echo "[tb_watch] 已中断分析子进程 $CHILD"
        kill -TERM "$DPID" 2>/dev/null && echo "[tb_watch] 已发 SIGTERM 给守护 $DPID"
        sleep 2
        if kill -0 "$DPID" 2>/dev/null; then
          kill -9 "$DPID" 2>/dev/null && echo "[tb_watch] 守护未退出，已强杀 $DPID"
        fi
        rm -f "$PIDF"
        echo "[tb_watch] 已停止（pid 文件已清理）"
      else
        echo "[tb_watch] 无 pid 文件，未在跑（若确认仍有残留守护进程，可用 pgrep -af tb_watch.py 定位后 kill）"
      fi
    else
      python3 "$PY" --config "$CFG" --stop
    fi
    ;;
  status)
    PROJ="$(project_dir_of)"
    PIDF="$PROJ/.icode_output/tb_watch/watch.pid"
    if [ -f "$PIDF" ]; then
      DPID=$(cat "$PIDF")
      if kill -0 "$DPID" 2>/dev/null; then
        CHILD=$(ps --ppid "$DPID" -o pid= 2>/dev/null | tr -d ' ')
        echo "[tb_watch] 运行中 PID=$DPID（工程: $PROJ）${CHILD:+| 分析子进程: $CHILD}"
      else
        echo "[tb_watch] pid 文件存在但进程 $DPID 已死（可能异常退出，建议 stop --force 清理）"
      fi
    else
      echo "[tb_watch] 未运行（无 pid 文件，工程: $PROJ）"
    fi
    ;;
  *) usage;;
esac
