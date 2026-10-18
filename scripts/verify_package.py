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
from ingestion.adsblol.policy import ExtractionPolicy

for module in (admin.client, core.config, ingestion.updater, server.api):
    assert Path(module.__file__).is_relative_to(Path(sys.prefix)), module.__file__
assert not hasattr(ExtractionPolicy(), 'fixed_wing_types')
assert not hasattr(ExtractionPolicy(), 'callsign_prefixes')
with TestClient(server.api.app) as client:
    assert client.get('/').status_code == 200
    for asset in Path(server.api.WEB_ROOT).rglob('*'):
        if asset.is_file():
            path = asset.relative_to(server.api.WEB_ROOT).as_posix()
            assert client.get('/assets/' + path).status_code == 200, path
    assert client.get('/health/ready').status_code == 200
    catalog = client.get('/v1/catalog')
    assert catalog.status_code == 200
    assert 'providers' not in catalog.json()['dataset']
    assert 'sources' not in catalog.json()['dataset']
    assert client.get('/data-sources').status_code == 200
    schemas = client.get('/openapi.json').json()['components']['schemas']
    assert not {'FlightRecord', 'RouteVerification', 'FlightRef', 'TraceEvent'}.intersection(schemas)
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
    excluded["icao"], excluded["dbFlags"] = "000003", 1
    with tarfile.open(archive, "w") as target:
        for data in (valid, invalid, excluded):
            payload = gzip.compress(json.dumps(data).encode(), mtime=0)
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
        root / filename for filename in ("manifest.json", "airports.csv", "policy.json")
    ]
    manifest.write_text(json.dumps({"year": 2025, "assets": [asset]}))
    airports.write_text(
        "iata_code,latitude_deg,longitude_deg,scheduled_service,type\nAAA,40,-70,yes,large_airport\nBBB,41,-71,yes,large_airport\n"
    )
    rules.write_text("{}")
    prefix = [str(executable), "-m", "ingestion", "--db", str(root / "ingestion.db")]
    arguments = [
        "adsblol",
        "run",
        "--offline",
        "--manifest",
        str(manifest),
        "--airport-catalog",
        str(airports),
        "--policy",
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

    workspace = json.loads(
        invoke(["adsblol", "workspace", "--disk-workspace", "--data-root", str(root)]).stdout
    )
    assert workspace["workspace"]["mode"] == "disk"
    event = finished(invoke(arguments))
    assert event["records_applied"] == 2 and not event["counts_historical"]
    counts = event["diagnostics"]["counts"]
    assert counts["aircraft_exclusions"] == {"military": 1}
    assert counts["ignored_observations"] == {"invalid_position": 1}
    assert counts["segments"]["local_accepted"] == 2
    assert counts["local_rejections"] == {}
    assert not archive.exists()
    repeat = finished(invoke(arguments))
    assert repeat["counts_historical"] and repeat["records_applied"] == 0
    status = json.loads(invoke(["adsblol", "status"]).stdout)
    assert status["records"] == 2 and status["diagnostics"]["days_reported"] == 1
    assert status["diagnostics"]["totals"]["counts"] == counts
    # Seed both reference catalogs locally: this installed probe must never depend
    # on GitHub availability or silently fetch carrier data during publication.
    carriers = root / "carriers.csv"
    carriers.write_text("Code,Name,ICAO,IATA\nUAL,United Airlines,UAL,UA\n")
    catalogs = json.loads(
        invoke(
            [
                "references",
                "refresh",
                "--airports-csv",
                str(airports),
                "--carriers-csv",
                str(carriers),
            ]
        ).stdout
    )
    assert {item["kind"] for item in catalogs} == {"airports", "carriers"}
    serving = root / "serving.db"
    published = json.loads(
        invoke(
            [
                "publish",
                "--vrs",
                "cached",
                "--serving-db",
                str(serving),
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-01",
            ]
        ).stdout
    )
    assert published["inserted"] == 2
    admin_prefix = [str(executable), "-m", "admin", "--db", str(root / "ingestion.db")]
    inspected = subprocess.run(
        admin_prefix + ["inspect", "query", "SELECT count(*) FROM flights"],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(inspected.stdout)["rows"] == [[2]]
    operator = subprocess.run(
        admin_prefix + ["inspect", "query", "SELECT operator_name FROM flights LIMIT 1"],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(operator.stdout)["rows"] == [["United Airlines"]]
    blocked = subprocess.run(
        admin_prefix + ["maintenance", "init"],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode != 0 and "read/query-only" in blocked.stderr

    # Exercise VRS from the installed CLI, with bounded local fixtures, no network.
    vrs_routes, vrs_airports = root / "vrs-routes.csv", root / "vrs-airports.csv"
    vrs_routes.write_text("Callsign,AirportCodes\nUAL123,KAAA-KBBB\n")
    vrs_airports.write_text("Code,IATA\nKAAA,AAA\nKBBB,BBB\n")
    reference = json.loads(
        invoke(
            ["vrs", "import", "--routes-csv", str(vrs_routes), "--airports-csv", str(vrs_airports)]
        ).stdout
    )
    assert reference["counts"]["routes"] == 1 and not reference["flight_records_changed"]
    checked = json.loads(invoke(["vrs", "report"]).stdout)
    assert checked["counts"]["exact_two_airport_association"]["records"] == 2
    assert not checked["admission_changed"]
    audited_routes = json.loads(
        invoke(
            [
                "publish",
                "--vrs",
                "cached",
                "--serving-db",
                str(serving),
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-01",
            ]
        ).stdout
    )
    assert audited_routes["route_audit"]["status"] == "checked"
    assert audited_routes["route_audit"]["counts"]["reference_exact"] == 2
    quality_queue = json.loads(invoke(["review", "queue"]).stdout)
    assert quality_queue["version"] == "itinerary-cohort-review-v1"
    inspected_review = subprocess.run(
        admin_prefix + ["review", "queue"],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(inspected_review.stdout)["pending_records"] == 0

    # Exercise the installed handoff on a disposable legacy-shaped snapshot.
    legacy, adopted = root / "legacy.db", root / "adopted.db"
    snapshot_code = """import sqlite3,sys
from contextlib import closing
from ingestion.adoption import ARCHIVE_TABLES
with closing(sqlite3.connect(sys.argv[1])) as source, closing(sqlite3.connect(sys.argv[2])) as target:
    source.backup(target)
    tables = [row[0] for row in target.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
    for name in tables:
        if name not in ARCHIVE_TABLES | {'ingestion_profiles'}:
            target.execute('DROP TABLE "'+name.replace('"','""')+'"')
    target.execute('DROP INDEX ingestion_publication_time')
    target.commit()
"""
    subprocess.run(
        [str(executable), "-c", snapshot_code, str(root / "ingestion.db"), str(legacy)],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    handoff = json.loads(
        invoke(
            [
                "--db",
                str(adopted),
                "adopt",
                "--from-db",
                str(legacy),
                "--source-root",
                str(root),
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-01",
            ]
        ).stdout
    )
    assert handoff["role"] == "ingestion" and handoff["source_changed"] is False
    audited = json.loads(
        invoke(
            ["--db", str(adopted), "audit", "--apply", "--report", str(root / "audit.json")]
        ).stdout
    )
    assert audited["health"]["passed"] and audited["counts"]["records"] == 2
    # Adoption intentionally starts with a fresh reference catalog. Seed its
    # local fixtures too, so this publication cannot download missing catalogs.
    invoke(
        [
            "--db",
            str(adopted),
            "references",
            "refresh",
            "--airports-csv",
            str(airports),
            "--carriers-csv",
            str(carriers),
        ]
    )
    replacement = root / "replacement.db"
    released = json.loads(
        invoke(
            [
                "--db",
                str(adopted),
                "publish",
                "--vrs",
                "cached",
                "--serving-db",
                str(replacement),
                "--preserve-serving",
                str(serving),
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-01",
            ]
        ).stdout
    )
    assert released["inserted"] == 2


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
