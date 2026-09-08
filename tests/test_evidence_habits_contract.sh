#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

SOURCE="references/evidence_and_verification.md"
if [ -f "$SOURCE" ] \
  && grep -q '现场事实优先' "$SOURCE" \
  && grep -q '主代理.*决定性证据' "$SOURCE" \
  && grep -q '无日志.*上游' "$SOURCE" \
  && grep -q '多 Git 根' "$SOURCE" \
  && grep -q '诊断.*实现.*验证' "$SOURCE" \
  && grep -q '送达.*消费' "$SOURCE"; then
  ok "证据与验证习惯有统一真源"
else
  bad "统一证据习惯缺失或不完整"
fi

steps_ok=1
for file in steps/log.md steps/01_plan.md steps/05_deepcheck.md steps/06_audit.md steps/verify.md; do
  grep -q 'references/evidence_and_verification.md\|../references/evidence_and_verification.md' "$file" \
    || steps_ok=0
done
if [ "$steps_ok" -eq 1 ]; then
  ok "log/plan/deepcheck/audit/verify 统一引用真源"
else
  bad "step 文档存在自建或遗漏的证据习惯"
fi

if grep -q 'skill-packs/manifest.json' README.md \
  && grep -q 'Claude Code' README.md \
  && grep -q 'Codex' README.md \
  && grep -q 'skill-packs/manifest.json' README.zh-CN.md \
  && grep -q 'Claude Code' README.zh-CN.md \
  && grep -q 'Codex' README.zh-CN.md \
  && grep -q 'sync-to-global.sh' README.md \
  && grep -q 'sync-to-global.sh' README.zh-CN.md; then
  ok "中英文 README 说明双端技能发布"
else
  bad "README 缺双端技能发布说明"
fi

if grep -q 'evidence_and_verification.md' SKILL.md \
  && grep -q 'skill_routing.md' SKILL.md; then
  ok "ICODE 入口懒加载证据习惯与技能路由"
else
  bad "ICODE 入口未登记统一习惯真源"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
