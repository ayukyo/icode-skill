#!/usr/bin/env bash
# Temporary repository fixtures only; never reads or mutates live ticket history.
set -euo pipefail
cd "$(dirname "$0")/.."
# 跨平台 Python 解析（Windows 硬规则）：ICODE_PYTHON_BIN 显式指定优先；
# Windows(Git Bash/MSYS) 优先 py -3 —— 裸 python3/python 可能是 WindowsApps 假 stub
# （退出码 0 且无输出）或 MSYS 自带 Python（与系统包不共享）；其余平台优先 python3。
_py_ok() { [ "$("$@" -c 'print(1)' 2>/dev/null)" = "1" ]; }
PY=()
if [[ -n "${ICODE_PYTHON_BIN:-}" ]]; then
  PY=("$ICODE_PYTHON_BIN")
elif { [[ -n "${MSYSTEM:-}" ]] || [[ "$(uname -s 2>/dev/null)" == MINGW* ]] \
       || [[ "$(uname -s 2>/dev/null)" == MSYS* ]]; } && _py_ok py -3; then
  PY=(py -3)
elif _py_ok python3; then
  PY=(python3)
elif _py_ok py -3; then
  PY=(py -3)
elif _py_ok python; then
  PY=(python)
else
  PY=(python3)
fi
rounds="${1:-20}"
if [[ ! "$rounds" =~ ^[1-9][0-9]*$ ]]; then
  echo "rounds must be a positive integer" >&2
  exit 2
fi
for ((round=1; round<=rounds; round++)); do
  "${PY[@]}" -m py_compile tools/icode_control.py tools/icode_crosscheck.py tools/inspection_worklist.py
  for script in tests/*.sh; do bash -n "$script"; done
  bash tests/test_inspection_worklist_contract.sh
  bash tests/test_crosscheck_contract.sh
  "${PY[@]}" -m pytest -p no:anyio tests/test_inspection_worklist.py tests/test_inspection_integration.py tests/test_crosscheck.py -q
  printf '[round %02d] PASS syntax/dependencies/logic/errors/association/compatibility/runtime\n' "$round"
done
printf 'PASS: %s isolated inspection-worklist rounds\n' "$rounds"
