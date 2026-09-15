#!/usr/bin/env bash
# Crosscheck 独立复评能力：默认 20 轮 7 维自检。
set -u
cd "$(dirname "$0")/.." || exit 1

ROUNDS="${1:-20}"
FAILED=0
LOG_FILE="$(mktemp)"
trap 'rm -f "$LOG_FILE"' EXIT

echo "=== crosscheck ${ROUNDS} 轮自检 ==="
for round in $(seq 1 "$ROUNDS")
do
  error=0

  # 1. 语法/解析
  python3 -m py_compile tools/icode_crosscheck.py tests/test_crosscheck.py >"$LOG_FILE" 2>&1 || error=1
  bash -n tests/test_crosscheck_contract.sh tests/test_crosscheck_demo_sim.sh tools/selfcheck_crosscheck.sh >>"$LOG_FILE" 2>&1 || error=1
  python3 -m json.tool schemas/crosscheck-manifest.schema.json >/dev/null 2>>"$LOG_FILE" || error=1
  python3 -m json.tool schemas/crosscheck-round.schema.json >/dev/null 2>>"$LOG_FILE" || error=1

  # 2~5. 依赖、逻辑、异常、关联
  python3 -m pytest -p no:anyio tests/test_crosscheck.py -q >>"$LOG_FILE" 2>&1 || error=1
  bash tests/test_crosscheck_contract.sh >>"$LOG_FILE" 2>&1 || error=1

  # 6~7. 兼容与可运行：真实 demo legacy 工单隔离副本模拟
  bash tests/test_crosscheck_demo_sim.sh >>"$LOG_FILE" 2>&1 || error=1

  if [[ "$error" -eq 0 ]]
  then
    printf '  [round %02d] PASS 语法/依赖/逻辑/异常/关联/兼容/可运行\n' "$round"
  else
    printf '  [round %02d] FAIL\n' "$round"
    tail -n 80 "$LOG_FILE"
    FAILED=1
  fi
  : >"$LOG_FILE"
done

if [[ "$FAILED" -eq 0 ]]
then
  echo "PASS: 全部 ${ROUNDS} 轮 crosscheck 自检通过"
  exit 0
fi
echo "FAIL: 存在失败轮次"
exit 1
