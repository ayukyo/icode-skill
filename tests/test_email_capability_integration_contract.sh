#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

NAME="email-evidence-intake"
MCP="icode-mail-observe"
SKILL="skill-packs/$NAME/SKILL.md.template"

if python3 - "$NAME" "$MCP" <<'PY'
import json, sys
skill, server = sys.argv[1:]
manifest = json.load(open("skill-packs/manifest.json", encoding="utf-8"))
routes = json.load(open("mcp/workflow-gate/skill-routes.json", encoding="utf-8"))
policy = json.load(open("mcp/icode-mcp-policy/policy.json", encoding="utf-8"))
names = [item["name"] for item in manifest["skills"]]
assert len(names) == len(set(names)) == 16
assert skill in names
route = next(item for item in routes["routes"] if item["skill"] == skill)
assert {"init", "log", "plan", "doc", "deepcheck", "audit", "verify"} <= set(route["steps"])
assert {"mail_manifest", "content_coverage", "attachment_routes", "mail_analysis", "verdict"} <= set(route["output_contract"])
assert policy["servers"][server]["operations"] == ["read", "managed_evidence_write"]
assert set(policy["servers"][server]["tools"]) == {
    "describe_capabilities", "list_mailboxes", "search_messages",
    "get_message", "get_thread", "save_attachment",
}
assert policy["servers"][server]["tool_operations"]["save_attachment"] == ["managed_evidence_write"]
assert all(
    operations == ["read"]
    for tool, operations in policy["servers"][server]["tool_operations"].items()
    if tool != "save_attachment"
)
for step in ("init", "log", "plan", "doc", "deepcheck", "audit", "verify"):
    assert any(item["server"] == server for item in policy["steps"][step])
PY
then ok "邮件 Skill、步骤路由和 MCP 最小权限策略完整"; else bad "邮件能力路由或策略缺失"; fi

if [[ -f "$SKILL" ]] \
  && head -4 "$SKILL" | rg -q '^description: Use when' \
  && rg -qF 'mail_manifest' "$SKILL" \
  && rg -qF 'BODY.PEEK' "$SKILL" \
  && rg -q 'remote image|远程图片' "$SKILL" \
  && rg -qF 'cheap-research' "$SKILL" \
  && rg -q 'spreadsheet|表格' "$SKILL" \
  && rg -q 'schematic|原理图' "$SKILL" \
  && ! rg -q 'mcp__|TaskOutput|functions\.exec|collaboration\.' "$SKILL"; then
  ok "邮件 Skill 覆盖正文、表格、图片、附件、隐私和宿主无关边界"
else
  bad "邮件 Skill 内容合同不完整"
fi

if rg -qF 'explicit webmail URL' "$SKILL" \
  && rg -qF 'already logged-in browser is the default adapter' "$SKILL" \
  && rg -qF 'mail reading pane' "$SKILL" \
  && rg -qF 'unrelated mailbox' "$SKILL" \
  && rg -qF 'multiple URLs serially' "$SKILL" \
  && rg -qF 'download original message' "$SKILL" \
  && rg -qF 'batch acquisition' "$SKILL" \
  && rg -qF 'quoted-history segmentation' "$SKILL" \
  && rg -qF 'resource preflight' "$SKILL" \
  && rg -qF 'completeness gate' "$SKILL" \
  && rg -qF 'explicit webmail URLs use the already logged-in browser first' references/host_adapters.md \
  && rg -qF 'does not require IMAP credentials' references/skill_routing.md \
  && rg -qF 'optional unattended mailbox adapter' mcp/icode-mail-observe/README.md; then
  ok "显式网页邮件链接默认复用登录态并落实四个闭环"
else
  bad "网页邮箱优先级、页面定界或四个闭环合同缺失"
fi

if python3 - "$MCP" <<'PY'
import json, sys
server = sys.argv[1]
policy = json.load(open("mcp/icode-mcp-policy/policy.json", encoding="utf-8"))
conditions = [
    item["condition"]
    for entries in policy["steps"].values()
    for item in entries
    if item["server"] == server
]
assert conditions
assert all("网页邮箱" not in condition and "webmail" not in condition.lower() for condition in conditions)
assert all("IMAP" in condition or "邮箱观察器" in condition for condition in conditions)
PY
then ok "网页链接不再把 IMAP MCP 当作默认前置"; else bad "IMAP MCP 仍会被普通网页邮件链接默认触发"; fi

if [[ -f "tools/email_intake.py" ]] \
  && [[ -f "mcp/$MCP/server.py" ]] \
  && [[ -f "mcp/$MCP/tools_manifest.json" ]] \
  && rg -qF 'tools/email_intake.py' SKILL.md references/skill_routing.md steps/install.md README.md README.zh-CN.md \
  && rg -qF "$MCP" references/mcp_integration.md references/mcp_per_step.md steps/install.md README.md README.zh-CN.md \
  && rg -qF './mcp/install.sh --client all icode-mail-observe' "mcp/$MCP/README.md" \
  && rg -qF "$NAME" steps/00_init.md steps/doc.md \
  && ! rg -q '/icode (mail|email)' SKILL.md README.md README.zh-CN.md steps references; then
  ok "邮件能力复用既有命令且双语安装文档已同步"
else
  bad "邮件工具、MCP 或文档集成缺失/新增了公开命令"
fi

if [[ -f "tests/skill-evals/$NAME.json" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-red.md" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-green.md" ]]; then
  ok "邮件 Skill 保留 RED/GREEN 行为评测证据"
else
  bad "邮件 Skill 缺少行为评测证据"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
