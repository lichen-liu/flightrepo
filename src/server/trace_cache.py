"""Fetch one indexed gzip member using HTTP Range, with a bounded local cache."""

import gzip
import hashlib
import io
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from core.storage import adsblol_budget_lock

TOTAL_BUDGET = 20 * 2**30
CACHE_BUDGET = 8 * 2**30
MAX_TRACE_BYTES = 32 * 1024**2


def trace_ranges(observation, assets):
    if (
        observation.trace_offset is None
        or observation.trace_bytes is None
        or not observation.trace_sha256
    ):
        raise ValueError("Trace location absent; rebuild this observation's catalogue")
    offset, remaining, ranges = observation.trace_offset, observation.trace_bytes, []
    for asset in assets:
        url, size = urlsplit(asset["url"]), asset["bytes"]
        if (
            url.scheme != "https"
            or url.netloc != "github.com"
            or url.query
            or url.fragment
            or not re.fullmatch(
                r"/adsblol/globe_history_\d{4}/releases/download/v[0-9.]+-planes-readsb-(?:prod|staging)-\d+(?:tmp)?/v[0-9.]+-planes-readsb-(?:prod|staging)-\d+(?:tmp)?\.tar(?:\.[a-z]{2})?",
                url.path,
            )
            or type(size) is not int
            or size <= 0
        ):
            raise ValueError("Invalid archive location")
        if offset >= size:
            offset -= size
            continue
        count = min(size - offset, remaining)
        ranges.append((asset["url"], offset, offset + count - 1, size))
        remaining -= count
        offset = 0
        if remaining == 0:
            return ranges
    raise ValueError("Trace location exceeds published archive size")


def decode_trace(compressed, observation):
    if hashlib.sha256(compressed).hexdigest() != observation.trace_sha256:
        raise ValueError("Trace SHA-256 differs from the indexed archive")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
        raw = source.read(MAX_TRACE_BYTES + 1)
    if len(raw) > MAX_TRACE_BYTES:
        raise ValueError("Decompressed trace exceeds size limit")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("icao") != observation.aircraft.icao_hex:
        raise ValueError("Trace ICAO differs from catalogue")
    return data


def cached_trace(observation, assets, root: Path, *, client=None):
    ranges = trace_ranges(observation, assets)
    root = root.resolve()
    cache = root / "trace-cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{observation.trace_sha256}.json.gz"
    # Coordinate workers and processes so concurrent misses cannot exceed the limit.
    with adsblol_budget_lock(root):
        if path.is_file():
            try:
                if path.stat().st_size > MAX_TRACE_BYTES:
                    raise ValueError("Cached trace exceeds size limit")
                data = decode_trace(path.read_bytes(), observation)
                os.utime(path)
                return data, True
            except (ValueError, OSError, EOFError):
                path.unlink()
        compressed = bytearray()
        owns = client is None
        client = client or httpx.Client(
            follow_redirects=True, timeout=60, headers={"User-Agent": "Flyji-ADSB-trace/1.0"}
        )
        try:
            for url, start, end, size in ranges:
                with client.stream(
                    "GET", url, headers={"Range": f"bytes={start}-{end}"}
                ) as response:
                    response.raise_for_status()
                    if (
                        response.status_code != 206
                        or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}"
                    ):
                        raise ValueError(
                            "Source did not honor the exact byte range; full archive download refused"
                        )
                    received = 0
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > end - start + 1:
                            raise ValueError("Source exceeded requested trace range")
                        compressed.extend(chunk)
                    if received != end - start + 1:
                        raise ValueError("Incomplete trace range")
        finally:
            if owns:
                client.close()
        data = decode_trace(compressed, observation)
        files = sorted(cache.glob("*.json.gz"), key=lambda file: file.stat().st_mtime)
        cache_bytes = sum(file.stat().st_size for file in files)
        total_bytes = sum(file.stat().st_size for file in root.rglob("*") if file.is_file())
        while files and (
            cache_bytes + len(compressed) > CACHE_BUDGET
            or total_bytes + len(compressed) > TOTAL_BUDGET
        ):
            oldest = files.pop(0)
            removed = oldest.stat().st_size
            oldest.unlink()
            cache_bytes -= removed
            total_bytes -= removed
        if total_bytes + len(compressed) > TOTAL_BUDGET:
            # The catalogue or active raw day occupies the allowance. Serve uncached.
            return data, False
        path.write_bytes(compressed)
        return data, False
