#!/usr/bin/env bash
# Shared installer implementation. Sourced only after the server directory guard.

install_managed_node_mcp() {
  local server="$1" here="$2"
  shift 2
  if [ "$#" -gt 1 ] || [[ "${1:-}" == --* && "${1:-}" != --uninstall ]]; then
    echo "用法: install.sh [<npm_package>|--uninstall]" >&2
    return 1
  fi

  # A successful exit/--version is insufficient: WindowsApps aliases can be empty stubs.
  # Keep the launcher and its -3 selector in separate array elements.
  local -a python_cmd=()
  local candidate output
  if [ -n "${ICODE_PYTHON_BIN:-}" ]; then
    candidate="$ICODE_PYTHON_BIN"
    if output="$("$candidate" -c 'print(1)' 2>/dev/null)" && [ "${output//$'\r'/}" = "1" ]; then
      python_cmd=("$candidate")
    else
      echo "❌ ICODE_PYTHON_BIN 不是可运行的 Python: $candidate" >&2
      return 1
    fi
  else
    for candidate in python3 py python; do
      candidate="$(command -v "$candidate" 2>/dev/null || true)"
      [ -n "$candidate" ] || continue
      if [[ "$candidate" == */py || "$candidate" == */py.exe ]]; then
        if output="$("$candidate" -3 -c 'print(1)' 2>/dev/null)" && [ "${output//$'\r'/}" = "1" ]; then
          python_cmd=("$candidate" -3)
          break
        fi
      elif output="$("$candidate" -c 'print(1)' 2>/dev/null)" && [ "${output//$'\r'/}" = "1" ]; then
        python_cmd=("$candidate")
        break
      fi
    done
  fi
  if [ "${#python_cmd[@]}" -eq 0 ]; then
    echo "❌ 未找到可运行的 Python；可用 ICODE_PYTHON_BIN 指定解释器。" >&2
    return 1
  fi

  # Reuse the existing unregister implementation with the verified interpreter.
  # Uninstall only removes registration; it never needs Node/npm or deletes package/data.
  if [ "${1:-}" = "--uninstall" ]; then
    "${python_cmd[@]}" "$here/../_lib/claude_registry.py" unregister "$server"
    return $?
  fi

  local detected node_bin npx_bin
  # The registrar owns catalog matching: explicit official package/version arguments
  # also use managed startup. Only its legacy custom-package branch requires npx.
  if ! detected="$("${python_cmd[@]}" "$here/../_lib/find_node.py" --node-only)"; then
    return 1
  fi
  IFS=$'\t' read -r node_bin npx_bin <<<"${detected//$'\r'/}"
  if [ -z "$node_bin" ]; then
    echo "❌ 未找到 node；请安装 Node.js 或设置 ICODE_NODE_BIN。" >&2
    return 1
  fi
  local -a register_args=("$node_bin" "")
  if [ -n "${1:-}" ]; then
    register_args=("$node_bin" "$npx_bin" "$1")
  fi
  echo "📦 $server 安装"
  echo "   node: $node_bin"
  # Default registration provisions/reuses its locked package; npm is installation-only.
  "${python_cmd[@]}" "$here/scripts/register_mcp.py" "${register_args[@]}" || return $?
  echo "🎀 完成；重载所选宿主或新建会话后生效。"
}
