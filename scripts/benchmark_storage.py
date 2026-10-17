"""Compare SQLite write/index costs on a disposable, deterministic observed-flight fixture."""

import argparse
import json
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from core.storage import QUERY_INDEXES
from ingestion.adsblol.extraction import SCHEMA, STAGING_SCHEMA


def airport(number):
    return "A" + chr(65 + number // 26) + chr(65 + number % 26)


def populate(connection, rows):
    connection.execute("INSERT INTO data_sources(code,name) VALUES('benchmark','Benchmark')")
    connection.executemany(
        "INSERT INTO airports(iata) VALUES(?)", [(airport(i),) for i in range(200)]
    )
    start = time.perf_counter()
    connection.execute(
        """WITH RECURSIVE numbers(n) AS (
            VALUES(0) UNION ALL SELECT n+1 FROM numbers WHERE n+1<:rows
        ), facts AS (
            SELECT n, 1672531200000000+(n%365)*86400000000+(n%1000)*60000000 AS departure
            FROM numbers
        ) INSERT INTO flight_legs(source_id,source_record_key,origin_iata,destination_iata,
            evidence_kind,departure_observed_lower_us,departure_observed_upper_us,
            arrival_observed_lower_us,arrival_observed_upper_us,first_seen_us,last_seen_us)
        SELECT 1,printf('%d',n),'A'||char(65+(n%200)/26)||char(65+(n%200)%26),
            'A'||char(65+((n+1)%200)/26)||char(65+((n+1)%200)%26),
            'inferred_ground_brackets',departure,departure+30000000,
            departure+7200000000,departure+7230000000,departure,departure FROM facts""",
        {"rows": rows},
    )
    connection.commit()
    return time.perf_counter() - start


def arrival_read(connection):
    query = (
        "SELECT arrival_lower_us,arrival_upper_us FROM searchable_legs "
        "WHERE destination_iata=? AND time_basis='observed'"
    )
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        result = connection.execute(query, ("AAB",)).fetchall()
        timings.append((time.perf_counter() - start) * 1000)
    return {
        "median_ms": statistics.median(timings),
        "rows": len(result),
        "plan": [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query, ("AAB",))],
    }


def benchmark(rows, directory):
    report = {"fixture_rows": rows, "sqlite": sqlite3.sqlite_version}
    with closing(sqlite3.connect(directory / "stage.db", cached_statements=0)) as stage:
        stage.executescript(STAGING_SCHEMA)
        report["stage_insert_seconds"] = populate(stage, rows)
    with closing(sqlite3.connect(directory / "indexed.db", cached_statements=0)) as connection:
        connection.executescript(SCHEMA)
        report["indexed_insert_seconds"] = populate(connection, rows)
        report["inbound_with_index"] = arrival_read(connection)
        sizes = None
        if connection.execute("SELECT sqlite_compileoption_used('ENABLE_DBSTAT_VTAB')").fetchone()[
            0
        ]:
            sizes = {
                name: size
                for name, size in connection.execute(
                    "SELECT name,SUM(pgsize) FROM dbstat GROUP BY name"
                )
                if name.startswith("flight_legs_")
            }
        report["query_index_bytes"] = sizes
        for basis in ("scheduled", "actual", "observed"):
            connection.execute(f"DROP INDEX flight_legs_{basis}_destination")
        report["inbound_without_index"] = arrival_read(connection)
        connection.executescript(QUERY_INDEXES)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--output", type=Path, default=Path("var/storage-benchmark.json"))
    args = parser.parse_args()
    if not 1 <= args.rows <= 1000000:
        parser.error("rows must be between 1 and 1,000,000")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="flight-storage-", dir=args.output.parent) as temporary:
        report = benchmark(args.rows, Path(temporary))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
