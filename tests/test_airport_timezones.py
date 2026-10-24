import json
from datetime import date, datetime, timedelta

import pytest

from core.airport_timezones import protect, state_rows
from core.connections import Airport
from core.storage import SQLiteConnection
from core.timezones import resolve_local
from core.tzdb import zone
from ingestion.airport_timezones import apply, digest, enrich, plan, read_inputs, resolve, resolver
from ingestion.references import publish_catalog
from ingestion.serving_controls import prepare_serving, verify_serving_controls
from ingestion.store import IngestionStore
from ingestion.updater import FlightUpdater


class Lookup:
    def __init__(self, candidates=("America/Toronto",), revision=1):
        self.zones = candidates
        self.manifest = {"test": revision}
        self.identity = digest(self.manifest)

    def candidates(self, latitude, longitude):
        return self.zones


@pytest.fixture
def store(tmp_path):
    store = IngestionStore(tmp_path / "private.db")
    store.initialize()
    with store.connect() as c:
        c.execute("INSERT INTO airports(iata,latitude,longitude) VALUES('YYZ',43.67,-79.63)")
    return store


def facts(path):
    with SQLiteConnection(path, read_only=True).connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM airports")], state_rows(c)


def test_real_pinned_data_and_rules():
    lookup = resolver()
    for lat, lon, name in [
        (43.67, -79.63, "America/Toronto"),
        (47.45, -122.31, "America/Los_Angeles"),
        (35.76, 140.38, "Asia/Tokyo"),
        (27.6966, 85.3591, "Asia/Kathmandu"),
        (52.5061, 13.358, "Europe/Berlin"),
    ]:
        assert resolve(lat, lon, lookup) == (name, "resolved", [name])
    assert len(lookup.finder.timezone_names) > 400
    assert resolve(44.04, 87.416, lookup)[1] == "ambiguous"
    for name in ("America/Toronto", "America/Los_Angeles"):
        assert datetime(2023, 1, 1, tzinfo=zone(name)).utcoffset() == timedelta(
            hours=-5 if name.endswith("Toronto") else -8
        )
        assert datetime(2023, 7, 1, tzinfo=zone(name)).utcoffset() == timedelta(
            hours=-4 if name.endswith("Toronto") else -7
        )
        for day, hours in [(date(2023, 3, 12), 23), (date(2023, 11, 5), 25)]:
            start = datetime.combine(day, datetime.min.time())
            assert resolve_local(start + timedelta(days=1), name) - resolve_local(
                start, name
            ) == timedelta(hours=hours)
    assert datetime(2023, 7, 1, tzinfo=zone("Asia/Tokyo")).utcoffset() == timedelta(hours=9)
    assert datetime(2023, 7, 1, tzinfo=zone("Asia/Kathmandu")).utcoffset() == timedelta(
        hours=5, minutes=45
    )
    assert zone("America/Toronto").key == "America/Toronto"


@pytest.mark.parametrize(
    "candidates,status",
    [
        ([], "no_match"),
        (["Etc/GMT+5"], "no_match"),
        (["America/Toronto", "America/New_York"], "ambiguous"),
        (["Invalid/Zone"], "invalid"),
    ],
)
def test_unresolved_never_guesses(candidates, status):
    value, result, _ = resolve(43, -79, Lookup(candidates))
    assert value is None and result == status
    assert resolve(None, None, Lookup())[1] == "missing_coordinates"
    assert resolve(float("nan"), 0, Lookup())[1] == "invalid"


def test_dry_run_backfill_noop_and_new_airport(store):
    before = store.path.read_bytes()
    preview = enrich(store.path, dry_run=True, lookup=Lookup())
    assert store.path.read_bytes() == before
    assert facts(store.path)[0][0]["timezone"] is None
    assert preview["counts"]["populated"] == 1
    first = enrich(store.path, lookup=Lookup())
    rows, states = facts(store.path)
    assert rows[0]["timezone"] == "America/Toronto"
    with store.connect() as c:
        initial_changes = c.total_changes
        inputs, previous = read_inputs(c)
        apply(c, plan(inputs, previous, Lookup()), Lookup())
        assert c.total_changes == initial_changes
    with store.connect() as c:
        c.execute("INSERT INTO airports(iata) VALUES('XYZ')")
    enrich(store.path, lookup=Lookup())
    assert facts(store.path)[1]["YYZ"] == states["YYZ"]
    assert first["after"]["resolved"] == 1
    assert facts(store.path)[1]["XYZ"]["status"] == "missing_coordinates"


def test_refresh_owned_and_protect_external_and_legacy(store):
    enrich(store.path, lookup=Lookup())
    enrich(store.path, lookup=Lookup(["America/New_York"], 2))
    assert facts(store.path)[0][0]["timezone"] == "America/New_York"
    with store.connect() as c:
        c.execute("UPDATE airports SET timezone='Asia/Tokyo' WHERE iata='YYZ'")
    result = enrich(store.path, lookup=Lookup())
    assert result["counts"]["protected"] == 1
    assert facts(store.path)[1]["YYZ"]["owner"] == "legacy"
    enrich(store.path, lookup=Lookup(), release=["YYZ"])
    assert facts(store.path)[1]["YYZ"]["owner"] == "reference"
    enrich(store.path, lookup=Lookup([], 3))
    assert facts(store.path)[0][0]["timezone"] is None


def test_concurrent_input_or_ownership_change_rejected(store):
    with store.connect() as c:
        inputs, previous = read_inputs(c)
    prepared = plan(inputs, previous, Lookup())
    with store.connect() as c:
        protect(c, "YYZ")
    with pytest.raises(ValueError, match="changed"):
        with store.connect() as c:
            apply(c, prepared, Lookup())
    assert facts(store.path)[0][0]["timezone"] is None


def test_publication_updates_owned_preserves_pins_and_replacement(store, tmp_path):
    target = tmp_path / "serving.db"
    enrich(store.path, lookup=Lookup())
    publish_catalog(store.path, target)
    assert facts(target)[1]["YYZ"]["owner"] == "reference"
    enrich(store.path, lookup=Lookup(["America/New_York"], 2))
    publish_catalog(store.path, target)
    assert facts(target)[0][0]["timezone"] == "America/New_York"
    writer = FlightUpdater(target)
    writer.update_airports([Airport(iata="YYZ", timezone="America/New_York")], [])
    writer.update_airports([Airport(iata="YYZ", name="Retained")], [])
    assert facts(target)[0][0]["timezone"] == "America/New_York"
    assert facts(target)[1]["YYZ"]["owner"] == "manual"
    enrich(store.path, lookup=Lookup())
    assert publish_catalog(store.path, target)["timezones"]["protected"] == 1
    replacement = tmp_path / "replacement.db"
    prepare_serving(target, replacement)
    verify_serving_controls(target, replacement)
    assert facts(target) == facts(replacement)
    writer.release_airport_timezone("YYZ")
    publish_catalog(store.path, target)
    assert facts(target)[0][0]["timezone"] == "America/Toronto"
    writer.reset()
    assert facts(target) == ([], {})


def test_serving_coordinate_override_and_private_reset(store, tmp_path):
    target = tmp_path / "serving.db"
    enrich(store.path, lookup=Lookup())
    publish_catalog(store.path, target)
    writer = FlightUpdater(target)
    writer.update_airports([Airport(iata="YYZ", latitude=40, longitude=-75)], [])
    assert facts(target)[0][0]["timezone"] is None
    report = publish_catalog(store.path, target)
    assert report["timezones"]["coordinate_conflicts"] == 1
    assert facts(target)[0][0]["timezone"] is None
    store.clear(reset=True)
    assert facts(store.path) == ([], {})


def test_reference_import_uses_retained_coordinates_and_failure_atomic(store, monkeypatch):
    from ingestion import airport_timezones, references

    class Observe(Lookup):
        def candidates(self, latitude, longitude):
            assert (latitude, longitude) == (43.67, -79.63)
            return self.zones

    monkeypatch.setattr(airport_timezones, "resolver", lambda: Observe())
    data = b"iata_code,type,name,latitude_deg,longitude_deg\nYYZ,large_airport,Toronto,1,2\n"
    references.import_catalog(store.path, "airports", data)
    assert facts(store.path)[0][0]["timezone"] == "America/Toronto"
    before = facts(store.path)
    monkeypatch.setattr(airport_timezones, "resolver", lambda: Lookup(revision=2))
    monkeypatch.setattr(
        airport_timezones, "apply", lambda *a, **k: (_ for _ in ()).throw(ValueError("failure"))
    )
    with pytest.raises(ValueError, match="failure"):
        references.import_catalog(store.path, "airports", data.replace(b"YYZ", b"SEA"))
    assert facts(store.path) == before


def test_cli_dry_run_and_admin_private_write_restriction(store, capsys):
    from admin.client import run as admin
    from ingestion.cli import run

    run(["--db", str(store.path), "references", "timezones", "--dry-run"])
    assert json.loads(capsys.readouterr().out)["dry_run"]
    with pytest.raises(ValueError, match="read/query-only"):
        admin(["airports", "release-timezone", "YYZ"], database_path=store.path)


def test_quota_failure_rolls_back_all_reference_changes(store, monkeypatch):
    from ingestion import airport_timezones

    before = facts(store.path)

    def exceeded(*args):
        raise OSError("quota")

    monkeypatch.setattr(airport_timezones, "check_quota", exceeded)
    with pytest.raises(OSError, match="quota"):
        enrich(store.path, lookup=Lookup())
    assert facts(store.path) == before
    with store.connect() as c:
        assert not c.execute("SELECT 1 FROM airport_timezone_snapshots").fetchone()


def test_integrity_failure_is_not_reported_as_unresolved(tmp_path, monkeypatch):
    from ingestion import airport_timezones

    manifest = json.loads(airport_timezones.MANIFEST.read_text())
    manifest["files"]["timezone_names.txt"] = "0" * 64
    path = tmp_path / "bad-manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(airport_timezones, "MANIFEST", path)
    with pytest.raises(ValueError, match="integrity"):
        airport_timezones.Resolver()


def test_readonly_legacy_database_and_writer_upgrade(store, tmp_path):
    from server.reader import FlightReader

    with store.connect() as c:
        c.execute("DROP TABLE airport_timezone_state")
        c.execute("DROP TABLE airport_timezone_snapshots")
    preview = enrich(store.path, dry_run=True, lookup=Lookup())
    assert preview["counts"]["populated"] == 1
    with SQLiteConnection(store.path, read_only=True).connect() as c:
        assert not c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='airport_timezone_state'"
        ).fetchone()
    store.initialize()
    enrich(store.path, lookup=Lookup())
    target = tmp_path / "serving.db"
    publish_catalog(store.path, target)
    exported = FlightReader(target).timezone_reference()
    assert exported["license"] == "ODbL-1.0"
    assert exported["mappings"][0]["timezone"] == "America/Toronto"
    assert "snapshot_id" not in json.dumps(exported)


def test_publication_does_not_trust_stale_producer_ownership(store, tmp_path):
    enrich(store.path, lookup=Lookup())
    target = tmp_path / "serving.db"
    publish_catalog(store.path, target)
    with store.connect() as c:
        c.execute("UPDATE airports SET timezone='Asia/Tokyo' WHERE iata='YYZ'")
    publish_catalog(store.path, target)
    assert facts(target)[0][0]["timezone"] == "Asia/Tokyo"
    assert facts(target)[1]["YYZ"]["owner"] == "legacy"


def test_publication_rejects_source_coordinate_change_before_enrichment(store, tmp_path):
    enrich(store.path, lookup=Lookup())
    with store.connect() as c:
        c.execute("UPDATE airports SET latitude=40,longitude=-75 WHERE iata='YYZ'")
    target = tmp_path / "serving.db"
    assert publish_catalog(store.path, target)["timezones"]["source_conflicts"] == 1
    assert facts(target)[0][0]["timezone"] is None


def test_partial_airport_creation_preserves_or_invalidates_generated_zone_correctly(
    store, tmp_path
):
    enrich(store.path, lookup=Lookup())
    target = tmp_path / "serving.db"
    publish_catalog(store.path, target)
    writer = FlightUpdater(target)
    writer.create_airport(Airport(iata="YYZ", name="Completed name"))
    assert facts(target)[0][0]["timezone"] == "America/Toronto"
    assert facts(target)[1]["YYZ"]["owner"] == "reference"
    writer.create_airport(Airport(iata="YYZ", latitude=40, longitude=-75))
    assert facts(target)[0][0]["timezone"] is None
    writer.create_airport(Airport(iata="YYZ", timezone="America/New_York"))
    assert facts(target)[1]["YYZ"]["owner"] == "manual"
