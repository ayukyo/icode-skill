#!/usr/bin/env bash
# Offline integration only: mocks, synthetic media and Linux PTYs; no devices
# or Git commit fixtures. Optional FFmpeg cases report explicit skips if absent.
set -euo pipefail
cd "$(dirname "$0")/.."

test_python="${ICODE_TEST_PYTHON:-python3}"
test_output="${ICODE_VALIDATION_DIR:-.icode_output/validation/$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -p "$(dirname "$test_output")"
mkdir "$test_output"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1

"$test_python" -c 'import pytest, serial; assert serial.VERSION == "3.5", "tests require pyserial==3.5"'
"$test_python" -B -m pytest -p no:cacheprovider -q \
  --junitxml="$test_output/pytest.xml" \
  tests/test_evidence_schemas.py \
  tests/test_serial_session.py \
  tests/test_evidence_bundle.py \
  tests/test_file_identity_compat.py \
  tests/test_visual_evidence.py \
  tests/test_stage_acceptance.py \
  tests/test_stage_discovery.py \
  tests/test_stage_host_runner.py \
  tests/test_stage_ui.py \
  tests/test_stage_preview.py \
  tests/test_verify_request.py \
  tests/test_test_result_summary.py::ReportTests \
  tests/test_control_linter_runtime.py \
  tests/test_ui_action_policy.py \
  tests/test_agent_ui.py \
  tests/test_agent_ui_dashboard.py \
  tests/test_ui_launcher_portability.py \
  tests/test_agent_ui_catalog.py \
  tests/test_agent_ui_manager_v2.py 2>&1 | tee "$test_output/pytest.log"

for test_contract in \
  tests/test_embedded_verification_contract.sh \
  tests/test_verification_debt_contract.sh \
  tests/test_layered_verification_contract.sh \
  tests/test_media_routing_contract.sh \
  tests/test_control_plane_verify_doc_contract.sh; do
  bash "$test_contract" 2>&1 | tee "$test_output/$(basename "$test_contract").log"
done

printf 'Offline validation artifacts: %s\n' "$test_output"
printf 'Physical serial/camera and installed-client sync are outside this test.\n'
