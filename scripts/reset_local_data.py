#!/usr/bin/env python3
"""Run from any directory with the project's Python environment."""
import sys
from admin.client import run

if __name__ == "__main__":
    run(["reset", *sys.argv[1:]])
