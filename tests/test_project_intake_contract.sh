#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

SERVER="mcp/icode-workspace/server.py"
MANIFEST="mcp/icode-workspace/tools_manifest.json"
POLICY="mcp/icode-mcp-policy/policy.json"
REFERENCE="references/project_intake.md"

if python3 - "$SERVER" "$MANIFEST" "$POLICY" <<'PY'
import ast, json, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
policy = json.load(open(sys.argv[3], encoding="utf-8"))
functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
expected = {"resolve_project", "inspect_project_profile"}
assert expected <= functions
assert expected <= {item["name"] for item in manifest["tools"]}
assert expected <= set(policy["servers"]["icode-workspace"]["tools"])
PY
then ok "工程解析与画像工具已在实现/manifest/policy 同步登记"; else bad "工具登记不同步"; fi

if rg -q '唯一嵌套 Git 根' "$REFERENCE" \
  && rg -q '多根/截断即阻断' "$REFERENCE" \
  && rg -q '不得运行.*build\.sh' "$REFERENCE" \
  && rg -q 'huge（≥150k）' "$REFERENCE"; then
  ok "共享合同覆盖唯一根、歧义阻断、静态探测和大仓预算"
else
  bad "共享工程接入合同不完整"
fi

if rg -q 'project_intake\.md' SKILL.md \
  && rg -q 'project_intake\.md' steps/00_init.md \
  && rg -q 'project_intake\.md' steps/log.md \
  && rg -q 'project_intake\.md' steps/01_plan.md \
  && rg -q 'project_intake\.md' steps/doc.md \
  && rg -q 'project_intake\.md' steps/limit.md; then
  ok "核心入口均接入工程解析真源"
else
  bad "核心入口存在未接线项"
fi

if ! rg -q '/home/[^ >`]*|tn2610|ssc308qe|rk3576' "$REFERENCE"; then
  ok "共享合同不含真实项目路径或产品代号"
else
  bad "共享合同泄漏真实项目术语"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
