import json
from datetime import date

import httpx
import pytest

from core.connections import Airport
from core.models import FlightRecord
from core.storage import SCHEMA, SQLiteConnection, source_id
from ingestion import cli, references
from ingestion.publish import publish_day
from ingestion.serving_controls import prepare_serving, verify_serving_controls
from ingestion.store import IngestionStore
from ingestion.updater import FlightUpdater
from server.reader import FlightReader

AIRPORTS = b"iata_code,type,name,icao_code,municipality,iso_country,iso_region,latitude_deg,longitude_deg\nYYZ,large_airport,Toronto Pearson,CYYZ,Toronto,CA,CA-ON,43.67,-79.63\nSEA,large_airport,Seattle Tacoma,KSEA,Seattle,US,US-WA,47.45,-122.31\n"
CARRIERS = (
    b"Code,Name,ICAO,IATA\nACA,Air Canada,ACA,AC\nROU,Air Canada Rouge,ROU,AC\n1B,Abacus,,1B\n"
)


def fact(key="one", callsign="ACA541"):
    return FlightRecord(
        provider="adsblol",
        provider_id=key,
        callsign=callsign,
        origin_iata="YYZ",
        destination_iata="SEA",
        departure_actual="2023-07-10T10:00:00Z",
        arrival_actual="2023-07-10T15:00:00Z",
        fetched_at="2023-07-11T00:00:00Z",
    )


@pytest.fixture
def store(tmp_path):
    store = IngestionStore(tmp_path / "private.db")
    store.initialize()
    return store


def load(store):
    references.import_catalog(store.path, "airports", AIRPORTS)
    references.import_catalog(store.path, "carriers", CARRIERS)


def test_shared_catalog_backfill_publish_no_flight_mutation(store, tmp_path):
    store.upsert([fact()])
    with store.connect() as c:
        before = tuple(c.execute("SELECT * FROM flight_legs").fetchone())
    load(store)
    assert references.import_catalog(store.path, "airports", AIRPORTS)["unchanged"]
    assert store.upsert([fact()]) == 1
    with store.connect() as c:
        assert tuple(c.execute("SELECT * FROM flight_legs").fetchone()) == before
    target = tmp_path / "serving.db"
    assert references.publish_catalog(store.path, target)["flight_facts_changed"] is False
    assert publish_day(store.path, target, date(2023, 7, 10), audit_routes=False)["inserted"] == 1
    reader = FlightReader(target)
    airport = reader.airports(query="CYYZ")[0]
    assert airport.name == "Toronto Pearson" and airport.municipality == "Toronto"
    assert airport.country_code == "CA" and airport.timezone is None and airport.city_id is None
    flight = reader.flight("adsblol", "one")
    assert flight.operator_name == "Air Canada" and flight.operator_icao == "ACA"
    assert flight.airline_iata is None and flight.flight_number is None
    assert reader.flight("adsblol", "one").model_dump()["operator_name"] == "Air Canada"
    with SQLiteConnection(target).connect() as c:
        assert c.execute("SELECT COUNT(*) FROM reference_snapshots").fetchone()[0] == 2
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_callsign_matching_is_strict_and_never_guesses_iata(store):
    load(store)
    callsigns = [
        " aca541 ",
        "ACA",
        "ACAABC",
        "ACA123456",
        "ACA5-1",
        "N5203D",
        None,
        "ZZZ123",
        "AC541",
    ]
    store.upsert([fact(str(i), value) for i, value in enumerate(callsigns)])
    for i in range(len(callsigns)):
        flight = FlightReader(store.path).flight("adsblol", str(i))
        assert flight.operator_name == ("Air Canada" if i == 0 else None)
        assert flight.airline_iata is None


def test_airport_field_backfill_preserves_coordinates_admin_and_transfers(store, tmp_path):
    load(store)
    target = tmp_path / "serving.db"
    updater = FlightUpdater(target)
    updater.initialize()
    with updater.connect() as c:
        c.execute(
            "INSERT INTO airports(iata,name,city_id,timezone,latitude,longitude) VALUES('YYZ','My name','TOR','America/Toronto',44,-80)"
        )
        c.execute("INSERT INTO airports(iata,latitude,longitude) VALUES('SEA',48,-123)")
        source_id(c, "other-source")
    references.publish_catalog(store.path, target)
    yyz = FlightReader(target).airports(query="YYZ")[0]
    assert (yyz.name, yyz.latitude, yyz.timezone, yyz.city_id) == (
        "My name",
        44,
        "America/Toronto",
        "TOR",
    )
    assert yyz.icao == "CYYZ"
    sea = FlightReader(target).airports(query="SEA")[0]
    assert sea.name == "Seattle Tacoma" and sea.latitude == 48
    with updater.connect() as c:
        assert (
            c.execute(
                "SELECT s.code FROM reference_snapshots r JOIN data_sources s ON s.id=r.source_id WHERE r.kind='carriers'"
            ).fetchone()[0]
            == "vrs-airlines"
        )
    replacement = tmp_path / "replacement.db"
    prepare_serving(target, replacement)
    verify_serving_controls(target, replacement)
    assert FlightReader(replacement).airports(query="YYZ")[0] == yyz
    with SQLiteConnection(replacement, read_only=True).connect() as c:
        assert c.execute("SELECT name FROM carriers WHERE icao='ACA'").fetchone()[0] == "Air Canada"


def test_snapshot_validation_failure_atomic_withdrawal_and_reset(store, tmp_path):
    load(store)
    bad = CARRIERS + b"ACA2,Duplicate,ACA,XX\n"
    with pytest.raises(ValueError, match="Ambiguous"):
        references.import_catalog(store.path, "carriers", bad)
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM carriers").fetchone()[0] == 3
    target = tmp_path / "serving.db"
    references.publish_catalog(store.path, target)
    references.import_catalog(
        store.path, "carriers", b"Code,Name,ICAO,IATA\nACA,Air Canada renamed,ACA,AC\n"
    )
    references.publish_catalog(store.path, target)
    with SQLiteConnection(target, read_only=True).connect() as c:
        assert [tuple(row) for row in c.execute("SELECT name FROM carriers")] == [
            ("Air Canada renamed",)
        ]
    store.clear(reset=True)
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM carriers").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM reference_snapshots").fetchone()[0] == 0


def test_duplicate_airport_codes_are_observable_not_arbitrarily_selected():
    rows, ignored = references.parse_airports(AIRPORTS + AIRPORTS.splitlines()[1] + b"\n")
    assert [row["iata"] for row in rows] == ["SEA"]
    assert ignored["ambiguous_iata_rows"] == 2


def test_existing_database_writer_upgrade_and_compatibility_triggers(tmp_path):
    path = tmp_path / "old.db"
    # Construct the old airport/flight projection without the optional reference extension.
    old = SCHEMA
    for line in (
        "    icao TEXT,\n",
        "    municipality TEXT,\n",
        "    country_code TEXT,\n",
        "    region_code TEXT,\n",
    ):
        old = old.replace(line, "")
    from core.storage import _compatibility_schema

    old = old[: old.index("CREATE VIEW IF NOT EXISTS flights AS")] + _compatibility_schema(
        operator_reference=False
    )
    with SQLiteConnection(path).connect() as c:
        c.executescript(old)
    with SQLiteConnection(path).connect() as c:
        c.execute("DROP TABLE carriers")
        c.execute("DROP TABLE reference_snapshots")
    original = path.read_bytes()
    reader = FlightReader(path)
    assert reader.airports(query="Toronto") == []
    assert reader.overview()["reference_carriers"] == 0
    assert reader.web_catalog().airports == []
    assert path.read_bytes() == original
    writer = FlightUpdater(path)
    writer.initialize()
    writer.initialize()
    writer.upsert([fact()])
    with writer.connect() as c:
        c.execute("UPDATE flights SET callsign='ACA542' WHERE provider_id='one'")
        assert c.execute("SELECT callsign FROM flights").fetchone()[0] == "ACA542"
        assert (
            c.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE type='trigger' AND name LIKE 'flights_%'"
            ).fetchone()[0]
            == 3
        )


def test_cli_fresh_node_downloads_in_memory_and_publishes(store, tmp_path, monkeypatch, capsys):
    requests = []

    def download(url):
        requests.append(url)
        return AIRPORTS if url == references.AIRPORTS_URL else CARRIERS

    monkeypatch.setattr(references, "download", download)
    cli.run(["--db", str(store.path), "references", "refresh"])
    assert len(requests) == 2
    assert len(json.loads(capsys.readouterr().out)) == 2
    target = tmp_path / "serving.db"
    cli.run(["--db", str(store.path), "references", "publish", "--serving-db", str(target)])
    assert json.loads(capsys.readouterr().out)["published"]
    references.ensure_carriers(store.path)
    assert len(requests) == 2
    with pytest.raises(ValueError):
        references.publish_catalog(target, store.path)


def test_airport_admin_roundtrip_new_fields(tmp_path):
    writer = FlightUpdater(tmp_path / "serving.db")
    writer.initialize()
    airport = Airport(
        iata="YYZ",
        name="Toronto Pearson",
        icao="CYYZ",
        municipality="Toronto",
        country_code="CA",
        region_code="CA-ON",
    )
    writer.update_airports([airport], [])
    assert FlightReader(writer.path).airports(query="Toronto")[0] == airport
    writer.update_airports([Airport(iata="YYZ", name="Admin new name")], [])
    assert FlightReader(writer.path).airports(query="YYZ")[0].icao == "CYYZ"
    writer.update_airports([Airport(iata="YYZ", name="Admin new name", icao=None)], [])
    assert FlightReader(writer.path).airports(query="YYZ")[0].icao is None
    writer.reset()
    with writer.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM reference_snapshots").fetchone()[0] == 0


def test_raw_reference_download_is_memory_bounded(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"12345"

    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: Response())
    monkeypatch.setattr(references, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="64 MiB"):
        references.download(references.AIRPORTS_URL)


def test_edited_callsign_invalidates_operator_reference():
    flight = FlightRecord.model_validate(
        fact().model_dump() | {"operator_icao": "ACA", "operator_name": "Air Canada"}
    )
    assert flight.operator_name == "Air Canada"
    edited = FlightRecord.model_validate(flight.model_dump() | {"callsign": "ROU541"})
    assert edited.operator_name is None and edited.operator_icao is None


def test_aggregate_queries_never_resolve_operator_names(store):
    load(store)
    store.upsert([fact()])
    with store.connect() as c:
        calls = []

        def upper(value):
            calls.append(value)
            return value.upper() if value else value

        c.create_function("upper", 1, upper)
        assert c.execute("SELECT COUNT(*) FROM flights").fetchone()[0] == 1
        assert c.execute("SELECT DISTINCT provider FROM flights").fetchone()[0] == "adsblol"
        assert c.execute(
            "SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights"
        ).fetchall()
        assert calls == []
        assert c.execute("SELECT operator_name FROM flights").fetchone()[0] == "Air Canada"
        assert calls


def test_unchanged_reference_publication_does_not_rewrite_dimensions(store, tmp_path):
    load(store)
    target = FlightUpdater(tmp_path / "serving.db")
    target.initialize()
    with store.connect() as source, target.connect() as output:
        references.copy_catalog(source, output)
        before = output.total_changes
        references.copy_catalog(source, output)
        assert output.total_changes == before
        # An unchanged receipt must not hide a missing or damaged dimension.
        output.execute("DELETE FROM carriers WHERE icao='ACA'")
        references.copy_catalog(source, output)
        assert (
            output.execute("SELECT name FROM carriers WHERE icao='ACA'").fetchone()[0]
            == "Air Canada"
        )
        assert not output.execute("PRAGMA foreign_key_check").fetchall()
