"""Install a built wheel and smoke-test it outside the checkout.

Third-party dependencies are reused from the invoking Python environment.
Application imports must resolve inside the temporary installation.
"""

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
import site
import subprocess
import tarfile
import venv
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

PROBE = """
import sys
from pathlib import Path
import admin.client, core.config, ingestion.updater, server.api
from fastapi.testclient import TestClient
from ingestion.adsblol.filters import DEFAULT_FILTER_PATH, CommercialFilter

for module in (admin.client, core.config, ingestion.updater, server.api):
    assert Path(module.__file__).is_relative_to(Path(sys.prefix)), module.__file__
assert DEFAULT_FILTER_PATH.is_relative_to(Path(sys.prefix)) and DEFAULT_FILTER_PATH.is_file()
CommercialFilter.model_validate_json(DEFAULT_FILTER_PATH.read_text())
with TestClient(server.api.app) as client:
    assert client.get('/').status_code == 200
    for asset in Path(server.api.WEB_ROOT).rglob('*'):
        if asset.is_file():
            path = asset.relative_to(server.api.WEB_ROOT).as_posix()
            assert client.get('/assets/' + path).status_code == 200, path
    assert client.get('/health/ready').status_code == 200
    assert client.get('/v1/catalog').status_code == 200
"""


def probe_ingestion(executable, directory, environment):
    """Check the installed CLI's diagnostics with local fixtures, without downloads."""
    root = Path(directory) / "ingestion"
    raw = root / "raw/2025-01-01"
    raw.mkdir(parents=True)
    name = "v2025.01.01-planes-readsb-prod-0.tar"
    archive = raw / name
    valid = {
        "icao": "000001",
        "t": "A320",
        "timestamp": 1735689600,
        "trace": [
            [0, 40, -70, "ground", 0, 0, 0, 0, {"flight": "UAL123"}],
            *[
                [offset, 40.5, -70.5, 10000, 300, 0, 0, 0, None]
                for offset in (10, 100, 200, 300, 400)
            ],
            [410, 41, -71, "ground", 0, 0, 0, 0, None],
        ],
    }
    invalid, excluded = copy.deepcopy(valid), copy.deepcopy(valid)
    invalid["icao"], invalid["trace"][3][1] = "000002", 95
    excluded["icao"], excluded["t"] = "000003", None
    with tarfile.open(archive, "w") as target:
        for data in (valid, invalid, excluded):
            payload = gzip.compress(json.dumps(data).encode())
            member = tarfile.TarInfo(f"traces/00/trace_full_{data['icao']}.json")
            member.size = len(payload)
            target.addfile(member, io.BytesIO(payload))
    asset = {
        "date": "2025-01-01",
        "name": name,
        "url": f"https://github.com/adsblol/globe_history_2025/releases/download/v2025.01.01-planes-readsb-prod-0/{name}",
        "bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    archive.with_name(name + ".verified.json").write_text(json.dumps(asset))
    manifest, airports, rules = [
        root / filename for filename in ("manifest.json", "airports.csv", "filter.json")
    ]
    manifest.write_text(json.dumps({"year": 2025, "assets": [asset]}))
    airports.write_text(
        "iata_code,latitude_deg,longitude_deg,scheduled_service,type\nAAA,40,-70,yes,large_airport\nBBB,41,-71,yes,large_airport\n"
    )
    rules.write_text(json.dumps({"fixed_wing_types": ["A320"], "callsign_prefixes": ["UAL"]}))
    prefix = [str(executable), "-m", "admin", "--db", str(root / "admin.db")]
    arguments = [
        "ingest",
        "adsblol",
        "run",
        "--manifest",
        str(manifest),
        "--airport-catalog",
        str(airports),
        "--filter",
        str(rules),
        "--data-root",
        str(root),
        "--start",
        "2025-01-01",
        "--end",
        "2025-01-01",
    ]

    def invoke(arguments):
        return subprocess.run(
            prefix + arguments,
            cwd=directory,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    def finished(result):
        events = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
        return next(event for event in events if event["event"] == "day_finished")

    event = finished(invoke(arguments))
    assert event["records_applied"] == 1 and not event["counts_historical"]
    counts = event["diagnostics"]["counts"]
    assert counts["aircraft_exclusions"] == {"missing_type": 1}
    assert counts["ignored_observations"] == {"invalid_position": 1}
    assert counts["segments"]["local_accepted"] == 1
    assert counts["local_rejections"] == {"missing_origin_ground": 1}
    assert not archive.exists()
    repeat = finished(invoke(arguments))
    assert repeat["counts_historical"] and repeat["records_applied"] == 0
    status = json.loads(invoke(["ingest", "adsblol", "status"]).stdout)
    assert status["records"] == 1 and status["diagnostics"]["days_reported"] == 1
    assert status["diagnostics"]["totals"]["counts"] == counts


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
            subprocess.run(
                [str(command), "maintenance", "init"], cwd=directory, env=environment, check=True
            )
        run("-c", PROBE)
        probe_ingestion(executable, directory, environment)
    report = {
        "wheel": wheel.name,
        "installed_package_probe_passed": True,
        "installed_ingestion_diagnostics_probe_passed": True,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
