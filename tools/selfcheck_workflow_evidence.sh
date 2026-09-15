#!/usr/bin/env bash
# Isolated receipt, evidence and scope regression; no real ticket mutation.
set -euo pipefail
cd "$(dirname "$0")/.."
rounds="${1:-20}"
if [[ ! "$rounds" =~ ^[1-9][0-9]*$ ]]; then
  echo "rounds must be a positive integer" >&2
  exit 2
fi
for ((round=1; round<=rounds; round++)); do
  python3 -m py_compile tools/icode_control.py tools/lint_mcp_coverage.py tests/test_workflow_evidence_gaps.py
  python3 -m json.tool mcp/workflow-gate/gates.json >/dev/null
  python3 -m json.tool mcp/cheap-research/gates.json >/dev/null
  python3 -m json.tool schemas/ticket-metadata.schema.json >/dev/null
  python3 -m pytest -p no:anyio tests/test_workflow_evidence_gaps.py -q
  printf '[round %02d] PASS syntax/dependencies/logic/errors/cross-file/compatibility/runtime\n' "$round"
done
printf 'PASS: %s isolated workflow-evidence rounds\n' "$rounds"
