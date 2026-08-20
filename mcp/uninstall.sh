#!/usr/bin/env bash
# mcp/ 顶层一键卸载:扫描 mcp/*/uninstall.sh,逐一执行。
# 与 mcp/install.sh 对称。
# 用法:
#   ./mcp/uninstall.sh                    # 扫描 + 全部卸载(默认只清 Claude Code)
#   ./mcp/uninstall.sh <name>             # 只卸载指定子工程
#   ./mcp/uninstall.sh --client codex     # 全部卸载 + 同时清 Codex 注册
#   --client 取值: claude(默认)| codex | all
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

# 解析参数: --client <v> 或 --client=<v>, 其余为子工程名
client="claude"
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --client)
      if [ $# -lt 2 ]; then echo "❌ --client 需要参数: claude|codex|all"; exit 1; fi
      client="$2"; shift 2 ;;
    --client=*)
      client="${1#--client=}"; shift ;;
    *)
      positional+=("$1"); shift ;;
  esac
done
case "$client" in
  claude|codex|all) ;;
  *) echo "❌ --client 取值须为 claude|codex|all (当前: $client)"; exit 1 ;;
esac

# 探测 Python 解释器(Codex 分支跑 client_registry.py 用; 与子工程 uninstall 探测模式一致)
PYTHON_BIN=""
for _py in python3 python; do
  if command -v "$_py" >/dev/null 2>&1; then
    PYTHON_BIN="$_py"
    break
  fi
done

# 扫描所有 mcp/*/uninstall.sh
shopt -s nullglob
uninstallers=("$HERE"/*/uninstall.sh)
shopt -u nullglob

if [ ${#uninstallers[@]} -eq 0 ]; then
  echo "ℹ️  mcp/ 下没有子工程(未找到 * /uninstall.sh)"
  exit 0
fi

# 若指定子工程,过滤
if [ -n "${positional[0]:-}" ]; then
  target="$HERE/${positional[0]}/uninstall.sh"
  if [ ! -f "$target" ]; then
    echo "❌ mcp/${positional[0]} 下没有 uninstall.sh"
    echo "   可用子工程:"
    for un in "${uninstallers[@]}"; do
      echo "     - $(basename "$(dirname "$un")")"
    done
    exit 1
  fi
  uninstallers=("$target")
fi

echo "🧹 mcp 一键卸载:扫描到 ${#uninstallers[@]} 个子工程"
case "$client" in
  claude) echo "   客户端: claude (~/.claude.json, 默认)" ;;
  codex)  echo "   客户端: codex (子工程卸载 Claude + codex mcp 移除)" ;;
  all)    echo "   客户端: all (Claude Code + Codex 双清理)" ;;
esac
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
  # Codex 分支: 与子工程卸载结果独立(对称清理), 未注册幂等跳过
  if [ "$client" = "codex" ] || [ "$client" = "all" ]; then
    if [ -z "$PYTHON_BIN" ]; then
      echo "   ⚠️ 未找到 python3/python，跳过 Codex 清理（client_registry.py 需 Python）"
      fail_count=$((fail_count + 1))
      failed+=("$name(codex:no-python)")
    elif "$PYTHON_BIN" "$HERE/_lib/client_registry.py" codex-unregister "$name"; then
      :
    else
      fail_count=$((fail_count + 1))
      failed+=("$name(codex)")
    fi
  fi
  echo ""
done

echo "════════════════════════════════════════"
echo "✅ 卸载成功: $ok_count"
if [ $fail_count -gt 0 ]; then
  echo "❌ 失败: $fail_count (${failed[*]})"
  exit 1
fi
case "$client" in
  codex|all)
    echo "🎉 全部卸载完成!"
    echo "   Claude Code: 重启 Claude Code 后生效"
    echo "   Codex:       新建或重开 Codex 任务后生效"
    ;;
  *)
    echo "🎉 全部卸载完成!重启 Claude Code 让注册失效。"
    ;;
esac
