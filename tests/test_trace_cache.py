import gzip
import hashlib
import json
from datetime import date

import httpx
import pytest
from test_adsblol import asset, rules, trace

from ingestion.adsblol.observations import summarize_trace
from server import trace_cache


def indexed():
    data = gzip.compress(json.dumps(trace()).encode())
    observation, _ = summarize_trace(trace(), date(2025, 1, 1), rules())
    observation.trace_offset = 80
    observation.trace_bytes = len(data)
    observation.trace_sha256 = hashlib.sha256(data).hexdigest()
    assets = [
        {"url": asset()["url"] + ".aa", "bytes": 100},
        {"url": asset()["url"] + ".ab", "bytes": 1000},
    ]
    return observation, assets, data


def test_range_fetch_crosses_tar_part_boundary_then_hits_cache(tmp_path):
    observation, assets, data = indexed()
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith(".aa"):
            chunk, start, end, size = data[:20], 80, 99, 100
        else:
            chunk, start, end, size = data[20:], 0, len(data) - 21, 1000
        assert request.headers["Range"] == f"bytes={start}-{end}"
        return httpx.Response(
            206, headers={"Content-Range": f"bytes {start}-{end}/{size}"}, content=chunk
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result, hit = trace_cache.cached_trace(observation, assets, tmp_path, client=client)
        assert result == trace() and not hit
        result, hit = trace_cache.cached_trace(observation, assets, tmp_path, client=client)
        assert result == trace() and hit
    assert len(requests) == 2
    assert sum(p.stat().st_size for p in (tmp_path / "trace-cache").iterdir()) == len(data)


@pytest.mark.parametrize("status,content_range", [(200, None), (206, "bytes 1-2/100"), (404, None)])
def test_source_errors_never_fall_back_to_full_archives(tmp_path, status, content_range):
    observation, assets, data = indexed()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                headers={"Content-Range": content_range} if content_range else {},
                content=data,
            )
        )
    ) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            trace_cache.cached_trace(observation, assets, tmp_path, client=client)
    assert not list((tmp_path / "trace-cache").glob("*.json.gz"))


def test_cache_evicts_oldest_and_serves_without_writing_if_catalogue_uses_budget(
    tmp_path, monkeypatch
):
    observation, assets, data = indexed()
    cache = tmp_path / "trace-cache"
    cache.mkdir()
    old = cache / "old.json.gz"
    old.write_bytes(b"x" * len(data))
    monkeypatch.setattr(trace_cache, "CACHE_BUDGET", len(data))

    def respond(request):
        chunk, header = (
            (data[:20], "bytes 80-99/100")
            if request.url.path.endswith(".aa")
            else (data[20:], f"bytes 0-{len(data) - 21}/1000")
        )
        return httpx.Response(206, headers={"Content-Range": header}, content=chunk)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result, hit = trace_cache.cached_trace(observation, assets, tmp_path, client=client)
        assert result == trace() and not hit
        assert not old.exists()
        (tmp_path / "catalogue.db").write_bytes(b"x" * len(data))
        monkeypatch.setattr(trace_cache, "TOTAL_BUDGET", len(data))
        # Force a miss, keeping only the full catalogue in the allowance.
        next(cache.glob("*.json.gz")).unlink()
        result, hit = trace_cache.cached_trace(observation, assets, tmp_path, client=client)
        assert result == trace() and not hit
        assert not list(cache.glob("*.json.gz"))


def test_rejects_unknown_locations_and_arbitrary_hosts():
    observation, assets, _ = indexed()
    assets[0]["url"] = "https://localhost/private"
    with pytest.raises(ValueError, match="Invalid archive"):
        trace_cache.trace_ranges(observation, assets)
    observation.trace_offset = None
    with pytest.raises(ValueError, match="location absent"):
        trace_cache.trace_ranges(observation, assets)
