#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/../_lib/install_local_python_mcp.sh" "icode-device-observe" "ICODE_DEVICE_OBSERVE_CONFIG" "$HERE" "${1:-}"
