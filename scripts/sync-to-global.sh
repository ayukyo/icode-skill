#!/usr/bin/env bash
# Mirror the ICODE runtime, manifest-declared shared skills, and host adapters.
# Developer default is a no-write dry-run for detected/specified clients.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# Test/CI/custom deployments commonly override host roots. In that mode an
# implicit probe of the caller's real HOME must not add an unrelated client.
SYNC_TARGETS_OVERRIDDEN=false
if [[ -n "${GLOBAL_DIR+x}" || -n "${CLAUDE_SKILLS_ROOT+x}" \
  || -n "${AGENTS_DIR+x}" || -n "${AGENTS_SKILLS_ROOT+x}" ]]; then
  SYNC_TARGETS_OVERRIDDEN=true
fi

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
CODEBUDDY_COMMAND_SOURCE="$DEV_REPO/integrations/codebuddy/commands/icode.md"
CODEBUDDY_LEGACY_COMMANDS_DIR="$DEV_REPO/integrations/codebuddy/commands/legacy"
if [[ -n "${CODEBUDDY_COMMANDS_DIR+x}" ]]; then
  CODEBUDDY_COMMANDS_EXPLICIT=true
else
  CODEBUDDY_COMMANDS_EXPLICIT=false
  CODEBUDDY_COMMANDS_DIR="$HOME/.codebuddy/commands"
fi
CODEBUDDY_COMMAND_TARGET="$CODEBUDDY_COMMANDS_DIR/icode.md"
CODEBUDDY_COMMAND_MARKER="$CODEBUDDY_COMMAND_TARGET.icode-install-owner.json"

usage() {
  cat <<'EOF'
Usage: ./scripts/sync-to-global.sh [options]

Options:
  --dry-run                 Report changes without writing (default)
  --apply                   Apply the synchronization
  --client claude|codex|codebuddy|all
                            Select host roots/adapters (default: all)
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
        echo "❌ --client 需要参数: claude|codex|codebuddy|all" >&2
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
  claude|codex|codebuddy|all) ;;
  *)
    echo "❌ --client 取值须为 claude|codex|codebuddy|all (当前: $CLIENT)" >&2
    exit 2
    ;;
esac

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    _bin="$(command -v "$candidate")"
    if [ -n "$("$_bin" --version 2>&1 | grep -i 'python')" ]; then
      PYTHON_BIN="$_bin"
      break
    fi
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
PUBLISH_CODEBUDDY_COMMAND=false
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
  codebuddy)
    # CodeBuddy scans the Claude-compatible skill root; only its command
    # bridge is client-specific, so no third ICODE payload copy is created.
    TARGET_ROOTS+=("$CLAUDE_SKILLS_ROOT")
    ICODE_DIRS+=("$GLOBAL_DIR")
    TARGET_LABELS+=("CodeBuddy (shared Claude skill root)")
    PUBLISH_CODEBUDDY_COMMAND=true
    ;;
  all)
    TARGET_ROOTS+=("$CLAUDE_SKILLS_ROOT" "$AGENTS_SKILLS_ROOT")
    ICODE_DIRS+=("$GLOBAL_DIR" "$AGENTS_DIR")
    TARGET_LABELS+=("Claude Code" "Codex")
    if [[ "$CODEBUDDY_COMMANDS_EXPLICIT" == true \
      || ( "$SYNC_TARGETS_OVERRIDDEN" == false && -d "$HOME/.codebuddy" ) ]]; then
      PUBLISH_CODEBUDDY_COMMAND=true
    fi
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

has_valid_codebuddy_command_marker() {
  local marker="$1"
  [[ -f "$marker" && ! -L "$marker" ]] || return 1
  "$PYTHON_BIN" - "$marker" <<'PY'
import json
import sys
from pathlib import Path
try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {
    "schema_version": 1,
    "owner": "icode-skill",
    "artifact": "codebuddy-command",
    "name": "icode",
}
raise SystemExit(0 if value == expected else 1)
PY
}

is_known_legacy_codebuddy_command() {
  local target="$1" candidate
  for candidate in "$CODEBUDDY_LEGACY_COMMANDS_DIR"/*.md; do
    [[ -f "$candidate" && ! -L "$candidate" ]] || continue
    if cmp -s "$candidate" "$target"; then
      return 0
    fi
  done
  return 1
}

preflight_codebuddy_command() {
  [[ "$PUBLISH_CODEBUDDY_COMMAND" == true ]] || return 0
  if [[ -z "$CODEBUDDY_COMMANDS_DIR" || "$CODEBUDDY_COMMANDS_DIR" == "/" ]]; then
    echo "❌ CodeBuddy 命令目录不安全: ${CODEBUDDY_COMMANDS_DIR:-<empty>}" >&2
    return 1
  fi
  if [[ ! -f "$CODEBUDDY_COMMAND_SOURCE" || -L "$CODEBUDDY_COMMAND_SOURCE" ]]; then
    echo "❌ CodeBuddy 命令模板不存在或不是普通文件: $CODEBUDDY_COMMAND_SOURCE" >&2
    return 1
  fi
  if [[ -e "$CODEBUDDY_COMMAND_MARKER" ]] \
    && ! has_valid_codebuddy_command_marker "$CODEBUDDY_COMMAND_MARKER"; then
    echo "❌ CodeBuddy 命令所有权标记无效: $CODEBUDDY_COMMAND_MARKER" >&2
    return 1
  fi
  if [[ ! -e "$CODEBUDDY_COMMAND_TARGET" ]]; then
    if [[ -e "$CODEBUDDY_COMMAND_MARKER" ]]; then
      echo "❌ CodeBuddy 命令缺失但所有权标记仍存在: $CODEBUDDY_COMMAND_MARKER" >&2
      return 1
    fi
    return 0
  fi
  if [[ ! -f "$CODEBUDDY_COMMAND_TARGET" || -L "$CODEBUDDY_COMMAND_TARGET" ]]; then
    echo "❌ CodeBuddy 命令目标不是普通文件: $CODEBUDDY_COMMAND_TARGET" >&2
    return 1
  fi
  if [[ -e "$CODEBUDDY_COMMAND_MARKER" ]]; then
    return 0
  fi
  if cmp -s "$CODEBUDDY_COMMAND_SOURCE" "$CODEBUDDY_COMMAND_TARGET"; then
    return 0
  fi
  if is_known_legacy_codebuddy_command "$CODEBUDDY_COMMAND_TARGET"; then
    return 0
  fi
  echo "❌ CodeBuddy 已存在未托管的 /icode 命令，拒绝覆盖: $CODEBUDDY_COMMAND_TARGET" >&2
  return 1
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

# All target conflicts are checked before any selected ICODE root is modified.
preflight_codebuddy_command
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
if [[ "$PUBLISH_CODEBUDDY_COMMAND" == true ]]; then
  if [[ "$MODE" == "dry-run" ]]; then
    echo "   CodeBuddy: (dry-run) would publish /icode -> $CODEBUDDY_COMMAND_TARGET"
  else
    echo "   CodeBuddy: publish /icode -> $CODEBUDDY_COMMAND_TARGET"
  fi
fi

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

publish_codebuddy_command() {
  [[ "$PUBLISH_CODEBUDDY_COMMAND" == true ]] || return 0
  [[ "$MODE" == "apply" ]] || return 0
  "$PYTHON_BIN" - "$CODEBUDDY_COMMAND_SOURCE" "$CODEBUDDY_COMMAND_TARGET" \
    "$CODEBUDDY_COMMAND_MARKER" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
marker = Path(sys.argv[3])
target.parent.mkdir(parents=True, exist_ok=True)
marker_value = {
    "schema_version": 1,
    "owner": "icode-skill",
    "artifact": "codebuddy-command",
    "name": "icode",
}

def atomic_publish_bytes(path: Path, payload: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.icode-stage-", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

atomic_publish_bytes(target, source.read_bytes(), 0o644)
atomic_publish_bytes(
    marker,
    (json.dumps(marker_value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(),
    0o644,
)
PY
  echo "   ✅ CodeBuddy /icode 命令桥已校验并发布"
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
      --exclude='.venv' --exclude='mcp/*/config.json' \
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

# Windows 实时防护(Defender/AV)可能短暂锁定刚写入的目录树，使目录 rename 偶发
# "Permission denied"。对事务提交/回滚中的目录重命名做有界延时重试，避免把
# 瞬时锁误判为事务失败而回滚；重试耗尽仍失败才真正报错。
retry_move_dir() {
  # Windows 上目录 rename 偶发 "Permission denied" 可持续到索引/防护扫描完成
  # (实测最长约 30s)。用较长重试窗口(max*delay≈72s)兜底,仍失败才真正报错。
  local src="$1" dst="$2" attempt=1 max=24 delay=3
  while :; do
    if mv -- "$src" "$dst" 2>/dev/null; then
      return 0
    fi
    if [[ "$attempt" -ge "$max" ]]; then
      echo "❌ 目录重命名重试 ${max} 次(约 $((max * delay))s)仍失败: $src -> $dst" >&2
      return 1
    fi
    sleep "$delay"
    attempt=$((attempt + 1))
  done
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
      if ! retry_move_dir "$dst" "$backup"; then
        echo "❌ 无法暂存原 ICODE 目标: $dst" >&2
        if [[ ! -e "$dst" && -e "$backup" ]]; then
          retry_move_dir "$backup" "$dst" || true
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
    if ! retry_move_dir "$stage" "$dst"; then
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
        retry_move_dir "${committed_backups[$rollback_index]}" \
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
  publish_codebuddy_command
  echo "✅ 同步完成：ICODE 与 manifest 共享技能均已校验"
else
  echo "ℹ️ dry-run 未做任何修改；确认后使用 --apply"
fi
