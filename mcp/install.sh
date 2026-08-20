#!/usr/bin/env bash
# mcp/ 顶层一键安装:扫描 mcp/*/install.sh,逐一执行。
# 每个子工程 install.sh 自带环境探测+查漏补缺+注册 Claude Code(~/.claude.json)。
# 可选: 注册到 Codex(codex mcp CLI), 依赖子工程 install 成功导出的 entry。
# 用法:
#   ./mcp/install.sh                      # 扫描 + 全部安装(默认只注册 Claude Code)
#   ./mcp/install.sh <name>               # 只装指定子工程(如 vision-bridge)
#   ./mcp/install.sh --client codex       # 全部安装 + 注册到 Codex
#   ./mcp/install.sh --client all <name>  # 指定子工程 + 注册到 Claude Code 和 Codex
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

# 探测 Python 解释器(Codex 分支跑 client_registry.py 用; 与子工程 install 探测模式一致)
PYTHON_BIN=""
for _py in python3 python; do
  if command -v "$_py" >/dev/null 2>&1; then
    PYTHON_BIN="$_py"
    break
  fi
done

# 扫描所有 mcp/*/install.sh
shopt -s nullglob
installers=("$HERE"/*/install.sh)
shopt -u nullglob

if [ ${#installers[@]} -eq 0 ]; then
  echo "ℹ️  mcp/ 下没有子工程(未找到 * /install.sh)"
  exit 0
fi

# 若指定子工程,过滤
if [ -n "${positional[0]:-}" ]; then
  target="$HERE/${positional[0]}/install.sh"
  if [ ! -f "$target" ]; then
    echo "❌ mcp/${positional[0]} 下没有 install.sh"
    echo "   可用子工程:"
    for ins in "${installers[@]}"; do
      echo "     - $(basename "$(dirname "$ins")")"
    done
    exit 1
  fi
  installers=("$target")
fi

echo "📦 mcp 一键安装:扫描到 ${#installers[@]} 个子工程"
case "$client" in
  claude) echo "   客户端: claude (~/.claude.json, 默认)" ;;
  codex)  echo "   客户端: codex (子工程注册 Claude + codex mcp 注册)" ;;
  all)    echo "   客户端: all (Claude Code + Codex 双注册)" ;;
esac
echo ""

ok_count=0
fail_count=0
failed=()

for installer in "${installers[@]}"; do
  name="$(basename "$(dirname "$installer")")"
  echo "─── $name ─────────────────────────────"
  if bash "$installer"; then
    ok_count=$((ok_count + 1))
    # Codex 分支: 依赖子工程 install 成功导出的 entry
    if [ "$client" = "codex" ] || [ "$client" = "all" ]; then
      if [ -z "$PYTHON_BIN" ]; then
        echo "   ⚠️ 未找到 python3/python，跳过 Codex 注册（client_registry.py 需 Python）"
        fail_count=$((fail_count + 1))
        failed+=("$name(codex:no-python)")
      elif "$PYTHON_BIN" "$HERE/_lib/client_registry.py" codex-register "$name"; then
        :
      else
        fail_count=$((fail_count + 1))
        failed+=("$name(codex)")
      fi
    fi
  else
    fail_count=$((fail_count + 1))
    failed+=("$name")
  fi
  echo ""
done

echo "════════════════════════════════════════"
echo "✅ Claude Code 成功: $ok_count"
if [ $fail_count -gt 0 ]; then
  echo "❌ 失败: $fail_count (${failed[*]})"
  exit 1
fi
case "$client" in
  codex|all)
    echo "🎉 全部完成!"
    echo "   Claude Code: 重启 Claude Code 后生效"
    echo "   Codex:       新建或重开 Codex 任务后生效(当前任务不会热加载新 MCP)"
    ;;
  *)
    echo "🎉 全部完成!记得重启 Claude Code 让注册生效。"
    ;;
esac
