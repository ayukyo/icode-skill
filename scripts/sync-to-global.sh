#!/usr/bin/env bash
# ============================================================
# sync-to-global.sh —— 镜像同步 dev_repo → ~/.claude/skills/icode/
#
# 关键机制:
#   rsync --filter=':- .gitignore' 自动应用工程顶层 .gitignore 规则
#   确保 mcp/*/config.json 等用户本地运行时配置不被 --delete 误删
#
# 用法:
#   ./scripts/sync-to-global.sh                 # 默认 dry-run (推荐先用)
#   ./scripts/sync-to-global.sh --apply         # 实际执行同步
#   ./scripts/sync-to-global.sh --no-delete     # 不清理目标独有文件
#
# 设计意图:
#   - 单一入口, 所有规则集中在顶层 .gitignore
#   - 默认排除 .git/ .claude/ demo/ tests/ (开发仓库本地配置)
#   - 默认 dry-run, 显式 --apply 才落地, 防止误操作
# ============================================================
set -euo pipefail

# 路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
GLOBAL_DIR="${GLOBAL_DIR:-$HOME/.claude/skills/icode}"

# 参数解析
MODE="dry-run"
RSYNC_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --apply)        MODE="apply" ;;
    --dry-run)      MODE="dry-run" ;;
    --no-delete)    RSYNC_ARGS+=(--no-delete) ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "❌ 未知参数: $arg" >&2
      echo "   支持: --apply | --dry-run | --no-delete" >&2
      exit 2
      ;;
  esac
done

# 前置检查
if ! command -v rsync >/dev/null 2>&1; then
  echo "❌ rsync 未安装,请先安装: sudo apt install rsync" >&2
  exit 1
fi

if [ ! -f "$DEV_REPO/.gitignore" ]; then
  echo "❌ $DEV_REPO/.gitignore 不存在,无法应用 --filter=':- .gitignore'" >&2
  exit 1
fi

if [ "$MODE" = "apply" ]; then
  echo "⚠️  即将执行实际同步 (--apply)"
  echo "   src: $DEV_REPO/"
  echo "   dst: $GLOBAL_DIR/"
  echo "   规则: 顶层 .gitignore + 默认排除 .git/ .claude/ demo/ tests/"
  echo ""
else
  echo "🔍 dry-run 模式 (默认),加 --apply 才会真正写入"
  echo "   src: $DEV_REPO/"
  echo "   dst: $GLOBAL_DIR/"
  echo ""
fi

# 核心命令
# --filter=':- .gitignore'  : rsync 自动读 dev_repo 顶层 .gitignore 并应用排除规则
# --exclude                 : 额外硬排除开发仓库本地产物 (防止 .gitignore 漏配)
# --delete                  : 镜像同步语义——删除目标端 dev_repo 已不存在的文件
#                            (dev repo 删除的文件会同步删除; --no-delete 可关闭)
#                            被 .gitignore 排除的 mcp/*/config.json 等用户配置不受影响
rsync -avc --delete "${RSYNC_ARGS[@]}" \
  --filter=':- .gitignore' \
  --exclude='.git/' \
  --exclude='.claude/' \
  --exclude='demo/' \
  --exclude='tests/' \
  "$DEV_REPO/" "$GLOBAL_DIR/"

echo ""
if [ "$MODE" = "apply" ]; then
  echo "✅ 同步完成。如 vision-bridge 等 MCP 子工程首次使用,"
  echo "   请执行: ./mcp/install.sh vision-bridge (会自动生成 config.json)"
else
  echo "ℹ️  dry-run 未做任何修改。确认无误后重跑加 --apply"
fi