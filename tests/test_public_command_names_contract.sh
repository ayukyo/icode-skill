#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_FILES=(
  "$ROOT/SKILL.md"
  "$ROOT/README.md"
  "$ROOT/README.zh-CN.md"
  "$ROOT/steps"
  "$ROOT/references"
)

PASS=0
FAIL=0

ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }
require_text() {
  local file="$1" text="$2" label="$3"
  if rg -qF -- "$text" "$ROOT/$file"; then ok "$label"; else bad "$label (missing: $text)"; fi
}
reject_public() {
  local pattern="$1" label="$2"
  local matches
  if matches="$(rg -n -- "$pattern" "${PUBLIC_FILES[@]}" 2>/dev/null)"; then
    bad "$label"
    printf '%s\n' "$matches" | sed -n '1,8p' >&2
  else
    ok "$label"
  fi
}

require_text SKILL.md '/icode worktree --merge' 'worktree merge is canonical'
require_text steps/worktree.md '/icode worktree --update [--target <ref>]' 'worktree target flag is canonical'
require_text steps/reopen.md '/icode worktree --reopen [--ticket <ticket_id>] [--target <ref>]' 'reopen target flag is canonical'
require_text steps/status.md '/icode status --pending' 'verification debt uses pending'
require_text steps/status.md '/icode status --scan' 'verdict scan uses scan'
require_text steps/status.md '--replacement <ticket_id>' 'replacement ticket flag is canonical'
require_text steps/status.md '--dependency <module>:<commit>[:<path>]' 'premise dependency flag is canonical'
require_text steps/verify.md '/icode verify' 'verify command remains present'
require_text steps/verify.md '--test <target>' 'device-side verification uses test'
require_text steps/verify.md '--reuse <artifact>' 'artifact reuse uses reuse'
require_text steps/list.md '--all' 'stale inclusion uses all'
require_text steps/list.md '--plain' 'plain output flag is canonical'
require_text steps/install.md '/icode install --basic' 'basic install flag is canonical'
require_text steps/install.md '/icode install --preview' 'install preview flag is canonical'

reject_public '--submit-check' 'old submit-check public flag is absent'
reject_public '--verification-pending' 'old verification-pending public flag is absent'
reject_public '--reuse-build' 'old reuse-build public flag is absent'
reject_public '/icode verify[^\n`]*--device' 'old verify device public form is absent'
reject_public '--scan-verdict' 'old scan-verdict public flag is absent'
reject_public '--to-ref' 'old to-ref public flag is absent'
reject_public '--superseded-by' 'old superseded-by public flag is absent'
reject_public '--premise-dep' 'old premise-dep public flag is absent'
reject_public '--include-stale' 'old include-stale public flag is absent'
reject_public '--no-color' 'old no-color public flag is absent'
reject_public '/icode install[^\n`]*--skip-mcp' 'old public install skip-mcp form is absent'
reject_public '/icode install[^\n`]*--dry-run' 'old public install dry-run form is absent'
reject_public '/icode patch --test([[:space:]`]|$)' 'deprecated patch test alias is absent'
reject_public '/icode patch --listen/--test|patch --listen/--test' 'combined deprecated patch alias is absent'

require_text steps/install.md '内部 `install.sh --skip-mcp`' 'public basic maps to internal installer flag'
require_text steps/install.md '内部 `install.sh --dry-run`' 'public preview maps to internal installer flag'
require_text steps/verify.md '内部控制面参数 `--device <id>`' 'public test maps to internal control field'

printf '\nRESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
