"""Apply serving admission without changing source identity or marking human approval."""

import json

from core.quality import review_recommended_sql
from core.storage import SQLiteConnection, refresh_admission_view
from ingestion.adsblol.legs import airport_timing_is_implausible


def apply_admission(path):
    if not path.is_file():
        raise ValueError("Select an existing initialized database")
    flags = []
    with SQLiteConnection(path, read_only=True).connect() as connection:
        connection.execute("BEGIN")
        before = connection.execute(
            "SELECT COUNT(DISTINCT leg_id) FROM searchable_legs"
        ).fetchone()[0]
        rows = connection.execute("""SELECT f.id,f.revision,f.review_reasons,
            f.departure_observed_lower_us,f.arrival_observed_upper_us,
            a.latitude AS origin_lat,a.longitude AS origin_lon,
            b.latitude AS destination_lat,b.longitude AS destination_lon
            FROM flight_legs f JOIN data_sources s ON s.id=f.source_id
            JOIN airports a ON a.iata=f.origin_iata JOIN airports b ON b.iata=f.destination_iata
            WHERE s.code='adsblol' AND f.locally_modified_us IS NULL
            AND f.departure_observed_lower_us IS NOT NULL AND f.arrival_observed_upper_us IS NOT NULL""")
        for row in rows:
            reasons = json.loads(row["review_reasons"])
            if "implausible_airport_timing" in reasons:
                continue
            origin = (row["origin_lat"], row["origin_lon"])
            destination = (row["destination_lat"], row["destination_lon"])
            if None in (*origin, *destination):
                continue
            elapsed = (
                row["arrival_observed_upper_us"] - row["departure_observed_lower_us"]
            ) / 1_000_000
            if airport_timing_is_implausible(origin, destination, elapsed):
                flags.append(
                    (
                        json.dumps([*reasons, "implausible_airport_timing"]),
                        row["id"],
                        row["revision"],
                    )
                )
    # Scan without holding a writer lock. Recheck revisions so a concurrent
    # ingestor/admin edit cannot be overwritten by this quality reassessment.
    with SQLiteConnection(path).connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        updated = 0
        for reasons, leg_id, revision in flags:
            updated += connection.execute(
                "UPDATE flight_legs SET review_reasons=?,revision=revision+1 WHERE id=? AND revision=? AND locally_modified_us IS NULL",
                (reasons, leg_id, revision),
            ).rowcount
        refresh_admission_view(connection)
    with SQLiteConnection(path, read_only=True).connect() as connection:
        after = connection.execute("SELECT COUNT(DISTINCT leg_id) FROM searchable_legs").fetchone()[
            0
        ]
        held = connection.execute(
            f"SELECT COUNT(*) FROM flights WHERE {review_recommended_sql()}"
        ).fetchone()[0]
    return {
        "database": str(path),
        "admission_policy": "labeled-itinerary-uncertainty-v2",
        "searchable_before": before,
        "searchable_after": after,
        "needs_review": held,
        "timing_flags_added": updated,
        "manual_approvals_added": 0,
    }
