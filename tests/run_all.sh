#!/usr/bin/env bash
# tests/run_all.sh —— tests/test_*.sh 契约测试的并行运行器
#
# 目的：70 个契约测试串行在 Windows(Git Bash) 上要 40+ 分钟。各测试自身用
#       mktemp -d 建独立临时目录，主体互不依赖，可以安全地并行调度。
#
# 用法（在仓库任意目录执行均可，脚本会自行切到仓库根）：
#   bash tests/run_all.sh                     # 自动并发：nproc（默认上限 8）
#   bash tests/run_all.sh -j 4                # 显式指定并发数
#   bash tests/run_all.sh -t 600              # 单个测试超时 600 秒（默认 300）
#   bash tests/run_all.sh crosscheck          # 只跑文件名包含 crosscheck 的测试
#   bash tests/run_all.sh -j 2 a_contract b_contract  # 多个过滤词为“或”关系
#   bash tests/run_all.sh -h                  # 显示帮助
#
# 输出约定：
#   [N/M] 测试名 ... OK / FAIL(rc=N) / TIMEOUT     —— 每个测试完成时打印一行
#   PASS=x FAIL=y TIMEOUT=z TOTAL=n                 —— 汇总行
#   失败时额外打印失败清单与各自日志的绝对路径。
#   每个测试的完整输出都写到独立日志，默认不刷屏。
#
# 并发风险说明（务必了解）：
#   1. 绝大多数测试使用 mktemp -d 独立目录，可并发；但以下测试会写仓库内固定路径，
#      同一个测试被两个 runner 实例同时执行时会互相覆盖：
#        - test_reasoning_gate_demo_sim.sh：demo/.icode_output/.gate_sim/
#          以及 /tmp/gate_sim_<case>.json
#        - test_workflow_gate_demo_sim.sh：demo/.icode_output/.workflow_sim/
#          并在 demo/ 下执行 make clean && make（写 main.o/calc.o/calc_demo）
#      因此不要同时启动多个 run_all.sh 实例。
#   2. tests/lib/python3-shim.sh 共享 ${TMPDIR}/icode-python3-shim：包装器先用
#      PID 后缀写入再 mv（原子），并发安全；仅 `cp -f` 生成 python 别名存在极短
#      非原子窗口，重复执行会自愈。
#   3. 默认并发上限 8：Windows 上进程创建开销大且杀软扫描会放大竞争，超过 8
#      通常不再提速，反而让超时判定抖动。显式 -j 可突破该上限，后果自负。
#   4. 超时优先使用 GNU timeout（存在时附带 -k 5 升级强杀）；没有 timeout 命令
#      时退化为后台看门狗 + kill。强杀只会终止直接子进程，测试残留的孙进程
#      （如个别 python 子进程）可能需要手工清理。
#   5. 测试统一以 `bash <script>` 方式启动（不依赖可执行位），stdin 接 /dev/null，
#      避免任何交互式读取导致挂死。
#
# 退出码：0 = 全部通过；1 = 有 FAIL 或 TIMEOUT；2 = 用法/环境错误。

set -u

if [ -z "${BASH_VERSION:-}" ]; then
  echo "请使用 bash 运行：bash tests/run_all.sh" >&2
  exit 2
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || exit 2
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd) || exit 2
cd "$ROOT_DIR" || exit 2
TEST_DIR="$ROOT_DIR/tests"

usage() {
  cat <<'USAGE'
用法: bash tests/run_all.sh [-j N] [-t SECONDS] [过滤词 ...]

  -j N        并发测试数（默认取 nproc，缺失时回退 4，自动默认上限 8）
  -t SECONDS  单个测试超时秒数（默认 300；超时标记 TIMEOUT 并计为失败）
  -h          显示本帮助

过滤词为子串匹配，多个过滤词之间是“或”关系；不传则运行 tests/test_*.sh 全部。
示例: bash tests/run_all.sh -j 4 -t 600 crosscheck control_plane
USAGE
}

# 自动并发数：nproc -> getconf -> 4；默认值不超过 8，避免 Windows 过载。
detect_default_jobs() {
  local n=""
  if command -v nproc >/dev/null 2>&1; then
    n=$(nproc 2>/dev/null)
  elif command -v getconf >/dev/null 2>&1; then
    n=$(getconf _NPROCESSORS_ONLN 2>/dev/null)
  fi
  case "$n" in
    ''|*[!0-9]*) n=4 ;;
  esac
  [ "$n" -lt 1 ] && n=4
  [ "$n" -gt 8 ] && n=8
  printf '%s\n' "$n"
}

is_positive_int() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$1" -ge 1 ]
}

JOBS=""
TIMEOUT_SECONDS=300
FILTERS=()

# -------- 参数解析 --------
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -j)
      if [ $# -lt 2 ] || ! is_positive_int "$2"; then
        echo "-j 需要一个正整数并发数" >&2
        exit 2
      fi
      JOBS="$2"
      shift 2
      ;;
    -j*)
      JOBS="${1#-j}"
      if ! is_positive_int "$JOBS"; then
        echo "-j 需要一个正整数并发数" >&2
        exit 2
      fi
      shift
      ;;
    -t)
      if [ $# -lt 2 ] || ! is_positive_int "$2"; then
        echo "-t 需要一个正整数秒数" >&2
        exit 2
      fi
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    -t*)
      TIMEOUT_SECONDS="${1#-t}"
      if ! is_positive_int "$TIMEOUT_SECONDS"; then
        echo "-t 需要一个正整数秒数" >&2
        exit 2
      fi
      shift
      ;;
    --)
      shift
      while [ $# -gt 0 ]; do
        FILTERS+=("$1")
        shift
      done
      ;;
    -*)
      echo "未知选项: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      FILTERS+=("$1")
      shift
      ;;
  esac
done

if [ -z "$JOBS" ]; then
  JOBS=$(detect_default_jobs)
fi

# -------- 发现测试 --------
SELECTED=()
for path in "$TEST_DIR"/test_*.sh; do
  [ -f "$path" ] || continue
  name=${path##*/}
  name=${name%.sh}
  if [ "${#FILTERS[@]}" -eq 0 ]; then
    SELECTED+=("$name")
    continue
  fi
  for filter in "${FILTERS[@]}"; do
    case "$name" in
      *"$filter"*)
        SELECTED+=("$name")
        break
        ;;
    esac
  done
done

TOTAL=${#SELECTED[@]}
if [ "$TOTAL" -eq 0 ]; then
  echo "没有匹配的 tests/test_*.sh（过滤词: ${FILTERS[*]:-无}）" >&2
  exit 2
fi

# -------- 运行环境准备 --------
LOG_DIR=$(mktemp -d "${TMPDIR:-/tmp}/icode-test-run.XXXXXX") || {
  echo "无法创建日志临时目录" >&2
  exit 2
}

# 超时工具探测：GNU timeout 支持 -k（到期强杀），其它实现只用基础形式。
TIMEOUT_CMD=""
TIMEOUT_GNU=0
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD=$(command -v timeout)
  if "$TIMEOUT_CMD" --version >/dev/null 2>&1; then
    TIMEOUT_GNU=1
  fi
fi

PASS=0
FAIL=0
TIMEOUT_COUNT=0
FAILED_SUMMARY=()
TIMEOUT_IDX=()

# 单个测试的执行体（在后台子 shell 中运行）。
# 参数: $1=测试名  $2=状态文件  $3=日志文件
run_one() {
  local name="$1"
  local status_file="$2"
  local log_file="$3"
  local test_abs="$TEST_DIR/$name.sh"
  local rc=0

  if [ -n "$TIMEOUT_CMD" ]; then
    if [ "$TIMEOUT_GNU" -eq 1 ]; then
      "$TIMEOUT_CMD" -k 5 "$TIMEOUT_SECONDS" "$BASH" "$test_abs" \
        </dev/null >"$log_file" 2>&1
    else
      "$TIMEOUT_CMD" "$TIMEOUT_SECONDS" "$BASH" "$test_abs" \
        </dev/null >"$log_file" 2>&1
    fi
    rc=$?
    case "$rc" in
      0)        printf 'OK\n' >"$status_file" ;;
      124|137)  printf 'TIMEOUT\n' >"$status_file" ;;
      *)        printf 'FAIL:%s\n' "$rc" >"$status_file" ;;
    esac
    return 0
  fi

  # 无 timeout 命令：后台执行 + 看门狗（sleep 到期后 TERM，再 KILL）。
  # 用标记文件区分“超时”与“测试自身非零退出”；正常结束后立即杀掉看门狗，
  # 避免其 sleep 结束时误伤后续被复用的 PID。
  local timeout_flag="$log_file.timeout"
  rm -f "$timeout_flag"
  "$BASH" "$test_abs" </dev/null >"$log_file" 2>&1 &
  local child=$!
  (
    sleep "$TIMEOUT_SECONDS"
    if kill -0 "$child" 2>/dev/null; then
      : >"$timeout_flag"
      kill -TERM "$child" 2>/dev/null
      sleep 2
      kill -KILL "$child" 2>/dev/null
    fi
  ) &
  local watchdog=$!
  wait "$child"
  rc=$?
  kill "$watchdog" 2>/dev/null
  wait "$watchdog" 2>/dev/null
  if [ -e "$timeout_flag" ] && [ "$rc" -ne 0 ]; then
    printf 'TIMEOUT\n' >"$status_file"
  elif [ "$rc" -eq 0 ]; then
    printf 'OK\n' >"$status_file"
  else
    printf 'FAIL:%s\n' "$rc" >"$status_file"
  fi
  return 0
}

# 打印单条结果并累计计数。$1=选中列表下标，$2=wait 得到的退出码
report_result() {
  local idx="$1"
  local wait_rc="$2"
  local name="${SELECTED[$idx]}"
  local status_file="$LOG_DIR/$name.status"
  local log_file="$LOG_DIR/$name.log"
  local status=""
  local line=""

  if [ -f "$status_file" ]; then
    status=$(cat "$status_file" 2>/dev/null)
  fi
  case "$status" in
    OK)
      PASS=$((PASS + 1))
      line="OK"
      ;;
    TIMEOUT)
      TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
      TIMEOUT_IDX+=("$idx")
      line="TIMEOUT"
      ;;
    FAIL:*)
      FAIL=$((FAIL + 1))
      line="FAIL(rc=${status#FAIL:})"
      FAILED_SUMMARY+=("$name ($line) -> $log_file")
      ;;
    *)
      # 状态文件缺失（如子 shell 被外部强杀）：按 wait 退出码兜底判定。
      if [ "$wait_rc" -eq 0 ]; then
        PASS=$((PASS + 1))
        line="OK"
      elif [ "$wait_rc" -eq 124 ] || [ "$wait_rc" -eq 137 ]; then
        TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
        TIMEOUT_IDX+=("$idx")
        line="TIMEOUT"
      else
        FAIL=$((FAIL + 1))
        line="FAIL(rc=$wait_rc)"
        FAILED_SUMMARY+=("$name ($line) -> $log_file")
      fi
      ;;
  esac
  printf '[%d/%d] %s ... %s\n' "$((idx + 1))" "$TOTAL" "$name" "$line"
}

# -------- 主调度：后台进程 + wait（轮询收割已完成的任务）--------
# 说明：不依赖 GNU parallel，也不使用非 POSIX 的 `wait -n`。主循环按并发上限
# 启动任务，然后轮询 kill -0 探测结束，再用 wait 回收并取得退出码。
printf '发现 %d 个测试，并发 %d，单测超时 %d 秒。\n' "$TOTAL" "$JOBS" "$TIMEOUT_SECONDS"
printf '日志目录: %s\n' "$LOG_DIR"

active_pids=()
active_idx=()
next=0

while [ "$next" -lt "$TOTAL" ] || [ "${#active_pids[@]}" -gt 0 ]; do
  # 补满并发槽位
  while [ "$next" -lt "$TOTAL" ] && [ "${#active_pids[@]}" -lt "$JOBS" ]; do
    name="${SELECTED[$next]}"
    log_file="$LOG_DIR/$name.log"
    status_file="$LOG_DIR/$name.status"
    # 后台函数调用本身就运行在子 shell 中，无需再包一层 `( ... )`，
    # 在 Windows 上可省掉每个测试一次进程创建。
    run_one "$name" "$status_file" "$log_file" &
    active_pids+=("$!")
    active_idx+=("$next")
    next=$((next + 1))
  done

  # 轮询回收：逐个检查已启动任务是否退出
  remain_pids=()
  remain_idx=()
  k=0
  while [ "$k" -lt "${#active_pids[@]}" ]; do
    pid="${active_pids[$k]}"
    idx="${active_idx[$k]}"
    if kill -0 "$pid" 2>/dev/null; then
      remain_pids+=("$pid")
      remain_idx+=("$idx")
    else
      wait "$pid"
      rc=$?
      report_result "$idx" "$rc"
    fi
    k=$((k + 1))
  done
  if [ "${#remain_pids[@]}" -gt 0 ]; then
    active_pids=("${remain_pids[@]}")
    active_idx=("${remain_idx[@]}")
  else
    active_pids=()
    active_idx=()
  fi

  if [ "${#active_pids[@]}" -gt 0 ]; then
    sleep 0.2
  fi
done

# -------- 超时用例串行重试 --------
# 并发下重测试可能只是被其它测试争抢拖过阈值。先串行重跑一次：通过则判定为
# 并发争抢（计 PASS），仍失败才进失败清单。JOBS=1 时无需重试。
if [ "${#TIMEOUT_IDX[@]}" -gt 0 ] && [ "$JOBS" -gt 1 ]; then
  printf '
%d 个用例超时，串行重试一次以排除并发争抢。
' "${#TIMEOUT_IDX[@]}"
  for idx in "${TIMEOUT_IDX[@]}"; do
    name="${SELECTED[$idx]}"
    retry_log="$LOG_DIR/$name.retry.log"
    retry_status="$LOG_DIR/$name.retry.status"
    rm -f "$retry_status"
    run_one "$name" "$retry_status" "$retry_log"
    retry_line=""
    [ -f "$retry_status" ] && retry_line="$(cat "$retry_status" 2>/dev/null)"
    case "$retry_line" in
      OK)
        TIMEOUT_COUNT=$((TIMEOUT_COUNT - 1))
        PASS=$((PASS + 1))
        printf '[retry] %s ... OK（串行通过，判定为并发争抢）
' "$name"
        ;;
      *)
        printf '[retry] %s ... %s
' "$name" "${retry_line:-TIMEOUT}"
        FAILED_SUMMARY+=("$name (TIMEOUT，串行重试仍失败) -> $retry_log")
        ;;
    esac
  done
fi

# -------- 汇总 --------
printf '\nPASS=%d FAIL=%d TIMEOUT=%d TOTAL=%d\n' \
  "$PASS" "$FAIL" "$TIMEOUT_COUNT" "$TOTAL"

if [ "${#FAILED_SUMMARY[@]}" -gt 0 ]; then
  printf '失败清单:\n'
  for item in "${FAILED_SUMMARY[@]}"; do
    printf '  - %s\n' "$item"
  done
  printf '完整日志目录: %s\n' "$LOG_DIR"
  exit 1
fi

exit 0