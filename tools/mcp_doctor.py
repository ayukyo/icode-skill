#!/usr/bin/env python3
"""Offline MCP registration, core-installation and recorded gate diagnostics."""
import argparse
import json
from pathlib import Path
import platform
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp/_lib"))
from environment_diagnostics import compare_installation, diagnose_registration, runtime_inventory, summarize_gates


def build_report(configs, entries_dir, *, source=None, installed=(), ticket_dir=None):
    registrations = [diagnose_registration(path, entries_dir) for path in configs]
    installations = [compare_installation(source, path) for path in installed] if source else []
    gates = summarize_gates(ticket_dir) if ticket_dir is not None else None
    problems = sum(row["summary"]["problems"] for row in registrations)
    problems += sum(row["status"] != "match" for row in installations)
    problems += bool(gates and gates["status"] in {"attention", "partial"})
    return {"schema_version": 1, "read_only": True, "network": False,
            "host": {"os": platform.system(), "arch": platform.machine()},
            "runtime": runtime_inventory(),
            "registrations": registrations, "installations": installations, "gates": gates,
            "session": {"status": "unverified", "reason": "requires_actual_host_tool_call"},
            "summary": {"problems": problems}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, action="append", help="JSON mcpServers or Codex TOML config; repeat for explicit hosts")
    parser.add_argument("--entries-dir", type=Path, default=Path.home() / ".claude/icode_data/mcp_entries")
    parser.add_argument("--source", type=Path, help="Verified source root for core file comparisons")
    parser.add_argument("--installed", type=Path, action="append", help="Installed root; repeat as needed")
    parser.add_argument("--ticket-dir", type=Path, help="Explicit ticket; read gate observations only")
    parser.add_argument("--json", action="store_true", help="Machine output (also the default)")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on observed problems; default query exits 0")
    args = parser.parse_args(argv)
    if args.installed and not args.source:
        parser.error("--installed requires --source")
    configs = args.config
    if configs is None:
        configs = [Path.home() / ".claude.json"]
        configs.extend(path for path in (Path.home() / ".codebuddy/mcp.json", Path.home() / ".workbuddy/mcp.json",
                                         Path.home() / ".codex/config.toml") if path.is_file())
    installed = args.installed
    if installed is None:
        installed = [root / "skills/icode" for root in (Path.home() / ".claude", Path.home() / ".agents", Path.home() / ".workbuddy") if (root / "skills/icode").is_dir()]
    report = build_report(configs, args.entries_dir, source=args.source, installed=installed, ticket_dir=args.ticket_dir)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if args.strict and report["summary"]["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
