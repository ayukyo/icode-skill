#!/usr/bin/env bash
# Temporary repository fixtures only; never reads or mutates live ticket history.
set -euo pipefail
cd "$(dirname "$0")/.."
rounds="${1:-20}"
if [[ ! "$rounds" =~ ^[1-9][0-9]*$ ]]; then
  echo "rounds must be a positive integer" >&2
  exit 2
fi
for ((round=1; round<=rounds; round++)); do
  python3 -m py_compile tools/icode_control.py tools/icode_crosscheck.py tools/inspection_worklist.py
  for script in tests/*.sh; do bash -n "$script"; done
  bash tests/test_inspection_worklist_contract.sh
  bash tests/test_crosscheck_contract.sh
  python3 -m pytest -p no:anyio tests/test_inspection_worklist.py tests/test_inspection_integration.py tests/test_crosscheck.py -q
  printf '[round %02d] PASS syntax/dependencies/logic/errors/association/compatibility/runtime\n' "$round"
done
printf 'PASS: %s isolated inspection-worklist rounds\n' "$rounds"
