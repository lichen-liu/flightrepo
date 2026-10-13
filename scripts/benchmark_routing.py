"""Repeatable synthetic CPU/SQLite benchmarks. No network, no normal DB changes."""

import argparse
import json
import platform
import sqlite3
import statistics
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import LOCAL_DATA_DIR
from core.connections import RebuildRequest
from core.models import FlightRecord, FlightStatus
from core.planning import rebuild_from_snapshot
from core.routing.models import ENGINE_VERSION, RouteSearchRequest
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot
from ingestion.updater import FlightUpdater
from server.reader import FlightReader

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def flight(identifier, origin, destination, out, duration):
    return FlightRecord(
        provider="benchmark",
        provider_id=str(identifier),
        flight_number="TEST",
        origin_iata=origin,
        destination_iata=destination,
        departure_scheduled=BASE + timedelta(minutes=out),
        arrival_scheduled=BASE + timedelta(minutes=out + duration),
        status=FlightStatus.SCHEDULED,
        fetched_at=BASE,
    )


def request(**updates):
    return RouteSearchRequest.model_validate(
        {
            "origin": "AAA",
            "destination": "ZZZ",
            "departure_start": BASE,
            "departure_end": BASE + timedelta(days=5),
            "horizon_end": BASE + timedelta(days=7),
            "max_legs": 3,
            "minimum_connection_minutes": 15,
            "maximum_connection_minutes": 30,
            **updates,
        }
    )


def measure(name, execute, count, iterations):
    execute().model_dump_json()  # warm-up; fixture creation/import excluded
    timings = []
    result = None
    for _ in range(iterations):
        start = time.perf_counter_ns()
        result = execute()
        result.model_dump_json()
        timings.append((time.perf_counter_ns() - start) / 1_000_000)
    tracemalloc.start()
    execute().model_dump_json()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "case": name,
        "fixture_records": count,
        "iterations": iterations,
        "milliseconds": timings,
        "median_ms": statistics.median(timings),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "peak_operation_mib": peak / 1024**2,
        "returned_items": len(getattr(result, "items", getattr(result, "options", []))),
        "diagnostics": result.diagnostics.model_dump(mode="json"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=LOCAL_DATA_DIR / "routing-benchmark.json")
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 20:
        parser.error("iterations must be 1..20")
    cases = []
    direct = RoutingSnapshot(tuple(flight(i, "AAA", "ZZZ", i, 60 + i % 9) for i in range(6000)))
    q = request()
    cases.append(measure("6000_direct", lambda: search_routes(q, direct), 6000, args.iterations))
    dense = RoutingSnapshot(
        tuple(
            [
                *[flight("s" + str(i), "AAA", "BBB", i, 60) for i in range(32)],
                *[flight("m" + str(i), "BBB", "CCC", 60 + i, 45) for i in range(600)],
                *[flight("t" + str(i), "CCC", "ZZZ", 90 + i, 60) for i in range(600)],
            ]
        )
    )
    q_dense = request(departure_end=BASE + timedelta(hours=3))
    cases.append(
        measure(
            "dense_connections",
            lambda: search_routes(q_dense, dense),
            len(dense.flights),
            args.iterations,
        )
    )
    q_cap = q_dense.model_copy(
        update={"limits": q_dense.limits.model_copy(update={"max_candidates": 100})}
    )
    cases.append(
        measure(
            "candidate_budget_100",
            lambda: search_routes(q_cap, dense),
            len(dense.flights),
            args.iterations,
        )
    )
    decoys = RoutingSnapshot(
        tuple(
            [
                *[flight("s" + str(i), "AAA", "BBB", i, 60) for i in range(16)],
                *[flight("t" + str(i), "BBB", "ZZZ", 90 + i, 30) for i in range(30)],
                *[flight("x" + str(i), "BBB", "XXX", -10000 + i, 30) for i in range(8000)],
            ]
        )
    )
    q_decoys = request(maximum_connection_minutes=60)
    cases.append(
        measure(
            "8000_outside_time_window",
            lambda: search_routes(q_decoys, decoys),
            len(decoys.flights),
            args.iterations,
        )
    )
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    choices = RoutingSnapshot(
        (prefix, *[flight(i, "BBB", "ZZZ", 75 + i / 2, 30) for i in range(6000)])
    )
    q_rebuild = RebuildRequest(
        prefix=[{"provider": "benchmark", "provider_id": "prefix"}],
        destination="ZZZ",
        horizon_end=BASE + timedelta(days=3),
        max_legs=3,
    )
    cases.append(
        measure(
            "6000_continuation_options",
            lambda: rebuild_from_snapshot(q_rebuild, choices),
            len(choices.flights),
            args.iterations,
        )
    )
    LOCAL_DATA_DIR.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="routing-benchmark-", dir=LOCAL_DATA_DIR) as folder:
        updater = FlightUpdater(Path(folder) / "benchmark.db")
        updater.initialize()
        records = [
            *[flight("valid" + str(i), "AAA", "ZZZ", i, 60) for i in range(64)],
            *[flight("irrelevant" + str(i), "XXX", "YYY", i / 30, 60) for i in range(30000)],
        ]
        updater.upsert(records)
        reader = FlightReader(updater.path)
        cases.append(
            measure(
                "sqlite_30000_unreachable",
                lambda: reader.search_itineraries(request()),
                len(records),
                args.iterations,
            )
        )
        with reader.connect() as connection:
            query_plan = [
                dict(r)
                for r in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM flights WHERE origin_iata=? AND departure_scheduled>=? AND departure_scheduled<? AND arrival_scheduled<=? AND status != 'cancelled' ORDER BY departure_scheduled,provider,provider_id LIMIT 10001",
                    (
                        "AAA",
                        BASE.isoformat(),
                        (BASE + timedelta(days=5)).isoformat(),
                        (BASE + timedelta(days=7)).isoformat(),
                    ),
                )
            ]
    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "environment": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "method": "One warm-up, timed full core/reader call plus JSON serialization; setup/import excluded. Memory is a separate tracemalloc run. Local synthetic single-request measurements, not production latency guarantees.",
        "sqlite_query_plan": query_plan,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cases": [
                    {
                        k: c[k]
                        for k in (
                            "case",
                            "fixture_records",
                            "median_ms",
                            "peak_operation_mib",
                        )
                    }
                    | {
                        "checked": c["diagnostics"]["stats"]["candidates_checked"],
                        "loaded": c["diagnostics"]["stats"]["rows_loaded"],
                        "exhaustive": c["diagnostics"]["exhaustive"],
                    }
                    for c in cases
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
