#!/usr/bin/env bash
# Windows: python3 可能是 WindowsApps 假 stub（无输出、exit 49）；加载共享 shim
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/python3-shim.sh" 2>/dev/null || true
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

if python3 - <<'PY'
import json
manifest = json.load(open("skill-packs/manifest.json", encoding="utf-8"))
routes = json.load(open("mcp/workflow-gate/skill-routes.json", encoding="utf-8"))
names = [item["name"] for item in manifest["skills"]]
assert len(names) == len(set(names))
# 这 5 个技能按顺序发布；后续新增技能可以追加在它们之后
_expected = [
    "technical-document-intake",
    "hardware-spec-contract-audit",
    "schematic-interface-audit",
    "mcu-hardware-software-contract-audit",
    "email-evidence-intake",
]
_idx = [names.index(n) for n in _expected]
assert _idx == list(range(_idx[0], _idx[0] + 5)), _idx
assert {item["skill"] for item in routes["routes"]} == set(names)
PY
then ok "manifest/routes 一一对应且技能集合一致"; else bad "manifest/routes 数量或对应关系错误"; fi

if rg -qF 'technical-document-intake' references/skill_routing.md \
  && rg -qF 'hardware-spec-contract-audit' references/skill_routing.md \
  && rg -qF 'schematic-interface-audit' references/skill_routing.md \
  && rg -qF 'mcu-hardware-software-contract-audit' references/skill_routing.md \
  && rg -qF 'tools/document_intake.py' references/skill_routing.md \
  && rg -qF 'tools/media_router.py' references/skill_routing.md \
  && rg -q '技术文档.*不增加公开命令|不增加公开命令.*技术文档' references/skill_routing.md; then
  ok "路由参考明确三层能力和既有命令入口"
else
  bad "路由参考未集成技术文档能力"
fi

if rg -qF 'tools/document_intake.py' SKILL.md \
  && rg -q '技术文档.*原理图' SKILL.md \
  && rg -qF 'references/skill_routing.md' SKILL.md \
  && ! rg -q '/icode (document|spec|schematic)' SKILL.md; then
  ok "ICODE 路由器暴露能力但未新增公开命令"
else
  bad "ICODE 路由器集成或命令边界错误"
fi

if rg -qF 'skill-packs/manifest.json' README.md README.zh-CN.md \
  && rg -qF 'technical-document-intake' README.md README.zh-CN.md \
  && rg -qF 'schematic-interface-audit' README.md README.zh-CN.md \
  && rg -qF './install.sh --client all' README.md README.zh-CN.md; then
  ok "双语 README 按 manifest 声明共享技能与双端开源安装"
else
  bad "双语 README 的能力或安装说明未同步"
fi

# 共享技能数量以 manifest 为真源，避免文档数字再次漂移
python3 - <<'PY'
import json, re
count = len(json.load(open("skill-packs/manifest.json", encoding="utf-8"))["skills"])
text = open("steps/install.md", encoding="utf-8").read()
assert re.search(rf"当前\s*{count}\s*个", text), f"install.md 未声明当前 {count} 个共享技能"
PY
INSTALL_COUNT_RC=$?

if [[ "$INSTALL_COUNT_RC" -eq 0 ]] \
  && rg -qF 'tools/document_intake.py' steps/install.md \
  && rg -qF 'tools/media_router.py' steps/install.md \
  && rg -qF 'skill-packs/manifest.json' steps/install.md \
  && rg -qF 'Claude Code' steps/install.md \
  && rg -qF 'Codex' steps/install.md; then
  ok "安装步骤区分内置工具和 manifest 声明的独立技能"
else
  bad "安装步骤未同步文档接入发布边界"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
