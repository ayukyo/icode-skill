#!/usr/bin/env bash
# mcp/ 顶层一键安装:扫描 mcp/*/install.sh,逐一执行。
# 每个子工程 install.sh 自带环境探测+查漏补缺+注册 ~/.claude.json。
# 用法:
#   ./mcp/install.sh                 # 扫描 + 全部安装
#   ./mcp/install.sh <name>          # 只装指定子工程(如 vision-bridge)
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

# 扫描所有 mcp/*/install.sh
shopt -s nullglob
installers=("$HERE"/*/install.sh)
shopt -u nullglob

if [ ${#installers[@]} -eq 0 ]; then
  echo "ℹ️  mcp/ 下没有子工程(未找到 * /install.sh)"
  exit 0
fi

# 若指定子工程,过滤
if [ -n "${1:-}" ]; then
  target="$HERE/$1/install.sh"
  if [ ! -f "$target" ]; then
    echo "❌ mcp/$1 下没有 install.sh"
    echo "   可用子工程:"
    for ins in "${installers[@]}"; do
      echo "     - $(basename "$(dirname "$ins")")"
    done
    exit 1
  fi
  installers=("$target")
fi

echo "📦 mcp 一键安装:扫描到 ${#installers[@]} 个子工程"
echo ""

ok_count=0
fail_count=0
failed=()

for installer in "${installers[@]}"; do
  name="$(basename "$(dirname "$installer")")"
  echo "─── $name ─────────────────────────────"
  if bash "$installer"; then
    ok_count=$((ok_count + 1))
  else
    fail_count=$((fail_count + 1))
    failed+=("$name")
  fi
  echo ""
done

echo "════════════════════════════════════════"
echo "✅ 成功: $ok_count"
if [ $fail_count -gt 0 ]; then
  echo "❌ 失败: $fail_count (${failed[*]})"
  exit 1
fi
echo "🎉 全部完成!记得重启 Claude Code 让注册生效。"
