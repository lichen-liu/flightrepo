"""Interactive local record management and bounded read-only SQL inspection."""

import sqlite3

from core.models import FlightRecord
from core.storage import SQLiteConnection
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


def ask(label, default=None):
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input(f"{label}{suffix}: ").strip()
        if value.lower() == "cancel":
            raise EOFError
        if value or default is not None:
            return value or default
        print("A value is required.")


def yes_no(label, default="n"):
    while True:
        value = ask(label + " (y/n)", default).lower()
        if value in ("y", "yes", "n", "no"):
            return value in ("y", "yes")
        print("Enter y or n.")


def edit_flight(path, provider, provider_id, output):
    original = FlightReader(path).flight(provider, provider_id)
    print(f"Database: {path.resolve()}")
    output(original)
    print("Enter keeps a field; '-' clears an optional field. Type cancel to discard.")
    try:
        while True:
            data = original.model_dump(mode="json")
            for field in (
                "flight_number",
                "airline_iata",
                "origin_iata",
                "destination_iata",
                "departure_scheduled",
                "arrival_scheduled",
                "departure_actual",
                "arrival_actual",
                "status",
                "codeshares",
            ):
                current = data[field]
                default = ",".join(current) if field == "codeshares" else (current or "-")
                value = ask(field, default)
                if field == "codeshares":
                    data[field] = (
                        [] if value == "-" else [code.strip() for code in value.split(",")]
                    )
                elif field in ("airline_iata", "departure_actual", "arrival_actual"):
                    data[field] = None if value == "-" else value
                else:
                    data[field] = value
            try:
                replacement = FlightRecord.model_validate(data)
            except ValueError as error:
                print(f"Invalid flight: {error}\nRe-enter the fields.")
                continue
            break
        output(replacement)
        if not yes_no("Save these changes?"):
            print("Changes discarded.")
            return
    except (EOFError, KeyboardInterrupt):
        print("\nEdit cancelled; no changes saved.")
        return
    output({"updated": FlightUpdater(path).update(original, replacement)})


def delete_flight(path, provider, provider_id, confirmed, output):
    original = FlightReader(path).flight(provider, provider_id)
    if not confirmed:
        print(f"Database: {path.resolve()}")
        output(original)
        try:
            if not yes_no("Delete this flight?"):
                print("Deletion cancelled.")
                return
        except (EOFError, KeyboardInterrupt):
            print("\nDeletion cancelled.")
            return
    output({"deleted": FlightUpdater(path).delete(provider, provider_id, expected=original)})


def query_database(path, sql, limit):
    """Only allow SELECT operations, including CTEs, on any SQLite schema."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    def authorize(action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() == "load_extension":
            return sqlite3.SQLITE_DENY
        allowed = (
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE,
        )
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    with SQLiteConnection(path, read_only=True).connect() as connection:
        connection.set_authorizer(authorize)
        cursor = connection.execute(sql)
        columns = [column[0] for column in cursor.description or ()]
        rows = cursor.fetchmany(limit + 1)
        connection.set_authorizer(None)
    return {
        "columns": columns,
        "rows": [list(row) for row in rows[:limit]],
        "truncated": len(rows) > limit,
    }


def route_request():
    from core.routing.models import RouteSearchRequest

    print("Search stored flights. Timestamps require Z or an offset; cancel aborts.")
    while True:
        try:
            data = {
                "origin": ask("Origin"),
                "destination": ask("Destination"),
                "departure_start": ask("Departure window start"),
                "departure_end": ask("Departure window end (exclusive)"),
                "horizon_end": ask("Arrival deadline"),
                "via_airport": ask("Via airport (optional)", "-"),
                "avoid_airports": ask("Avoid airports (space separated)", "-"),
                "max_legs": ask("Maximum legs", "3"),
                "minimum_connection_minutes": ask("Minimum connection minutes", "45"),
                "maximum_connection_minutes": ask("Maximum connection minutes", "360"),
                "ranking": ask(
                    "Ranking: fewest_legs, shortest_elapsed, earliest_arrival", "fewest_legs"
                ),
                "limit": ask("Maximum results", "20"),
                "allow_airport_change": yes_no("Allow same-city airport changes?"),
                "trace": yes_no("Include routing trace?"),
            }
            data["via_airport"] = None if data["via_airport"] == "-" else data["via_airport"]
            data["avoid_airports"] = (
                [] if data["avoid_airports"] == "-" else data["avoid_airports"].split()
            )
            return RouteSearchRequest.model_validate(data)
        except (EOFError, KeyboardInterrupt):
            print("\nSearch cancelled.")
            return None
        except ValueError as error:
            print(f"Invalid request: {error}\nRe-enter the search.")
