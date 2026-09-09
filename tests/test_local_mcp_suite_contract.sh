#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVERS=(icode-evidence icode-workspace icode-device-observe icode-mcp-health icode-mcp-policy icode-local-index)

for name in "${SERVERS[@]}"; do
  dir="$ROOT/mcp/$name"
  for file in server.py README.md requirements.txt pyproject.toml config.example.json tools_manifest.json install.sh uninstall.sh; do
    test -f "$dir/$file" || { echo "missing $name/$file" >&2; exit 1; }
  done
  bash -n "$dir/install.sh"
  bash -n "$dir/uninstall.sh"
done

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest "$ROOT/tests/test_local_mcp_suite.py"
python3 - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
names = {"icode-evidence", "icode-workspace", "icode-device-observe", "icode-mcp-health", "icode-mcp-policy", "icode-local-index"}
for name in names:
    manifest = json.loads((root / "mcp" / name / "tools_manifest.json").read_text())
    assert manifest["server"] == name
    assert manifest["api_key_required"] is False
    assert manifest["tools"]
    config = (root / "mcp" / name / "config.example.json").read_text().lower()
    assert "api_key" not in config and "password" not in config and "private_key" not in config
policy = json.loads((root / "mcp" / "icode-mcp-policy" / "policy.json").read_text())
assert names == set(policy["servers"])
assert {"init", "log", "plan", "code", "deepcheck", "audit", "patch", "verify", "install", "worktree"} <= set(policy["steps"])
device = (root / "mcp" / "icode-device-observe" / "tools_manifest.json").read_text()
for forbidden in ("arbitrary_command", "deploy", "flash", "reboot", "kill", "write"):
    assert forbidden in device
print("local MCP suite contract: pass")
PY
