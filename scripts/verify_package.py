"""Install a built wheel and smoke-test it outside the checkout.

Third-party dependencies are reused from the invoking Python environment.
Application imports must resolve inside the temporary installation.
"""

import argparse
import json
import os
import site
import subprocess
import venv
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

PROBE = """
import sys
from pathlib import Path
import admin.client, core.config, ingestion.updater, server.api
from fastapi.testclient import TestClient

for module in (admin.client, core.config, ingestion.updater, server.api):
    assert Path(module.__file__).is_relative_to(Path(sys.prefix)), module.__file__
with TestClient(server.api.app) as client:
    assert client.get('/').status_code == 200
    for asset in Path(server.api.WEB_ROOT).rglob('*'):
        if asset.is_file():
            path = asset.relative_to(server.api.WEB_ROOT).as_posix()
            assert client.get('/assets/' + path).status_code == 200, path
    assert client.get('/health/ready').status_code == 200
    assert client.get('/v1/catalog').status_code == 200
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument(
        "--database", type=Path, help="optional existing database to probe read-only"
    )
    parser.add_argument("--output", type=Path, help="optional JSON report; keep under var/")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    root = Path(__file__).resolve().parents[1]
    with ZipFile(wheel) as package:
        for asset in (root / "src").rglob("*"):
            if asset.is_file() and "__pycache__" not in asset.parts and asset.suffix != ".pyc":
                name = asset.relative_to(root / "src").as_posix()
                assert package.read(name) == asset.read_bytes(), (
                    f"Missing or stale wheel file: {name}"
                )
    with TemporaryDirectory(prefix="flyji-package-") as directory:
        environment_path = Path(directory) / "environment"
        venv.EnvBuilder(with_pip=True).create(environment_path)
        executable = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment = dict(
            os.environ,
            PYTHONPATH="",
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
            PIP_NO_INDEX="1",
            PIP_NO_CACHE_DIR="1",
            FLIGHT_DATABASE_PATH=str(
                args.database.resolve() if args.database else Path(directory) / "smoke.db"
            ),
        )

        def run(*arguments):
            subprocess.run(
                [str(executable), *arguments], cwd=directory, env=environment, check=True
            )

        run(
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--disable-pip-version-check",
            "--quiet",
            str(wheel),
        )
        # A nested venv does not inherit the invoking venv's dependencies. Add
        # those directories after installing the wheel; their editable-import
        # .pth hooks are not executed. Our application import checks still
        # require the temporary installation, never the checkout.
        purelib = subprocess.check_output(
            [str(executable), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            cwd=directory,
            env=environment,
            text=True,
        ).strip()
        Path(purelib, "smoke-dependencies.pth").write_text("\n".join(site.getsitepackages()) + "\n")
        if args.database is None:
            # Exercise the installed CLI and initialize only a temporary, empty DB.
            command = environment_path / ("Scripts/flyji.exe" if os.name == "nt" else "bin/flyji")
            subprocess.run([str(command), "stats"], cwd=directory, env=environment, check=True)
        run("-c", PROBE)
    report = {"wheel": wheel.name, "installed_package_probe_passed": True}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
