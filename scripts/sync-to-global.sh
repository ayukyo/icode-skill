#!/usr/bin/env bash
# ============================================================
# sync-to-global.sh —— 镜像同步 dev_repo → 全局 skills 目录
#   (~/.claude/skills/icode/ + ~/.agents/skills/icode/ 共享目录, 供 CODEX 等其它 agent)
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
#   - 同一份排除规则依次镜像到多个目标目录, 保持规则唯一
#   - 默认 dry-run, 显式 --apply 才落地, 防止误操作
# ============================================================
set -euo pipefail

# 路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
GLOBAL_DIR="${GLOBAL_DIR:-$HOME/.claude/skills/icode}"
AGENTS_DIR="${AGENTS_DIR:-$HOME/.agents/skills/icode}"  # 跨工具共享 skills (CODEX 等其它 agent)

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
if [ ! -f "$DEV_REPO/.gitignore" ]; then
  echo "❌ $DEV_REPO/.gitignore 不存在,无法应用排除规则(.gitignore)" >&2
  exit 1
fi

# 传输引擎: rsync 优先(带 --delete 镜像 + .gitignore filter)。
# rsync 缺失时(如 Windows Git Bash 默认不含)自动降级到 cp 增量同步,
# 与 mcp/*/install.sh 的既有兜底模式一致; cp 兜底不做 --delete,
# 故 --no-delete 语义天然满足。
if command -v rsync >/dev/null 2>&1; then
  SYNC_ENGINE="rsync"
else
  SYNC_ENGINE="cp"
  echo "⚠️ rsync 未安装(Windows Git Bash 默认不含),自动降级到 cp 增量同步兜底。"
  echo "   cp 兜底不做 --delete(不删目标端多余文件),故 --no-delete 语义天然满足。"
  echo "   如需 rsync 全量镜像语义,请先安装: sudo apt install rsync / pacman -S rsync"
  echo ""
fi

if [ "$MODE" = "apply" ]; then
  echo "⚠️  即将执行实际同步 (--apply)  [引擎: $SYNC_ENGINE]"
  echo "   src: $DEV_REPO/"
  echo "   dst: $GLOBAL_DIR/"
  echo "   dst: $AGENTS_DIR/   (共享 skills, 供 CODEX 等其它 agent)"
  echo "   规则: 顶层 .gitignore + 默认排除 .git/ .claude/ demo/ tests/"
  echo ""
else
  echo "🔍 dry-run 模式 (默认),加 --apply 才会真正写入  [引擎: $SYNC_ENGINE]"
  echo "   src: $DEV_REPO/"
  echo "   dst: $GLOBAL_DIR/"
  echo "   dst: $AGENTS_DIR/   (共享 skills, 供 CODEX 等其它 agent)"
  echo ""
fi

# cp 兜底: 用 git ls-files 枚举"已跟踪 + 未忽略未跟踪"文件(自动应用 .gitignore),
# 再显式排除与 rsync --exclude 对齐的 .git/ .claude/ demo/ tests/。
# 用 tar 单流管道复制(不经变量, 避免 bash 命令替换剥掉 NUL 分隔符),
# 保留目录结构/权限, 避免逐文件 cp 在 Windows 上过慢。
sync_with_cp() {
  local dst="$1"
  mkdir -p "$dst"
  local count
  count="$(git -C "$DEV_REPO" ls-files --cached --others --exclude-standard \
    | grep -v -E '^(\.git/|\.claude/|demo/|tests/)' | wc -l)"
  if [ "$MODE" = "apply" ]; then
    ( cd "$DEV_REPO"
      git ls-files -z --cached --others --exclude-standard \
        | grep -z -v -E '^(\.git/|\.claude/|demo/|tests/)' \
        | tar --null -T - -cf - ) \
      | tar -xf - -C "$dst"
    echo "   ✅ copied $count files -> $dst"
  else
    echo "   (dry-run) would copy $count files -> $dst"
    git -C "$DEV_REPO" ls-files --cached --others --exclude-standard \
      | grep -v -E '^(\.git/|\.claude/|demo/|tests/)' | head -5 \
      | sed 's/^/   would copy: /'
  fi
}

# 核心命令 —— 同一份排除规则依次镜像到各目标目录
# --filter=':- .gitignore'  : rsync 自动读 dev_repo 顶层 .gitignore 并应用排除规则
# --exclude                 : 额外硬排除开发仓库本地产物 (防止 .gitignore 漏配)
# --delete                  : 镜像同步语义——删除目标端 dev_repo 已不存在的文件
#                            (dev repo 删除的文件会同步删除; --no-delete 可关闭)
#                            被 .gitignore 排除的 mcp/*/config.json 等用户配置不受影响
for dst in "$GLOBAL_DIR" "$AGENTS_DIR"; do
  if [ "$SYNC_ENGINE" = "rsync" ]; then
    rsync -avc --delete "${RSYNC_ARGS[@]}" \
      --filter=':- .gitignore' \
      --exclude='.git/' \
      --exclude='.claude/' \
      --exclude='demo/' \
      --exclude='tests/' \
      "$DEV_REPO/" "$dst/"
  else
    sync_with_cp "$dst"
  fi
done

echo ""
if [ "$MODE" = "apply" ]; then
  echo "✅ 同步完成: $GLOBAL_DIR/ + $AGENTS_DIR/"
  echo "   如 vision-bridge 等 MCP 子工程首次使用,"
  echo "   请执行: ./mcp/install.sh vision-bridge (会自动生成 config.json)"
else
  echo "ℹ️  dry-run 未做任何修改。确认无误后重跑加 --apply"
fi