#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Pin the scratch root to /tmp: on MSYS hosts mktemp -d follows TEMP (a
# "C:\..." path) and cleaning such paths up can be rejected by hardened rm
# wrappers, leaving the suite exit non-zero even when every assertion passed.
TMP="$(mktemp -d /tmp/icode_wb_mcp.XXXXXX)"
trap 'rm -rf "$TMP" >/dev/null 2>&1 &' EXIT

PASS=0
FAIL=0
ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }

FIXTURE="$TMP/mcp"
mkdir -p "$FIXTURE/_lib" "$FIXTURE/demo"
cp "$ROOT/mcp/uninstall.sh" "$FIXTURE/uninstall.sh"
cp "$ROOT/mcp/_lib/client_registry.py" "$FIXTURE/_lib/client_registry.py"
cp "$ROOT/mcp/_lib/claude_registry.py" "$FIXTURE/_lib/claude_registry.py"
printf '#!/usr/bin/env bash\nexit 0\n' >"$FIXTURE/demo/uninstall.sh"

WB_HOME="$TMP/home"
mkdir -p "$WB_HOME/.workbuddy"
printf '%s\n' \
  '{"theme":"dark","mcpServers":{"demo":{"command":"demo"},"keep":{"command":"keep"}}}' \
  >"$WB_HOME/.workbuddy/mcp.json"
OUTPUT=""
if OUTPUT="$(HOME="$WB_HOME" USERPROFILE="$WB_HOME" bash "$FIXTURE/uninstall.sh" \
     --client workbuddy demo 2>&1)" \
  && python - "$WB_HOME/.workbuddy/mcp.json" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["theme"] == "dark"
assert "demo" not in value["mcpServers"]
assert value["mcpServers"]["keep"]["command"] == "keep"
PY
  grep -q 'WorkBuddy' <<<"$OUTPUT"; then
  ok "top-level uninstall removes only the selected WorkBuddy MCP entry"
else
  bad "top-level uninstall removes only the selected WorkBuddy MCP entry"
fi

BEFORE="$(sha256sum "$WB_HOME/.workbuddy/mcp.json")"
if HOME="$WB_HOME" USERPROFILE="$WB_HOME" bash "$FIXTURE/uninstall.sh" \
     --client workbuddy demo >/dev/null 2>&1 \
  && [[ "$BEFORE" == "$(sha256sum "$WB_HOME/.workbuddy/mcp.json")" ]]; then
  ok "repeated WorkBuddy MCP uninstall is idempotent"
else
  bad "repeated WorkBuddy MCP uninstall is idempotent"
fi

BROKEN_HOME="$TMP/broken-home"
mkdir -p "$BROKEN_HOME/.workbuddy"
printf '{broken json\n' >"$BROKEN_HOME/.workbuddy/mcp.json"
BROKEN_HASH="$(sha256sum "$BROKEN_HOME/.workbuddy/mcp.json")"
if HOME="$BROKEN_HOME" USERPROFILE="$BROKEN_HOME" python "$FIXTURE/_lib/client_registry.py" \
     workbuddy-unregister demo >/dev/null 2>&1; then
  bad "malformed WorkBuddy MCP config fails closed"
elif [[ "$BROKEN_HASH" == "$(sha256sum "$BROKEN_HOME/.workbuddy/mcp.json")" ]]; then
  ok "malformed WorkBuddy MCP config fails closed without rewriting"
else
  bad "malformed WorkBuddy MCP config fails closed without rewriting"
fi

MISSING_HOME="$TMP/missing-home"
mkdir -p "$MISSING_HOME/.workbuddy"
if HOME="$MISSING_HOME" USERPROFILE="$MISSING_HOME" python "$FIXTURE/_lib/client_registry.py" \
     workbuddy-unregister demo >/dev/null 2>&1; then
  ok "unregister on missing WorkBuddy MCP entry is an idempotent no-op"
else
  bad "unregister on missing WorkBuddy MCP entry is an idempotent no-op"
fi

REGISTRY_HELP="$(python "$ROOT/mcp/_lib/client_registry.py" 2>&1 || true)"
if bash "$ROOT/mcp/install.sh" --help | grep -q 'claude|codex|codebuddy|workbuddy|all' \
  && grep -q 'claude|codex|codebuddy|workbuddy|all' "$ROOT/mcp/uninstall.sh" \
  && grep -q 'workbuddy-register' <<<"$REGISTRY_HELP"; then
  ok "MCP entrypoint help exposes symmetric WorkBuddy operations"
else
  bad "MCP entrypoint help exposes symmetric WorkBuddy operations"
fi

printf 'WorkBuddy MCP contract: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
