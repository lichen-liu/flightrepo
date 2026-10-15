"""Embedded SQLite repository shared by independent local Python processes."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

TIME_FIELDS = (
    "departure_scheduled",
    "arrival_scheduled",
    "departure_actual",
    "arrival_actual",
    "departure_observed_lower",
    "departure_observed_upper",
    "arrival_observed_lower",
    "arrival_observed_upper",
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def utc_us(value: datetime) -> int:
    if value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    delta = value.astimezone(UTC) - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _sql_timestamp(column):
    # Integer arithmetic preserves microseconds, including dates before 1970.
    fraction = f"(({column}%1000000+1000000)%1000000)"
    seconds = f"(({column}-{fraction})/1000000)"
    return f"strftime('%Y-%m-%dT%H:%M:%S',{seconds},'unixepoch')||printf('.%06d+00:00',{fraction})"


def _compatibility_schema():
    fields = [
        "s.code AS provider",
        "f.source_record_key AS provider_id",
        "f.flight_number",
        "f.airline_iata",
        "f.origin_iata",
        "f.destination_iata",
        "f.status",
        "(SELECT json_group_array(flight_number) FROM (SELECT flight_number FROM flight_codeshares c WHERE c.leg_id=f.id ORDER BY ordinal,flight_number)) AS codeshares",
        _sql_timestamp("f.last_seen_us") + " AS fetched_at",
        "f.aircraft_type_code",
        "f.callsign",
        "f.evidence_kind",
        "f.itinerary_reviewed",
        "f.review_reasons",
        "NULLIF(f.departure_actual_kind,'unknown') AS departure_actual_kind",
        "NULLIF(f.arrival_actual_kind,'unknown') AS arrival_actual_kind",
    ]
    for name in TIME_FIELDS:
        fields.extend([_sql_timestamp(f"f.{name}_us") + f" AS {name}", f"f.{name}_us"])
    lower = "CASE WHEN f.departure_scheduled_us IS NOT NULL AND f.arrival_scheduled_us IS NOT NULL THEN f.departure_scheduled_us ELSE COALESCE(f.departure_observed_lower_us,f.departure_actual_us,f.departure_scheduled_us) END"
    upper = "CASE WHEN f.departure_scheduled_us IS NOT NULL AND f.arrival_scheduled_us IS NOT NULL THEN f.arrival_scheduled_us ELSE COALESCE(f.arrival_observed_upper_us,f.arrival_actual_us,f.arrival_scheduled_us) END"
    fields.extend([_sql_timestamp(lower) + " AS departure", _sql_timestamp(upper) + " AS arrival"])
    view = (
        "CREATE VIEW IF NOT EXISTS flights AS SELECT "
        + ",".join(fields)
        + " FROM flight_legs f JOIN data_sources s ON s.id=f.source_id WHERE NOT EXISTS (SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key);"
    )

    # Legacy SQL inspection/mutation keeps addressing a projection of the same
    # typed facts. Runtime writers use store_flight, not these compatibility triggers.
    def parse_time(value):
        tail = f"substr({value},instr({value},'.')+1)"
        fraction = f"CASE WHEN instr({tail},'+')>0 THEN substr({tail},1,instr({tail},'+')-1) WHEN instr({tail},'-')>0 THEN substr({tail},1,instr({tail},'-')-1) ELSE rtrim({tail},'Zz') END"
        return f"CASE WHEN {value} IS NULL THEN NULL ELSE CAST(strftime('%s',{value}) AS INTEGER)*1000000 + CASE WHEN instr({value},'.')=0 THEN 0 ELSE CAST(substr(({fraction})||'000000',1,6) AS INTEGER) END END"

    sid = "(SELECT id FROM data_sources WHERE code=NEW.provider)"
    mapping = {
        "source_id": sid,
        "source_record_key": "NEW.provider_id",
        "origin_iata": "NEW.origin_iata",
        "destination_iata": "NEW.destination_iata",
        "flight_number": "NEW.flight_number",
        "airline_iata": "NEW.airline_iata",
        "callsign": "NEW.callsign",
        "aircraft_type_code": "NEW.aircraft_type_code",
        "status": "COALESCE(NEW.status,'unknown')",
        "evidence_kind": "COALESCE(NEW.evidence_kind,'source_reported')",
        "itinerary_reviewed": "COALESCE(NEW.itinerary_reviewed,0)",
        "review_reasons": "COALESCE(NEW.review_reasons,'[]')",
        "first_seen_us": parse_time("NEW.fetched_at"),
        "last_seen_us": parse_time("NEW.fetched_at"),
        "departure_actual_kind": "CASE WHEN NEW.departure_actual IS NOT NULL THEN COALESCE(NEW.departure_actual_kind,'unknown') END",
        "arrival_actual_kind": "CASE WHEN NEW.arrival_actual IS NOT NULL THEN COALESCE(NEW.arrival_actual_kind,'unknown') END",
    }
    mapping.update({name + "_us": parse_time("NEW." + name) for name in TIME_FIELDS})
    body = f"""
        INSERT OR IGNORE INTO data_sources(code,name) VALUES(NEW.provider,NEW.provider);
        INSERT OR IGNORE INTO airports(iata) VALUES(NEW.origin_iata);
        INSERT OR IGNORE INTO airports(iata) VALUES(NEW.destination_iata);
        INSERT INTO flight_legs({",".join(mapping)}) VALUES({",".join(mapping.values())})
        ON CONFLICT(source_id,source_record_key) DO UPDATE SET
        {",".join(name + "=excluded." + name for name in mapping if name not in ("source_id", "source_record_key", "first_seen_us"))},
        first_seen_us=MIN(flight_legs.first_seen_us,excluded.first_seen_us),revision=revision+1;
        DELETE FROM flight_tombstones WHERE source_id={sid} AND source_record_key=NEW.provider_id;
        DELETE FROM flight_codeshares WHERE leg_id=(SELECT id FROM flight_legs WHERE source_id={sid} AND source_record_key=NEW.provider_id);
        INSERT OR IGNORE INTO flight_codeshares(leg_id,flight_number,ordinal)
        SELECT (SELECT id FROM flight_legs WHERE source_id={sid} AND source_record_key=NEW.provider_id),value,key FROM json_each(COALESCE(NEW.codeshares,'[]'));
    """
    return (
        view
        + f"""
    CREATE TRIGGER IF NOT EXISTS flights_insert INSTEAD OF INSERT ON flights BEGIN {body} END;
    CREATE TRIGGER IF NOT EXISTS flights_update INSTEAD OF UPDATE ON flights BEGIN
        {body}
        UPDATE flight_legs SET locally_modified_us=CAST(strftime('%s','now') AS INTEGER)*1000000
        WHERE source_id={sid} AND source_record_key=NEW.provider_id;
        DELETE FROM flight_legs WHERE source_id=(SELECT id FROM data_sources WHERE code=OLD.provider)
        AND source_record_key=OLD.provider_id AND (OLD.provider<>NEW.provider OR OLD.provider_id<>NEW.provider_id);
    END;
    CREATE TRIGGER IF NOT EXISTS flights_delete INSTEAD OF DELETE ON flights BEGIN
        INSERT OR REPLACE INTO flight_tombstones(source_id,source_record_key,deleted_us)
        VALUES((SELECT id FROM data_sources WHERE code=OLD.provider),OLD.provider_id,CAST(strftime('%s','now') AS INTEGER)*1000000);
        DELETE FROM flight_legs WHERE source_id=(SELECT id FROM data_sources WHERE code=OLD.provider) AND source_record_key=OLD.provider_id;
    END;
    """
    )


SCHEMA = Path(__file__).with_name("schema.sql").read_text() + _compatibility_schema()


def source_id(connection, code):
    connection.execute("INSERT OR IGNORE INTO data_sources(code,name) VALUES (?,?)", (code, code))
    return connection.execute("SELECT id FROM data_sources WHERE code=?", (code,)).fetchone()[0]


def store_flight(connection, record, *, restore=True, local_edit=False, batch_id=None):
    """Write one validated leg inside the caller's transaction; no connection/commit."""
    import json

    from core.models import FlightRecord

    record = FlightRecord.model_validate(record.model_dump())
    provider = source_id(connection, record.provider)
    identity = (provider, record.provider_id)
    deleted = connection.execute(
        "SELECT 1 FROM flight_tombstones WHERE source_id=? AND source_record_key=?", identity
    ).fetchone()
    old = connection.execute(
        "SELECT id,locally_modified_us,first_seen_us FROM flight_legs WHERE source_id=? AND source_record_key=?",
        identity,
    ).fetchone()
    if not restore and (deleted or old is not None and old[1] is not None):
        return "protected"
    connection.executemany(
        "INSERT OR IGNORE INTO airports(iata) VALUES (?)",
        [(record.origin_iata,), (record.destination_iata,)],
    )
    data = {
        "source_id": provider,
        "source_record_key": record.provider_id,
        "origin_iata": record.origin_iata,
        "destination_iata": record.destination_iata,
        "flight_number": record.flight_number,
        "airline_iata": record.airline_iata,
        "callsign": record.callsign,
        "aircraft_type_code": record.aircraft_type_code,
        "status": record.status.value,
        "evidence_kind": record.evidence_kind,
        "itinerary_reviewed": int(record.itinerary_reviewed),
        "review_reasons": json.dumps(record.review_reasons),
        "departure_actual_kind": (record.departure_actual_kind or "unknown")
        if record.departure_actual is not None
        else None,
        "arrival_actual_kind": (record.arrival_actual_kind or "unknown")
        if record.arrival_actual is not None
        else None,
        "first_seen_us": min(old[2], utc_us(record.fetched_at))
        if old
        else utc_us(record.fetched_at),
        "last_seen_us": utc_us(record.fetched_at),
    }
    for name in TIME_FIELDS:
        value = getattr(record, name)
        data[name + "_us"] = utc_us(value) if value is not None else None
    if old is not None:
        row = connection.execute(
            "SELECT * FROM flights WHERE provider=? AND provider_id=?",
            (record.provider, record.provider_id),
        ).fetchone()
        if row is not None:
            current = (
                dict(
                    zip(
                        (
                            d[0]
                            for d in connection.execute("SELECT * FROM flights LIMIT 0").description
                        ),
                        row,
                    )
                )
                if not isinstance(row, sqlite3.Row)
                else dict(row)
            )
            current["codeshares"] = json.loads(current["codeshares"])
            current["review_reasons"] = json.loads(current["review_reasons"])
            if (
                FlightRecord.model_validate(current).model_copy(
                    update={"fetched_at": record.fetched_at}
                )
                == record
                and not local_edit
            ):
                connection.execute(
                    "UPDATE flight_legs SET first_seen_us=MIN(first_seen_us,?),last_seen_us=? WHERE id=?",
                    (utc_us(record.fetched_at), utc_us(record.fetched_at), old[0]),
                )
                return "duplicate"
        assignments = ",".join(f"{name}=?" for name in data)
        connection.execute(
            f"UPDATE flight_legs SET {assignments},revision=revision+1,locally_modified_us=COALESCE(?,locally_modified_us) WHERE id=?",
            (*data.values(), utc_us(datetime.now(UTC)) if local_edit else None, old[0]),
        )
        leg_id = old[0]
    else:
        data["first_batch_id"] = batch_id
        data["locally_modified_us"] = utc_us(datetime.now(UTC)) if local_edit else None
        cursor = connection.execute(
            f"INSERT INTO flight_legs ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
            tuple(data.values()),
        )
        leg_id = cursor.lastrowid
    connection.execute("DELETE FROM flight_codeshares WHERE leg_id=?", (leg_id,))
    connection.executemany(
        "INSERT INTO flight_codeshares VALUES (?,?,?)",
        [(leg_id, code, index) for index, code in enumerate(dict.fromkeys(record.codeshares))],
    )
    if restore:
        connection.execute(
            "DELETE FROM flight_tombstones WHERE source_id=? AND source_record_key=?", identity
        )
    return "updated" if old else "inserted"


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class SQLiteConnection:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only

    @contextmanager
    def connect(self):
        if self.read_only:
            connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
