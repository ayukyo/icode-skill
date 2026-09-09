#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/../_lib/install_local_python_mcp.sh" "icode-local-index" "ICODE_LOCAL_INDEX_CONFIG" "$HERE" "${1:-}"
