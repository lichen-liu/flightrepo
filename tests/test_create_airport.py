import pytest

from admin import client
from core.config import Settings
from core.connections import Airport, AirportTransfer
from ingestion.updater import FlightUpdater
from server.reader import FlightReader

VALUES = [" yyz ", " Toronto Pearson ", "toronto", "America/Toronto", "43.6777", "-79.6248"]
FIELDS = ["iata", "name", "city_id", "timezone", "latitude", "longitude"]


def answers(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(values))


def airport(**changes):
    return Airport(
        **{
            "iata": "YYZ",
            "name": "Toronto Pearson",
            "city_id": "toronto",
            "timezone": "America/Toronto",
            "latitude": 43.6777,
            "longitude": -79.6248,
        }
        | changes
    )


@pytest.mark.parametrize("entry", ["direct", "shell"])
def test_create_airport_uses_selected_database_and_reaches_catalog(
    tmp_path, monkeypatch, capsys, entry
):
    path, fallback = tmp_path / "selected.db", tmp_path / "fallback.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=fallback))
    values = iter(VALUES)

    def respond(prompt):
        if prompt.startswith("Save "):
            assert "Airport preview:" in capsys.readouterr().out
            assert not path.exists(), "preview precedes initialization and insertion"
            return "y"
        return next(values)

    monkeypatch.setattr("builtins.input", respond)
    if entry == "direct":
        client.run(["--db", str(path), "create-airport"])
    else:
        client.LocalShell(path).onecmd("create-airport")
    reader = FlightReader(path)
    assert reader.airports(query="YYZ") == [airport()]
    assert [a.model_dump() for a in reader.web_catalog().airports] == [airport().model_dump()]
    assert reader.stats()["flights"] == 0
    assert not fallback.exists()
    assert '"created": "YYZ"' in capsys.readouterr().out


def test_create_airport_oneoff_db_override_keeps_shell_selection(tmp_path, monkeypatch):
    selected, target = tmp_path / "selected.db", tmp_path / "other db.sqlite"
    monkeypatch.setattr(client, "get_settings", lambda: pytest.fail("explicit DB read settings"))
    answers(monkeypatch, VALUES + ["y"])
    shell = client.LocalShell(selected)
    shell.onecmd(f'--db "{target}" create-airport')
    assert shell.database_path == selected
    assert not selected.exists()
    assert FlightReader(target).airports(query="YYZ") == [airport()]


@pytest.mark.parametrize("ending", ["n", ""])
def test_create_airport_decline_does_not_initialize(tmp_path, monkeypatch, ending):
    path = tmp_path / "missing parent" / "new.db"
    answers(monkeypatch, VALUES + [ending])
    client.run(["--db", str(path), "create-airport"])
    assert not path.parent.exists()


@pytest.mark.parametrize("stop", ["cancel", EOFError, KeyboardInterrupt])
@pytest.mark.parametrize("at", [0, 6])
def test_create_airport_cancellation_before_fields_or_at_save(tmp_path, monkeypatch, stop, at):
    path = tmp_path / "missing parent" / "new.db"
    calls = 0

    def respond(prompt):
        nonlocal calls
        if calls == at:
            if isinstance(stop, str):
                return stop
            raise stop
        value = VALUES[calls]
        calls += 1
        return value

    monkeypatch.setattr("builtins.input", respond)
    client.run(["--db", str(path), "create-airport"])
    assert not path.parent.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("iata", "YY"),
        ("timezone", "Mars/Olympus"),
        ("timezone", "/etc/localtime"),
        ("latitude", "91"),
        ("latitude", "north"),
        ("latitude", "nan"),
        ("latitude", "inf"),
        ("longitude", "-181"),
        ("longitude", "nan"),
    ],
)
def test_create_airport_invalid_input_can_be_corrected(tmp_path, monkeypatch, capsys, field, value):
    path = tmp_path / "airport.db"
    index = FIELDS.index(field)
    invalid, corrections = VALUES.copy(), [""] * len(FIELDS)
    invalid[index], corrections[index] = value, VALUES[index]
    answers(monkeypatch, invalid + corrections + ["y"])
    client.run(["--db", str(path), "create-airport"])
    assert "Invalid airport:" in capsys.readouterr().out
    assert FlightReader(path).airports(query="YYZ") == [airport()]


def test_create_airport_requires_nonblank_name(tmp_path, monkeypatch, capsys):
    path = tmp_path / "airport.db"
    answers(monkeypatch, [VALUES[0], "   ", *VALUES[1:], "y"])
    client.run(["--db", str(path), "create-airport"])
    assert "A value is required." in capsys.readouterr().out
    assert FlightReader(path).airports(query="YYZ") == [airport()]


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.db"
    updater = FlightUpdater(path)
    updater.initialize()
    updater.update_airports(
        [airport(), airport(iata="YTZ", name="Toronto City")],
        [
            AirportTransfer(
                arrival_iata="YYZ", departure_iata="YTZ", minimum_minutes=120, source="test"
            )
        ],
    )
    return path, updater


def test_create_airport_duplicate_keeps_metadata_and_transfers(catalog, monkeypatch):
    path, _ = catalog
    before = FlightReader(path).airport_catalog()
    values = VALUES.copy()
    values[1:3] = ["Replacement", "other-city"]
    answers(monkeypatch, values + ["y"])
    with pytest.raises(ValueError, match="Airport YYZ already exists"):
        client.run(["--db", str(path), "create-airport"])
    assert FlightReader(path).airport_catalog() == before


def test_create_airport_decline_keeps_existing_catalog(catalog, monkeypatch):
    path, _ = catalog
    before = FlightReader(path).airport_catalog()
    answers(monkeypatch, ["YUL", *VALUES[1:], "n"])
    client.run(["--db", str(path), "create-airport"])
    assert FlightReader(path).airport_catalog() == before


def test_create_airport_refuses_an_insert_during_review(tmp_path, monkeypatch):
    path = tmp_path / "race.db"
    values = iter(VALUES)
    competing = airport(name="Saved by another writer")

    def respond(prompt):
        if prompt.startswith("Save "):
            updater = FlightUpdater(path)
            updater.initialize()
            updater.create_airport(competing)
            return "y"
        return next(values)

    monkeypatch.setattr("builtins.input", respond)
    with pytest.raises(ValueError, match="Airport YYZ already exists"):
        client.run(["--db", str(path), "create-airport"])
    assert FlightReader(path).airports(query="YYZ") == [competing]


def test_airport_writer_revalidates_before_opening_database(tmp_path):
    path = tmp_path / "missing.db"
    invalid = airport().model_copy(update={"latitude": float("nan")})
    with pytest.raises(ValueError):
        FlightUpdater(path).create_airport(invalid)
    assert not path.exists()


def test_create_airport_help_does_not_open_settings(monkeypatch, capsys):
    monkeypatch.setattr(client, "get_settings", lambda: pytest.fail("help read settings"))
    with pytest.raises(SystemExit) as error:
        client.run(["create-airport", "--help"])
    assert error.value.code == 0
    assert "create-airport" in capsys.readouterr().out
