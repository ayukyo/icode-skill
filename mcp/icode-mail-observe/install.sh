#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/../_lib/install_local_python_mcp.sh" "icode-mail-observe" "ICODE_MAIL_OBSERVE_CONFIG" "$HERE" "${1:-}"
