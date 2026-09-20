#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYNC="$ROOT/scripts/sync-to-global.sh"
INSTALL="$ROOT/install.sh"
COMMAND_SOURCE="$ROOT/integrations/codebuddy/commands/icode.md"
LEGACY_COMMANDS_DIR="$ROOT/integrations/codebuddy/commands/legacy"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }

run_sync() {
  local test_home="$1"
  local claude_root="$2"
  local agents_root="$3"
  local command_dir="$4"
  shift 4
  HOME="$test_home" \
  CLAUDE_SKILLS_ROOT="$claude_root" \
  AGENTS_SKILLS_ROOT="$agents_root" \
  CODEBUDDY_COMMANDS_DIR="$command_dir" \
    "$SYNC" "$@"
}

DRY_HOME="$TMP/dry-home"
DRY_CLAUDE="$TMP/dry-claude"
DRY_AGENTS="$TMP/dry-agents"
DRY_COMMANDS="$TMP/dry-commands"
if run_sync "$DRY_HOME" "$DRY_CLAUDE" "$DRY_AGENTS" "$DRY_COMMANDS" \
     --dry-run --client codebuddy >/dev/null 2>&1 \
  && [[ ! -e "$DRY_CLAUDE" ]] \
  && [[ ! -e "$DRY_AGENTS" ]] \
  && [[ ! -e "$DRY_COMMANDS" ]]; then
  ok "CodeBuddy dry-run performs zero writes"
else
  bad "CodeBuddy dry-run performs zero writes"
fi

DETECTED_HOME="$TMP/detected-home"
mkdir -p "$DETECTED_HOME/.codebuddy"
DETECTED_OUTPUT="$(HOME="$DETECTED_HOME" "$SYNC" --dry-run --client all 2>&1)"
if grep -q 'CodeBuddy:.*would publish' <<<"$DETECTED_OUTPUT" \
  && [[ ! -e "$DETECTED_HOME/.codebuddy/commands" ]] \
  && [[ ! -e "$DETECTED_HOME/.claude" ]] \
  && [[ ! -e "$DETECTED_HOME/.agents" ]]; then
  ok "default all-client dry-run detects CodeBuddy without writing"
else
  bad "default all-client dry-run detects CodeBuddy without writing"
fi

UNDETECTED_HOME="$TMP/undetected-home"
UNDETECTED_OUTPUT="$(HOME="$UNDETECTED_HOME" "$SYNC" --dry-run --client all 2>&1)"
if ! grep -q 'CodeBuddy:.*would publish' <<<"$UNDETECTED_OUTPUT" \
  && [[ ! -e "$UNDETECTED_HOME" ]]; then
  ok "all-client sync does not create a phantom CodeBuddy host"
else
  bad "all-client sync does not create a phantom CodeBuddy host"
fi

CB_HOME="$TMP/codebuddy-home"
CB_CLAUDE="$TMP/codebuddy-claude"
CB_AGENTS="$TMP/codebuddy-agents"
CB_COMMANDS="$TMP/codebuddy-commands"
if run_sync "$CB_HOME" "$CB_CLAUDE" "$CB_AGENTS" "$CB_COMMANDS" \
     --apply --client codebuddy >/dev/null 2>&1 \
  && [[ -f "$CB_CLAUDE/icode/SKILL.md" ]] \
  && [[ ! -e "$CB_AGENTS" ]] \
  && cmp -s "$COMMAND_SOURCE" "$CB_COMMANDS/icode.md" \
  && grep -q '"artifact":"codebuddy-command"' \
       "$CB_COMMANDS/icode.md.icode-install-owner.json"; then
  ok "CodeBuddy installs shared Claude skills plus its command bridge"
else
  bad "CodeBuddy installs shared Claude skills plus its command bridge"
fi

BEFORE_COMMAND_HASH="$(sha256sum "$CB_COMMANDS/icode.md" 2>/dev/null || true)"
BEFORE_MARKER_HASH="$(sha256sum "$CB_COMMANDS/icode.md.icode-install-owner.json" 2>/dev/null || true)"
if run_sync "$CB_HOME" "$CB_CLAUDE" "$CB_AGENTS" "$CB_COMMANDS" \
     --apply --client codebuddy >/dev/null 2>&1 \
  && [[ "$BEFORE_COMMAND_HASH" == "$(sha256sum "$CB_COMMANDS/icode.md")" ]] \
  && [[ "$BEFORE_MARKER_HASH" == "$(sha256sum "$CB_COMMANDS/icode.md.icode-install-owner.json")" ]]; then
  ok "repeated CodeBuddy apply is content-idempotent"
else
  bad "repeated CodeBuddy apply is content-idempotent"
fi

ALL_HOME="$TMP/all-home"
ALL_CLAUDE="$TMP/all-claude"
ALL_AGENTS="$TMP/all-agents"
ALL_COMMANDS="$TMP/all-commands"
if run_sync "$ALL_HOME" "$ALL_CLAUDE" "$ALL_AGENTS" "$ALL_COMMANDS" \
     --apply --client all >/dev/null 2>&1 \
  && [[ -f "$ALL_CLAUDE/icode/SKILL.md" ]] \
  && [[ -f "$ALL_AGENTS/icode/SKILL.md" ]] \
  && cmp -s "$COMMAND_SOURCE" "$ALL_COMMANDS/icode.md"; then
  ok "all-client sync includes the CodeBuddy command when explicitly configured"
else
  bad "all-client sync includes the CodeBuddy command when explicitly configured"
fi

CONFLICT_HOME="$TMP/conflict-home"
CONFLICT_CLAUDE="$TMP/conflict-claude"
CONFLICT_AGENTS="$TMP/conflict-agents"
CONFLICT_COMMANDS="$TMP/conflict-commands"
mkdir -p "$CONFLICT_COMMANDS"
printf 'user-owned command\n' >"$CONFLICT_COMMANDS/icode.md"
CONFLICT_HASH="$(sha256sum "$CONFLICT_COMMANDS/icode.md")"
CONFLICT_OUTPUT=""
if CONFLICT_OUTPUT="$(run_sync "$CONFLICT_HOME" "$CONFLICT_CLAUDE" \
     "$CONFLICT_AGENTS" "$CONFLICT_COMMANDS" --apply --client all 2>&1)"; then
  bad "unmanaged CodeBuddy command rejects the whole transaction"
elif [[ ! -e "$CONFLICT_CLAUDE" ]] \
  && [[ ! -e "$CONFLICT_AGENTS" ]] \
  && [[ "$CONFLICT_HASH" == "$(sha256sum "$CONFLICT_COMMANDS/icode.md")" ]] \
  && grep -q 'CodeBuddy.*未托管' <<<"$CONFLICT_OUTPUT"; then
  ok "unmanaged CodeBuddy conflict fails before any skill write"
else
  bad "unmanaged CodeBuddy conflict fails before any skill write"
fi

ADOPT_HOME="$TMP/adopt-home"
ADOPT_CLAUDE="$TMP/adopt-claude"
ADOPT_AGENTS="$TMP/adopt-agents"
ADOPT_COMMANDS="$TMP/adopt-commands"
mkdir -p "$ADOPT_COMMANDS"
cp "$COMMAND_SOURCE" "$ADOPT_COMMANDS/icode.md" 2>/dev/null || true
if run_sync "$ADOPT_HOME" "$ADOPT_CLAUDE" "$ADOPT_AGENTS" "$ADOPT_COMMANDS" \
     --apply --client codebuddy >/dev/null 2>&1 \
  && [[ -f "$ADOPT_COMMANDS/icode.md.icode-install-owner.json" ]] \
  && cmp -s "$COMMAND_SOURCE" "$ADOPT_COMMANDS/icode.md"; then
  ok "identical unmanaged CodeBuddy command is safely adopted"
else
  bad "identical unmanaged CodeBuddy command is safely adopted"
fi

# Each published predecessor must upgrade, but a user-edited predecessor must not.
for LEGACY_COMMAND in "$LEGACY_COMMANDS_DIR"/*.md; do
  LEGACY_VERSION="$(basename "$LEGACY_COMMAND" .md)"
  LEGACY_HOME="$TMP/$LEGACY_VERSION-home"
  LEGACY_CLAUDE="$TMP/$LEGACY_VERSION-claude"
  LEGACY_AGENTS="$TMP/$LEGACY_VERSION-agents"
  LEGACY_COMMANDS="$TMP/$LEGACY_VERSION-commands"
  mkdir -p "$LEGACY_COMMANDS"
  cp "$LEGACY_COMMAND" "$LEGACY_COMMANDS/icode.md"
  if run_sync "$LEGACY_HOME" "$LEGACY_CLAUDE" "$LEGACY_AGENTS" "$LEGACY_COMMANDS" \
       --apply --client codebuddy >/dev/null 2>&1 \
    && cmp -s "$COMMAND_SOURCE" "$LEGACY_COMMANDS/icode.md" \
    && [[ -f "$LEGACY_COMMANDS/icode.md.icode-install-owner.json" ]]; then
    ok "exact $LEGACY_VERSION CodeBuddy bridge migrates safely"
  else
    bad "exact $LEGACY_VERSION CodeBuddy bridge migrates safely"
  fi

  CUSTOM_COMMANDS="$TMP/$LEGACY_VERSION-custom-commands"
  mkdir -p "$CUSTOM_COMMANDS"
  cp "$LEGACY_COMMAND" "$CUSTOM_COMMANDS/icode.md"
  printf '\nUser customization must survive.\n' >>"$CUSTOM_COMMANDS/icode.md"
  CUSTOM_HASH="$(sha256sum "$CUSTOM_COMMANDS/icode.md")"
  if run_sync "$TMP/custom-home" "$TMP/custom-claude" "$TMP/custom-agents" "$CUSTOM_COMMANDS" \
       --apply --client all >/dev/null 2>&1; then
    bad "customized $LEGACY_VERSION bridge must not be adopted"
  elif [[ "$CUSTOM_HASH" == "$(sha256sum "$CUSTOM_COMMANDS/icode.md")" ]] \
    && [[ ! -e "$TMP/custom-claude" && ! -e "$TMP/custom-agents" ]]; then
    ok "customized $LEGACY_VERSION bridge preserved with no skill writes"
  else
    bad "customized $LEGACY_VERSION rejection must preserve all targets"
  fi
done

INSTALL_HOME="$TMP/install-home"
INSTALL_CLAUDE="$TMP/install-claude"
INSTALL_AGENTS="$TMP/install-agents"
INSTALL_COMMANDS="$TMP/install-commands"
if HOME="$INSTALL_HOME" \
     CLAUDE_SKILLS_ROOT="$INSTALL_CLAUDE" \
     AGENTS_SKILLS_ROOT="$INSTALL_AGENTS" \
     CODEBUDDY_COMMANDS_DIR="$INSTALL_COMMANDS" \
     "$INSTALL" --dry-run --skip-mcp --client codebuddy >/dev/null 2>&1 \
  && [[ ! -e "$INSTALL_CLAUDE" ]] \
  && [[ ! -e "$INSTALL_AGENTS" ]] \
  && [[ ! -e "$INSTALL_COMMANDS" ]]; then
  ok "public installer accepts CodeBuddy and remains zero-write in dry-run"
else
  bad "public installer accepts CodeBuddy and remains zero-write in dry-run"
fi

FAKE_PYTHON="$TMP/fake-python"
printf '#!/usr/bin/env bash\nexit 0\n' >"$FAKE_PYTHON"
chmod +x "$FAKE_PYTHON"
APPLY_INSTALL_HOME="$TMP/apply-install-home"
APPLY_INSTALL_CLAUDE="$TMP/apply-install-claude"
APPLY_INSTALL_AGENTS="$TMP/apply-install-agents"
APPLY_INSTALL_COMMANDS="$TMP/apply-install-commands"
if HOME="$APPLY_INSTALL_HOME" \
     CLAUDE_SKILLS_ROOT="$APPLY_INSTALL_CLAUDE" \
     AGENTS_SKILLS_ROOT="$APPLY_INSTALL_AGENTS" \
     CODEBUDDY_COMMANDS_DIR="$APPLY_INSTALL_COMMANDS" \
     ICODE_PYTHON_BIN="$FAKE_PYTHON" \
     "$INSTALL" --client codebuddy --skip-mcp >/dev/null 2>&1 \
  && [[ -f "$APPLY_INSTALL_CLAUDE/icode/SKILL.md" ]] \
  && [[ ! -e "$APPLY_INSTALL_AGENTS" ]] \
  && cmp -s "$COMMAND_SOURCE" "$APPLY_INSTALL_COMMANDS/icode.md"; then
  ok "public installer completes an isolated CodeBuddy apply"
else
  bad "public installer completes an isolated CodeBuddy apply"
fi

if grep -q '/icode ui' "$COMMAND_SOURCE" 2>/dev/null \
  && grep -q 'claude|codex|codebuddy|all' <("$SYNC" --help); then
  ok "published bridge and CLI help expose the current CodeBuddy contract"
else
  bad "published bridge and CLI help expose the current CodeBuddy contract"
fi

printf 'CodeBuddy sync contract: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
