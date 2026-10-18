"""Coverage, ranking and work regressions for indexed database routing."""

import pytest
from test_routing import BASE, flight, identity, oracle_paths, oracle_score, query

from core.connections import RebuildRequest
from core.routing.models import ENGINE_VERSION
from core.routing.snapshot import digest
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


@pytest.fixture
def store(tmp_path):
    writer = FlightUpdater(tmp_path / "flights.db")
    writer.initialize()
    return writer, FlightReader(writer.path)


@pytest.mark.parametrize("ranking", ["fewest_legs", "earliest_arrival", "shortest_elapsed"])
def test_connected_future_detours_are_not_loaded_when_they_cannot_improve_a_page(store, ranking):
    writer, reader = store
    records = [flight("root", "AAA", "BBB", 0, 20)]
    records += [flight(f"terminal-{i}", "BBB", "EEE", 40 + i, 80 + i) for i in range(8)]
    records += [flight(f"future-{i}", "BBB", "CCC", 144000 + i, 144100 + i) for i in range(12000)]
    records += [flight("future-terminal", "CCC", "EEE", 160000, 160100)]
    writer.upsert(records)
    request = query(
        ranking=ranking,
        max_legs=None,
        limit=2,
        horizon_end=BASE.replace(year=2027),
        maximum_connection_minutes=None,
    )
    result = reader.search_itineraries(request)
    expected = sorted(oracle_paths(records[:9], request), key=lambda p: oracle_score(p, ranking))[
        :2
    ]
    assert [identity(item.legs) for item in result.items] == [identity(p) for p in expected]
    assert result.next_offset == 2 and result.diagnostics.exhaustive
    assert result.diagnostics.stats.rows_loaded < 100
    assert result.diagnostics.stats.records_hydrated == 3


@pytest.mark.parametrize("ranking", ["fewest_legs", "earliest_arrival", "shortest_elapsed"])
def test_incumbent_bounds_preserve_late_fewer_leg_routes_and_all_pages(store, ranking):
    writer, reader = store
    records = [flight(f"late-direct-{i}", "AAA", "EEE", 10 + i, 300000 + i) for i in range(4)]
    records += [flight("root", "AAA", "BBB", 20, 40)]
    records += [flight(f"early-{i}", "BBB", "EEE", 60 + i, 100 + i) for i in range(5)]
    writer.upsert(records)
    request = query(ranking=ranking, limit=2, max_legs=None, horizon_end=BASE.replace(year=2027))
    expected = sorted(oracle_paths(records, request), key=lambda p: oracle_score(p, ranking))
    actual, offset = [], 0
    while True:
        result = reader.search_itineraries(request.model_copy(update={"offset": offset}))
        actual.extend(identity(item.legs) for item in result.items)
        assert result.diagnostics.exhaustive
        if result.next_offset is None:
            break
        offset = result.next_offset
    assert actual == [identity(p) for p in expected]


@pytest.mark.parametrize("ranking", ["earliest_arrival", "shortest_elapsed"])
def test_reranking_a_complete_small_seed_keeps_the_original_replay_request(store, ranking):
    writer, reader = store
    writer.upsert([flight("only", "AAA", "EEE", 10, 100)])
    result = reader.search_itineraries(query(ranking=ranking, limit=20))
    diagnostics = result.diagnostics
    assert diagnostics.ranking == ranking and diagnostics.exhaustive
    assert diagnostics.request_id == digest(
        {
            "engine": ENGINE_VERSION,
            "snapshot": diagnostics.snapshot_id,
            "query": result.query.model_dump(mode="json"),
        }
    )


@pytest.mark.parametrize("ranking", ["fewest_legs", "earliest_arrival", "shortest_elapsed"])
def test_explicit_candidate_budget_covers_all_search_phases(store, ranking):
    writer, reader = store
    writer.upsert([flight(str(i), "AAA", "EEE", i, 100 + i) for i in range(5)])
    result = reader.search_itineraries(
        query(ranking=ranking, limit=1, limits={"max_candidates": 1})
    )
    assert result.diagnostics.stats.candidates_checked == 1
    assert result.diagnostics.termination_reasons == ["candidate_limit"]
    assert not result.diagnostics.exhaustive


def test_ranking_seed_and_final_projection_use_one_sqlite_snapshot(store, monkeypatch):
    import server.routing_projection as projection

    writer, reader = store
    records = [flight(str(i), "AAA", "EEE", i, 100 + i) for i in range(5)]
    writer.upsert(records)
    original = projection._ranked_window

    def publish_then_project(*args, **kwargs):
        writer.upsert([flight("new-best", "AAA", "EEE", 10, 20)])
        return original(*args, **kwargs)

    monkeypatch.setattr(projection, "_ranked_window", publish_then_project)
    request = query(ranking="earliest_arrival", limit=2)
    result = reader.search_itineraries(request)
    assert [item.legs[0].provider_id for item in result.items] == ["0", "1"]
    monkeypatch.setattr(projection, "_ranked_window", original)
    assert reader.search_itineraries(request).items[0].legs[0].provider_id == "new-best"


def test_read_only_search_works_before_the_additive_index_upgrade(store):
    writer, reader = store
    writer.upsert([flight("direct", "AAA", "EEE", 0, 60)])
    with writer.connect() as connection:
        connection.execute("DROP INDEX flight_legs_airport_pair")
    assert reader.search_itineraries(query()).items
    with reader.connect() as connection:
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='flight_legs_airport_pair'"
        ).fetchone()
    writer.initialize()
    with reader.connect() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT DISTINCT origin_iata,destination_iata FROM flight_legs"
        ).fetchall()
        assert any("COVERING INDEX flight_legs_airport_pair" in row[3] for row in plan)


@pytest.mark.parametrize("later_key", ["a-later", "zz-later"])
def test_tail_arrival_dominance_preserves_identity_ties(store, later_key):
    writer, reader = store
    records = [
        flight("prefix", "AAA", "BBB", 0, 10),
        flight("choice", "BBB", "CCC", 30, 40),
        flight("z-early", "CCC", "DDD", 60, 70),
        flight(later_key, "CCC", "DDD", 65, 75),
        flight("last", "DDD", "EEE", 100, 110),
    ]
    writer.upsert(records)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        horizon_end=BASE.replace(year=2027),
        window={"minimum_minutes": 10},
    )
    result = reader.rebuild(request)
    expected = min(
        oracle_paths(records, request, prefix=[records[0]], active_window=request.window),
        key=lambda p: (p[-1].arrival_upper, len(p), identity(p[2:])),
    )
    assert identity(result.legs) == identity(expected)
    assert result.complete and result.diagnostics.exhaustive


def test_tail_dominance_keeps_different_visited_airports_after_a_required_via(store):
    writer, reader = store
    records = [
        flight("prefix", "AAA", "BBB", 0, 10),
        flight("choice", "BBB", "XXX", 30, 40),
        flight("early", "XXX", "CCC", 60, 70),
        flight("early-via", "CCC", "FFF", 90, 100),
        flight("later", "XXX", "DDD", 65, 80),
        flight("later-via", "DDD", "FFF", 100, 110),
        flight("return", "FFF", "CCC", 130, 140),
        flight("last", "CCC", "EEE", 160, 170),
    ]
    writer.upsert(records)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        via_airport="FFF",
        horizon_end=BASE.replace(year=2027),
        window={"minimum_minutes": 10},
    )
    result = reader.rebuild(request)
    assert [f.provider_id for f in result.legs] == [
        "prefix",
        "choice",
        "later",
        "later-via",
        "return",
        "last",
    ]
    assert result.complete and result.diagnostics.exhaustive


def test_empty_tail_trials_expand_to_include_a_month_later_departure(store):
    writer, reader = store
    records = [
        flight("prefix", "AAA", "BBB", 0, 10),
        flight("choice", "BBB", "CCC", 30, 40),
        flight("late", "CCC", "EEE", 43200, 43300),
    ]
    writer.upsert(records)
    result = reader.rebuild(
        RebuildRequest(
            prefix=[{"provider": "test", "provider_id": "prefix"}],
            destination="EEE",
            horizon_end=BASE.replace(year=2027),
            window={"minimum_minutes": 10, "maximum_minutes": 60},
        )
    )
    assert [f.provider_id for f in result.legs] == ["prefix", "choice", "late"]
    assert result.complete and result.diagnostics.exhaustive


def test_continuation_counts_all_options_without_solving_tails_outside_the_page(store):
    writer, reader = store
    records = [flight("prefix", "AAA", "BBB", 0, 10)]
    records += [
        flight(f"choice-{i:03d}", "BBB", "CCC", 30 + i / 1000, 40 + i / 1000) for i in range(200)
    ]
    records += [flight("last", "CCC", "EEE", 100, 110)]
    writer.upsert(records)
    result = reader.rebuild(
        RebuildRequest(
            prefix=[{"provider": "test", "provider_id": "prefix"}],
            destination="EEE",
            horizon_end=BASE.replace(year=2027),
            option_limit=1,
            window={"minimum_minutes": 10},
        )
    )
    assert result.options_total == 200 and result.next_option_offset == 1
    assert result.complete and result.diagnostics.exhaustive
    assert [f.provider_id for f in result.legs] == ["prefix", "choice-000", "last"]
    assert len(result.options) == 1 and result.options[0].complete
    assert result.diagnostics.stats.candidates_checked < 205
