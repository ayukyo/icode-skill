#!/usr/bin/env bash
# tests/test_submission_contracts.sh — 提案 worktree-upstream-push-guard §9 十二验收测试回归
#
# 用法：bash tests/test_submission_contracts.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 依赖：python3、git（版本 ≥ 2.28 支持 git init -b）。测试全部在 /tmp mock 仓库内运行，
# 不触碰真实工程 / 不 push 任何真实 remote / 不 commit 到任何真实仓库。
#
# 被测对象：
#   - scripts/submission_guard.py（阶段 4：normalize-url / migrate-legacy / g2-check / submit-check）
#   - references/worktree_isolation.md §3.8⑩ / §3.10 的机器闸门逻辑（测试内镜像文档 bash 原语）

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUARD="$SCRIPT_DIR/../scripts/submission_guard.py"

TMP=$(mktemp -d -t icode_submission_XXXXX)
trap 'rm -rf "$TMP"' EXIT
FAIL=0

# ---------------------------------------------------------------------------
# mock git 仓库 helpers
# ---------------------------------------------------------------------------

# new_repo <name>：建主仓（无 commit、无 upstream）
new_repo() {
  git init -q -b master "$TMP/$1"
  git -C "$TMP/$1" config user.email t@t
  git -C "$TMP/$1" config user.name t
  echo "$1" > "$TMP/$1/seed.txt"
  git -C "$TMP/$1" add .
  git -C "$TMP/$1" commit -qm init
}

# add_remote <repo> <remote-name> <bare-dir>：加 bare remote 并把 master 推上去、设 upstream
add_remote() {
  local repo="$TMP/$1" name="$2" bare="$TMP/$3"
  git init -q --bare "$bare"
  git -C "$repo" remote add "$name" "$bare"
  git -C "$repo" push -q "$name" master
  git -C "$repo" branch --set-upstream-to="$name/master" master
}

# new_worktree <repo> <branch> <wt-dir> <baseline-ref>：在 repo 上建 worktree + 显式 upstream
new_worktree() {
  git -C "$TMP/$1" worktree add -q -b "$2" "$TMP/$3" "$4"
  git -C "$TMP/$3" branch --set-upstream-to="${4#refs/remotes/}" "$2"
}

# write_meta <outdir> <json>：写 metadata
write_meta() {
  mkdir -p "$TMP/$1"
  cat > "$TMP/$1/.ico_metadata.json" <<EOF
$2
EOF
}

# ---------------------------------------------------------------------------
# 断言 helpers
# ---------------------------------------------------------------------------

assert_eq() { # desc expected actual
  if [ "$2" = "$3" ]; then echo "  ✅ $1"; else echo "  ❌ $1 — 期望 [$2] 实际 [$3]"; FAIL=$((FAIL+1)); fi
}
assert_contains() { # file needle desc
  if grep -qF -- "$2" "$1"; then echo "  ✅ $3"; else echo "  ❌ $3 — $1 缺 [$2]"; FAIL=$((FAIL+1)); fi
}
assert_not_contains() {
  if ! grep -qF -- "$2" "$1"; then echo "  ✅ $3"; else echo "  ❌ $3 — $1 不该出现 [$2]"; FAIL=$((FAIL+1)); fi
}

echo "============================================================"
echo "T1 单仓单 remote 正常 upstream：创建后 tracking 与契约一致"
echo "============================================================"
new_repo t1; add_remote t1 origin t1_origin.git
new_worktree t1 icode/t1-ticket t1_wt refs/remotes/origin/master
write_meta t1_out '{
  "requirement": "t1",
  "worktree_path": "'$TMP'/t1_wt",
  "worktree_branch": "icode/t1-ticket"
}'
M1="$TMP/t1_out/.ico_metadata.json"
python3 "$GUARD" migrate-legacy --metadata "$M1" > "$TMP/t1.log" 2>&1 || { echo "  ❌ T1 迁移失败"; cat "$TMP/t1.log"; FAIL=$((FAIL+1)); }
assert_contains "$M1" '"migration_source": "legacy_inference"' "T1 契约带 legacy_inference"
assert_contains "$M1" '"tracking_verified": true' "T1 tracking_verified=true"
assert_contains "$M1" '"target_remote_ref": "refs/remotes/origin/master"' "T1 target=origin/master"
if python3 "$GUARD" g2-check --metadata "$M1" >/dev/null 2>&1; then
  echo "  ✅ T1 g2-check pass"
else
  echo "  ❌ T1 g2-check 应 pass"; FAIL=$((FAIL+1))
fi

echo "============================================================"
echo "T2 修改型工单主分支无 upstream：L1 阻断（migrate 标 needs_user_confirm）"
echo "============================================================"
new_repo t2   # 无 remote、无 upstream
git -C "$TMP/t2" worktree add -q -b icode/t2-ticket "$TMP/t2_wt"  # 基于本地 HEAD，无 @{u}
write_meta t2_out '{
  "requirement": "t2",
  "worktree_path": "'$TMP'/t2_wt",
  "worktree_branch": "icode/t2-ticket"
}'
M2="$TMP/t2_out/.ico_metadata.json"
set +e
python3 "$GUARD" migrate-legacy --metadata "$M2" > "$TMP/t2.log" 2>&1
RC2=$?
set -e
assert_eq "T2 退出码 2（needs_user_confirm）" "2" "$RC2"
assert_contains "$TMP/t2.log" "needs_user_confirm" "T2 报告 needs_user_confirm"
if grep -q '"submission_contracts"' "$M2"; then echo "  ❌ T2 歧义不应写契约"; FAIL=$((FAIL+1)); else echo "  ✅ T2 未写契约"; fi

echo "============================================================"
echo "T3 子仓 detached HEAD：g2-check 阻断"
echo "============================================================"
new_repo t3; add_remote t3 origin t3_origin.git
new_worktree t3 icode/t3-ticket t3_wt refs/remotes/origin/master
# 子仓原仓 + 子仓隔离 checkout
new_repo t3sub
git init -q --bare "$TMP/t3sub_origin.git"
git -C "$TMP/t3sub" remote add origin "$TMP/t3sub_origin.git"
git -C "$TMP/t3sub" push -q origin master
git -C "$TMP/t3sub" branch --set-upstream-to=origin/master master
git -C "$TMP/t3sub" worktree add -q -b icode/t3-ticket-sub "$TMP/t3_wt/sub" refs/remotes/origin/master
git -C "$TMP/t3_wt/sub" branch --set-upstream-to=origin/master icode/t3-ticket-sub
# 把子仓 checkout 置 detached（模拟历史事故：ticket 分支指针与实际工作提交脱节）
SUB_SHA=$(git -C "$TMP/t3sub" rev-parse master)
git -C "$TMP/t3_wt/sub" checkout -q --detach "$SUB_SHA"
write_meta t3_out '{
  "requirement": "t3",
  "worktree_path": "'$TMP'/t3_wt",
  "worktree_branch": "icode/t3-ticket",
  "submission_contracts": [
    {"repo_role": "super", "repo_path": "'$TMP'/t3_wt", "worktree_branch": "icode/t3-ticket",
     "remote_name": "origin", "remote_url": "'$TMP'/t3_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "tracking_verified": true},
    {"repo_role": "sub", "repo_path": "'$TMP'/t3_wt/sub", "worktree_branch": "icode/t3-ticket-sub",
     "remote_name": "origin", "remote_url": "'$TMP'/t3sub_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "tracking_verified": true}
  ]
}'
M3="$TMP/t3_out/.ico_metadata.json"
set +e
python3 "$GUARD" g2-check --metadata "$M3" > "$TMP/t3.log" 2>&1
RC3=$?
set -e
assert_eq "T3 g2-check 退出码 2（blocked）" "2" "$RC3"
assert_contains "$TMP/t3.log" "detached HEAD" "T3 检出子仓 detached"

echo "============================================================"
echo "T4 同一 remote 上目标分支与 ticket 分支同名不同：双字段识别"
echo "============================================================"
new_repo t4; add_remote t4 origin t4_origin.git
# 远端造一个与契约目标同名的 ticket 分支（ticket 同名分支 ≠ 目标分支）
git -C "$TMP/t4_origin.git" branch icode/t4-ticket master
new_worktree t4 icode/t4-ticket t4_wt refs/remotes/origin/master
write_meta t4_out '{
  "requirement": "t4",
  "worktree_path": "'$TMP'/t4_wt",
  "worktree_branch": "icode/t4-ticket",
  "submission_contracts": [
    {"repo_role": "super", "repo_path": "'$TMP'/t4_wt", "worktree_branch": "icode/t4-ticket",
     "remote_name": "origin", "remote_url": "'$TMP'/t4_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "target_push_ref": "refs/heads/master", "tracking_verified": true}
  ]
}'
M4="$TMP/t4_out/.ico_metadata.json"
if python3 "$GUARD" g2-check --metadata "$M4" >/dev/null 2>&1; then
  echo "  ✅ T4 契约指向 master 而非同名 ticket 分支，g2 pass"
else
  echo "  ❌ T4 契约目标正确应 pass"; FAIL=$((FAIL+1))
fi
python3 "$GUARD" submit-check --metadata "$M4" > "$TMP/t4.log" 2>&1 || true
assert_contains "$TMP/t4.log" "refs/heads/master" "T4 精确 push 命令指向 refs/heads/master（非同名 ticket 分支）"
assert_not_contains "$TMP/t4.log" "refs/heads/icode/t4-ticket" "T4 不给出 push 到 ticket 同名分支的指令"

echo "============================================================"
echo "T5 多 remote 指向同一服务器但分支不同：URL + target ref 双字段识别"
echo "============================================================"
# SSH 与 HTTPS 指向同一服务器 → normalize 后 URL 相同
A=$(python3 "$GUARD" normalize-url "git@gitlab.example.com:org/repo.git")
B=$(python3 "$GUARD" normalize-url "https://gitlab.example.com/org/repo")
assert_eq "T5 SSH 与 HTTPS 等价归一" "gitlab.example.com/org/repo" "$A"
assert_eq "T5 两者相等（同一服务器）" "true" "$([ "$A" = "$B" ] && echo true || echo false)"
# ssh://user@host/path 与 scp-like git@host:path 等价（同一仓库两种 SSH 写法）
C=$(python3 "$GUARD" normalize-url "ssh://git@gitlab.example.com/org/repo.git")
assert_eq "T5 ssh:// 与 scp-like 等价" "true" "$([ "$A" = "$C" ] && echo true || echo false)"
# 同一 URL 但目标分支不同 → 必须用 target_remote_ref 区分（remote_url 相同不能推导目标相同）
U1=$(python3 "$GUARD" normalize-url "https://gitlab.example.com/org/repo")
R1="refs/remotes/remote_a/master"
R2="refs/remotes/remote_b/feature"
if [ "$U1" = "$U1" ] && [ "$R1" != "$R2" ]; then
  echo "  ✅ T5 同一 URL + 不同 target ref 被双字段区分"
else
  echo "  ❌ T5 双字段区分失败"; FAIL=$((FAIL+1))
fi

echo "============================================================"
echo "T6 创建后目标分支前进：显示 behind，禁止误报可直接 push"
echo "============================================================"
new_repo t6; add_remote t6 origin t6_origin.git
new_worktree t6 icode/t6-ticket t6_wt refs/remotes/origin/master
write_meta t6_out '{
  "requirement": "t6",
  "worktree_path": "'$TMP'/t6_wt",
  "worktree_branch": "icode/t6-ticket",
  "submission_contracts": [
    {"repo_role": "super", "repo_path": "'$TMP'/t6_wt", "worktree_branch": "icode/t6-ticket",
     "remote_name": "origin", "remote_url": "'$TMP'/t6_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "target_push_ref": "refs/heads/master", "tracking_verified": true}
  ]
}'
M6="$TMP/t6_out/.ico_metadata.json"
# 目标前进：main 新增提交并推上 origin/master（本地 t6_wt HEAD 不变 → 落后 1）
echo b > "$TMP/t6/seed.txt"; git -C "$TMP/t6" add .; git -C "$TMP/t6" commit -qm fwd
git -C "$TMP/t6" push -q origin master
# 拉取远程跟踪到本地 refs（模拟 fetch）
git -C "$TMP/t6_wt" fetch -q origin
python3 "$GUARD" submit-check --metadata "$M6" > "$TMP/t6.log" 2>&1 || true
assert_contains "$TMP/t6.log" "behind" "T6 标记 behind"
assert_contains "$TMP/t6.log" "先 fetch/merge/rebase" "T6 提示先 fetch/merge/rebase"
if grep -q "git push origin HEAD:refs/heads/master" "$TMP/t6.log"; then
  echo "  ❌ T6 落后不应给出可直接 push 命令"; FAIL=$((FAIL+1))
else
  echo "  ✅ T6 未给出可直接 push 命令"
fi

echo "============================================================"
echo "T7 用户手工修改 upstream：G2 检出 drift"
echo "============================================================"
new_repo t7; add_remote t7 origin t7_origin.git
new_worktree t7 icode/t7-ticket t7_wt refs/remotes/origin/master
git -C "$TMP/t7_origin.git" branch other master
git -C "$TMP/t7_wt" fetch -q origin
git -C "$TMP/t7_wt" branch --set-upstream-to=origin/other icode/t7-ticket   # 手工改 upstream
write_meta t7_out '{
  "requirement": "t7",
  "worktree_path": "'$TMP'/t7_wt",
  "worktree_branch": "icode/t7-ticket",
  "submission_contracts": [
    {"repo_role": "super", "repo_path": "'$TMP'/t7_wt", "worktree_branch": "icode/t7-ticket",
     "remote_name": "origin", "remote_url": "'$TMP'/t7_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "tracking_verified": true}
  ]
}'
M7="$TMP/t7_out/.ico_metadata.json"
set +e
python3 "$GUARD" g2-check --metadata "$M7" > "$TMP/t7.log" 2>&1
RC7=$?
set -e
assert_eq "T7 g2-check 退出码 2（drift blocked）" "2" "$RC7"
assert_contains "$TMP/t7.log" "upstream drift" "T7 检出 upstream drift"

echo "============================================================"
echo "T8 只有 super repo 文档变更、业务子仓无变更：仍进入提交清单"
echo "============================================================"
new_repo t8; add_remote t8 origin t8_origin.git
new_worktree t8 icode/t8-ticket t8_wt refs/remotes/origin/master
echo "doc change" > "$TMP/t8_wt/NEW.md"   # super 有未提交文档变更
write_meta t8_out '{
  "requirement": "t8",
  "worktree_path": "'$TMP'/t8_wt",
  "worktree_branch": "icode/t8-ticket",
  "submission_contracts": [
    {"repo_role": "super", "repo_path": "'$TMP'/t8_wt", "worktree_branch": "icode/t8-ticket",
     "remote_name": "origin", "remote_url": "'$TMP'/t8_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "target_push_ref": "refs/heads/master", "tracking_verified": true}
  ]
}'
M8="$TMP/t8_out/.ico_metadata.json"
python3 "$GUARD" submit-check --metadata "$M8" > "$TMP/t8.log" 2>&1 || true
assert_contains "$TMP/t8.log" "| super |" "T8 提交清单含 super 行（文档提交不因无子仓而被漏）"
assert_contains "$TMP/t8.log" "| yes |" "T8 报告 super 文档变更（dirty=yes）"
assert_contains "$TMP/t8.log" "精确安全命令" "T8 对 super 文档变更给出精确 push 命令"

echo "============================================================"
echo "T9 子仓 worktree branch 指针与当前 HEAD 脱离：检出并阻断"
echo "============================================================"
new_repo t9; add_remote t9 origin t9_origin.git
new_worktree t9 icode/t9-ticket t9_wt refs/remotes/origin/master
new_repo t9sub
git init -q --bare "$TMP/t9sub_origin.git"
git -C "$TMP/t9sub" remote add origin "$TMP/t9sub_origin.git"
git -C "$TMP/t9sub" push -q origin master
git -C "$TMP/t9sub" branch --set-upstream-to=origin/master master
git -C "$TMP/t9sub" worktree add -q -b icode/t9-ticket-sub "$TMP/t9_wt/sub" refs/remotes/origin/master
git -C "$TMP/t9_wt/sub" branch --set-upstream-to=origin/master icode/t9-ticket-sub
git -C "$TMP/t9_wt/sub" switch -q -c rogue-branch   # 指针脱离契约分支（branch drift）
write_meta t9_out '{
  "requirement": "t9",
  "worktree_path": "'$TMP'/t9_wt",
  "worktree_branch": "icode/t9-ticket",
  "submission_contracts": [
    {"repo_role": "super", "repo_path": "'$TMP'/t9_wt", "worktree_branch": "icode/t9-ticket",
     "remote_name": "origin", "remote_url": "'$TMP'/t9_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "tracking_verified": true},
    {"repo_role": "sub", "repo_path": "'$TMP'/t9_wt/sub", "worktree_branch": "icode/t9-ticket-sub",
     "remote_name": "origin", "remote_url": "'$TMP'/t9sub_origin.git", "target_remote_ref": "refs/remotes/origin/master",
     "tracking_verified": true}
  ]
}'
M9="$TMP/t9_out/.ico_metadata.json"
set +e
python3 "$GUARD" g2-check --metadata "$M9" > "$TMP/t9.log" 2>&1
RC9=$?
set -e
assert_eq "T9 g2-check 退出码 2（branch drift blocked）" "2" "$RC9"
assert_contains "$TMP/t9.log" "rogue-branch" "T9 检出子仓分支脱节（当前分支 != 契约）"

echo "============================================================"
echo "T10 用户误建远端 ticket 分支：push 后审计报告意外分支，不自动删除"
echo "============================================================"
new_repo t10; add_remote t10 origin t10_origin.git
# 模拟用户误 push 建了远端同名 ticket 分支
git -C "$TMP/t10_origin.git" branch icode/t10-ticket master
TARGET_REF="refs/remotes/origin/master"
# 镜像文档 G4 第 4 步：ls-remote 找与本工单契约目标分支不同的同名远端分支
LS=$(git ls-remote "$TMP/t10_origin.git" 'refs/heads/icode/*')
ACCIDENT=""
while read -r sha ref; do
  [ -z "$sha" ] && continue
  if [ "$ref" != "${TARGET_REF#refs/remotes/}" ]; then ACCIDENT="$ACCIDENT $ref"; fi
done <<< "$LS"
if [ -n "$ACCIDENT" ]; then
  echo "  ✅ T10 检出意外远端分支：$ACCIDENT"
else
  echo "  ❌ T10 未检出意外远端分支"; FAIL=$((FAIL+1))
fi
# 断言不自动删除：远端分支仍在
if git ls-remote "$TMP/t10_origin.git" "refs/heads/icode/t10-ticket" | grep -q "refs/heads/icode/t10-ticket"; then
  echo "  ✅ T10 意外分支未被删除（只报告，不自动删）"
else
  echo "  ❌ T10 意外分支被删除（违反 G4 契约）"; FAIL=$((FAIL+1))
fi

echo "============================================================"
echo "T11 所有仓库分别推到不同目标分支：逐仓在线包含性验证通过后才可 close"
echo "============================================================"
new_repo t11; add_remote t11 origin t11_origin.git
new_worktree t11 icode/t11-ticket t11_wt refs/remotes/origin/master
new_repo t11sub
git init -q --bare "$TMP/t11sub_origin.git"
git -C "$TMP/t11sub" remote add origin "$TMP/t11sub_origin.git"
git -C "$TMP/t11sub" push -q origin master
git -C "$TMP/t11sub" branch --set-upstream-to=origin/master master
git -C "$TMP/t11sub" worktree add -q -b icode/t11-ticket-sub "$TMP/t11_wt/sub" refs/remotes/origin/master
git -C "$TMP/t11_wt/sub" branch --set-upstream-to=origin/master icode/t11-ticket-sub
# 各推一个 commit 到各自不同目标分支（add 仅加普通文件，避免把子仓当嵌入式仓库加 gitlink）
echo sup > "$TMP/t11_wt/s.txt"; git -C "$TMP/t11_wt" add s.txt; git -C "$TMP/t11_wt" commit -qm super-wk
echo sub > "$TMP/t11_wt/sub/s.txt"; git -C "$TMP/t11_wt/sub" add .; git -C "$TMP/t11_wt/sub" commit -qm sub-wk
git -C "$TMP/t11_wt" push -q origin icode/t11-ticket:master      # super → origin/master
# 子仓【未推】→ 逐仓在线验证应为 blocked
SUPER_TICKET=$(git -C "$TMP/t11_wt" rev-parse icode/t11-ticket)
SUB_TICKET=$(git -C "$TMP/t11_wt/sub" rev-parse icode/t11-ticket-sub)
SUPER_ONLINE=$(git ls-remote "$TMP/t11_origin.git" refs/heads/master | awk '{print $1}')
SUB_ONLINE=$(git ls-remote "$TMP/t11sub_origin.git" refs/heads/master | awk '{print $1}')
SUPER_OK="false"; SUB_OK="false"
git -C "$TMP/t11_wt" merge-base --is-ancestor "$SUPER_TICKET" "$SUPER_ONLINE" 2>/dev/null && SUPER_OK="true"
git -C "$TMP/t11_wt/sub" merge-base --is-ancestor "$SUB_TICKET" "$SUB_ONLINE" 2>/dev/null && SUB_OK="true"
assert_eq "T11 super 在线包含（已推 master）" "true" "$SUPER_OK"
assert_eq "T11 sub 在线不包含（未推）" "false" "$SUB_OK"
if [ "$SUPER_OK" = "true" ] && [ "$SUB_OK" = "true" ]; then
  echo "  ✅ T11 全部仓库包含 → 可 close"
else
  echo "  ✅ T11 任一仓库未包含 → 总 blocked，不可 close（子仓未推被拦截）"
fi

echo "============================================================"
echo "T12 旧 metadata 无契约：唯一证据可迁移，歧义证据必须等待用户确认"
echo "============================================================"
# 12a 唯一证据（有 @{u}、单 remote、分支一致）→ legacy_inference 可迁移
new_repo t12a; add_remote t12a origin t12a_origin.git
new_worktree t12a icode/t12a-ticket t12a_wt refs/remotes/origin/master
write_meta t12a_out '{
  "requirement": "t12a",
  "worktree_path": "'$TMP'/t12a_wt",
  "worktree_branch": "icode/t12a-ticket"
}'
MA="$TMP/t12a_out/.ico_metadata.json"
python3 "$GUARD" migrate-legacy --metadata "$MA" >/dev/null 2>&1 \
  && echo "  ✅ T12a 唯一证据可一次性迁移" || { echo "  ❌ T12a 迁移失败"; FAIL=$((FAIL+1)); }
assert_contains "$MA" '"migration_source": "legacy_inference"' "T12a 契约带 legacy_inference"
# 12b 歧义证据（无 @{u}）→ 必须等用户确认，不写契约
new_repo t12b
git -C "$TMP/t12b" worktree add -q -b icode/t12b-ticket "$TMP/t12b_wt"
write_meta t12b_out '{
  "requirement": "t12b",
  "worktree_path": "'$TMP'/t12b_wt",
  "worktree_branch": "icode/t12b-ticket"
}'
MB="$TMP/t12b_out/.ico_metadata.json"
set +e
python3 "$GUARD" migrate-legacy --metadata "$MB" > "$TMP/t12b.log" 2>&1
RCB=$?
set -e
assert_eq "T12b 歧义退出码 2（needs_user_confirm）" "2" "$RCB"
if grep -q '"submission_contracts"' "$MB"; then echo "  ❌ T12b 歧义不应写契约"; FAIL=$((FAIL+1)); else echo "  ✅ T12b 歧义未写契约（等用户确认）"; fi

echo "============================================================"
if [ "$FAIL" -eq 0 ]; then
  echo "🎉 全部 12 类验收断言通过 — 提案 §9 回归覆盖完毕"
  exit 0
else
  echo "❌ $FAIL 个断言失败 — 见上方 ❌ 行"
  exit 1
fi
