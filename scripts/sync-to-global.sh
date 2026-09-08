#!/usr/bin/env bash
# Mirror the ICODE runtime and manifest-declared shared skills to host roots.
# Developer default is a no-write dry-run for both Claude Code and Codex.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -n "${GLOBAL_DIR+x}" ]]; then
  CLAUDE_SKILLS_ROOT="${CLAUDE_SKILLS_ROOT:-$(dirname "$GLOBAL_DIR")}"
else
  CLAUDE_SKILLS_ROOT="${CLAUDE_SKILLS_ROOT:-$HOME/.claude/skills}"
  GLOBAL_DIR="$CLAUDE_SKILLS_ROOT/icode"
fi
if [[ -n "${AGENTS_DIR+x}" ]]; then
  AGENTS_SKILLS_ROOT="${AGENTS_SKILLS_ROOT:-$(dirname "$AGENTS_DIR")}"
else
  AGENTS_SKILLS_ROOT="${AGENTS_SKILLS_ROOT:-$HOME/.agents/skills}"
  AGENTS_DIR="$AGENTS_SKILLS_ROOT/icode"
fi

SKILL_PACK_MANIFEST="${SKILL_PACK_MANIFEST:-$DEV_REPO/skill-packs/manifest.json}"
SKILL_PACK_VALIDATOR="$DEV_REPO/tools/validate_skill_pack.py"
SKILL_PACK_INSTALLER="$DEV_REPO/tools/install_skill_pack.py"
SKILL_ROUTES="${SKILL_ROUTES:-$DEV_REPO/mcp/workflow-gate/skill-routes.json}"

usage() {
  cat <<'EOF'
Usage: ./scripts/sync-to-global.sh [options]

Options:
  --dry-run                 Report changes without writing (default)
  --apply                   Apply the synchronization
  --client claude|codex|all Select host roots (default: all)
  --no-delete               Preserve target-only managed payload files
  -h, --help                Show this help
EOF
}

MODE="dry-run"
CLIENT="all"
NO_DELETE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      MODE="apply"
      shift
      ;;
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --no-delete)
      NO_DELETE=true
      shift
      ;;
    --client)
      if [[ $# -lt 2 ]]; then
        echo "❌ --client 需要参数: claude|codex|all" >&2
        exit 2
      fi
      CLIENT="$2"
      shift 2
      ;;
    --client=*)
      CLIENT="${1#--client=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "❌ 未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done
case "$CLIENT" in
  claude|codex|all) ;;
  *)
    echo "❌ --client 取值须为 claude|codex|all (当前: $CLIENT)" >&2
    exit 2
    ;;
esac

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "❌ 未找到 python3/python，共享技能校验与安装无法运行" >&2
  exit 1
fi
for required in "$DEV_REPO/.gitignore" "$SKILL_PACK_MANIFEST" "$SKILL_ROUTES"; do
  if [[ ! -f "$required" ]]; then
    echo "❌ 必需文件不存在: $required" >&2
    exit 1
  fi
done
for executable in "$SKILL_PACK_VALIDATOR" "$SKILL_PACK_INSTALLER"; do
  if [[ ! -x "$executable" ]]; then
    echo "❌ 必需工具不存在或不可执行: $executable" >&2
    exit 1
  fi
done
"$PYTHON_BIN" "$SKILL_PACK_VALIDATOR" \
  --manifest "$SKILL_PACK_MANIFEST" --routes "$SKILL_ROUTES" >/dev/null

case "${ICODE_SYNC_ENGINE:-auto}" in
  auto)
    if command -v rsync >/dev/null 2>&1; then
      SYNC_ENGINE="rsync"
    else
      SYNC_ENGINE="cp"
    fi
    ;;
  rsync)
    if ! command -v rsync >/dev/null 2>&1; then
      echo "❌ ICODE_SYNC_ENGINE=rsync，但 rsync 不在 PATH" >&2
      exit 1
    fi
    SYNC_ENGINE="rsync"
    ;;
  cp)
    SYNC_ENGINE="cp"
    ;;
  *)
    echo "❌ ICODE_SYNC_ENGINE 仅允许 auto|rsync|cp" >&2
    exit 2
    ;;
esac

TARGET_ROOTS=()
ICODE_DIRS=()
TARGET_LABELS=()
case "$CLIENT" in
  claude)
    TARGET_ROOTS+=("$CLAUDE_SKILLS_ROOT")
    ICODE_DIRS+=("$GLOBAL_DIR")
    TARGET_LABELS+=("Claude Code")
    ;;
  codex)
    TARGET_ROOTS+=("$AGENTS_SKILLS_ROOT")
    ICODE_DIRS+=("$AGENTS_DIR")
    TARGET_LABELS+=("Codex")
    ;;
  all)
    TARGET_ROOTS+=("$CLAUDE_SKILLS_ROOT" "$AGENTS_SKILLS_ROOT")
    ICODE_DIRS+=("$GLOBAL_DIR" "$AGENTS_DIR")
    TARGET_LABELS+=("Claude Code" "Codex")
    ;;
esac

canonical_path() {
  "$PYTHON_BIN" - "$1" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

same_path() {
  [[ "$(canonical_path "$1")" == "$(canonical_path "$2")" ]]
}

has_icode_frontmatter() {
  local skill_file="$1"
  [[ -f "$skill_file" ]] || return 1
  awk '
    /^---[[:space:]]*$/ { delimiters++; if (delimiters == 2) exit }
    delimiters == 1 && /^name:[[:space:]]*icode[[:space:]]*$/ { found=1 }
    END { exit(found ? 0 : 1) }
  ' "$skill_file"
}

has_valid_icode_marker() {
  local marker="$1"
  "$PYTHON_BIN" - "$marker" <<'PY'
import json
import sys
from pathlib import Path
try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {"schema_version": 1, "owner": "icode-skill", "skill": "icode"}
raise SystemExit(0 if value == expected else 1)
PY
}

preflight_icode_target() {
  local dst="$1"
  if same_path "$DEV_REPO" "$dst"; then
    return 0
  fi
  if [[ ! -e "$dst" ]]; then
    return 0
  fi
  if [[ ! -d "$dst" || -L "$dst" ]]; then
    echo "❌ ICODE 目标不是普通目录: $dst" >&2
    return 1
  fi
  if [[ -e "$dst/.icode-install-owner.json" ]]; then
    if has_valid_icode_marker "$dst/.icode-install-owner.json"; then
      return 0
    fi
    echo "❌ ICODE 所有权标记无效: $dst/.icode-install-owner.json" >&2
    return 1
  fi
  if has_icode_frontmatter "$dst/SKILL.md"; then
    return 0
  fi
  echo "❌ 目标目录已存在且不是可识别的 ICODE 安装: $dst" >&2
  return 1
}

INSTALL_ROOT_ARGS=()
for root in "${TARGET_ROOTS[@]}"; do
  INSTALL_ROOT_ARGS+=(--target-root "$root")
done

# All target conflicts are checked before either ICODE root is modified.
for dst in "${ICODE_DIRS[@]}"; do
  preflight_icode_target "$dst"
done
PREFLIGHT_ARGS=(--manifest "$SKILL_PACK_MANIFEST" "${INSTALL_ROOT_ARGS[@]}" --dry-run)
"$PYTHON_BIN" "$SKILL_PACK_INSTALLER" "${PREFLIGHT_ARGS[@]}" >/dev/null

if [[ "$SYNC_ENGINE" == "cp" ]]; then
  echo "⚠️ rsync 未使用，采用 cp/tar 增量同步；ICODE 目标独有文件不会删除。"
fi
if [[ "$MODE" == "apply" ]]; then
  echo "⚠️ 即将安装 ICODE 与共享技能 [client=$CLIENT engine=$SYNC_ENGINE]"
else
  echo "🔍 dry-run：不会写入文件 [client=$CLIENT engine=$SYNC_ENGINE]"
fi
for index in "${!ICODE_DIRS[@]}"; do
  echo "   ${TARGET_LABELS[$index]}: ${ICODE_DIRS[$index]}"
done

sync_with_cp() {
  local dst="$1"
  local report_dst="${2:-$dst}"
  local count
  count="$(git -C "$DEV_REPO" ls-files --cached --others --exclude-standard \
    | grep -v -E '^(\.git/|\.claude/|\.worktrees/|demo/|tests/)' | wc -l)"
  if [[ "$MODE" == "dry-run" ]]; then
    echo "   (dry-run) would copy $count ICODE files -> $report_dst"
    return 0
  fi
  mkdir -p "$dst"
  (
    cd "$DEV_REPO"
    git ls-files -z --cached --others --exclude-standard \
      | grep -z -v -E '^(\.git/|\.claude/|\.worktrees/|demo/|tests/)' \
      | tar --null -T - -cf -
  ) | tar -xf - -C "$dst"
  echo "   ✅ copied $count ICODE files -> $report_dst"
}

report_legacy_entrypoints() {
  local dst="$1"
  local skill_name skill_source entrypoint legacy
  while IFS=$'\t' read -r skill_name skill_source entrypoint; do
    [[ -n "$skill_name" ]] || continue
    legacy="$dst/skill-packs/$skill_name/SKILL.md"
    if [[ -e "$legacy" ]]; then
      if [[ "$NO_DELETE" == false ]]; then
        echo "   (dry-run) would remove legacy nested entry: $legacy"
      else
        echo "   ⚠️ --no-delete 保留旧嵌套入口: $legacy"
      fi
    fi
  done < <("$PYTHON_BIN" "$SKILL_PACK_VALIDATOR" \
    --manifest "$SKILL_PACK_MANIFEST" --list)
}

remove_legacy_entrypoints() {
  local dst="$1"
  local skill_name skill_source entrypoint legacy
  [[ "$NO_DELETE" == true ]] && return 0
  while IFS=$'\t' read -r skill_name skill_source entrypoint; do
    [[ -n "$skill_name" ]] || continue
    legacy="$dst/skill-packs/$skill_name/SKILL.md"
    [[ ! -e "$legacy" ]] || rm -f -- "$legacy"
  done < <("$PYTHON_BIN" "$SKILL_PACK_VALIDATOR" \
    --manifest "$SKILL_PACK_MANIFEST" --list)
}

write_icode_marker() {
  local dst="$1"
  printf '%s\n' \
    '{"schema_version":1,"owner":"icode-skill","skill":"icode"}' \
    >"$dst/.icode-install-owner.json"
}

RSYNC_DELETE_ARGS=(--delete)
[[ "$NO_DELETE" == true ]] && RSYNC_DELETE_ARGS=()

sync_icode_payload() {
  local dst="$1"
  local report_dst="${2:-$dst}"
  if [[ "$SYNC_ENGINE" == "rsync" ]]; then
    rsync -avc "${RSYNC_DELETE_ARGS[@]}" \
      --filter=':- .gitignore' \
      --exclude='.git' --exclude='.git/' \
      --exclude='.claude/' --exclude='.worktrees/' \
      --exclude='demo/' --exclude='tests/' \
      --exclude='.icode-install-owner.json' \
      "$DEV_REPO/" "$dst/"
  else
    sync_with_cp "$dst" "$report_dst"
  fi
}

remove_transaction_tree() {
  local path="$1"
  local base
  [[ -n "$path" && -e "$path" ]] || return 0
  base="$(basename "$path")"
  case "$base" in
    .icode-stage-*|.icode-backup-*) rm -rf -- "$path" ;;
    *)
      echo "❌ 拒绝清理非事务目录: $path" >&2
      return 1
      ;;
  esac
}

remove_committed_target() {
  local target="$1"
  local candidate matched=false
  for candidate in "${ICODE_DIRS[@]}"; do
    if [[ "$target" == "$candidate" ]] && ! same_path "$DEV_REPO" "$target"; then
      matched=true
      break
    fi
  done
  if [[ "$matched" != true ]]; then
    echo "❌ 拒绝回滚未声明的 ICODE 目标: $target" >&2
    return 1
  fi
  [[ ! -e "$target" ]] || rm -rf -- "$target"
}

ICODE_STAGE_DIRS=()
ICODE_STAGE_TARGETS=()

cleanup_prepared_stages() {
  local stage
  for stage in "${ICODE_STAGE_DIRS[@]}"; do
    remove_transaction_tree "$stage" || true
  done
}

prepare_icode_stage() {
  local dst="$1"
  local parent stage
  parent="$(dirname "$dst")"
  mkdir -p "$parent" || return 1
  stage="$(mktemp -d "$parent/.icode-stage-$(basename "$dst").XXXXXX")" \
    || return 1
  ICODE_STAGE_DIRS+=("$stage")
  ICODE_STAGE_TARGETS+=("$dst")
  if [[ -d "$dst" ]]; then
    cp -a "$dst/." "$stage/" || return 1
  fi
  sync_icode_payload "$stage" "$dst" || return 1
  remove_legacy_entrypoints "$stage" || return 1
  write_icode_marker "$stage" || return 1
  if ! has_icode_frontmatter "$stage/SKILL.md"; then
    echo "❌ ICODE 暂存结果缺少有效入口: $stage/SKILL.md" >&2
    return 1
  fi
  if [[ -e "$stage/.git" ]]; then
    echo "❌ ICODE 暂存结果意外包含 .git: $stage/.git" >&2
    return 1
  fi
}

commit_icode_stages() {
  local committed_targets=()
  local committed_backups=()
  local index dst stage parent backup rollback_index
  local commit_failed=false
  for index in "${!ICODE_STAGE_DIRS[@]}"; do
    dst="${ICODE_STAGE_TARGETS[$index]}"
    stage="${ICODE_STAGE_DIRS[$index]}"
    parent="$(dirname "$dst")"
    backup=""
    if [[ -e "$dst" ]]; then
      backup="$parent/.icode-backup-$(basename "$dst").$$.$index"
      if [[ -e "$backup" ]]; then
        echo "❌ ICODE 事务备份路径已存在: $backup" >&2
        commit_failed=true
        break
      fi
      if ! mv -- "$dst" "$backup"; then
        echo "❌ 无法暂存原 ICODE 目标: $dst" >&2
        if [[ ! -e "$dst" && -e "$backup" ]]; then
          mv -- "$backup" "$dst" || true
        fi
        commit_failed=true
        break
      fi
    fi
    # Record the original backup before publishing the staged tree so a failed
    # destination rename (including a partially effective wrapper) is handled
    # by the same rollback path as earlier committed targets.
    committed_targets+=("$dst")
    committed_backups+=("$backup")
    if ! mv -- "$stage" "$dst"; then
      echo "❌ 无法提交 ICODE 暂存目录，开始回滚: $dst" >&2
      commit_failed=true
      break
    fi
  done
  if [[ "$commit_failed" == true ]]; then
    for ((rollback_index=${#committed_targets[@]} - 1;
          rollback_index >= 0; rollback_index--)); do
      remove_committed_target "${committed_targets[$rollback_index]}" || true
      if [[ -n "${committed_backups[$rollback_index]}" \
        && -e "${committed_backups[$rollback_index]}" ]]; then
        mv -- "${committed_backups[$rollback_index]}" \
          "${committed_targets[$rollback_index]}" || true
      fi
    done
    cleanup_prepared_stages
    return 1
  fi
  for backup in "${committed_backups[@]}"; do
    [[ -z "$backup" ]] || remove_transaction_tree "$backup"
  done
}

if [[ "$MODE" == "dry-run" ]]; then
  for dst in "${ICODE_DIRS[@]}"; do
    if same_path "$DEV_REPO" "$dst"; then
      echo "   ↪ source 与 ICODE 目标相同，跳过自同步: $dst"
      continue
    fi
    if [[ "$SYNC_ENGINE" == "rsync" ]]; then
      if [[ ! -d "$(dirname "$dst")" ]]; then
        echo "   (dry-run) would mirror ICODE -> $dst"
      else
        rsync -avc --dry-run "${RSYNC_DELETE_ARGS[@]}" \
          --filter=':- .gitignore' \
          --exclude='.git' --exclude='.git/' \
          --exclude='.claude/' --exclude='.worktrees/' \
          --exclude='demo/' --exclude='tests/' \
          --exclude='.icode-install-owner.json' \
          "$DEV_REPO/" "$dst/"
      fi
    else
      sync_with_cp "$dst"
    fi
    report_legacy_entrypoints "$dst"
  done
else
  for dst in "${ICODE_DIRS[@]}"; do
    if same_path "$DEV_REPO" "$dst"; then
      echo "   ↪ source 与 ICODE 目标相同，跳过自同步: $dst"
      continue
    fi
    if ! prepare_icode_stage "$dst"; then
      echo "❌ ICODE 暂存失败，所有原目标保持不变: $dst" >&2
      cleanup_prepared_stages
      exit 1
    fi
  done
  if ! commit_icode_stages; then
    echo "❌ ICODE 提交失败；已尝试恢复全部原目标" >&2
    exit 1
  fi
fi

INSTALL_ARGS=(--manifest "$SKILL_PACK_MANIFEST" "${INSTALL_ROOT_ARGS[@]}")
if [[ "$MODE" == "dry-run" ]]; then
  INSTALL_ARGS+=(--dry-run)
fi
if [[ "$NO_DELETE" == true ]]; then
  INSTALL_ARGS+=(--keep-extra)
fi
"$PYTHON_BIN" "$SKILL_PACK_INSTALLER" "${INSTALL_ARGS[@]}"

if [[ "$MODE" == "apply" ]]; then
  VERIFY_ARGS=(--manifest "$SKILL_PACK_MANIFEST")
  for root in "${TARGET_ROOTS[@]}"; do
    VERIFY_ARGS+=(--target-root "$root")
  done
  [[ "$NO_DELETE" == true ]] && VERIFY_ARGS+=(--allow-extra)
  "$PYTHON_BIN" "$SKILL_PACK_VALIDATOR" "${VERIFY_ARGS[@]}" >/dev/null
  echo "✅ 同步完成：ICODE 与 manifest 共享技能均已校验"
else
  echo "ℹ️ dry-run 未做任何修改；确认后使用 --apply"
fi
