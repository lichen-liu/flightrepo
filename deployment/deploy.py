"""Explicit single-command install/provision/start/check; never starts BoboWatch."""

import argparse
import importlib.util
import os
import subprocess
from pathlib import Path

from manage import LABELS, check, loaded, runtime_root, start, stop

REPO = Path(__file__).resolve().parents[1]


def deploy(root, watch_runtime=None):
    # Fetch only the recorded submodule revision, before stopping a running app.
    subprocess.run(
        ["git", "submodule", "update", "--init", "vendor/bobowatch"], cwd=REPO, check=True
    )
    spec = importlib.util.spec_from_file_location(
        "flyji_installer", REPO / "installation/install.py"
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    if any(loaded(job) for job in LABELS):
        stop()
    installer.install(root, REPO, watch_runtime=watch_runtime)
    start(root)
    check(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["deploy"])
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--bobowatch-runtime-dir", type=Path)
    args = parser.parse_args()
    if os.getuid() == 0:
        parser.error("Run as your normal user, without sudo")
    root = args.runtime_dir.expanduser() if args.runtime_dir else runtime_root()
    if not root.is_absolute():
        parser.error("The runtime directory must be absolute")
    try:
        deploy(root, args.bobowatch_runtime_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Deployment failed: {error}\n")


if __name__ == "__main__":
    main()
