"""Compare a committed reader/preflight with this checkout on an isolated fixture.

Run: PYTHONPATH=src python scripts/benchmark_archive_queries.py --rows 250000
Never opens the hosted archive. First/fresh/cached timings do not purge OS caches.
"""

import argparse
import asyncio
import io
import json
import sqlite3
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from time import perf_counter
from types import ModuleType
from unittest.mock import patch

import server.reader as reader_module
import server.routing_projection as routing_module
from core.storage import SCHEMA
from server.archive_queries import AIRPORT_PAIRS_CTE, airport_pairs
from server.reader import FlightReader

REPO = Path(__file__).resolve().parents[1]


def load_source(name, path, revision=None):
    source = (
        subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=REPO, text=True)
        if revision
        else (REPO / path).read_text()
    )
    module = ModuleType(name)
    module.__file__ = str(REPO / path)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def fixture(root, rows):
    database = root / "fixture.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO data_sources(id,code,name) VALUES(1,'archive','Fixture')")
        connection.executemany(
            "INSERT INTO airports(iata) VALUES(?)",
            [
                (a + b + c,)
                for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                for c in "AZ"
            ],
        )
        connection.execute(
            """WITH RECURSIVE n(i) AS (
            VALUES(1) UNION ALL SELECT i+1 FROM n WHERE i<?)
            INSERT INTO flight_legs(source_id,source_record_key,origin_iata,destination_iata,
                status,evidence_kind,review_reasons,
                departure_observed_lower_us,departure_observed_upper_us,
                arrival_observed_lower_us,arrival_observed_upper_us,first_seen_us,last_seen_us)
            SELECT 1,printf('%09d',i),'A'||char(65+i%26)||'A','Z'||char(65+(i/26)%26)||'Z',
                CASE WHEN i%101=0 THEN 'cancelled' ELSE 'landed' END,
                'inferred_airport_proximity',
                CASE WHEN i%97=0 THEN '["future_unknown"]' ELSE '["observation_gap"]' END,
                1735689600000000+((i*15485863)%?)*60000000,
                1735689600000001+((i*15485863)%?)*60000000,
                1735693200000000+((i*15485863)%?)*60000000,
                1735693200000001+((i*15485863)%?)*60000000,
                1735700000000000,1735700000000000 FROM n""",
            (rows,) * 5,
        )
    (root / "settings.json").write_text(
        json.dumps(
            {
                "hostname": "fixture.example",
                "port": 8014,
                "database_path": str(database),
                "data_mode": "real",
            }
        )
    )
    return database


def measure(operation):
    started = perf_counter()
    value = operation()
    return round(perf_counter() - started, 6), value


def benchmark(root, rows, baseline):
    database = fixture(root, rows)
    before_module = load_source("baseline_reader", "src/server/reader.py", baseline)
    before_module.WebBootstrap = load_source(
        "baseline_web_models", "src/server/web_models.py", baseline
    ).WebBootstrap
    before = before_module.FlightReader
    before_serve = load_source("baseline_serve", "deployment/serve.py", baseline)
    after_serve = load_source("current_serve", "deployment/serve.py")
    report = {
        "rows": rows,
        "bytes": database.stat().st_size,
        "sqlite": sqlite3.sqlite_version,
        "baseline": baseline,
        "cache_note": "OS caches uncontrolled; fresh reader twice, then same-reader cache hit",
        "measurements": {},
        "plans": {},
    }
    for label, cls, serve in (
        ("before", before, before_serve),
        ("after", FlightReader, after_serve),
    ):
        results = {}
        for name in ("stats", "web_catalog", "web_bootstrap"):
            times = []
            for _ in range(2):
                reader = cls(database)
                seconds, _ = measure(getattr(reader, name))
                times.append(seconds)
            seconds, _ = measure(getattr(reader, name))
            results[name] = {"fresh_readers": times, "same_reader": seconds}
        for count in (True, False):
            if label == "before":

                def operation():
                    return cls(database).stats()["flights"]
            else:

                def operation():
                    return cls(database).validate_dataset(data_mode="real", count=count)

            results["validate_count" if count else "validate_sources"] = [
                measure(operation)[0] for _ in range(2)
            ]
        pairs = airport_pairs
        if label == "before":

            def pairs(connection):
                return connection.execute(
                    "SELECT DISTINCT origin_iata,destination_iata FROM flight_legs"
                )

        with (
            patch.object(reader_module, "FlightReader", cls),
            patch.object(routing_module, "airport_pairs", pairs),
        ):
            phases = io.StringIO()
            with redirect_stdout(phases):
                results["offline_preflight"] = [
                    measure(lambda: asyncio.run(serve.check_installation(root)))[0]
                    for _ in range(2)
                ]
            results["preflight_progress"] = phases.getvalue().splitlines()
        report["measurements"][label] = results
    with FlightReader(database).connect() as connection:
        queries = {
            "before_time_basis": "SELECT COUNT(*),MIN(departure_lower_us),MAX(departure_lower_us) FROM searchable_legs WHERE time_basis='observed'",
            "before_pairs": "SELECT DISTINCT origin_iata,destination_iata FROM flight_legs",
            "after_pairs": AIRPORT_PAIRS_CTE
            + " SELECT origin,destination FROM pairs WHERE destination IS NOT NULL",
        }
        traced = []
        connection.set_trace_callback(traced.append)
        FlightReader._time_summary(connection)
        connection.set_trace_callback(None)
        queries["after_time_summary"] = traced[0]
        for name, sql in queries.items():
            report["plans"][name] = [
                row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql)
            ]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=250_000)
    parser.add_argument("--baseline", default="6897542")
    args = parser.parse_args()
    if args.rows < 1000:
        parser.error("Use at least 1000 fixture rows")
    with tempfile.TemporaryDirectory(prefix="flightrepo-archive-benchmark-") as temporary:
        print(json.dumps(benchmark(Path(temporary), args.rows, args.baseline), indent=2))


if __name__ == "__main__":
    main()
