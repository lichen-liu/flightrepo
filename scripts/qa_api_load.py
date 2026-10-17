"""Small loopback-only concurrent HTTP probe; synthetic measurements, not an SLA."""

import argparse
import hashlib
import json
import math
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8013")
    parser.add_argument("--requests", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("var/api-load.json"))
    parser.add_argument(
        "--query-json", type=Path, help="local-search request JSON; defaults to the demo query"
    )
    parser.add_argument(
        "--utc", action="store_true", help="use the UTC search contract with --query-json"
    )
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("this probe targets a local development server")
    if not 1 <= args.requests <= 1000 or not 1 <= args.workers <= 32:
        parser.error("requests must be 1–1000; workers must be 1–32")
    if args.utc and not args.query_json:
        parser.error("--utc requires --query-json")
    endpoint = "/v1/itineraries/search" + ("" if args.utc else "/local")
    body = {
        "origin": "YYZ",
        "destination": "CDG",
        "departure_date": "2026-10-17",
        "arrival_by_local": "2026-10-21T23:59",
        "allow_airport_change": True,
        "ranking": "earliest_arrival",
        "trace": False,
    }
    if args.query_json:
        body = json.loads(args.query_json.read_text())
    wire = json.dumps(body).encode()

    def request(_):
        health_start = time.perf_counter()
        with urlopen(args.url + "/health/ready", timeout=5) as response:
            assert json.load(response)["status"] == "ready"
        health_ms = (time.perf_counter() - health_start) * 1000
        start = time.perf_counter()
        try:
            with urlopen(
                Request(
                    args.url + endpoint,
                    data=wire,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=120,
            ) as response:
                data = json.load(response)
        except HTTPError as error:
            data = json.load(error)
            if error.code != 503 or data.get("detail", {}).get("code") != "server_busy":
                raise RuntimeError(f"HTTP {error.code}: {data.get('detail')}") from error
            assert error.headers.get("Retry-After"), "overload must tell callers to retry"
            return None, None, health_ms
        assert data["diagnostics"]["exhaustive"], "expected complete ranking for the chosen query"
        assert data["items"], "expected matching routes"
        return (time.perf_counter() - start) * 1000, data, health_ms

    assert request(None)[0] is not None  # warm-up outside timed batch
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        results = list(workers.map(request, range(args.requests)))
    total = time.perf_counter() - start
    completed = [(ms, data) for ms, data, _ in results if ms is not None]
    assert completed, "no request completed"
    fingerprints = {
        hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        for _, data in completed
    }
    assert len(fingerprints) == 1, "a stable fixture produced inconsistent responses"
    timings = sorted(ms for ms, _ in completed)
    health_timings = sorted(ms for _, _, ms in results)
    report = {
        "method": "One warm-up, then concurrent loopback HTTP searches and readiness probes. Includes transport and JSON parsing. Server-busy responses are counted separately; other errors fail the probe. Run with representative data and query windows before choosing capacity settings.",
        "python": platform.python_version(),
        "requests": args.requests,
        "workers": args.workers,
        "wall_seconds": total,
        "median_ms": statistics.median(timings),
        "p95_ms": timings[math.ceil(len(timings) * 0.95) - 1],
        "max_ms": max(timings),
        "completed_requests": len(completed),
        "rejected_busy": len(results) - len(completed),
        "health_p95_ms": health_timings[math.ceil(len(health_timings) * 0.95) - 1],
        "identical_response_fingerprints": len(fingerprints),
        "failed_requests": 0,
        "query": body,
        "endpoint": endpoint,
        "diagnostics": completed[0][1]["diagnostics"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "requests",
                    "workers",
                    "wall_seconds",
                    "median_ms",
                    "p95_ms",
                    "completed_requests",
                    "rejected_busy",
                    "health_p95_ms",
                    "failed_requests",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
