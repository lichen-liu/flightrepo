"""Mixed releases are independent snapshots, not extra pieces of one tar."""

import copy
import gzip
import hashlib
import io
import json
import tarfile
from datetime import date

import httpx
import pytest
from test_adsblol import rules
from test_adsblol_extraction import daily_input, rows
from test_adsblol_legs import AIRPORTS, candidates, leg_trace

from ingestion.adsblol.archive import archive_traces
from ingestion.adsblol.diagnostics import Diagnostics
from ingestion.adsblol.extraction import extract_day


def mixed_input(left, right):
    def archive_bytes(traces):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for data in traces:
                content = gzip.compress(json.dumps(data).encode(), mtime=0)
                member = tarfile.TarInfo(f"traces/5c/trace_full_{data['icao']}.json")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return stream.getvalue()

    day = date(2025, 1, 1)
    full, _ = daily_input(day, left)
    full_bytes, split_bytes = archive_bytes(left), archive_bytes(right)
    full.update(bytes=len(full_bytes), sha256=hashlib.sha256(full_bytes).hexdigest())
    payloads = {full["name"]: full_bytes}
    assets = [full]
    # Deliberately cross both a header and compressed member boundary.
    for suffix, content in zip(("aa", "ab"), (split_bytes[:600], split_bytes[600:]), strict=True):
        name = full["name"] + "." + suffix
        payloads[name] = content
        assets.append(
            {
                **full,
                "name": name,
                "url": full["url"] + "." + suffix,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return assets, payloads


def read_mixed(tmp_path, left, right, *, diagnostics=None):
    _, payloads = mixed_input(left, right)
    paths = []
    for name, content in payloads.items():
        path = tmp_path / name
        path.write_bytes(content)
        paths.append(path)
    return [data for data, _ in archive_traces(paths[::-1], diagnostics=diagnostics)]


def test_reads_both_streams_and_keeps_aircraft_unique_to_either(tmp_path):
    shared = leg_trace()
    first = {**shared, "icao": "000001"}
    last = {**shared, "icao": "000002"}
    left = [shared, first]
    right = [shared, last]
    result = read_mixed(tmp_path, left, right)
    assert [data["icao"] for data in result] == [shared["icao"], "000001", "000002"]
    assert candidates(result[1], AIRPORTS, rules())
    assert candidates(result[2], AIRPORTS, rules())


def test_complementary_snapshots_rebase_absolute_microseconds_and_complete_leg(tmp_path):
    original = leg_trace()
    left = {**original, "trace": original["trace"][:4]}
    right = copy.deepcopy(original)
    right["timestamp"] += 200
    right["trace"] = right["trace"][4:]
    for row in right["trace"]:
        row[0] -= 200
    (merged,) = read_mixed(tmp_path, [left], [right])
    assert merged["trace"] == original["trace"]
    assert candidates(merged, AIRPORTS, rules()) == candidates(original, AIRPORTS, rules())


def test_equal_time_conflicts_keep_longest_snapshot_whole_row_and_exclusions(tmp_path):
    original = leg_trace()
    shorter = copy.deepcopy(original)
    shorter["trace"] = shorter["trace"][2:4]
    shorter["trace"][0][1:3] = [0, 0]
    shorter["trace"][0][8] = {"category": "A7"}
    (merged,) = read_mixed(tmp_path, [shorter], [original])
    same_time = [row for row in merged["trace"] if row[0] == 100]
    assert same_time == [original["trace"][2], shorter["trace"][0]]
    assert candidates(merged, AIRPORTS, rules()) == []


def test_military_flags_and_type_conflicts_are_conservative(tmp_path):
    original = leg_trace()
    shorter = {**original, "trace": original["trace"][-2:], "t": "B738", "dbFlags": 1}
    diagnostics = Diagnostics()
    (merged,) = read_mixed(tmp_path, [shorter], [original], diagnostics=diagnostics)
    assert merged["dbFlags"] == 1 and merged["t"] is None
    assert candidates(merged, AIRPORTS, rules()) == []
    assert diagnostics.groups["metadata"]["mixed_snapshot_type_conflict"] == 1


def test_same_time_valid_fallback_and_nearby_distinct_samples_are_retained(tmp_path):
    original = leg_trace()
    longer = copy.deepcopy(original)
    longer["trace"][2][1] = 95
    shorter = copy.deepcopy(original)
    shorter["t"] = " a320 "
    shorter["trace"] = shorter["trace"][2:4]
    near = copy.deepcopy(shorter["trace"][0])
    near[0] += 0.01
    shorter["trace"].insert(1, near)
    (merged,) = read_mixed(tmp_path, [shorter], [longer])
    assert [row[0] for row in merged["trace"]][2:5] == [100, 100, 100.01]
    assert merged["t"] == "A320"
    assert len(candidates(merged, AIRPORTS, rules())) == 1


def test_mixed_day_commits_all_source_identities_and_resumes_without_download(tmp_path):
    original = leg_trace()
    left = {**original, "trace": original["trace"][:4]}
    right = {**original, "trace": original["trace"][4:]}
    assets, payloads = mixed_input([left], [right])
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=payloads[request.url.path.rsplit("/", 1)[1]]
            )
        )
    ) as client:
        receipt = extract_day(
            tmp_path, "2025-01-01", assets, AIRPORTS, rules(), download=True, client=client
        )
    assert len(receipt["inputs"]) == 3
    assert receipt["counts"]["legs_completed"] == 1
    before = rows(tmp_path, "itinerary_legs")
    assert len(before) == 1
    resumed = extract_day(tmp_path, "2025-01-01", assets[::-1], AIRPORTS, rules())
    assert resumed["result"] == "already_committed"
    assert rows(tmp_path, "itinerary_legs") == before
    assert not list((tmp_path / "raw").rglob("*.tar*"))


def test_corrupt_second_stream_never_commits_a_partial_day(tmp_path):
    original = leg_trace()
    assets, payloads = mixed_input([original], [original])
    broken = io.BytesIO()
    with tarfile.open(fileobj=broken, mode="w") as archive:
        member = tarfile.TarInfo("traces/5c/trace_full_a5ea5c.json")
        member.size = 7
        archive.addfile(member, io.BytesIO(b"corrupt"))
    content = broken.getvalue()
    for item, part in zip(assets[1:], (content[:600], content[600:]), strict=True):
        payloads[item["name"]] = part
        item.update(bytes=len(part), sha256=hashlib.sha256(part).hexdigest())
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=payloads[request.url.path.rsplit("/", 1)[1]]
                )
            )
        ) as client,
        pytest.raises(gzip.BadGzipFile),
    ):
        extract_day(tmp_path, "2025-01-01", assets, AIRPORTS, rules(), download=True, client=client)
    assert rows(tmp_path, "itinerary_legs") == []
    assert rows(tmp_path, "extraction_days") == []


def test_duplicate_member_within_a_stream_is_still_rejected(tmp_path):
    _, payloads = mixed_input([leg_trace()], [leg_trace()])
    broken = io.BytesIO()
    with tarfile.open(fileobj=broken, mode="w") as archive:
        for _ in range(2):
            content = gzip.compress(json.dumps(leg_trace()).encode(), mtime=0)
            member = tarfile.TarInfo("traces/5c/trace_full_000000.json")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    payloads[next(iter(payloads))] = broken.getvalue()
    paths = []
    for name, content in payloads.items():
        path = tmp_path / name
        path.write_bytes(content)
        paths.append(path)
    with pytest.raises(ValueError, match="Duplicate aircraft"):
        list(archive_traces(paths))
