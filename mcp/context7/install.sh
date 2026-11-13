#!/usr/bin/env bash
# 默认注册固定版本的受管 Node 包；显式 npm 包名保留 legacy npx 兼容。
# 用法: ./install.sh [<npm_package>|--uninstall]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$HERE/scripts/register_mcp.py" ] || [ ! -f "$HERE/../_lib/install_managed_node_mcp.sh" ]; then
  echo "❌ 当前目录不是 context7 工程根目录: $HERE" >&2
  exit 1
fi
source "$HERE/../_lib/install_managed_node_mcp.sh"
install_managed_node_mcp "context7" "$HERE" "$@"
