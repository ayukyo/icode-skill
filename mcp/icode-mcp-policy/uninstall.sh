#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/../_lib/uninstall_local_python_mcp.sh" "icode-mcp-policy"
