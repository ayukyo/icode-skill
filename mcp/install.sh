#!/usr/bin/env bash
# mcp/ 顶层一键安装:扫描 mcp/*/install.sh,逐一执行。
# 每个子工程 install.sh 自带环境探测+查漏补缺+注册 Claude Code(~/.claude.json)。
# 可选: 注册到 Codex(codex mcp CLI), 依赖子工程 install 成功导出的 entry。
# 用法:
#   ./mcp/install.sh                      # 扫描 + 全部安装(默认只注册 Claude Code)
#   ./mcp/install.sh <name>               # 只装指定子工程(如 vision-bridge)
#   ./mcp/install.sh --client codex       # 全部安装 + 注册到 Codex
#   ./mcp/install.sh --client codebuddy   # 全部安装 + 注册到 CodeBuddy(~/.codebuddy/mcp.json)
#   ./mcp/install.sh --client workbuddy   # 全部安装 + 注册到 WorkBuddy(~/.workbuddy/mcp.json)
#   ./mcp/install.sh --client all <name>  # 指定子工程 + 注册到 Claude Code/Codex/检测到的 CodeBuddy/WorkBuddy
#   --client 取值: claude(默认)| codex | codebuddy | workbuddy | all
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<'EOF'
Usage: ./mcp/install.sh [--check|--dry-run] [--no-delete] [--client claude|codex|codebuddy|workbuddy|all] [mcp-name]

Installs all MCP subprojects, or one named subproject. This is the MCP-only
maintenance entry; use the repository root install.sh for a full installation.

Options:
  --check  Validate arguments and selected installer entrypoints without writes.
  --dry-run  Preview managed registration changes without writes or installs.
  --no-delete  Retain obsolete managed registrations and runtime directories.
EOF
}

# 解析参数: --client <v> 或 --client=<v>, 其余为子工程名
client="claude"
check_only=false
dry_run=false
no_delete=false
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check)
      check_only=true; shift ;;
    --dry-run)
      dry_run=true; shift ;;
    --no-delete)
      no_delete=true; shift ;;
    --client)
      if [ $# -lt 2 ]; then echo "❌ --client 需要参数: claude|codex|codebuddy|workbuddy|all"; exit 1; fi
      client="$2"; shift 2 ;;
    --client=*)
      client="${1#--client=}"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    -*)
      echo "❌ 不支持的选项: $1" >&2
      usage >&2
      exit 2 ;;
    *)
      positional+=("$1")
      if [ ${#positional[@]} -gt 1 ]; then
        echo "❌ 最多指定一个 MCP 子工程" >&2
        exit 2
      fi
      shift ;;
  esac
done
case "$client" in
  claude|codex|codebuddy|workbuddy|all) ;;
  *) echo "❌ --client 取值须为 claude|codex|codebuddy|workbuddy|all (当前: $client)"; exit 1 ;;
esac

# `all` preserves the historical Claude+Codex path and adds CodeBuddy/WorkBuddy
# only when that host is detected. Explicit `codebuddy`/`workbuddy` always selects it.
codebuddy_enabled=false
if [ "$client" = "codebuddy" ] \
  || { [ "$client" = "all" ] && [ -d "$HOME/.codebuddy" ]; }; then
  codebuddy_enabled=true
fi
workbuddy_enabled=false
if [ "$client" = "workbuddy" ] \
  || { [ "$client" = "all" ] && [ -d "$HOME/.workbuddy" ]; }; then
  workbuddy_enabled=true
fi

# 探测 Python 解释器(Codex 分支跑 client_registry.py 用; 与子工程 install 探测模式一致)
# Windows 上裸 python3/python 可能是 WindowsApps 假 stub（无输出但退出码 0），
# 用真实输出验证；同时补 py 启动器回退（Windows 只有 py 可用时仍能注册 MCP）。
_py_ok() { [ "$("$@" -c 'print(1)' 2>/dev/null)" = "1" ]; }
PYTHON_BIN=()
if [ -n "${ICODE_PYTHON_BIN:-}" ] && _py_ok "$ICODE_PYTHON_BIN"; then
  PYTHON_BIN=("$ICODE_PYTHON_BIN")
elif { [ -n "${MSYSTEM:-}" ] || case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*) true;; *) false;; esac; } \
   && _py_ok py -3; then
  PYTHON_BIN=(py -3)
elif _py_ok python3; then
  PYTHON_BIN=(python3)
elif _py_ok py -3; then
  PYTHON_BIN=(py -3)
elif _py_ok python; then
  PYTHON_BIN=(python)
fi

# A failed update may retain an old installer for recovery. Its presence must
# not add a retired service back to the current distribution's desired set.
RECONCILE="$HERE/_lib/install_reconcile.py"
if [ ${#PYTHON_BIN[@]} -eq 0 ]; then
  echo "❌ MCP 安装收敛需要可用 Python" >&2
  exit 1
fi
DESIRED_ARGS=(desired --source-root "$HERE/..")
if [ -n "${ICODE_MCP_RECONCILE_PLAN:-}" ]; then
  DESIRED_ARGS+=(--plan "$ICODE_MCP_RECONCILE_PLAN")
fi
desired_names="$("${PYTHON_BIN[@]}" "$RECONCILE" "${DESIRED_ARGS[@]}")"
installers=()
while IFS= read -r name; do
  [ -z "$name" ] || installers+=("$HERE/$name/install.sh")
done <<<"$desired_names"

# 若指定子工程,过滤
if [ -n "${positional[0]:-}" ]; then
  target="$HERE/${positional[0]}/install.sh"
  if [ ! -f "$target" ] || ! printf '%s\n' "$desired_names" | grep -qxF -- "${positional[0]}"; then
    echo "❌ mcp/${positional[0]} 下没有 install.sh"
    echo "   可用子工程:"
    for ins in "${installers[@]}"; do
      echo "     - $(basename "$(dirname "$ins")")"
    done
    exit 1
  fi
  installers=("$target")
fi

# The public root installer calls this before writing ICODE or shared skills.
# Entrypoints are invoked through bash, so readability and syntax matter; an
# executable bit is intentionally not required for historical compatibility.
for installer in "${installers[@]}"; do
  if [ ! -f "$installer" ] || [ ! -r "$installer" ]; then
    echo "❌ MCP 安装入口不可读: $installer" >&2
    exit 1
  fi
  if ! bash -n "$installer"; then
    echo "❌ MCP 安装入口语法检查失败: $installer" >&2
    exit 1
  fi
done
if [ "$client" = "codex" ] || [ "$client" = "codebuddy" ] || [ "$client" = "workbuddy" ] || [ "$client" = "all" ]; then
  if [ ${#PYTHON_BIN[@]} -eq 0 ]; then
    echo "❌ Codex/CodeBuddy/WorkBuddy MCP 注册需要 python3/python" >&2
    exit 1
  fi
  if [ ! -f "$HERE/_lib/client_registry.py" ] \
    || [ ! -r "$HERE/_lib/client_registry.py" ]; then
    echo "❌ MCP 跨客户端注册入口不可读: $HERE/_lib/client_registry.py" >&2
    exit 1
  fi
fi
if [ "$check_only" = true ]; then
  echo "✅ MCP 预检通过: ${#installers[@]} 个子工程 [client=$client]"
  exit 0
fi

PREPARE_ARGS=(prepare --source-root "$HERE/.." --installed-root "$HERE/.." --client "$client")
[ -z "${positional[0]:-}" ] || PREPARE_ARGS+=(--single "${positional[0]}")
[ "$no_delete" = false ] || PREPARE_ARGS+=(--no-delete)
if [ "$dry_run" = true ]; then
  "${PYTHON_BIN[@]}" "$RECONCILE" "${PREPARE_ARGS[@]}" --dry-run
  exit 0
fi
if [ -z "${ICODE_MCP_RECONCILE_PLAN:-}" ]; then
  ICODE_MCP_RECONCILE_PLAN="$("${PYTHON_BIN[@]}" "$RECONCILE" "${PREPARE_ARGS[@]}")"
fi
# Host discovery is frozen before root synchronization can create command
# bridge directories. Register only hosts included in this plan's success gate.
selected_hosts="$("${PYTHON_BIN[@]}" "$RECONCILE" hosts --plan "$ICODE_MCP_RECONCILE_PLAN")"
ICODE_MCP_SELECTED_HOSTS="$(printf '%s' "$selected_hosts" | tr '\n' ',')"
export ICODE_MCP_SELECTED_HOSTS
"${PYTHON_BIN[@]}" "$RECONCILE" profile-preflight --plan "$ICODE_MCP_RECONCILE_PLAN"
claude_enabled=false
codex_enabled=false
codebuddy_enabled=false
workbuddy_enabled=false
while IFS= read -r selected_host; do
  case "$selected_host" in
    claude) claude_enabled=true ;;
    codex) codex_enabled=true ;;
    codebuddy) codebuddy_enabled=true ;;
    workbuddy) workbuddy_enabled=true ;;
  esac
done <<<"$selected_hosts"

echo "📦 mcp 一键安装:扫描到 ${#installers[@]} 个子工程"
case "$client" in
  claude)    echo "   客户端: claude (~/.claude.json, 默认)" ;;
  codex)     echo "   客户端: codex (生成 entry + codex mcp 注册)" ;;
  codebuddy) echo "   客户端: codebuddy (生成 entry + 写入 ~/.codebuddy/mcp.json)" ;;
  workbuddy) echo "   客户端: workbuddy (生成 entry + 写入 ~/.workbuddy/mcp.json)" ;;
  all)
    hosts="Claude Code"
    [ "$codex_enabled" = true ] && hosts="$hosts + Codex"
    [ "$codebuddy_enabled" = true ] && hosts="$hosts + 已检测到的 CodeBuddy"
    [ "$workbuddy_enabled" = true ] && hosts="$hosts + 已检测到的 WorkBuddy"
    echo "   客户端: all ($hosts)"
    ;;
esac
echo ""

ok_count=0
fail_count=0
failed=()

for installer in "${installers[@]}"; do
  name="$(basename "$(dirname "$installer")")"
  echo "─── $name ─────────────────────────────"
  export_only=1
  if [ "$claude_enabled" = true ]; then
    export_only=0
  fi
  if ICODE_MCP_EXPORT_ONLY="$export_only" bash "$installer"; then
    ok_count=$((ok_count + 1))
    # Codex 分支: 依赖子工程 install 成功导出的 entry
    if [ "$codex_enabled" = true ]; then
      if [ ${#PYTHON_BIN[@]} -eq 0 ]; then
        echo "   ⚠️ 未找到 python3/python，跳过 Codex 注册（client_registry.py 需 Python）"
        fail_count=$((fail_count + 1))
        failed+=("$name(codex:no-python)")
      elif "${PYTHON_BIN[@]}" "$HERE/_lib/client_registry.py" codex-register "$name"; then
        :
      else
        fail_count=$((fail_count + 1))
        failed+=("$name(codex)")
      fi
    fi
    # CodeBuddy 分支: 同样依赖子工程导出的 entry，写入 ~/.codebuddy/mcp.json
    if [ "$codebuddy_enabled" = true ]; then
      if [ ${#PYTHON_BIN[@]} -eq 0 ]; then
        echo "   ⚠️ 未找到 python3/python，跳过 CodeBuddy 注册（client_registry.py 需 Python）"
        fail_count=$((fail_count + 1))
        failed+=("$name(codebuddy:no-python)")
      elif "${PYTHON_BIN[@]}" "$HERE/_lib/client_registry.py" codebuddy-register "$name"; then
        :
      else
        fail_count=$((fail_count + 1))
        failed+=("$name(codebuddy)")
      fi
    fi
    # WorkBuddy 分支: 同样依赖子工程导出的 entry，写入 ~/.workbuddy/mcp.json
    if [ "$workbuddy_enabled" = true ]; then
      if [ ${#PYTHON_BIN[@]} -eq 0 ]; then
        echo "   ⚠️ 未找到 python3/python，跳过 WorkBuddy 注册（client_registry.py 需 Python）"
        fail_count=$((fail_count + 1))
        failed+=("$name(workbuddy:no-python)")
      elif "${PYTHON_BIN[@]}" "$HERE/_lib/client_registry.py" workbuddy-register "$name"; then
        :
      else
        fail_count=$((fail_count + 1))
        failed+=("$name(workbuddy)")
      fi
    fi
  else
    fail_count=$((fail_count + 1))
    failed+=("$name")
  fi
  echo ""
done

echo "════════════════════════════════════════"
echo "✅ MCP 子工程成功: $ok_count"
if [ $fail_count -gt 0 ]; then
  echo "❌ 失败: $fail_count (${failed[*]})"
  exit 1
fi
if [ "${ICODE_MCP_RECONCILE_ROOT_OWNER:-}" = 1 ]; then
  echo "✅ MCP 子工程安装完成，等待根安装器验证与退役收敛"
  exit 0
fi
"${PYTHON_BIN[@]}" "$RECONCILE" finish --plan "$ICODE_MCP_RECONCILE_PLAN" --installed-root "$HERE/.."
case "$client" in
  all)
    echo "🎉 全部完成!"
    echo "   Claude Code: 重启 Claude Code 后生效"
    [ "$codex_enabled" != true ] || echo "   Codex:       新建或重开 Codex 任务后生效(当前任务不会热加载新 MCP)"
    if [ "$codebuddy_enabled" = true ]; then
      echo "   CodeBuddy:   新建或重开 CodeBuddy 会话后生效"
    fi
    if [ "$workbuddy_enabled" = true ]; then
      echo "   WorkBuddy:   新建或重开 WorkBuddy 会话后生效"
    fi
    ;;
  codex)
    echo "🎉 全部完成!"
    echo "   Codex:       新建或重开 Codex 任务后生效(当前任务不会热加载新 MCP)"
    ;;
  codebuddy)
    echo "🎉 全部完成!"
    echo "   CodeBuddy:   新建或重开 CodeBuddy 会话后生效"
    ;;
  workbuddy)
    echo "🎉 全部完成!"
    echo "   WorkBuddy:   新建或重开 WorkBuddy 会话后生效"
    ;;
  *)
    echo "🎉 全部完成!记得重启 Claude Code 让注册生效。"
    ;;
esac
