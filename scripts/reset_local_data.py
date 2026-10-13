#!/usr/bin/env python3
"""Run from any directory with the project's Python environment."""

import sys

from admin.client import main

if __name__ == "__main__":
    main(["reset", *sys.argv[1:]])
