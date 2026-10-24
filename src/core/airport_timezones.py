"""Field-specific timezone ownership; safe to import without producer dependencies."""

import json

STATE_FIELDS = (
    "iata",
    "owner",
    "applied_zone",
    "latitude",
    "longitude",
    "snapshot_id",
    "status",
    "candidates",
)


def has_state(connection):
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='airport_timezone_state'"
        ).fetchone()
    )


def state_rows(connection):
    return (
        {
            row["iata"]: dict(row)
            for row in connection.execute("SELECT * FROM airport_timezone_state")
        }
        if has_state(connection)
        else {}
    )


def save_state(connection, values):
    existing = connection.execute(
        "SELECT * FROM airport_timezone_state WHERE iata=?", (values["iata"],)
    ).fetchone()
    if existing and all(existing[key] == values[key] for key in STATE_FIELDS):
        return
    connection.execute(
        f"INSERT INTO airport_timezone_state({','.join(STATE_FIELDS)}) "
        f"VALUES({','.join(':' + key for key in STATE_FIELDS)}) "
        "ON CONFLICT(iata) DO UPDATE SET "
        + ",".join(f"{key}=excluded.{key}" for key in STATE_FIELDS if key != "iata"),
        values,
    )


def protect(connection, iata, owner="manual"):
    row = connection.execute(
        "SELECT iata,timezone,latitude,longitude FROM airports WHERE iata=?", (iata,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown airport: {iata}")
    save_state(
        connection,
        dict(
            iata=iata,
            owner=owner,
            applied_zone=row["timezone"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            snapshot_id=None,
            status="protected",
            candidates="[]",
        ),
    )


def coverage(connection):
    counts = dict(
        connection.execute(
            "SELECT COUNT(*) AS airports, COUNT(timezone) AS resolved, "
            "COUNT(*)-COUNT(timezone) AS unresolved FROM airports"
        ).fetchone()
    )
    counts["statuses"] = (
        dict(
            connection.execute("SELECT status,COUNT(*) FROM airport_timezone_state GROUP BY status")
        )
        if has_state(connection)
        else {}
    )
    return counts


def normalized(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
