"""Profile a read-only search or continuation against an explicit serving database."""

import argparse
import cProfile
import json
import platform
import resource
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from core.connections import RebuildRequest
from core.routing.models import ENGINE_VERSION, RouteSearchRequest
from server.reader import FlightReader


class ProfiledReader(FlightReader):
    sql_statements = 0

    @contextmanager
    def connect(self):
        with super().connect() as connection:

            def executed(_statement):
                self.sql_statements += 1

            connection.set_trace_callback(executed)
            try:
                yield connection
            finally:
                connection.set_trace_callback(None)


def comparison(result):
    fields = (
        ("items", "next_offset")
        if "items" in result
        else ("legs", "connections", "complete", "options", "options_total", "next_option_offset")
    )
    return {field: result.get(field) for field in fields}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--query-json", type=Path, required=True)
    parser.add_argument("--operation", choices=("search", "rebuild"), default="search")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result-json", type=Path)
    parser.add_argument("--compare-json", type=Path)
    parser.add_argument("--cpu-profile", type=Path)
    args = parser.parse_args()
    model = RouteSearchRequest if args.operation == "search" else RebuildRequest
    request = model.model_validate_json(args.query_json.read_text())
    reader = ProfiledReader(args.db, serving_only=True)
    execute = reader.search_itineraries if args.operation == "search" else reader.rebuild
    profile = cProfile.Profile() if args.cpu_profile else None
    started = perf_counter()
    if profile:
        profile.enable()
    result = execute(request)
    if profile:
        profile.disable()
    elapsed = perf_counter() - started
    payload = result.model_dump(mode="json")
    matched = None
    if args.compare_json:
        matched = comparison(payload) == comparison(json.loads(args.compare_json.read_text()))
    report = {
        "engine_version": ENGINE_VERSION,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "operation": args.operation,
        "elapsed_seconds": elapsed,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "sql_statements": reader.sql_statements,
        "returned_items": len(payload.get("items", payload.get("options", []))),
        "next_offset": payload.get("next_offset", payload.get("next_option_offset")),
        "exhaustive": result.diagnostics.exhaustive,
        "stats": result.diagnostics.stats.model_dump(),
        "matches_comparison": matched,
        "cpu_profiling_enabled": profile is not None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if args.result_json:
        args.result_json.write_text(json.dumps(payload, indent=2) + "\n")
    if profile:
        profile.dump_stats(args.cpu_profile)
    print(json.dumps(report, indent=2))
    if matched is False:
        raise SystemExit(
            "Itineraries, continuation choices or pagination differ from the comparison"
        )
    if not result.diagnostics.exhaustive:
        raise SystemExit("The caller's work budget prevented complete ranking")


if __name__ == "__main__":
    main()
