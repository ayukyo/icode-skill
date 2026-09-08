#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="$ROOT/scripts/submission_guard.py"
TMP="$(mktemp -d -t icode_merge_contract_XXXXX)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
RUN_RC=0

ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then ok "$label"; else bad "$label (expected=$expected actual=$actual)"; fi
}
assert_contains() {
  local file="$1" needle="$2" label="$3"
  if grep -qF -- "$needle" "$file"; then ok "$label"; else bad "$label (missing: $needle)"; fi
}
assert_not_contains() {
  local file="$1" needle="$2" label="$3"
  if grep -qF -- "$needle" "$file"; then bad "$label (unexpected: $needle)"; else ok "$label"; fi
}

new_fixture() {
  local name="$1"
  local source="$TMP/${name}_source"
  local remote="$TMP/${name}_origin.git"
  local worktree="$TMP/${name}_wt"
  git init -q -b master "$source"
  git -C "$source" config user.email test@example.com
  git -C "$source" config user.name tester
  printf '%s\n' base >"$source/seed.txt"
  git -C "$source" add seed.txt
  git -C "$source" commit -qm init
  git init -q --bare "$remote"
  git -C "$source" remote add origin "$remote"
  git -C "$source" push -q -u origin master
  git -C "$source" worktree add -q -b "icode/${name}" "$worktree" refs/remotes/origin/master
  git -C "$worktree" branch --set-upstream-to=origin/master "icode/${name}" >/dev/null
  git -C "$worktree" config user.email test@example.com
  git -C "$worktree" config user.name tester
}

write_single_meta() {
  local name="$1" remote_url="${2:-$TMP/${1}_origin.git}"
  local out="$TMP/${name}_out"
  mkdir -p "$out"
  printf '%s\n' \
    '{' \
    '  "schema_version": 3,' \
    "  \"ticket_id\": \"$name\"," \
    '  "requirement": "merge contract",' \
    '  "created_at": "2026-09-08T00:00:00Z",' \
    '  "submission_contracts": [' \
    "    {\"repo_role\": \"super\", \"repo_path\": \"$TMP/${name}_wt\", \"worktree_branch\": \"icode/${name}\"," \
    "     \"remote_name\": \"origin\", \"remote_url\": \"$remote_url\", \"target_remote_ref\": \"refs/remotes/origin/master\"," \
    '     "target_push_ref": "refs/heads/master", "tracking_verified": true}' \
    '  ]' \
    '}' >"$out/.ico_metadata.json"
}

run_merge() {
  local metadata="$1" output="$2"
  set +e
  python3 "$GUARD" submit-check --metadata "$metadata" --merge >"$output" 2>&1
  RUN_RC=$?
  set -e
}

run_check() {
  local metadata="$1" output="$2"
  set +e
  python3 "$GUARD" submit-check --metadata "$metadata" >"$output" 2>&1
  RUN_RC=$?
  set -e
}

snapshot() {
  local repo="$1"
  printf '%s\n%s\n%s\n' \
    "$(git -C "$repo" rev-parse HEAD)" \
    "$(git -C "$repo" status --porcelain=v1 --untracked-files=all)" \
    "$(git -C "$repo" rev-parse -q --verify MERGE_HEAD || true)"
}

printf 'CASE fetch failure is fail-closed\n'
new_fixture fetchfail
missing_remote="$TMP/missing_origin.git"
git -C "$TMP/fetchfail_wt" remote set-url origin "$missing_remote"
write_single_meta fetchfail "$missing_remote"
fetch_before="$(snapshot "$TMP/fetchfail_wt")"
run_merge "$TMP/fetchfail_out/.ico_metadata.json" "$TMP/fetchfail.log"
assert_eq "fetch failure returns blocked" 2 "$RUN_RC"
assert_contains "$TMP/fetchfail.log" blocked "fetch failure is reported"
assert_not_contains "$TMP/fetchfail.log" "git push" "fetch failure prints no push command"
assert_eq "fetch failure preserves checkout" "$fetch_before" "$(snapshot "$TMP/fetchfail_wt")"

printf 'CASE online-only advance fast-forwards without a commit\n'
new_fixture fastforward
printf '%s\n' remote >"$TMP/fastforward_source/remote.txt"
git -C "$TMP/fastforward_source" add remote.txt
git -C "$TMP/fastforward_source" commit -qm remote
git -C "$TMP/fastforward_source" push -q origin master
online_fastforward="$(git -C "$TMP/fastforward_source" rev-parse HEAD)"
write_single_meta fastforward
fastforward_before="$(snapshot "$TMP/fastforward_wt")"
run_check "$TMP/fastforward_out/.ico_metadata.json" "$TMP/fastforward_check.log"
assert_eq "read-only audit blocks an online advance" 2 "$RUN_RC"
assert_contains "$TMP/fastforward_check.log" '/icode worktree --merge' "read-only audit names the merge command"
assert_eq "read-only audit preserves checkout" "$fastforward_before" "$(snapshot "$TMP/fastforward_wt")"
run_merge "$TMP/fastforward_out/.ico_metadata.json" "$TMP/fastforward.log"
assert_eq "fast-forward waits for business recheck" 3 "$RUN_RC"
assert_contains "$TMP/fastforward.log" recheck_pending "fast-forward status is recheck_pending"
assert_eq "fast-forward reaches frozen target" "$online_fastforward" "$(git -C "$TMP/fastforward_wt" rev-parse HEAD)"
assert_eq "fast-forward leaves no MERGE_HEAD" "" "$(git -C "$TMP/fastforward_wt" rev-parse -q --verify MERGE_HEAD || true)"
assert_eq "fast-forward leaves no unmerged entries" "" "$(git -C "$TMP/fastforward_wt" diff --name-only --diff-filter=U)"

printf 'CASE fast-forwarded online content must pass whitespace checks\n'
new_fixture whitespace
printf 'remote with trailing spaces   \n' >"$TMP/whitespace_source/remote.txt"
git -C "$TMP/whitespace_source" add remote.txt
git -C "$TMP/whitespace_source" commit -qm remote-whitespace
git -C "$TMP/whitespace_source" push -q origin master
write_single_meta whitespace
run_merge "$TMP/whitespace_out/.ico_metadata.json" "$TMP/whitespace.log"
assert_eq "fast-forward whitespace error blocks delivery" 2 "$RUN_RC"
assert_contains "$TMP/whitespace.log" "frozen target diff check failed" "fast-forward checks the fetched commit range"
assert_not_contains "$TMP/whitespace.log" "git push" "whitespace failure prints no push command"

printf 'CASE local-only advance stays unchanged\n'
new_fixture localahead
printf '%s\n' local >"$TMP/localahead_wt/local.txt"
git -C "$TMP/localahead_wt" add local.txt
git -C "$TMP/localahead_wt" commit -qm local
local_head="$(git -C "$TMP/localahead_wt" rev-parse HEAD)"
remote_head="$(git -C "$TMP/localahead_origin.git" rev-parse refs/heads/master)"
write_single_meta localahead
run_merge "$TMP/localahead_out/.ico_metadata.json" "$TMP/localahead.log"
assert_eq "local-ahead returns success" 0 "$RUN_RC"
assert_contains "$TMP/localahead.log" local_ahead "local-ahead status is explicit"
assert_eq "local-ahead HEAD is unchanged" "$local_head" "$(git -C "$TMP/localahead_wt" rev-parse HEAD)"
assert_eq "local-ahead does not push" "$remote_head" "$(git -C "$TMP/localahead_origin.git" rev-parse refs/heads/master)"

printf 'CASE clean divergence leaves an uncommitted merge\n'
new_fixture diverged
printf '%s\n' local >"$TMP/diverged_wt/local.txt"
git -C "$TMP/diverged_wt" add local.txt
git -C "$TMP/diverged_wt" commit -qm local
diverged_local_head="$(git -C "$TMP/diverged_wt" rev-parse HEAD)"
printf '%s\n' remote >"$TMP/diverged_source/remote.txt"
git -C "$TMP/diverged_source" add remote.txt
git -C "$TMP/diverged_source" commit -qm remote
git -C "$TMP/diverged_source" push -q origin master
diverged_target="$(git -C "$TMP/diverged_source" rev-parse HEAD)"
write_single_meta diverged
run_merge "$TMP/diverged_out/.ico_metadata.json" "$TMP/diverged.log"
assert_eq "clean divergence returns pending" 3 "$RUN_RC"
assert_contains "$TMP/diverged.log" merge_pending "clean divergence status is merge_pending"
assert_eq "divergent merge does not advance HEAD" "$diverged_local_head" "$(git -C "$TMP/diverged_wt" rev-parse HEAD)"
assert_eq "divergent merge freezes MERGE_HEAD" "$diverged_target" "$(git -C "$TMP/diverged_wt" rev-parse MERGE_HEAD)"
assert_eq "divergent merge has no unresolved paths" "" "$(git -C "$TMP/diverged_wt" diff --name-only --diff-filter=U)"
diverged_snapshot="$(snapshot "$TMP/diverged_wt")"
diverged_cached="$(git -C "$TMP/diverged_wt" diff --cached --binary)"
run_merge "$TMP/diverged_out/.ico_metadata.json" "$TMP/diverged_second.log"
assert_eq "matching pending merge remains pending" 3 "$RUN_RC"
assert_eq "matching pending merge is idempotent" "$diverged_snapshot" "$(snapshot "$TMP/diverged_wt")"
assert_eq "matching pending merge preserves index" "$diverged_cached" "$(git -C "$TMP/diverged_wt" diff --cached --binary)"

printf 'CASE conflicting divergence is blocked before mutation\n'
new_fixture conflict
printf '%s\n' local >"$TMP/conflict_wt/seed.txt"
git -C "$TMP/conflict_wt" add seed.txt
git -C "$TMP/conflict_wt" commit -qm local
printf '%s\n' remote >"$TMP/conflict_source/seed.txt"
git -C "$TMP/conflict_source" add seed.txt
git -C "$TMP/conflict_source" commit -qm remote
git -C "$TMP/conflict_source" push -q origin master
write_single_meta conflict
conflict_before="$(snapshot "$TMP/conflict_wt")"
run_merge "$TMP/conflict_out/.ico_metadata.json" "$TMP/conflict.log"
assert_eq "content conflict returns blocked" 2 "$RUN_RC"
assert_contains "$TMP/conflict.log" blocked "content conflict is reported"
assert_eq "content conflict preserves checkout" "$conflict_before" "$(snapshot "$TMP/conflict_wt")"
assert_eq "content conflict leaves no unmerged paths" "" "$(git -C "$TMP/conflict_wt" diff --name-only --diff-filter=U)"

printf 'CASE duplicate repository contracts are rejected before mutation\n'
new_fixture duplicate
printf '%s\n' remote >"$TMP/duplicate_source/remote.txt"
git -C "$TMP/duplicate_source" add remote.txt
git -C "$TMP/duplicate_source" commit -qm remote
git -C "$TMP/duplicate_source" push -q origin master
duplicate_dir="$TMP/duplicate_out"
mkdir -p "$duplicate_dir"
printf '%s\n' \
  '{' \
  '  "schema_version": 3, "ticket_id": "duplicate", "requirement": "duplicate contract", "created_at": "2026-09-08T00:00:00Z",' \
  '  "submission_contracts": [' \
  "    {\"repo_role\": \"super\", \"repo_path\": \"$TMP/duplicate_wt\", \"worktree_branch\": \"icode/duplicate\", \"remote_name\": \"origin\", \"remote_url\": \"$TMP/duplicate_origin.git\", \"target_remote_ref\": \"refs/remotes/origin/master\", \"target_push_ref\": \"refs/heads/master\", \"tracking_verified\": true}," \
  "    {\"repo_role\": \"sub\", \"repo_path\": \"$TMP/duplicate_wt\", \"worktree_branch\": \"icode/duplicate\", \"remote_name\": \"origin\", \"remote_url\": \"$TMP/duplicate_origin.git\", \"target_remote_ref\": \"refs/remotes/origin/master\", \"target_push_ref\": \"refs/heads/master\", \"tracking_verified\": true}" \
  '  ]' \
  '}' >"$duplicate_dir/.ico_metadata.json"
duplicate_before="$(snapshot "$TMP/duplicate_wt")"
run_merge "$duplicate_dir/.ico_metadata.json" "$TMP/duplicate.log"
assert_eq "duplicate repository contract returns blocked" 2 "$RUN_RC"
assert_contains "$TMP/duplicate.log" "duplicate repository contract" "duplicate repository issue is explicit"
assert_eq "duplicate repository contract preserves checkout" "$duplicate_before" "$(snapshot "$TMP/duplicate_wt")"

printf 'CASE all repositories pass preflight before any mutation\n'
new_fixture multi_a
printf '%s\n' remote >"$TMP/multi_a_source/remote.txt"
git -C "$TMP/multi_a_source" add remote.txt
git -C "$TMP/multi_a_source" commit -qm remote
git -C "$TMP/multi_a_source" push -q origin master
new_fixture multi_b
printf '%s\n' local >"$TMP/multi_b_wt/seed.txt"
git -C "$TMP/multi_b_wt" add seed.txt
git -C "$TMP/multi_b_wt" commit -qm local
printf '%s\n' remote >"$TMP/multi_b_source/seed.txt"
git -C "$TMP/multi_b_source" add seed.txt
git -C "$TMP/multi_b_source" commit -qm remote
git -C "$TMP/multi_b_source" push -q origin master
multi_dir="$TMP/multi_out"
mkdir -p "$multi_dir"
printf '%s\n' \
  '{' \
  '  "schema_version": 3, "ticket_id": "multi", "requirement": "multi repo", "created_at": "2026-09-08T00:00:00Z",' \
  '  "submission_contracts": [' \
  "    {\"repo_role\": \"super\", \"repo_path\": \"$TMP/multi_a_wt\", \"worktree_branch\": \"icode/multi_a\", \"remote_name\": \"origin\", \"remote_url\": \"$TMP/multi_a_origin.git\", \"target_remote_ref\": \"refs/remotes/origin/master\", \"target_push_ref\": \"refs/heads/master\", \"tracking_verified\": true}," \
  "    {\"repo_role\": \"sub\", \"repo_path\": \"$TMP/multi_b_wt\", \"worktree_branch\": \"icode/multi_b\", \"remote_name\": \"origin\", \"remote_url\": \"$TMP/multi_b_origin.git\", \"target_remote_ref\": \"refs/remotes/origin/master\", \"target_push_ref\": \"refs/heads/master\", \"tracking_verified\": true}" \
  '  ]' \
  '}' >"$multi_dir/.ico_metadata.json"
multi_a_before="$(snapshot "$TMP/multi_a_wt")"
multi_b_before="$(snapshot "$TMP/multi_b_wt")"
run_merge "$multi_dir/.ico_metadata.json" "$TMP/multi.log"
assert_eq "multi-repo conflict blocks the transaction" 2 "$RUN_RC"
assert_eq "first repository is not fast-forwarded" "$multi_a_before" "$(snapshot "$TMP/multi_a_wt")"
assert_eq "conflicting repository is untouched" "$multi_b_before" "$(snapshot "$TMP/multi_b_wt")"

printf '\nRESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
