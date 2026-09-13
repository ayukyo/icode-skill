#!/usr/bin/env bash
# ICODE Agent Runtime/UI v0.3 —— 无网络 20 轮七维自检。
# 重型安装/全仓合同在进入本脚本前单独执行一次；本脚本避免把包索引波动误判为代码回归。
set -u
cd "$(dirname "$0")/.." || exit 1

ROUNDS="${1:-20}"
FAILED=0
LOG_FILE="$(mktemp)"
trap 'rm -f "$LOG_FILE"' EXIT

PY_FILES=(
  tools/icode_control.py
  tools/icode_agent.py
  agent_runtime/icode_agent/__init__.py
  agent_runtime/icode_agent/models.py
  agent_runtime/icode_agent/coordinator.py
  agent_runtime/icode_agent/ticket_catalog.py
  agent_runtime/icode_agent/ui_settings.py
  agent_runtime/icode_agent/host_runner.py
  agent_runtime/icode_agent/ui_server.py
  agent_runtime/icode_agent/backends/base.py
  agent_runtime/icode_agent/backends/fake.py
  agent_runtime/icode_agent/backends/openai_responses.py
)
PYTEST_FILES=(
  tests/test_agent_runtime.py
  tests/test_agent_ui.py
  tests/test_agent_ui_catalog.py
  tests/test_agent_host_runner.py
  tests/test_ui_action_policy.py
  tests/test_agent_ui_dashboard.py
)
CONTRACTS=(
  tests/test_agent_lifecycle_contract.sh
  tests/test_agent_runtime_compat_contract.sh
  tests/test_agent_ui_contract.sh
  tests/test_prompt_budget_contract.sh
)

if ! [[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ROUNDS 必须是正整数" >&2
  exit 2
fi

echo "=== Agent Runtime/UI ${ROUNDS} 轮七维自检（无网络）==="
for round in $(seq 1 "$ROUNDS"); do
  err=0

  # 1. 语法/编译：Python、Shell、浏览器 JavaScript。
  for file in "${PY_FILES[@]}"; do
    python3 -m py_compile "$file" 2>"$LOG_FILE" || {
      echo "  [r$round] Python 语法失败: $file"; tail -n 20 "$LOG_FILE"; err=1;
    }
  done
  bash -n tools/selfcheck_agent_ui.sh "${CONTRACTS[@]}" 2>"$LOG_FILE" || {
    echo "  [r$round] Shell 语法失败"; tail -n 20 "$LOG_FILE"; err=1;
  }
  if command -v node >/dev/null 2>&1; then
    node --check agent_runtime/icode_agent/ui_assets/app.js 2>"$LOG_FILE" || {
      echo "  [r$round] JavaScript 语法失败"; tail -n 20 "$LOG_FILE"; err=1;
    }
  fi

  # 2/3/4. 依赖、逻辑边界、异常处理：离线单元/HTTP/控制面集成测试。
  if ! python3 -m pytest -p no:anyio -q "${PYTEST_FILES[@]}" >"$LOG_FILE" 2>&1; then
    echo "  [r$round] Runtime/UI pytest 失败"
    tail -n 40 "$LOG_FILE"
    err=1
  fi

  # 5/6. 关联与兼容：文档预算、旧 CLI、生命周期与 UI 静态安全合同。
  for contract in "${CONTRACTS[@]}"; do
    if ! bash "$contract" >"$LOG_FILE" 2>&1; then
      echo "  [r$round] 合同失败: $contract"
      tail -n 40 "$LOG_FILE"
      err=1
    fi
  done

  # 7. 可运行性：机器真源、命令入口和工作树补丁格式。
  if ! python3 - <<'PY' >"$LOG_FILE" 2>&1
import json
from pathlib import Path

gates = json.loads(Path("mcp/workflow-gate/gates.json").read_text(encoding="utf-8"))
policy = gates["state_machine"]["action_policy"]
assert policy["schema_version"] == 1
assert {"plan", "code", "verify", "status"} <= set(policy["actions"])
schema = json.loads(Path("schemas/ticket-metadata.schema.json").read_text(encoding="utf-8"))
assert schema["properties"]["schema_version"]["const"] == 3
assert Path("agent_runtime/icode_agent/ui_assets/index.html").stat().st_size > 0
assert Path("SKILL.md").stat().st_size <= 51200
PY
  then
    echo "  [r$round] 机器真源/预算校验失败"
    tail -n 20 "$LOG_FILE"
    err=1
  fi
  python3 tools/icode_agent.py ui --help >"$LOG_FILE" 2>&1 || {
    echo "  [r$round] UI CLI 不可运行"; tail -n 20 "$LOG_FILE"; err=1;
  }
  git diff --check >"$LOG_FILE" 2>&1 || {
    echo "  [r$round] diff 格式失败"; tail -n 20 "$LOG_FILE"; err=1;
  }

  if [[ "$err" -eq 0 ]]; then
    echo "  [r$round] ✅ 语法/依赖/逻辑/异常/关联/兼容/可运行全过"
  else
    echo "  [r$round] ❌ 本轮失败"
    FAILED=1
  fi
done

if [[ "$FAILED" -eq 0 ]]; then
  echo "✅ 全部 $ROUNDS 轮通过"
  exit 0
fi
echo "❌ 存在失败轮次"
exit 1
