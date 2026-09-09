#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYNC="$ROOT/scripts/sync-to-global.sh"
VALIDATE="$ROOT/tools/validate_skill_pack.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }

PACKS="$TMP/packs"
mkdir -p "$PACKS/fixture-evidence-skill"
cat >"$PACKS/manifest.json" <<'JSON'
{
  "schema_version": 1,
  "skills": [{
    "name": "fixture-evidence-skill",
    "path": "fixture-evidence-skill",
    "entrypoint": "SKILL.md.template"
  }]
}
JSON
cat >"$PACKS/routes.json" <<'JSON'
{
  "schema_version": 1,
  "routes": [{
    "skill": "fixture-evidence-skill",
    "triggers": ["fixture"],
    "steps": ["verify"],
    "input_contract": ["source"],
    "output_contract": ["verdict"],
    "fallback": "record an honest downgrade"
  }]
}
JSON
cat >"$PACKS/fixture-evidence-skill/SKILL.md.template" <<'MARKDOWN'
---
name: fixture-evidence-skill
description: Use when a synchronization contract needs a deterministic fixture.
---

# Fixture Evidence Skill
MARKDOWN
printf 'fixture reference\n' >"$PACKS/fixture-evidence-skill/reference.md"

run_sync() {
  local claude_root="$1"
  local agents_root="$2"
  shift 2
  CLAUDE_SKILLS_ROOT="$claude_root" \
  AGENTS_SKILLS_ROOT="$agents_root" \
  SKILL_PACK_MANIFEST="$PACKS/manifest.json" \
  SKILL_ROUTES="$PACKS/routes.json" \
    "$SYNC" "$@"
}

if "$VALIDATE" --manifest "$PACKS/manifest.json" \
     --routes "$PACKS/routes.json" >/dev/null; then
  ok "manifest and routes accept template skill packs"
else
  bad "manifest and routes accept template skill packs"
fi

CLAUDE_ROOT="$TMP/all-claude"
AGENTS_ROOT="$TMP/all-agents"
BEFORE_CLAUDE="$(find "$CLAUDE_ROOT" -type f -printf '%P:%s\n' 2>/dev/null | sort || true)"
BEFORE_AGENTS="$(find "$AGENTS_ROOT" -type f -printf '%P:%s\n' 2>/dev/null | sort || true)"
if run_sync "$CLAUDE_ROOT" "$AGENTS_ROOT" --dry-run --client all >/dev/null 2>&1 \
  && [[ "$BEFORE_CLAUDE" == "$(find "$CLAUDE_ROOT" -type f -printf '%P:%s\n' 2>/dev/null | sort || true)" ]] \
  && [[ "$BEFORE_AGENTS" == "$(find "$AGENTS_ROOT" -type f -printf '%P:%s\n' 2>/dev/null | sort || true)" ]]; then
  ok "dry-run with both clients performs zero writes"
else
  bad "dry-run with both clients performs zero writes"
fi

mkdir -p "$CLAUDE_ROOT/icode/skill-packs/fixture-evidence-skill" \
  "$AGENTS_ROOT/icode/skill-packs/fixture-evidence-skill"
cp "$ROOT/SKILL.md" "$CLAUDE_ROOT/icode/SKILL.md"
cp "$ROOT/SKILL.md" "$AGENTS_ROOT/icode/SKILL.md"
printf 'legacy nested entry\n' \
  >"$CLAUDE_ROOT/icode/skill-packs/fixture-evidence-skill/SKILL.md"
printf 'legacy nested entry\n' \
  >"$AGENTS_ROOT/icode/skill-packs/fixture-evidence-skill/SKILL.md"
ALL_APPLY_OK=false
if run_sync "$CLAUDE_ROOT" "$AGENTS_ROOT" --apply --client all >/dev/null 2>&1 \
  && cmp -s "$ROOT/SKILL.md" "$CLAUDE_ROOT/icode/SKILL.md" \
  && cmp -s "$ROOT/SKILL.md" "$AGENTS_ROOT/icode/SKILL.md" \
  && cmp -s "$PACKS/fixture-evidence-skill/SKILL.md.template" \
       "$CLAUDE_ROOT/fixture-evidence-skill/SKILL.md" \
  && cmp -s "$PACKS/fixture-evidence-skill/SKILL.md.template" \
       "$AGENTS_ROOT/fixture-evidence-skill/SKILL.md" \
  && [[ ! -e "$CLAUDE_ROOT/fixture-evidence-skill/SKILL.md.template" ]] \
  && [[ ! -e "$AGENTS_ROOT/fixture-evidence-skill/SKILL.md.template" ]] \
  && [[ ! -e "$CLAUDE_ROOT/icode/.git" ]] \
  && [[ ! -e "$AGENTS_ROOT/icode/.git" ]] \
  && [[ ! -e "$CLAUDE_ROOT/icode/skill-packs/fixture-evidence-skill/SKILL.md" ]] \
  && [[ ! -e "$AGENTS_ROOT/icode/skill-packs/fixture-evidence-skill/SKILL.md" ]]; then
  ok "all-client apply publishes normalized top-level skills and removes legacy entries"
  ALL_APPLY_OK=true
else
  bad "all-client apply publishes normalized top-level skills and removes legacy entries"
fi

if "$ALL_APPLY_OK"; then
  printf 'local runtime config\n' >"$CLAUDE_ROOT/fixture-evidence-skill/config.json"
  printf 'local runtime config\n' >"$AGENTS_ROOT/fixture-evidence-skill/config.json"
  printf 'stale\n' >"$CLAUDE_ROOT/fixture-evidence-skill/stale.txt"
  printf 'stale\n' >"$AGENTS_ROOT/fixture-evidence-skill/stale.txt"
  printf 'updated reference\n' >"$PACKS/fixture-evidence-skill/reference.md"
  if run_sync "$CLAUDE_ROOT" "$AGENTS_ROOT" --apply --client all >/dev/null 2>&1 \
    && grep -qxF 'local runtime config' "$CLAUDE_ROOT/fixture-evidence-skill/config.json" \
    && grep -qxF 'local runtime config' "$AGENTS_ROOT/fixture-evidence-skill/config.json" \
    && grep -qxF 'updated reference' "$CLAUDE_ROOT/fixture-evidence-skill/reference.md" \
    && grep -qxF 'updated reference' "$AGENTS_ROOT/fixture-evidence-skill/reference.md" \
    && [[ ! -e "$CLAUDE_ROOT/fixture-evidence-skill/stale.txt" ]] \
    && [[ ! -e "$AGENTS_ROOT/fixture-evidence-skill/stale.txt" ]]; then
    ok "managed updates preserve runtime config and remove stale payload"
  else
    bad "managed updates preserve runtime config and remove stale payload"
  fi
else
  bad "managed updates preserve runtime config and remove stale payload"
fi

if "$VALIDATE" --manifest "$PACKS/manifest.json" \
     --target-root "$CLAUDE_ROOT" --target-root "$AGENTS_ROOT" >/dev/null; then
  ok "post-sync normalized hashes match both targets"
else
  bad "post-sync normalized hashes match both targets"
fi

CLAUDE_ONLY="$TMP/claude-only"
CLAUDE_OTHER="$TMP/claude-other"
if run_sync "$CLAUDE_ONLY" "$CLAUDE_OTHER" --apply --client claude >/dev/null 2>&1 \
  && [[ -f "$CLAUDE_ONLY/icode/SKILL.md" ]] \
  && [[ -f "$CLAUDE_ONLY/fixture-evidence-skill/SKILL.md" ]] \
  && [[ ! -e "$CLAUDE_OTHER" ]]; then
  ok "claude client selection writes only the Claude root"
else
  bad "claude client selection writes only the Claude root"
fi

CODEX_OTHER="$TMP/codex-other"
CODEX_ONLY="$TMP/codex-only"
if run_sync "$CODEX_OTHER" "$CODEX_ONLY" --apply --client codex >/dev/null 2>&1 \
  && [[ ! -e "$CODEX_OTHER" ]] \
  && [[ -f "$CODEX_ONLY/icode/SKILL.md" ]] \
  && [[ -f "$CODEX_ONLY/fixture-evidence-skill/SKILL.md" ]]; then
  ok "codex client selection writes only the Codex root"
else
  bad "codex client selection writes only the Codex root"
fi

CONFLICT_CLAUDE="$TMP/conflict-claude"
CONFLICT_AGENTS="$TMP/conflict-agents"
mkdir -p "$CONFLICT_AGENTS/fixture-evidence-skill"
printf 'user owned\n' >"$CONFLICT_AGENTS/fixture-evidence-skill/SKILL.md"
CONFLICT_HASH="$(sha256sum "$CONFLICT_AGENTS/fixture-evidence-skill/SKILL.md")"
CONFLICT_OUTPUT=""
if CONFLICT_OUTPUT="$(run_sync "$CONFLICT_CLAUDE" "$CONFLICT_AGENTS" \
     --apply --client all 2>&1)"; then
  bad "one unmanaged conflict rejects all selected roots"
elif [[ ! -e "$CONFLICT_CLAUDE" ]] \
  && [[ "$CONFLICT_HASH" == "$(sha256sum "$CONFLICT_AGENTS/fixture-evidence-skill/SKILL.md")" ]] \
  && grep -q '未托管的同名技能' <<<"$CONFLICT_OUTPUT"; then
  ok "one unmanaged conflict rejects all selected roots before writes"
else
  bad "one unmanaged conflict rejects all selected roots before writes"
fi

SELF_ROOT="$TMP/self-skills"
SELF_SOURCE="$SELF_ROOT/icode"
mkdir -p "$SELF_SOURCE"
(cd "$ROOT" && tar --exclude=.git --exclude=.worktrees -cf - .) \
  | tar -xf - -C "$SELF_SOURCE"
if CLAUDE_SKILLS_ROOT="$SELF_ROOT" \
     AGENTS_SKILLS_ROOT="$TMP/self-unused" \
     SKILL_PACK_MANIFEST="$PACKS/manifest.json" \
     SKILL_ROUTES="$PACKS/routes.json" \
     "$SELF_SOURCE/scripts/sync-to-global.sh" \
       --apply --client claude >/dev/null 2>&1 \
  && [[ -f "$SELF_SOURCE/SKILL.md" ]] \
  && [[ -f "$SELF_ROOT/fixture-evidence-skill/SKILL.md" ]]; then
  ok "source equal to ICODE destination is skipped safely"
else
  bad "source equal to ICODE destination is skipped safely"
fi

CP_CLAUDE="$TMP/cp-claude"
CP_AGENTS="$TMP/cp-agents"
mkdir -p "$CP_CLAUDE/icode/skill-packs/fixture-evidence-skill" \
  "$CP_AGENTS/icode/skill-packs/fixture-evidence-skill"
cp "$ROOT/SKILL.md" "$CP_CLAUDE/icode/SKILL.md"
cp "$ROOT/SKILL.md" "$CP_AGENTS/icode/SKILL.md"
printf legacy >"$CP_CLAUDE/icode/skill-packs/fixture-evidence-skill/SKILL.md"
printf legacy >"$CP_AGENTS/icode/skill-packs/fixture-evidence-skill/SKILL.md"
if ICODE_SYNC_ENGINE=cp run_sync "$CP_CLAUDE" "$CP_AGENTS" \
     --apply --client all >/dev/null 2>&1 \
  && [[ -f "$CP_CLAUDE/fixture-evidence-skill/SKILL.md" ]] \
  && [[ -f "$CP_AGENTS/fixture-evidence-skill/SKILL.md" ]] \
  && [[ ! -e "$CP_CLAUDE/icode/.git" ]] \
  && [[ ! -e "$CP_AGENTS/icode/.git" ]] \
  && [[ ! -e "$CP_CLAUDE/icode/skill-packs/fixture-evidence-skill/SKILL.md" ]] \
  && [[ ! -e "$CP_AGENTS/icode/skill-packs/fixture-evidence-skill/SKILL.md" ]]; then
  ok "copy fallback publishes templates and removes exact legacy entries"
else
  bad "copy fallback publishes templates and removes exact legacy entries"
fi

ATOMIC_CLAUDE="$TMP/atomic-claude"
ATOMIC_AGENTS="$TMP/atomic-agents"
mkdir -p "$ATOMIC_CLAUDE/icode" "$ATOMIC_AGENTS/icode"
cp "$ROOT/SKILL.md" "$ATOMIC_CLAUDE/icode/SKILL.md"
cp "$ROOT/SKILL.md" "$ATOMIC_AGENTS/icode/SKILL.md"
printf 'claude original\n' >"$ATOMIC_CLAUDE/icode/original.txt"
printf 'agents original\n' >"$ATOMIC_AGENTS/icode/original.txt"
ATOMIC_CLAUDE_HASH="$(find "$ATOMIC_CLAUDE/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
ATOMIC_AGENTS_HASH="$(find "$ATOMIC_AGENTS/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
FAKE_RSYNC_BIN="$TMP/fake-rsync-bin"
mkdir -p "$FAKE_RSYNC_BIN"
cat >"$FAKE_RSYNC_BIN/rsync" <<'SH'
#!/usr/bin/env bash
count=0
[[ -f "$RSYNC_CALLS" ]] && count="$(cat "$RSYNC_CALLS")"
count=$((count + 1))
printf '%s\n' "$count" >"$RSYNC_CALLS"
source_dir="${@: -2:1}"
target_dir="${@: -1}"
mkdir -p "$target_dir"
cp -a "$source_dir/." "$target_dir/"
[[ "$count" -ne 2 ]]
SH
chmod +x "$FAKE_RSYNC_BIN/rsync"
RSYNC_CALLS="$TMP/rsync-calls"
if PATH="$FAKE_RSYNC_BIN:$PATH" RSYNC_CALLS="$RSYNC_CALLS" \
     ICODE_SYNC_ENGINE=rsync run_sync "$ATOMIC_CLAUDE" "$ATOMIC_AGENTS" \
       --apply --client all >/dev/null 2>&1; then
  bad "ICODE staging failure keeps every selected target unchanged"
elif [[ "$ATOMIC_CLAUDE_HASH" == "$(find "$ATOMIC_CLAUDE/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]] \
  && [[ "$ATOMIC_AGENTS_HASH" == "$(find "$ATOMIC_AGENTS/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]] \
  && [[ -z "$(find "$ATOMIC_CLAUDE" "$ATOMIC_AGENTS" -maxdepth 1 -name '.icode-stage-*' -print -quit)" ]]; then
  ok "ICODE staging failure keeps every selected target unchanged"
else
  bad "ICODE staging failure keeps every selected target unchanged"
fi

ROLLBACK_CLAUDE="$TMP/rollback-claude"
ROLLBACK_AGENTS="$TMP/rollback-agents"
mkdir -p "$ROLLBACK_CLAUDE/icode" "$ROLLBACK_AGENTS/icode"
cp "$ROOT/SKILL.md" "$ROLLBACK_CLAUDE/icode/SKILL.md"
cp "$ROOT/SKILL.md" "$ROLLBACK_AGENTS/icode/SKILL.md"
printf 'claude rollback original\n' >"$ROLLBACK_CLAUDE/icode/original.txt"
printf 'agents rollback original\n' >"$ROLLBACK_AGENTS/icode/original.txt"
ROLLBACK_CLAUDE_HASH="$(find "$ROLLBACK_CLAUDE/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
ROLLBACK_AGENTS_HASH="$(find "$ROLLBACK_AGENTS/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
REAL_MV="$(command -v mv)"
FAKE_MV_BIN="$TMP/fake-mv-bin"
mkdir -p "$FAKE_MV_BIN"
cat >"$FAKE_MV_BIN/mv" <<SH
#!/usr/bin/env bash
source_dir="\${@: -2:1}"
target_dir="\${@: -1}"
if [[ -n "\${MV_FAIL_SOURCE_PATTERN:-}" \
  && "\$source_dir" == \${MV_FAIL_SOURCE_PATTERN} \
  && "\$target_dir" == \${MV_FAIL_TARGET_PATTERN} ]]; then
  exit 42
fi
exec "$REAL_MV" "\$@"
SH
cat >"$FAKE_MV_BIN/sleep" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$FAKE_MV_BIN/mv"
chmod +x "$FAKE_MV_BIN/sleep"
COMMIT_ROLLBACK_OK=true
export MV_FAIL_SOURCE_PATTERN
export MV_FAIL_TARGET_PATTERN
MV_FAIL_SOURCE_PATTERN="$ROLLBACK_AGENTS/.icode-stage-icode.*"
MV_FAIL_TARGET_PATTERN="$ROLLBACK_AGENTS/icode"
if PATH="$FAKE_MV_BIN:$PATH" \
     ICODE_SYNC_ENGINE=cp run_sync "$ROLLBACK_CLAUDE" "$ROLLBACK_AGENTS" \
       --apply --client all >/dev/null 2>&1; then
  COMMIT_ROLLBACK_OK=false
elif [[ "$ROLLBACK_CLAUDE_HASH" != "$(find "$ROLLBACK_CLAUDE/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]] \
  || [[ "$ROLLBACK_AGENTS_HASH" != "$(find "$ROLLBACK_AGENTS/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]]; then
  COMMIT_ROLLBACK_OK=false
fi
MV_FAIL_SOURCE_PATTERN="$ROLLBACK_AGENTS/icode"
MV_FAIL_TARGET_PATTERN="$ROLLBACK_AGENTS/.icode-backup-icode.*"
if PATH="$FAKE_MV_BIN:$PATH" \
     ICODE_SYNC_ENGINE=cp run_sync "$ROLLBACK_CLAUDE" "$ROLLBACK_AGENTS" \
       --apply --client all >/dev/null 2>&1; then
  COMMIT_ROLLBACK_OK=false
elif [[ "$ROLLBACK_CLAUDE_HASH" != "$(find "$ROLLBACK_CLAUDE/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]] \
  || [[ "$ROLLBACK_AGENTS_HASH" != "$(find "$ROLLBACK_AGENTS/icode" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]]; then
  COMMIT_ROLLBACK_OK=false
fi
if [[ "$COMMIT_ROLLBACK_OK" == true ]]; then
  ok "ICODE commit failure rolls back earlier selected targets"
else
  bad "ICODE commit failure rolls back earlier selected targets"
fi
unset MV_FAIL_SOURCE_PATTERN MV_FAIL_TARGET_PATTERN

cat >"$PACKS/bad.json" <<'JSON'
{"schema_version":1,"skills":[
  {"name":"../escape","path":"../escape","entrypoint":"SKILL.md.template"}
]}
JSON
if "$VALIDATE" --manifest "$PACKS/bad.json" >/dev/null 2>&1; then
  bad "validator rejects traversal and invalid names"
else
  ok "validator rejects traversal and invalid names"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
