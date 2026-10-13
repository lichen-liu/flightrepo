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
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8013")
    parser.add_argument("--requests", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("var/api-load.json"))
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("this probe targets a local development server")
    if not 1 <= args.requests <= 1000 or not 1 <= args.workers <= 32:
        parser.error("requests must be 1–1000; workers must be 1–32")
    body = {
        "origin": "YYZ",
        "destination": "CDG",
        "departure_date": "2026-10-17",
        "arrival_by_local": "2026-10-21T23:59",
        "allow_airport_change": True,
        "ranking": "earliest_arrival",
        "trace": False,
    }
    wire = json.dumps(body).encode()

    def request(_):
        start = time.perf_counter()
        with urlopen(
            Request(
                args.url + "/v1/itineraries/search/local",
                data=wire,
                headers={"Content-Type": "application/json"},
            ),
            timeout=15,
        ) as response:
            data = json.load(response)
        assert data["diagnostics"]["exhaustive"], "expected the uncapped small demo to finish"
        assert data["items"], "expected demo routes"
        return (time.perf_counter() - start) * 1000, data

    request(None)  # warm-up outside timed batch
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        results = list(workers.map(request, range(args.requests)))
    total = time.perf_counter() - start
    fingerprints = {
        hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() for _, data in results
    }
    assert len(fingerprints) == 1, "a stable fixture produced inconsistent responses"
    timings = sorted(ms for ms, _ in results)
    source = Path(__file__)
    report = {
        "method": "One warm-up, then concurrent loopback HTTP reads of a twelve-flight demo. Includes transport and JSON parsing. No writer was active. This is not production capacity or provider validation.",
        "python": platform.python_version(),
        "requests": args.requests,
        "workers": args.workers,
        "wall_seconds": total,
        "median_ms": statistics.median(timings),
        "p95_ms": timings[math.ceil(len(timings) * 0.95) - 1],
        "max_ms": max(timings),
        "identical_response_fingerprints": len(fingerprints),
        "failed_requests": 0,
        "query": body,
        "diagnostics": results[0][1]["diagnostics"],
        "source_sha256": {
            str(source.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
        },
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
                    "failed_requests",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
