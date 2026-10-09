#!/usr/bin/env python3
"""Run from any directory with the project's Python environment."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flight_engine.admin.client import run

if __name__ == "__main__":
    run(["reset", *sys.argv[1:]])
