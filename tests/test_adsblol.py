import gzip
import io
import json
import tarfile
from datetime import date

import httpx
import pytest

from ingestion.adsblol.archive import days_in_year, download_manifest, validate_asset
from ingestion.adsblol.observations import CommercialFilter, summarize_archives, summarize_trace


def rules():
    return CommercialFilter(fixed_wing_types={"A320"}, callsign_prefixes={"UAL"})


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


def test_summary_preserves_metadata_but_does_not_invent_a_flight():
    observation, reason = summarize_trace(trace(), date(2025, 1, 1), rules())
    assert reason == "retained"
    assert observation.positions == 2
    assert observation.aircraft_type_code == "A320"
    assert observation.callsigns == ["UAL123"]
    assert observation.classification_evidence == ["callsign_prefix:UAL"]
    assert "departure_scheduled" not in observation.model_dump()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"icao": "~a5ea5c"}, "unidentified_address"),
        ({"t": "R44"}, "type_excluded_or_unknown"),
        ({"t": None}, "type_excluded_or_unknown"),
        ({"dbFlags": 1}, "military"),
        ({"timestamp": 1735862400}, "no_positions_in_day"),
    ],
)
def test_excludes_unneeded_or_unidentified_traces(changes, reason):
    assert summarize_trace(trace(**changes), date(2025, 1, 1), rules()) == (None, reason)


def test_airliner_type_alone_is_not_commercial_evidence():
    data = trace()
    for row in data["trace"]:
        row[8]["flight"] = "N480UA"
    assert summarize_trace(data, date(2025, 1, 1), rules()) == (None, "commercial_evidence_missing")


def test_trace_catalogue_reads_split_archives_without_extracting_members(tmp_path):
    stream = io.BytesIO()
    raw = gzip.compress(json.dumps(trace()).encode())
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
    observations, stats = summarize_archives([a, b], date(2025, 1, 1), rules())
    assert len(observations) == 1 and stats["retained"] == 1
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


def test_published_year_inventory_covers_all_days_and_handles_year_boundary():
    from pathlib import Path

    manifest = json.loads((Path(__file__).parents[1] / "catalogs/adsblol-2025.json").read_text())
    assert {item["date"] for item in manifest["assets"]} == {str(day) for day in days_in_year(2025)}
    assert manifest["bytes"] == sum(item["bytes"] for item in manifest["assets"])
    for item in manifest["assets"]:
        validate_asset(item)
    assert all(
        "globe_history_2026/" in item["url"]
        for item in manifest["assets"]
        if item["date"] == "2025-12-31"
    )


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
