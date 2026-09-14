#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

CB_HOME="$TMP/home"
mkdir -p "$CB_HOME/.codebuddy"
printf '%s\n' \
  '{"theme":"dark","mcpServers":{"demo":{"command":"demo"},"keep":{"command":"keep"}}}' \
  >"$CB_HOME/.codebuddy/mcp.json"
OUTPUT=""
if OUTPUT="$(HOME="$CB_HOME" bash "$FIXTURE/uninstall.sh" \
     --client codebuddy demo 2>&1)" \
  && python3 - "$CB_HOME/.codebuddy/mcp.json" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["theme"] == "dark"
assert "demo" not in value["mcpServers"]
assert value["mcpServers"]["keep"]["command"] == "keep"
PY
  grep -q 'CodeBuddy' <<<"$OUTPUT"; then
  ok "top-level uninstall removes only the selected CodeBuddy MCP entry"
else
  bad "top-level uninstall removes only the selected CodeBuddy MCP entry"
fi

BEFORE="$(sha256sum "$CB_HOME/.codebuddy/mcp.json")"
if HOME="$CB_HOME" bash "$FIXTURE/uninstall.sh" \
     --client codebuddy demo >/dev/null 2>&1 \
  && [[ "$BEFORE" == "$(sha256sum "$CB_HOME/.codebuddy/mcp.json")" ]]; then
  ok "repeated CodeBuddy MCP uninstall is idempotent"
else
  bad "repeated CodeBuddy MCP uninstall is idempotent"
fi

BROKEN_HOME="$TMP/broken-home"
mkdir -p "$BROKEN_HOME/.codebuddy"
printf '{broken json\n' >"$BROKEN_HOME/.codebuddy/mcp.json"
BROKEN_HASH="$(sha256sum "$BROKEN_HOME/.codebuddy/mcp.json")"
if HOME="$BROKEN_HOME" python3 "$FIXTURE/_lib/client_registry.py" \
     codebuddy-unregister demo >/dev/null 2>&1; then
  bad "malformed CodeBuddy MCP config fails closed"
elif [[ "$BROKEN_HASH" == "$(sha256sum "$BROKEN_HOME/.codebuddy/mcp.json")" ]]; then
  ok "malformed CodeBuddy MCP config fails closed without rewriting"
else
  bad "malformed CodeBuddy MCP config fails closed without rewriting"
fi

REGISTRY_HELP="$(python3 "$ROOT/mcp/_lib/client_registry.py" 2>&1 || true)"
if bash "$ROOT/mcp/install.sh" --help | grep -q 'claude|codex|codebuddy|all' \
  && grep -q 'claude|codex|codebuddy|all' "$ROOT/mcp/uninstall.sh" \
  && grep -q 'codebuddy-register' <<<"$REGISTRY_HELP"; then
  ok "MCP entrypoint help exposes symmetric CodeBuddy operations"
else
  bad "MCP entrypoint help exposes symmetric CodeBuddy operations"
fi

printf 'CodeBuddy MCP contract: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
