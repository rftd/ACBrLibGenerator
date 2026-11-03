#!/usr/bin/env python3
"""Entrypoint usable from a checkout or the builder image."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acbrlibgenerator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
