import gzip
import io
import json
import tarfile

import httpx
import pytest

from ingestion.adsblol.archive import (
    archive_traces,
    download_manifest,
)
from ingestion.adsblol.policy import ExtractionPolicy


def rules(**changes):
    return ExtractionPolicy(**changes)


def trace(**changes):
    return {
        "icao": "a5ea5c",
        "r": "N480UA",
        "t": "A320",
        "desc": "AIRBUS A-320",
        "ownOp": "UNITED AIRLINES INC",
        "dbFlags": 0,
        "timestamp": 1735689600,
        "trace": [
            [-10, 40, -70, 100, 200, 0, 0, 0, {"flight": "UAL123 "}, "mlat"],
            [
                10,
                40,
                -70,
                "ground",
                0,
                0,
                0,
                0,
                {"flight": "UAL123 ", "squawk": "1234", "category": "A3"},
                "adsb_icao",
            ],
            [50, 41, -71, 30000, 400, 0, 2, 0, {"flight": "UAL123 ", "emergency": "none"}, "mlat"],
        ],
        **changes,
    }


def test_reads_split_archives_without_extracting_members(tmp_path):
    stream = io.BytesIO()
    raw = gzip.compress(json.dumps(trace()).encode(), mtime=0)
    with tarfile.open(fileobj=stream, mode="w") as archive:
        item = tarfile.TarInfo("./traces/5c/trace_full_a5ea5c.json")
        item.size = len(raw)
        archive.addfile(item, io.BytesIO(raw))
        item = tarfile.TarInfo("../../escape")
        item.size = 5
        archive.addfile(item, io.BytesIO(b"hello"))
    data = stream.getvalue()
    a, b = tmp_path / "part.aa", tmp_path / "part.ab"
    a.write_bytes(data[:600])
    b.write_bytes(data[600:])
    members = list(archive_traces([a, b]))
    assert members == [(trace(), len(raw))]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["part.aa", "part.ab"]


def asset(**changes):
    name = "v2025.01.01-planes-readsb-prod-0.tar"
    return {
        "date": "2025-01-01",
        "name": name,
        "bytes": 100,
        "url": f"https://github.com/adsblol/globe_history_2025/releases/download/v2025.01.01-planes-readsb-prod-0/{name}",
        "sha256": None,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "../../escape"},
        {"url": "https://example.com/archive"},
        {"bytes": -1},
        {"sha256": "bad"},
        {"date": "2025-02-01"},
    ],
)
def test_rejects_invalid_manifest_before_download(tmp_path, changes):
    with pytest.raises(ValueError):
        download_manifest({"assets": [asset(**changes)]}, tmp_path / "raw")
    assert not (tmp_path / "raw").exists()


def test_budget_refuses_full_year_before_any_network_or_disk_writes(tmp_path):
    with pytest.raises(OSError, match="exceeds"):
        download_manifest({"assets": [asset(bytes=21 * 2**30)]}, tmp_path / "raw")
    assert not (tmp_path / "raw").exists()
    with pytest.raises(ValueError, match="at most 20"):
        download_manifest({"assets": []}, tmp_path / "raw", budget_bytes=21 * 2**30)


def test_archive_download_has_bounded_length_and_checks_digest(tmp_path):
    import hashlib

    data = b"a" * 100
    item = asset(sha256=hashlib.sha256(data).hexdigest())
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data))
    ) as client:
        download_manifest({"assets": [item]}, tmp_path, client)
    assert (tmp_path / item["date"] / item["name"]).read_bytes() == data
    bad = tmp_path / "bad"
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data + b"x"))
    ) as client:
        with pytest.raises(ValueError, match="exceeds published"):
            download_manifest({"assets": [item]}, bad, client)


def test_same_size_corrupt_download_is_not_published_as_verified(tmp_path):
    import hashlib

    item = asset(sha256=hashlib.sha256(b"a" * 100).hexdigest())
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"b" * 100))
    ) as client:
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            download_manifest({"assets": [item]}, tmp_path, client)
    path = tmp_path / item["date"] / item["name"]
    assert not path.exists()
    assert not path.with_name(path.name + ".verified.json").exists()
