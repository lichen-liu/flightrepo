"""Exact archive dimension queries that seek past repeated flight facts."""

# A row-value > comparison only constrains the first index column on some
# SQLite versions. Separate origin/destination seeks constrain both columns.
# No row cap: enumerate every distinct pair, including cancelled/blocked facts.
AIRPORT_PAIRS_CTE = """WITH RECURSIVE origins(origin) AS (
    SELECT MIN(origin_iata) FROM flight_legs
    UNION ALL
    SELECT (SELECT MIN(origin_iata) FROM flight_legs WHERE origin_iata>o.origin)
    FROM origins o WHERE o.origin IS NOT NULL
), pairs(origin,destination) AS (
    SELECT origin,(SELECT MIN(destination_iata) FROM flight_legs WHERE origin_iata=o.origin)
    FROM origins o WHERE origin IS NOT NULL
    UNION ALL
    SELECT origin,(SELECT MIN(destination_iata) FROM flight_legs
        WHERE origin_iata=p.origin AND destination_iata>p.destination)
    FROM pairs p WHERE destination IS NOT NULL
)"""


def has_airport_pair_index(connection):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='flight_legs_airport_pair' AND type='index'"
        ).fetchone()
        is not None
    )


def airport_pairs(connection):
    if has_airport_pair_index(connection):
        return connection.execute(
            AIRPORT_PAIRS_CTE
            + " SELECT origin,destination FROM pairs WHERE destination IS NOT NULL"
        )
    return connection.execute("SELECT DISTINCT origin_iata,destination_iata FROM flight_legs")
