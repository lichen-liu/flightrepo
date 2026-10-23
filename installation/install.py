"""Install a stopped Flyji snapshot and an initial synthetic database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deployment"))
from manage import (  # noqa: E402
    INSTALLATION_CHECK_TIMEOUT,
    LABELS,
    loaded,
    private_write,
    require_private,
    runtime_root,
)
from serve import read_settings  # noqa: E402


def install(root: Path, repo: Path = REPO):
    if any(loaded(job) for job in LABELS):
        raise ValueError("Stop Flyji before installing or updating it.")
    if root.exists() or root.is_symlink():
        require_private(root, directory=True)
    else:
        root.mkdir(parents=True, mode=0o700)
    for name in ("logs", "jobs", "venv"):
        path = root / name
        if path.exists() or path.is_symlink():
            require_private(path, directory=True)
        else:
            path.mkdir(mode=0o700)
    settings = root / "settings.json"
    if settings.exists() or settings.is_symlink():
        require_private(settings)
    else:
        private_write(
            settings,
            json.dumps(
                {
                    "hostname": "fly.boboji.fyi",
                    "port": 8014,
                    "database_path": str(repo / "var/hosted/flights.db"),
                    "data_mode": "synthetic",
                },
                indent=2,
            ).encode()
            + b"\n",
        )
    config = read_settings(root)
    monitoring = json.loads(settings.read_text()).get("monitoring", {})
    if not isinstance(monitoring, dict) or type(monitoring.get("enabled", False)) is not bool:
        raise ValueError("monitoring.enabled must be a boolean")
    database = Path(config["database_path"])
    if not database.resolve().is_relative_to((repo / "var").resolve()):
        raise ValueError("Keep the hosted database under this project's ignored var directory.")
    if database in (
        repo / "var/flights.db",
        repo / "var/connections-demo.db",
        repo / "var/connections-demo-legs.db",
    ):
        raise ValueError(
            "Choose a separate hosted database; normal and local-demo data are preserved."
        )
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require_private(database.parent, directory=True)
    if database.exists() or database.is_symlink():
        require_private(database)
    # Initialize the exact recorded adapter revision before building the wheel.
    subprocess.run(
        ["git", "submodule", "update", "--init", "vendor/bobowatch"], cwd=repo, check=True
    )
    # A failed update stays stopped until a successful install clears this marker.
    private_write(root / "installing", b"Installation in progress; do not start.\n")
    with tempfile.TemporaryDirectory(prefix="flyji-wheel-") as temporary:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                temporary,
                str(repo),
            ],
            check=True,
        )
        wheels = list(Path(temporary).glob("flight_routes_engine-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("The build must produce exactly one Flyji wheel.")
        wheel = wheels[0]
        fingerprint = hashlib.sha256(wheel.read_bytes()).hexdigest()
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv/bin/python"
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--force-reinstall",
                str(wheel),
            ],
            check=True,
        )
    copied = {}
    for source, name in [
        (repo / "deployment/manage.py", "manage.py"),
        (repo / "deployment/serve.py", "serve.py"),
        (repo / "fixtures/connections-demo.json", "synthetic-data.json"),
    ]:
        content = source.read_bytes()
        private_write(root / name, content)
        copied[name] = hashlib.sha256(content).hexdigest()
    for phase in ("seed", "check"):
        print(
            f"Verifying installed database/application: {phase} (limit {INSTALLATION_CHECK_TIMEOUT}s)",
            flush=True,
        )
        try:
            subprocess.run(
                [str(python), str(root / "serve.py"), f"--{phase}"],
                check=True,
                timeout=INSTALLATION_CHECK_TIMEOUT,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Installed {phase} exceeded {INSTALLATION_CHECK_TIMEOUT}s; installation remains incomplete. Review this phase before retrying."
            ) from error
    commit = subprocess.run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dependencies = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "freeze",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    private_write(
        root / "installation.json",
        json.dumps(
            {
                "source_commit": commit,
                "uncommitted_changes": bool(dirty),
                "wheel_sha256": fingerprint,
                "runtime_files_sha256": copied,
                "dependencies": dependencies,
                "database_path": str(database),
                "data_mode": config["data_mode"],
            },
            indent=2,
        ).encode()
        + b"\n",
    )
    (root / "installing").unlink()
    print(f"Installed Flyji at {root}")
    print(f"{config['data_mode'].capitalize()} flight database: {database}")
    if dirty:
        print("This snapshot includes uncommitted checkout changes.")
    print(
        "Next: brew install cloudflared, configure "
        f"{config['hostname']} → http://127.0.0.1:{config['port']},"
    )
    print("then run ./deployment/flyji tunnel and ./deployment/flyji start.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Flyji without starting or publishing it.")
    parser.add_argument("--runtime-dir", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.getuid() == 0 or sys.version_info < (3, 11):
        parser.error("Use Python 3.11+ on macOS as your normal user, without sudo.")
    os.umask(0o077)
    try:
        root = args.runtime_dir.expanduser() if args.runtime_dir else runtime_root()
        if not root.is_absolute():
            raise ValueError("The runtime directory must be an absolute path.")
        install(root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Installation failed: {error}\n")


if __name__ == "__main__":
    main()
