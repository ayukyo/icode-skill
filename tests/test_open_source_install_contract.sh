#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL="$ROOT/install.sh"
VALIDATE="$ROOT/tools/validate_skill_pack.py"
MANIFEST="$ROOT/skill-packs/manifest.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }

tree_hash() {
  local root="$1"
  find "$root" -type f -print0 | sort -z | xargs -0 sha256sum \
    | sha256sum | cut -d' ' -f1
}

run_install() {
  local fake_home="$1"
  local claude_root="$2"
  local agents_root="$3"
  shift 3
  HOME="$fake_home" \
  CLAUDE_SKILLS_ROOT="$claude_root" \
  AGENTS_SKILLS_ROOT="$agents_root" \
    "$INSTALL" "$@"
}

FAKE_HOME="$TMP/home-all"
CLAUDE_ROOT="$FAKE_HOME/.claude/skills"
AGENTS_ROOT="$FAKE_HOME/.agents/skills"
ALL_INSTALLED=false
if [[ -x "$INSTALL" ]] \
  && run_install "$FAKE_HOME" "$CLAUDE_ROOT" "$AGENTS_ROOT" \
       --client all --skip-mcp >/dev/null 2>&1; then
  ALL_INSTALLED=true
fi
if "$ALL_INSTALLED" \
  && [[ -f "$CLAUDE_ROOT/icode/SKILL.md" ]] \
  && [[ -f "$AGENTS_ROOT/icode/SKILL.md" ]] \
  && "$VALIDATE" --manifest "$MANIFEST" \
       --target-root "$CLAUDE_ROOT" --target-root "$AGENTS_ROOT" >/dev/null; then
  ok "fresh all-client install publishes ICODE and every shared skill"
else
  bad "fresh all-client install publishes ICODE and every shared skill"
fi

if "$ALL_INSTALLED" \
  && [[ "$(find "$CLAUDE_ROOT/icode/skill-packs" -type f -name SKILL.md | wc -l)" -eq 0 ]] \
  && [[ "$(find "$AGENTS_ROOT/icode/skill-packs" -type f -name SKILL.md | wc -l)" -eq 0 ]] \
  && [[ "$(find "$CLAUDE_ROOT" -mindepth 2 -maxdepth 2 -type f -name SKILL.md.template | wc -l)" -eq 0 ]] \
  && [[ "$(find "$AGENTS_ROOT" -mindepth 2 -maxdepth 2 -type f -name SKILL.md.template | wc -l)" -eq 0 ]]; then
  ok "installed roots expose no nested or top-level template duplicates"
else
  bad "installed roots expose no nested or top-level template duplicates"
fi

if "$ALL_INSTALLED"; then
  BEFORE_REPEAT="$(tree_hash "$FAKE_HOME")"
else
  BEFORE_REPEAT="missing"
fi
if "$ALL_INSTALLED" \
  && run_install "$FAKE_HOME" "$CLAUDE_ROOT" "$AGENTS_ROOT" \
       --client all --skip-mcp >/dev/null 2>&1 \
  && [[ "$BEFORE_REPEAT" == "$(tree_hash "$FAKE_HOME")" ]]; then
  ok "repeated public installation is content-idempotent"
else
  bad "repeated public installation is content-idempotent"
fi

DEFAULT_HOME="$TMP/home-default"
DEFAULT_CLAUDE="$DEFAULT_HOME/.claude/skills"
DEFAULT_AGENTS="$DEFAULT_HOME/.agents/skills"
if [[ -x "$INSTALL" ]] \
  && run_install "$DEFAULT_HOME" "$DEFAULT_CLAUDE" "$DEFAULT_AGENTS" \
       --skip-mcp >/dev/null 2>&1 \
  && [[ -f "$DEFAULT_CLAUDE/icode/SKILL.md" ]] \
  && [[ ! -e "$DEFAULT_AGENTS" ]]; then
  ok "default public installation targets Claude only"
else
  bad "default public installation targets Claude only"
fi

DRY_HOME="$TMP/home-dry"
if [[ -x "$INSTALL" ]] \
  && run_install "$DRY_HOME" "$DRY_HOME/.claude/skills" \
       "$DRY_HOME/.agents/skills" --dry-run --client all >/dev/null 2>&1 \
  && [[ ! -e "$DRY_HOME" ]]; then
  ok "public installer dry-run performs zero writes and skips MCP"
else
  bad "public installer dry-run performs zero writes and skips MCP"
fi

SELF_HOME="$TMP/home-self"
SELF_SOURCE="$SELF_HOME/.claude/skills/icode"
mkdir -p "$SELF_SOURCE"
(cd "$ROOT" && tar --exclude=.git --exclude=.worktrees -cf - .) \
  | tar -xf - -C "$SELF_SOURCE"
if [[ -x "$SELF_SOURCE/install.sh" ]] \
  && HOME="$SELF_HOME" \
     CLAUDE_SKILLS_ROOT="$SELF_HOME/.claude/skills" \
     AGENTS_SKILLS_ROOT="$SELF_HOME/.agents/skills" \
     "$SELF_SOURCE/install.sh" --client claude --skip-mcp >/dev/null 2>&1 \
  && [[ -f "$SELF_SOURCE/SKILL.md" ]] \
  && [[ -f "$SELF_HOME/.claude/skills/cross-layer-contract-audit/SKILL.md" ]]; then
  ok "direct clone inside Claude skills installs without self-copy failure"
else
  bad "direct clone inside Claude skills installs without self-copy failure"
fi

PREFLIGHT_HOME="$TMP/home-preflight"
PREFLIGHT_OUTPUT=""
if PREFLIGHT_OUTPUT="$(run_install "$PREFLIGHT_HOME" \
     "$PREFLIGHT_HOME/.claude/skills" "$PREFLIGHT_HOME/.agents/skills" \
     --client all missing-mcp 2>&1)"; then
  bad "unknown MCP component is rejected during preflight"
elif [[ ! -e "$PREFLIGHT_HOME" ]] \
  && grep -q '没有 install.sh' <<<"$PREFLIGHT_OUTPUT"; then
  ok "unknown MCP component is rejected before any skill write"
else
  bad "unknown MCP component is rejected before any skill write"
fi

REAL_MCP_SOURCE="$TMP/real-mcp-source"
mkdir -p "$REAL_MCP_SOURCE"
(cd "$ROOT" && tar --exclude=.git --exclude=.worktrees -cf - .) \
  | tar -xf - -C "$REAL_MCP_SOURCE"
find "$REAL_MCP_SOURCE/mcp" -mindepth 2 -type f -name install.sh -delete
mkdir -p "$REAL_MCP_SOURCE/mcp/fixture-mcp"
cat >"$REAL_MCP_SOURCE/mcp/fixture-mcp/install.sh" <<'SH'
#!/usr/bin/env bash
printf 'called\n' >"$MCP_CHILD_TRACE"
SH
chmod 0644 "$REAL_MCP_SOURCE/mcp/install.sh" \
  "$REAL_MCP_SOURCE/mcp/fixture-mcp/install.sh"
REAL_MCP_HOME="$TMP/home-real-mcp"
MCP_CHILD_TRACE="$TMP/mcp-child-trace.txt"
if HOME="$REAL_MCP_HOME" MCP_CHILD_TRACE="$MCP_CHILD_TRACE" \
     CLAUDE_SKILLS_ROOT="$REAL_MCP_HOME/.claude/skills" \
     AGENTS_SKILLS_ROOT="$REAL_MCP_HOME/.agents/skills" \
     "$REAL_MCP_SOURCE/install.sh" --client claude fixture-mcp \
       >/dev/null 2>&1 \
  && grep -qxF called "$MCP_CHILD_TRACE"; then
  ok "public installer accepts the real readable MCP entry without execute mode"
else
  bad "public installer accepts the real readable MCP entry without execute mode"
fi

MCP_SOURCE="$TMP/mcp-source"
mkdir -p "$MCP_SOURCE"
(cd "$ROOT" && tar --exclude=.git --exclude=.worktrees -cf - .) \
  | tar -xf - -C "$MCP_SOURCE"
cat >"$MCP_SOURCE/mcp/install.sh" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "--check" ]]; then
  exit 0
fi
printf '%s\n' "$@" >"$MCP_TRACE"
SH
chmod +x "$MCP_SOURCE/mcp/install.sh"
MCP_HOME="$TMP/home-mcp"
MCP_TRACE="$TMP/mcp-args.txt"
if [[ -x "$MCP_SOURCE/install.sh" ]] \
  && HOME="$MCP_HOME" MCP_TRACE="$MCP_TRACE" \
     CLAUDE_SKILLS_ROOT="$MCP_HOME/.claude/skills" \
     AGENTS_SKILLS_ROOT="$MCP_HOME/.agents/skills" \
     "$MCP_SOURCE/install.sh" --client all fixture-mcp >/dev/null 2>&1 \
  && [[ "$(sed -n '1p' "$MCP_TRACE")" == "--client" ]] \
  && [[ "$(sed -n '2p' "$MCP_TRACE")" == "all" ]] \
  && [[ "$(sed -n '3p' "$MCP_TRACE")" == "fixture-mcp" ]]; then
  ok "public installer invokes the installed MCP entry with exact arguments"
else
  bad "public installer invokes the installed MCP entry with exact arguments"
fi

MULTI_OUTPUT=""
if MULTI_OUTPUT="$(bash "$ROOT/mcp/install.sh" first second 2>&1)"; then
  bad "MCP installer rejects multiple component names"
elif grep -q '最多指定一个 MCP' <<<"$MULTI_OUTPUT"; then
  ok "MCP installer rejects multiple component names before installation"
else
  bad "MCP installer rejects multiple component names before installation"
fi

UNSUPPORTED_OUTPUT=""
if UNSUPPORTED_OUTPUT="$(bash "$ROOT/mcp/install.sh" --no-auto-install 2>&1)"; then
  bad "MCP installer rejects unsupported options"
elif grep -q '不支持的选项' <<<"$UNSUPPORTED_OUTPUT"; then
  ok "MCP installer rejects unsupported options explicitly"
else
  bad "MCP installer rejects unsupported options explicitly"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
