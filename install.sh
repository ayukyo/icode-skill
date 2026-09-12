#!/usr/bin/env bash
# Public installer for ICODE, bundled shared skills, and optional MCP servers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SYNC="$ROOT/scripts/sync-to-global.sh"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options] [mcp-name]

Installs ICODE, its isolated DOCX runtime, all manifest-declared shared skills,
then installs MCPs.

Options:
  --client claude|codex|codebuddy|all Select target client (default: claude)
  --skip-mcp                Install ICODE, DOCX runtime and shared skills only
  --dry-run                 Report skill changes; do not write or install MCPs
  -h, --help                Show this help
EOF
}

CLIENT="claude"
SKIP_MCP=false
DRY_RUN=false
MCP_NAMES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --skip-mcp)
      SKIP_MCP=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
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
  claude|codex|codebuddy|all) ;;
  *)
    echo "❌ --client 取值须为 claude|codex|codebuddy|all (当前: $CLIENT)" >&2
    exit 2
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

SYNC_ARGS=(--client "$CLIENT")
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

CLAUDE_ROOT="${CLAUDE_SKILLS_ROOT:-$HOME/.claude/skills}"
CODEX_ROOT="${AGENTS_SKILLS_ROOT:-$HOME/.agents/skills}"
CLAUDE_ICODE="${GLOBAL_DIR:-$CLAUDE_ROOT/icode}"
CODEX_ICODE="${AGENTS_DIR:-$CODEX_ROOT/icode}"
case "$CLIENT" in
  # CodeBuddy 与 Claude Code 共用 ~/.claude/skills，ICODE 目录同 claude；
  # 两者差异仅在 MCP 注册目标（CodeBuddy 写 ~/.codebuddy/mcp.json），由 mcp/install.sh 处理。
  claude|codebuddy|all) ICODE_DIR="$CLAUDE_ICODE" ;;
  codex) ICODE_DIR="$CODEX_ICODE" ;;
esac

DOCX_BOOTSTRAP="$ICODE_DIR/tools/docx/bootstrap_runtime.py"
if [[ ! -f "$DOCX_BOOTSTRAP" || ! -r "$DOCX_BOOTSTRAP" ]]; then
  echo "❌ 已安装 ICODE 中缺少可读的 DOCX runtime 安装器: $DOCX_BOOTSTRAP" >&2
  exit 1
fi
DOCX_PYTHON="${ICODE_PYTHON_BIN:-python3}"
if ! command -v "$DOCX_PYTHON" >/dev/null 2>&1; then
  echo "❌ 未找到 DOCX runtime 的 Python: $DOCX_PYTHON" >&2
  exit 1
fi
"$DOCX_PYTHON" "$DOCX_BOOTSTRAP"

if [[ "$SKIP_MCP" == true ]]; then
  echo "✅ ICODE、自管 DOCX runtime 与共享技能安装完成；已按要求跳过 MCP"
  exit 0
fi

MCP_INSTALLER="$ICODE_DIR/mcp/install.sh"
if [[ ! -f "$MCP_INSTALLER" || ! -r "$MCP_INSTALLER" ]]; then
  echo "❌ 已安装 ICODE 中缺少可读的 MCP 入口: $MCP_INSTALLER" >&2
  exit 1
fi

bash "$MCP_INSTALLER" --client "$CLIENT" "${MCP_NAMES[@]}"
echo "✅ ICODE、共享技能与 MCP 安装完成"
