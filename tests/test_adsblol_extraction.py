import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import httpx
import pytest
from test_adsblol import asset, rules
from test_adsblol_legs import AIRPORTS, leg_trace

from core.itinerary_cache import database_bytes, set_quota
from ingestion.adsblol import extraction
from ingestion.adsblol.extraction import extract_day
from ingestion.adsblol.movements import AirportLookup, reduce_movement
from ingestion.updater import FlightUpdater


def daily_input(day, traces):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for index, data in enumerate(traces):
            content = gzip.compress(json.dumps(data).encode())
            info = tarfile.TarInfo(f"traces/5c/trace_full_{index:06x}.json")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        info = tarfile.TarInfo("../../outside")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"skip"))
    content = stream.getvalue()
    name = f"v{day.strftime('%Y.%m.%d')}-planes-readsb-prod-0.tar"
    item = asset(
        date=str(day),
        name=name,
        bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        url=f"https://github.com/adsblol/globe_history_{day.year}/releases/download/v{day.strftime('%Y.%m.%d')}-planes-readsb-prod-0/{name}",
    )
    return item, content


def run(root, day, traces, **options):
    item, content = daily_input(day, traces)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    ) as client:
        receipt = extract_day(
            root, day, [item], AIRPORTS, rules(), download=True, client=client, **options
        )
    return receipt, item


def rows(root, table):
    with sqlite3.connect(root / "itineraries.db") as connection:
        return connection.execute(f"SELECT * FROM {table}").fetchall()


def test_daily_extraction_keeps_only_itinerary_data_then_removes_raw_and_resumes(tmp_path):
    receipt, item = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    assert receipt["counts"]["legs_completed"] == 1
    key, departure, raw = rows(tmp_path, "itinerary_legs")[0]
    assert len(key) == 32 and departure == "2025-01-01"
    data = json.loads(raw)
    assert data["origin_iata"] == "AAA" and data["destination_iata"] == "BBB"
    assert data["aircraft_type_code"] == "A320" and data["callsign"] == "UAL123"
    assert {"trace", "icao_hex", "lat", "lon", "departure_scheduled"}.isdisjoint(data)
    assert data["status"] == "inferred_candidate"
    sources = {row[0]: row for row in rows(tmp_path, "extraction_sources")}
    assert sources["adsblol"][3] == "ODbL-1.0"
    assert sources["ourairports"][3] == "Public Domain"
    assert not (tmp_path / "raw" / "2025-01-01" / item["name"]).exists()
    assert not (tmp_path.parent / "outside").exists()
    resumed = extract_day(tmp_path, "2025-01-01", [item], AIRPORTS, rules())
    assert resumed["result"] == "already_committed"
    assert len(rows(tmp_path, "itinerary_legs")) == 1


def midnight_traces():
    first, second = leg_trace(), leg_trace()
    first["trace"] = first["trace"][:5]
    for row in first["trace"]:
        row[0] += 86090
    second["timestamp"] += 86400
    second["trace"] = second["trace"][5:]
    for row in second["trace"]:
        row[0] += 86090 - 86400
    return first, second


def test_midnight_carry_survives_restart_without_positions(tmp_path):
    first, second = midnight_traces()
    run(tmp_path, date(2025, 1, 1), [first])
    assert rows(tmp_path, "itinerary_legs") == []
    pending = json.loads(rows(tmp_path, "pending_movements")[0][1])
    assert pending["ground"]["airport"] == "AAA"
    assert {"trace", "lat", "lon", "positions"}.isdisjoint(pending)
    receipt, _ = run(tmp_path, date(2025, 1, 2), [second])
    assert receipt["counts"]["legs_completed"] == 1
    assert rows(tmp_path, "itinerary_legs")[0][1] == "2025-01-01"
    assert receipt["pending_airborne"] == 0


def test_out_of_order_backfill_joins_midnight_without_redownloading_arrival_day(tmp_path):
    first, second = midnight_traces()
    run(tmp_path, date(2025, 1, 2), [second])
    assert rows(tmp_path, "itinerary_legs") == []
    receipt, item = run(tmp_path, date(2025, 1, 1), [first])
    assert len(rows(tmp_path, "itinerary_legs")) == 1
    assert receipt["counts"]["records_inserted"] == 1
    assert not list((tmp_path / "raw").rglob("*.tar"))
    extract_day(tmp_path, "2025-01-01", [item], AIRPORTS, rules())
    assert len(rows(tmp_path, "itinerary_legs")) == 1


def test_backfill_after_a_gap_never_joins_unobserved_days(tmp_path):
    first, second = midnight_traces()
    run(tmp_path, date(2025, 1, 1), [first])
    second["timestamp"] += 86400
    run(tmp_path, date(2025, 1, 3), [second])
    assert rows(tmp_path, "itinerary_legs") == []


@pytest.mark.parametrize("order", [(1, 2, 3), (3, 1, 2), (3, 2, 1), (1, 3, 2)])
def test_boundary_summary_propagates_through_a_whole_airborne_day(tmp_path, order):
    first, last = midnight_traces()
    last["timestamp"] += 86400
    middle = leg_trace()
    middle["timestamp"] += 86400
    middle["trace"] = [
        [offset, 40.5, -70.5, 20000, 400, 0, 0, 0, {"flight": "UAL123"}]
        for offset in [*range(0, 86400, 100), 86390]
    ]
    traces = {1: first, 2: middle, 3: last}
    for number in order:
        run(tmp_path, date(2025, 1, number), [traces[number]])
    assert len(rows(tmp_path, "itinerary_legs")) == 1
    assert rows(tmp_path, "itinerary_legs")[0][1] == "2025-01-01"
    assert not list((tmp_path / "raw").rglob("*.tar"))


def test_existing_day_can_expand_to_another_year_scope_without_duplicates(tmp_path):
    first, second = midnight_traces()
    first["timestamp"] = int(datetime(2025, 12, 31, tzinfo=UTC).timestamp())
    second["timestamp"] = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    flight = leg_trace()
    flight["icao"] = "b5ea5c"
    flight["timestamp"] = second["timestamp"]
    run(tmp_path, date(2025, 12, 31), [first], departure_year=2025)
    run(tmp_path, date(2026, 1, 1), [second, flight], departure_year=2025)
    assert len(rows(tmp_path, "itinerary_legs")) == 1
    receipt, _ = run(tmp_path, date(2026, 1, 1), [second, flight], departure_year=2026)
    assert len(rows(tmp_path, "itinerary_legs")) == 2
    assert receipt["counts"]["records_inserted"] == 1


def test_boundary_refresh_deduplicates_existing_v1_records(tmp_path):
    _, item = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    with sqlite3.connect(tmp_path / "itineraries.db") as connection:
        receipt = json.loads(
            connection.execute("SELECT receipt FROM extraction_days").fetchone()[0]
        )
        receipt.pop("boundary_version")
        connection.execute("UPDATE extraction_days SET receipt=?", (json.dumps(receipt),))
    receipt, _ = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    assert receipt["counts"]["records_inserted"] == 0
    assert receipt["counts"]["records_already_present"] == 1
    assert len(rows(tmp_path, "itinerary_legs")) == 1


def test_parallel_repeated_day_commits_only_once(tmp_path):
    item, content = daily_input(date(2025, 1, 1), [leg_trace()])
    requests = []

    def transport(request):
        requests.append(request.url)
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:

        def ingest(_):
            return extract_day(
                tmp_path, "2025-01-01", [item], AIRPORTS, rules(), download=True, client=client
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(ingest, range(2)))
    assert sorted(receipt["result"] for receipt in receipts) == ["already_committed", "committed"]
    assert len(requests) == 1 and len(rows(tmp_path, "itinerary_legs")) == 1


def test_external_output_database_is_budgeted_once_when_it_also_provides_policy(tmp_path):
    root, target = tmp_path / "scratch", tmp_path / "hosted" / "flights.db"
    FlightUpdater(target).initialize()
    receipt, item = run(root, date(2025, 1, 1), [leg_trace()], output_db=target, policy_db=target)
    assert receipt["counts"]["records_inserted"] == 1
    assert not (root / "itineraries.db").exists()
    budget = extraction.ExtractionBudget(root, policy_db=target, output_db=target)
    assert budget.used() == extraction.directory_bytes(root) + database_bytes(target)
    resumed = extract_day(
        root, "2025-01-01", [item], AIRPORTS, rules(), output_db=target, policy_db=target
    )
    assert resumed["result"] == "already_committed"
    set_quota(target, 0)
    with pytest.raises(OSError, match="quota"):
        run(root, date(2025, 1, 2), [], output_db=target, policy_db=target)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM itinerary_legs").fetchone()[0] == 1


def test_conflicting_identity_rolls_back_refresh_without_replacing_existing_record(tmp_path):
    run(tmp_path, date(2025, 1, 1), [leg_trace()])
    with sqlite3.connect(tmp_path / "itineraries.db") as connection:
        receipt = json.loads(
            connection.execute("SELECT receipt FROM extraction_days").fetchone()[0]
        )
        receipt.pop("boundary_version")
        connection.execute("UPDATE extraction_days SET receipt=?", (json.dumps(receipt),))
        record = json.loads(connection.execute("SELECT data FROM itinerary_legs").fetchone()[0])
        record["callsign"] = "UAL999"
        connection.execute("UPDATE itinerary_legs SET data=?", (extraction.canonical(record),))
    previous = rows(tmp_path, "itinerary_legs"), rows(tmp_path, "extraction_days")
    with pytest.raises(ValueError, match="Conflicting itinerary identity"):
        run(tmp_path, date(2025, 1, 1), [leg_trace()])
    assert previous == (rows(tmp_path, "itinerary_legs"), rows(tmp_path, "extraction_days"))


def test_year_boundary_retains_departure_year_without_importing_next_year_legs(tmp_path):
    first, second = midnight_traces()
    first["timestamp"] = int(datetime(2025, 12, 31, tzinfo=UTC).timestamp())
    second["timestamp"] = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    next_year = leg_trace()
    next_year["icao"] = "b5ea5c"
    next_year["timestamp"] = second["timestamp"]
    run(tmp_path, date(2025, 12, 31), [first], departure_year=2025)
    receipt, _ = run(tmp_path, date(2026, 1, 1), [second, next_year], departure_year=2025)
    assert len(rows(tmp_path, "itinerary_legs")) == 1
    assert receipt["counts"]["legs_outside_departure_year"] == 1


def test_failed_day_rolls_back_facts_receipt_and_state_and_preserves_raw(tmp_path):
    bad = leg_trace()
    bad["icao"] = "b5ea5c"
    bad["trace"][3][0] = -100
    with pytest.raises(ValueError, match="ordered"):
        run(tmp_path, date(2025, 1, 1), [leg_trace(), bad])
    assert not rows(tmp_path, "itinerary_legs")
    assert not rows(tmp_path, "extraction_days")
    assert not rows(tmp_path, "pending_movements")
    assert list((tmp_path / "raw").rglob("*.tar"))


def test_committed_day_cleans_raw_after_interrupted_cleanup(tmp_path, monkeypatch):
    cleanup = extraction.cleanup_raw
    monkeypatch.setattr(extraction, "cleanup_raw", lambda *args: None)
    _, item = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    assert list((tmp_path / "raw").rglob("*.tar"))
    monkeypatch.setattr(extraction, "cleanup_raw", cleanup)
    extract_day(tmp_path, "2025-01-01", [item], AIRPORTS, rules())
    assert not list((tmp_path / "raw").rglob("*.tar"))
    assert len(rows(tmp_path, "itinerary_legs")) == 1


def test_configuration_changes_are_rejected_but_separate_periods_are_allowed(tmp_path):
    _, item = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    with pytest.raises(ValueError, match="configuration"):
        extract_day(tmp_path, "2025-01-01", [item], AIRPORTS + [("CCC", 0, 0)], rules())
    third = leg_trace()
    third["timestamp"] += 2 * 86400
    run(tmp_path, date(2025, 1, 3), [third])
    assert len(rows(tmp_path, "itinerary_legs")) == 2


def test_partial_schema_initialization_can_be_repaired_on_restart(tmp_path):
    with sqlite3.connect(tmp_path / "itineraries.db") as connection:
        connection.execute(
            "CREATE TABLE itinerary_legs(key TEXT PRIMARY KEY, departure_date TEXT NOT NULL, data TEXT NOT NULL)"
        )
    receipt, _ = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    assert receipt["counts"]["legs_completed"] == 1


def test_existing_dataset_gains_license_metadata_without_redownloading(tmp_path):
    _, item = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    with sqlite3.connect(tmp_path / "itineraries.db") as connection:
        connection.execute("DROP TABLE extraction_sources")
    receipt = extract_day(tmp_path, "2025-01-01", [item], AIRPORTS, rules())
    assert receipt["result"] == "already_committed"
    assert rows(tmp_path, "extraction_sources") == extraction.SOURCE_ROWS
    assert len(rows(tmp_path, "itinerary_legs")) == 1


def test_cleanup_of_committed_raw_precedes_lowered_budget_check(tmp_path, monkeypatch):
    cleanup = extraction.cleanup_raw
    monkeypatch.setattr(extraction, "cleanup_raw", lambda *args: None)
    _, item = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    # Reopening a WAL database can create a 32 KiB shared-memory file.
    ceiling = extraction.directory_bytes(tmp_path) - item["bytes"] + 32768
    monkeypatch.setattr(extraction, "cleanup_raw", cleanup)
    receipt = extract_day(tmp_path, "2025-01-01", [item], AIRPORTS, rules(), limit=ceiling)
    assert receipt["result"] == "already_committed"
    assert extraction.directory_bytes(tmp_path) <= ceiling


def test_live_quota_change_aborts_uncommitted_day_without_removing_durable_facts(
    tmp_path, monkeypatch
):
    root = tmp_path / "extraction"
    policy_db = tmp_path / "server.db"
    FlightUpdater(policy_db).initialize()
    run(root, date(2025, 1, 1), [leg_trace()], policy_db=policy_db)
    reducer = extraction.reduce_movement

    def lower_quota(*args, **kwargs):
        result = reducer(*args, **kwargs)
        set_quota(policy_db, 0)
        return result

    monkeypatch.setattr(extraction, "reduce_movement", lower_quota)
    second = leg_trace()
    second["timestamp"] += 86400
    with pytest.raises(OSError, match="quota"):
        run(root, date(2025, 1, 2), [second], policy_db=policy_db)
    assert len(rows(root, "itinerary_legs")) == 1
    assert len(rows(root, "extraction_days")) == 1
    assert list((root / "raw" / "2025-01-02").glob("*.tar"))


def test_ambiguous_callsigns_stale_coverage_and_type_changes_do_not_invent_legs():
    start = 1735689600000000
    data = leg_trace()
    data["trace"][3][8] = {"flight": "UAL999"}
    assert (
        reduce_movement(data, None, AirportLookup(AIRPORTS), rules(), start, start + 86400000000)[0]
        == []
    )
    data = leg_trace()
    data["trace"][3][6] = 1
    assert (
        reduce_movement(data, None, AirportLookup(AIRPORTS), rules(), start, start + 86400000000)[0]
        == []
    )


def test_type_changes_interrupt_midnight_aircraft_assignment():
    start = 1735689600000000
    first, second = midnight_traces()
    _, state, _ = reduce_movement(
        first, None, AirportLookup(AIRPORTS), rules(), start, start + 86400000000
    )
    changed = rules()
    changed.fixed_wing_types.add("B738")
    second["t"] = "B738"
    assert (
        reduce_movement(
            second,
            state,
            AirportLookup(AIRPORTS),
            changed,
            start + 86400000000,
            start + 2 * 86400000000,
        )[0]
        == []
    )


def test_airport_buckets_match_across_dateline_poles_and_ambiguous_airports():
    lookup = AirportLookup([("AAA", 0, -179.999), ("BBB", 89.999, 90)])
    assert lookup.match(0, 179.999) == "AAA"
    assert lookup.match(89.999, -90) == "BBB"
    assert AirportLookup(AIRPORTS + [("CCC", 40.001, -70)]).match(40, -70) is None
