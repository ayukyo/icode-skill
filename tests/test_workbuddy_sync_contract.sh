#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYNC="$ROOT/scripts/sync-to-global.sh"
INSTALL="$ROOT/install.sh"
COMMAND_SOURCE="$ROOT/integrations/codebuddy/commands/icode.md"
# Pin the scratch root to /tmp: on MSYS hosts mktemp -d follows TEMP (a
# "C:\..." path) and cleaning such paths up can be rejected by hardened rm
# wrappers, leaving the suite exit non-zero even when every assertion passed.
TMP="$(mktemp -d /tmp/icode_wb_sync.XXXXXX)"
# Clean up in the background: the apply cases materialise ~2k skill files and
# deleting them synchronously in an EXIT trap can outlive sandboxed runners.
trap 'rm -rf "$TMP" >/dev/null 2>&1 &' EXIT

PASS=0
FAIL=0

ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }

run_sync() {
  local test_home="$1"
  local claude_root="$2"
  local agents_root="$3"
  local workbuddy_root="$4"
  local command_dir="$5"
  shift 5
  HOME="$test_home" \
  CLAUDE_SKILLS_ROOT="$claude_root" \
  AGENTS_SKILLS_ROOT="$agents_root" \
  WORKBUDDY_SKILLS_ROOT="$workbuddy_root" \
  WORKBUDDY_COMMANDS_DIR="$command_dir" \
    "$SYNC" "$@"
}

DRY_HOME="$TMP/dry-home"
DRY_CLAUDE="$TMP/dry-claude"
DRY_AGENTS="$TMP/dry-agents"
DRY_WB="$TMP/dry-wb"
DRY_COMMANDS="$TMP/dry-commands"
if run_sync "$DRY_HOME" "$DRY_CLAUDE" "$DRY_AGENTS" "$DRY_WB" "$DRY_COMMANDS" \
     --dry-run --client workbuddy >/dev/null 2>&1 \
  && [[ ! -e "$DRY_CLAUDE" ]] \
  && [[ ! -e "$DRY_AGENTS" ]] \
  && [[ ! -e "$DRY_WB" ]] \
  && [[ ! -e "$DRY_COMMANDS" ]]; then
  ok "WorkBuddy dry-run performs zero writes"
else
  bad "WorkBuddy dry-run performs zero writes"
fi

DETECTED_HOME="$TMP/detected-home"
mkdir -p "$DETECTED_HOME/.workbuddy"
DETECTED_OUTPUT="$(HOME="$DETECTED_HOME" "$SYNC" --dry-run --client all 2>&1)"
if grep -q 'WorkBuddy:.*would publish' <<<"$DETECTED_OUTPUT" \
  && [[ ! -e "$DETECTED_HOME/.workbuddy/commands" ]] \
  && [[ ! -e "$DETECTED_HOME/.claude" ]] \
  && [[ ! -e "$DETECTED_HOME/.agents" ]]; then
  ok "default all-client dry-run detects WorkBuddy without writing"
else
  bad "default all-client dry-run detects WorkBuddy without writing"
fi

UNDETECTED_HOME="$TMP/undetected-home"
UNDETECTED_OUTPUT="$(HOME="$UNDETECTED_HOME" "$SYNC" --dry-run --client all 2>&1)"
if ! grep -q 'WorkBuddy:.*would publish' <<<"$UNDETECTED_OUTPUT" \
  && [[ ! -e "$UNDETECTED_HOME" ]]; then
  ok "all-client sync does not create a phantom WorkBuddy host"
else
  bad "all-client sync does not create a phantom WorkBuddy host"
fi

WB_HOME="$TMP/workbuddy-home"
WB_CLAUDE="$TMP/workbuddy-claude"
WB_AGENTS="$TMP/workbuddy-agents"
WB_WB="$TMP/workbuddy-wb"
WB_COMMANDS="$TMP/workbuddy-commands"
if run_sync "$WB_HOME" "$WB_CLAUDE" "$WB_AGENTS" "$WB_WB" "$WB_COMMANDS" \
     --apply --client workbuddy >/dev/null 2>&1 \
  && [[ -f "$WB_WB/icode/SKILL.md" ]] \
  && [[ ! -e "$WB_CLAUDE" ]] \
  && [[ ! -e "$WB_AGENTS" ]] \
  && cmp -s "$COMMAND_SOURCE" "$WB_COMMANDS/icode.md" \
  && grep -q '"artifact":"workbuddy-command"' \
       "$WB_COMMANDS/icode.md.icode-install-owner.json"; then
  ok "WorkBuddy installs its own skills root plus command bridge"
else
  bad "WorkBuddy installs its own skills root plus command bridge"
fi

BEFORE_SKILL_HASH="$(sha256sum "$WB_WB/icode/SKILL.md" 2>/dev/null || true)"
BEFORE_COMMAND_HASH="$(sha256sum "$WB_COMMANDS/icode.md" 2>/dev/null || true)"
BEFORE_MARKER_HASH="$(sha256sum "$WB_COMMANDS/icode.md.icode-install-owner.json" 2>/dev/null || true)"
if run_sync "$WB_HOME" "$WB_CLAUDE" "$WB_AGENTS" "$WB_WB" "$WB_COMMANDS" \
     --apply --client workbuddy >/dev/null 2>&1 \
  && [[ "$BEFORE_SKILL_HASH" == "$(sha256sum "$WB_WB/icode/SKILL.md")" ]] \
  && [[ "$BEFORE_COMMAND_HASH" == "$(sha256sum "$WB_COMMANDS/icode.md")" ]] \
  && [[ "$BEFORE_MARKER_HASH" == "$(sha256sum "$WB_COMMANDS/icode.md.icode-install-owner.json")" ]]; then
  ok "repeated WorkBuddy apply is content-idempotent"
else
  bad "repeated WorkBuddy apply is content-idempotent"
fi

CONFLICT_HOME="$TMP/conflict-home"
CONFLICT_CLAUDE="$TMP/conflict-claude"
CONFLICT_AGENTS="$TMP/conflict-agents"
CONFLICT_WB="$TMP/conflict-wb"
CONFLICT_COMMANDS="$TMP/conflict-commands"
mkdir -p "$CONFLICT_COMMANDS"
printf 'user-owned command\n' >"$CONFLICT_COMMANDS/icode.md"
CONFLICT_HASH="$(sha256sum "$CONFLICT_COMMANDS/icode.md")"
CONFLICT_OUTPUT=""
if CONFLICT_OUTPUT="$(run_sync "$CONFLICT_HOME" "$CONFLICT_CLAUDE" \
     "$CONFLICT_AGENTS" "$CONFLICT_WB" "$CONFLICT_COMMANDS" --apply --client workbuddy 2>&1)"; then
  bad "unmanaged WorkBuddy command rejects the whole transaction"
elif [[ ! -e "$CONFLICT_WB" ]] \
  && [[ ! -e "$CONFLICT_CLAUDE" ]] \
  && [[ ! -e "$CONFLICT_AGENTS" ]] \
  && [[ "$CONFLICT_HASH" == "$(sha256sum "$CONFLICT_COMMANDS/icode.md")" ]] \
  && grep -q 'WorkBuddy.*未托管' <<<"$CONFLICT_OUTPUT"; then
  ok "unmanaged WorkBuddy conflict fails before any skill write"
else
  bad "unmanaged WorkBuddy conflict fails before any skill write"
fi

ADOPT_HOME="$TMP/adopt-home"
ADOPT_WB="$TMP/adopt-wb"
ADOPT_COMMANDS="$TMP/adopt-commands"
mkdir -p "$ADOPT_COMMANDS"
cp "$COMMAND_SOURCE" "$ADOPT_COMMANDS/icode.md" 2>/dev/null || true
if run_sync "$ADOPT_HOME" "$TMP/adopt-claude" "$TMP/adopt-agents" "$ADOPT_WB" "$ADOPT_COMMANDS" \
     --apply --client workbuddy >/dev/null 2>&1 \
  && [[ -f "$ADOPT_COMMANDS/icode.md.icode-install-owner.json" ]] \
  && cmp -s "$COMMAND_SOURCE" "$ADOPT_COMMANDS/icode.md"; then
  ok "identical unmanaged WorkBuddy command is safely adopted"
else
  bad "identical unmanaged WorkBuddy command is safely adopted"
fi

ALL_HOME="$TMP/all-home"
ALL_CLAUDE="$TMP/all-claude"
ALL_AGENTS="$TMP/all-agents"
ALL_WB="$TMP/all-wb"
ALL_COMMANDS="$TMP/all-commands"
if run_sync "$ALL_HOME" "$ALL_CLAUDE" "$ALL_AGENTS" "$ALL_WB" "$ALL_COMMANDS" \
     --apply --client all >/dev/null 2>&1 \
  && [[ -f "$ALL_CLAUDE/icode/SKILL.md" ]] \
  && [[ -f "$ALL_AGENTS/icode/SKILL.md" ]] \
  && [[ -f "$ALL_WB/icode/SKILL.md" ]] \
  && cmp -s "$COMMAND_SOURCE" "$ALL_COMMANDS/icode.md"; then
  ok "all-client sync includes the WorkBuddy root and command when explicitly configured"
else
  bad "all-client sync includes the WorkBuddy root and command when explicitly configured"
fi

INSTALL_HOME="$TMP/install-home"
INSTALL_CLAUDE="$TMP/install-claude"
INSTALL_AGENTS="$TMP/install-agents"
INSTALL_WB="$TMP/install-wb"
INSTALL_COMMANDS="$TMP/install-commands"
if HOME="$INSTALL_HOME" \
     CLAUDE_SKILLS_ROOT="$INSTALL_CLAUDE" \
     AGENTS_SKILLS_ROOT="$INSTALL_AGENTS" \
     WORKBUDDY_SKILLS_ROOT="$INSTALL_WB" \
     WORKBUDDY_COMMANDS_DIR="$INSTALL_COMMANDS" \
     "$INSTALL" --dry-run --skip-mcp --client workbuddy >/dev/null 2>&1 \
  && [[ ! -e "$INSTALL_CLAUDE" ]] \
  && [[ ! -e "$INSTALL_AGENTS" ]] \
  && [[ ! -e "$INSTALL_WB" ]] \
  && [[ ! -e "$INSTALL_COMMANDS" ]]; then
  ok "public installer accepts WorkBuddy and remains zero-write in dry-run"
else
  bad "public installer accepts WorkBuddy and remains zero-write in dry-run"
fi

FAKE_PYTHON="$TMP/fake-python"
printf '#!/usr/bin/env bash\nexit 0\n' >"$FAKE_PYTHON"
chmod +x "$FAKE_PYTHON"
APPLY_INSTALL_HOME="$TMP/apply-install-home"
APPLY_INSTALL_WB="$TMP/apply-install-wb"
APPLY_INSTALL_COMMANDS="$TMP/apply-install-commands"
if HOME="$APPLY_INSTALL_HOME" \
     WORKBUDDY_SKILLS_ROOT="$APPLY_INSTALL_WB" \
     WORKBUDDY_COMMANDS_DIR="$APPLY_INSTALL_COMMANDS" \
     ICODE_PYTHON_BIN="$FAKE_PYTHON" \
     "$INSTALL" --client workbuddy --skip-mcp >/dev/null 2>&1 \
  && [[ -f "$APPLY_INSTALL_WB/icode/SKILL.md" ]] \
  && cmp -s "$COMMAND_SOURCE" "$APPLY_INSTALL_COMMANDS/icode.md"; then
  ok "public installer completes an isolated WorkBuddy apply"
else
  bad "public installer completes an isolated WorkBuddy apply"
fi

if grep -q 'claude|codex|codebuddy|workbuddy|all' <("$SYNC" --help) \
  && grep -q 'claude|codex|codebuddy|workbuddy|all' "$ROOT/install.sh"; then
  ok "CLI help exposes the current WorkBuddy contract"
else
  bad "CLI help exposes the current WorkBuddy contract"
fi

printf 'WorkBuddy sync contract: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
