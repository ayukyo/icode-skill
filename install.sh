#!/usr/bin/env bash
# Public installer for ICODE, bundled shared skills, and optional MCP servers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SYNC="$ROOT/scripts/sync-to-global.sh"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options] [mcp-name]

Installs ICODE, its isolated DOCX runtime and compatible owned renderer, all manifest-declared shared skills,
then installs MCPs.

Options:
  --client claude|codex|codebuddy|workbuddy|all Select target client (default: claude)
  --skip-mcp                Install ICODE, DOCX runtime and shared skills only
  --no-delete               Retain obsolete managed skills and MCPs
  --dry-run                 Report skill changes; do not write or install MCPs
  -h, --help                Show this help
EOF
}

CLIENT="claude"
SKIP_MCP=false
DRY_RUN=false
NO_DELETE=false
MCP_NAMES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --client)
      if [[ $# -lt 2 ]]; then
        echo "❌ --client 需要参数: claude|codex|codebuddy|workbuddy|all" >&2
        exit 2
      fi
      CLIENT="$2"
      shift 2
      ;;
    --client=*)
      CLIENT="${1#--client=}"
      shift
      ;;
    --skip-mcp)
      SKIP_MCP=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --no-delete)
      NO_DELETE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "❌ 不支持的选项: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      MCP_NAMES+=("$1")
      if [[ ${#MCP_NAMES[@]} -gt 1 ]]; then
        echo "❌ 最多指定一个 MCP 子工程" >&2
        exit 2
      fi
      shift
      ;;
  esac
done
case "$CLIENT" in
  claude|codex|codebuddy|workbuddy|all) ;;
  *)
    echo "❌ --client 取值须为 claude|codex|codebuddy|workbuddy|all (当前: $CLIENT)" >&2
    exit 2
    ;;
esac

# Resolve this once for pre-sync reconciliation and installed DOCX bootstraps.
_docx_py_ok() { [ "$("$@" -c 'print(1)' 2>/dev/null)" = "1" ]; }
DOCX_PYTHON=()
if [[ -n "${ICODE_PYTHON_BIN:-}" ]]; then
  DOCX_PYTHON=("$ICODE_PYTHON_BIN")
elif { [[ -n "${MSYSTEM:-}" ]] || [[ "$(uname -s 2>/dev/null)" == MINGW* ]] \
       || [[ "$(uname -s 2>/dev/null)" == MSYS* ]]; } && _docx_py_ok py -3; then
  DOCX_PYTHON=(py -3)
elif _docx_py_ok python3; then
  DOCX_PYTHON=(python3)
elif _docx_py_ok py -3; then
  DOCX_PYTHON=(py -3)
elif _docx_py_ok python; then
  DOCX_PYTHON=(python)
fi
if [[ ${#DOCX_PYTHON[@]} -eq 0 ]]; then
  echo "❌ 未找到可用的安装 Python（可设 ICODE_PYTHON_BIN 显式指定）" >&2
  exit 1
fi

CLAUDE_ROOT="${CLAUDE_SKILLS_ROOT:-$HOME/.claude/skills}"
CODEX_ROOT="${AGENTS_SKILLS_ROOT:-$HOME/.agents/skills}"
WORKBUDDY_ROOT="${WORKBUDDY_SKILLS_ROOT:-$HOME/.workbuddy/skills}"
[[ -z "${GLOBAL_DIR+x}" ]] || CLAUDE_ROOT="${CLAUDE_SKILLS_ROOT:-$(dirname "$GLOBAL_DIR")}"
[[ -z "${AGENTS_DIR+x}" ]] || CODEX_ROOT="${AGENTS_SKILLS_ROOT:-$(dirname "$AGENTS_DIR")}"
[[ -z "${WORKBUDDY_DIR+x}" ]] || WORKBUDDY_ROOT="${WORKBUDDY_SKILLS_ROOT:-$(dirname "$WORKBUDDY_DIR")}"
CLAUDE_ICODE="${GLOBAL_DIR:-$CLAUDE_ROOT/icode}"
CODEX_ICODE="${AGENTS_DIR:-$CODEX_ROOT/icode}"
WORKBUDDY_ICODE="${WORKBUDDY_DIR:-$WORKBUDDY_ROOT/icode}"
ICODE_TARGETS=()
SKILL_TARGETS=()
case "$CLIENT" in
  claude|codebuddy) ICODE_DIR="$CLAUDE_ICODE"; ICODE_TARGETS+=("$ICODE_DIR"); SKILL_TARGETS+=("$CLAUDE_ROOT") ;;
  codex) ICODE_DIR="$CODEX_ICODE"; ICODE_TARGETS+=("$ICODE_DIR"); SKILL_TARGETS+=("$CODEX_ROOT") ;;
  workbuddy) ICODE_DIR="$WORKBUDDY_ICODE"; ICODE_TARGETS+=("$ICODE_DIR"); SKILL_TARGETS+=("$WORKBUDDY_ROOT") ;;
  all)
    ICODE_DIR="$CLAUDE_ICODE"
    ICODE_TARGETS+=("$CLAUDE_ICODE" "$CODEX_ICODE")
    SKILL_TARGETS+=("$CLAUDE_ROOT" "$CODEX_ROOT")
    targets_overridden=false
    if [[ -n "${GLOBAL_DIR+x}" || -n "${CLAUDE_SKILLS_ROOT+x}" \
      || -n "${AGENTS_DIR+x}" || -n "${AGENTS_SKILLS_ROOT+x}" \
      || -n "${WORKBUDDY_DIR+x}" || -n "${WORKBUDDY_SKILLS_ROOT+x}" ]]; then
      targets_overridden=true
    fi
    if [[ -n "${WORKBUDDY_COMMANDS_DIR+x}" \
      || ( "$targets_overridden" == false && -d "$HOME/.workbuddy" ) ]]; then
      ICODE_TARGETS+=("$WORKBUDDY_ICODE")
      SKILL_TARGETS+=("$WORKBUDDY_ROOT")
    fi
    ;;
esac

if [[ ! -x "$SYNC" ]]; then
  echo "❌ 同步入口不存在或不可执行: $SYNC" >&2
  exit 1
fi

SOURCE_MCP_INSTALLER="$ROOT/mcp/install.sh"
if [[ "$DRY_RUN" == false && "$SKIP_MCP" == false ]]; then
  if [[ ! -f "$SOURCE_MCP_INSTALLER" || ! -r "$SOURCE_MCP_INSTALLER" ]]; then
    echo "❌ MCP 安装入口不存在或不可读: $SOURCE_MCP_INSTALLER" >&2
    exit 1
  fi
  # Validate the selected MCP and its host-side registration dependencies
  # before ICODE or shared skills are written.
  bash "$SOURCE_MCP_INSTALLER" --check --client "$CLIENT" "${MCP_NAMES[@]}"
fi

# Plan against the old installation before synchronization replaces its code.
# The nested MCP installer uses this plan, but the public root owns finish.
RECONCILE="$ROOT/mcp/_lib/install_reconcile.py"
unset ICODE_MCP_RECONCILE_PLAN ICODE_MCP_RECONCILE_ROOT_OWNER ICODE_DEFER_SKILL_RETIREMENT
if [[ "$SKIP_MCP" == false ]]; then
  MCP_PREPARE=(prepare --source-root "$ROOT" --client "$CLIENT")
  for target in "${ICODE_TARGETS[@]}"; do
    MCP_PREPARE+=(--installed-root "$target")
  done
  [[ ${#MCP_NAMES[@]} -eq 0 ]] || MCP_PREPARE+=(--single "${MCP_NAMES[0]}")
  [[ "$NO_DELETE" == false ]] || MCP_PREPARE+=(--no-delete)
  if [[ "$DRY_RUN" == true ]]; then
    "${DOCX_PYTHON[@]}" "$RECONCILE" "${MCP_PREPARE[@]}" --dry-run
  else
    ICODE_MCP_RECONCILE_PLAN="$("${DOCX_PYTHON[@]}" "$RECONCILE" "${MCP_PREPARE[@]}")"
    export ICODE_MCP_RECONCILE_PLAN
    export ICODE_MCP_RECONCILE_ROOT_OWNER=1
    export ICODE_DEFER_SKILL_RETIREMENT=1
    "${DOCX_PYTHON[@]}" "$RECONCILE" profile-preflight --plan "$ICODE_MCP_RECONCILE_PLAN"
  fi
fi

SYNC_ARGS=(--client "$CLIENT")
[[ "$NO_DELETE" == false ]] || SYNC_ARGS+=(--no-delete)
if [[ "$DRY_RUN" == true ]]; then
  SYNC_ARGS+=(--dry-run)
else
  SYNC_ARGS+=(--apply)
fi
"$SYNC" "${SYNC_ARGS[@]}"

if [[ "$DRY_RUN" == true ]]; then
  echo "ℹ️ dry-run 不安装 DOCX runtime 或 MCP"
  exit 0
fi

DOCX_BOOTSTRAP="$ICODE_DIR/tools/docx/bootstrap_runtime.py"
if [[ ! -f "$DOCX_BOOTSTRAP" || ! -r "$DOCX_BOOTSTRAP" ]]; then
  echo "❌ 已安装 ICODE 中缺少可读的 DOCX runtime 安装器: $DOCX_BOOTSTRAP" >&2
  exit 1
fi
"${DOCX_PYTHON[@]}" "$DOCX_BOOTSTRAP"
DOCX_RENDERER_BOOTSTRAP="$ICODE_DIR/tools/docx/bootstrap_renderer.py"
if [[ ! -f "$DOCX_RENDERER_BOOTSTRAP" || ! -r "$DOCX_RENDERER_BOOTSTRAP" ]]; then
  echo "❌ 已安装 ICODE 中缺少 DOCX renderer 安装器: $DOCX_RENDERER_BOOTSTRAP" >&2
  exit 1
fi
"${DOCX_PYTHON[@]}" "$DOCX_RENDERER_BOOTSTRAP"

if [[ "$SKIP_MCP" == true ]]; then
  echo "✅ ICODE、自管 DOCX runtime 与共享技能安装完成；已按要求跳过 MCP"
  exit 0
fi

MCP_INSTALLER="$ICODE_DIR/mcp/install.sh"
if [[ ! -f "$MCP_INSTALLER" || ! -r "$MCP_INSTALLER" ]]; then
  echo "❌ 已安装 ICODE 中缺少可读的 MCP 入口: $MCP_INSTALLER" >&2
  exit 1
fi

MCP_INSTALL_ARGS=(--client "$CLIENT" "${MCP_NAMES[@]}")
[[ "$NO_DELETE" == false ]] || MCP_INSTALL_ARGS+=(--no-delete)
bash "$MCP_INSTALLER" "${MCP_INSTALL_ARGS[@]}"
MCP_FINISH=(finish --plan "$ICODE_MCP_RECONCILE_PLAN")
for target in "${ICODE_TARGETS[@]}"; do
  MCP_FINISH+=(--installed-root "$target")
done
"${DOCX_PYTHON[@]}" "$RECONCILE" "${MCP_FINISH[@]}"
# Shared Skill removal has the same complete-install success gate as MCP
# removal. Active Skills were already updated by sync; only now may old
# entrypoints leave the discovery roots.
SKILL_FINISH=(--manifest "${SKILL_PACK_MANIFEST:-$ROOT/skill-packs/manifest.json}")
for target in "${SKILL_TARGETS[@]}"; do
  SKILL_FINISH+=(--target-root "$target")
done
[[ "$NO_DELETE" == false ]] || SKILL_FINISH+=(--keep-extra)
"${DOCX_PYTHON[@]}" "$ROOT/tools/install_skill_pack.py" "${SKILL_FINISH[@]}"
echo "✅ ICODE、共享技能与 MCP 安装完成"
