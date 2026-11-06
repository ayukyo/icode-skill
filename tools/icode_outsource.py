#!/usr/bin/env python3
"""ICODE same-machine outsourcing CLI; no model, host installation or deployment."""
import sys

sys.dont_write_bytecode = True
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from outsource.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
