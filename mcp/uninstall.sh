#!/usr/bin/env bash
# mcp/ 顶层一键卸载:扫描 mcp/*/uninstall.sh,逐一执行。
# 与 mcp/install.sh 对称。
# 用法:
#   ./mcp/uninstall.sh                 # 扫描 + 全部卸载
#   ./mcp/uninstall.sh <name>          # 只卸载指定子工程
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

# 扫描所有 mcp/*/uninstall.sh
shopt -s nullglob
uninstallers=("$HERE"/*/uninstall.sh)
shopt -u nullglob

if [ ${#uninstallers[@]} -eq 0 ]; then
  echo "ℹ️  mcp/ 下没有子工程(未找到 * /uninstall.sh)"
  exit 0
fi

# 若指定子工程,过滤
if [ -n "${1:-}" ]; then
  target="$HERE/$1/uninstall.sh"
  if [ ! -f "$target" ]; then
    echo "❌ mcp/$1 下没有 uninstall.sh"
    echo "   可用子工程:"
    for un in "${uninstallers[@]}"; do
      echo "     - $(basename "$(dirname "$un")")"
    done
    exit 1
  fi
  uninstallers=("$target")
fi

echo "🧹 mcp 一键卸载:扫描到 ${#uninstallers[@]} 个子工程"
echo ""

ok_count=0
fail_count=0
failed=()

for un in "${uninstallers[@]}"; do
  name="$(basename "$(dirname "$un")")"
  echo "─── $name ─────────────────────────────"
  if bash "$un"; then
    ok_count=$((ok_count + 1))
  else
    fail_count=$((fail_count + 1))
    failed+=("$name")
  fi
  echo ""
done

echo "════════════════════════════════════════"
echo "✅ 卸载成功: $ok_count"
if [ $fail_count -gt 0 ]; then
  echo "❌ 失败: $fail_count (${failed[*]})"
  exit 1
fi
echo "🎉 全部卸载完成!重启 Claude Code 让注册失效。"
