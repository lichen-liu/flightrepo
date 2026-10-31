"""Exact calendar eligibility using indexed existence checks instead of flight totals."""

from datetime import datetime, time, timedelta

from core.admission import ADMITTED_SQL
from core.routing.models import RoutingError
from core.storage import EPOCH, SCHEMA, utc_us
from core.timezones import resolve_local
from core.tzdb import zone as ZoneInfo

_VIEW_MARKER = "CREATE VIEW IF NOT EXISTS searchable_legs AS"
_CURRENT_VIEW = _VIEW_MARKER + SCHEMA.split(_VIEW_MARKER, 1)[1].split(";", 1)[0]


def _view_sql(sql):
    return sql.replace("IF NOT EXISTS ", "").strip().rstrip(";")


def day_bounds(day, zone, last=None, onward_days=None):
    try:
        start = resolve_local(datetime.combine(day, time.min), zone)
        end = resolve_local(datetime.combine((last or day) + timedelta(days=1), time.min), zone)
        return start, end + timedelta(days=onward_days or 0)
    except (OverflowError, ValueError) as error:
        raise RoutingError(
            "invalid_local_dates", "Departure date is outside the supported range."
        ) from error


class SearchCalendar:
    def __init__(self, connection, origin, destination, basis, zone, onward_days):
        self.connection = connection
        self.origin = origin
        self.destination = destination
        self.zone = zone
        self.onward_days = onward_days
        if basis == "observed":
            self.departure = "departure_observed_lower_us"
            self.departure_upper = "departure_observed_upper_us"
            self.arrival = "arrival_observed_lower_us"
            self.arrival_upper = "arrival_observed_upper_us"
            complete = f"{self.departure} IS NOT NULL"
        else:
            # The reader validates the basis before constructing a calendar.
            if basis not in ("scheduled", "actual"):
                raise ValueError("Unknown flight time basis")
            self.departure = self.departure_upper = f"departure_{basis}_us"
            self.arrival = self.arrival_upper = f"arrival_{basis}_us"
            complete = f"{self.departure} IS NOT NULL AND {self.arrival} IS NOT NULL"
        view = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='searchable_legs'"
        ).fetchone()
        if view and _view_sql(view[0]) == _view_sql(_CURRENT_VIEW):
            admission = (
                f"{ADMITTED_SQL} AND NOT EXISTS (SELECT 1 FROM flight_tombstones t "
                "WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key)"
            )
        else:
            # Preserve older installed admission policies without changing the
            # database. The canonical view can use direct indexed facts; an
            # unfamiliar view remains authoritative through this slower check.
            admission = (
                "EXISTS (SELECT 1 FROM searchable_legs s "
                f"WHERE s.leg_id=f.id AND s.time_basis='{basis}')"
            )
        self.facts = (
            f"FROM flight_legs f WHERE f.status <> 'cancelled' AND {complete} AND {admission} "
        )
        row = connection.execute(
            f"SELECT {self.arrival} {self.facts} AND destination_iata=? "
            f"ORDER BY {self.arrival} DESC LIMIT 1",
            (destination,),
        ).fetchone()
        self.latest_arrival_lower = row[0] if row else None

    def departure_days(self):
        """Seek to the next occupied civil day; never walk every flight in a day."""
        timezone = ZoneInfo(self.zone)
        days = []
        cursor = None
        while True:
            clause = f"AND {self.departure}>=? " if cursor is not None else ""
            params = (self.origin, cursor) if cursor is not None else (self.origin,)
            row = self.connection.execute(
                f"SELECT {self.departure} {self.facts} AND origin_iata=? "
                f"{clause}ORDER BY {self.departure} LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                return days
            day = (EPOCH + timedelta(microseconds=row[0])).astimezone(timezone).date()
            start, end = day_bounds(day, self.zone)
            cursor = utc_us(end)
            # An observed interval must fit wholly inside the civil day. A
            # different flight with the same lower bound can still qualify.
            if self.connection.execute(
                f"SELECT 1 {self.facts} AND origin_iata=? "
                f"AND {self.departure}>=? AND {self.departure}<? "
                f"AND {self.departure_upper}<? LIMIT 1",
                (self.origin, utc_us(start), cursor, cursor),
            ).fetchone():
                days.append(day)

    def has_arrivals(self, day, last=None):
        start, end = day_bounds(day, self.zone, last, self.onward_days)
        if self.latest_arrival_lower is None or self.latest_arrival_lower < utc_us(start):
            return False
        if self.onward_days is None:
            # The unbounded window ends at the maximum admitted arrival upper
            # bound, so every later lower bound has a fully included interval.
            return True
        return (
            self.connection.execute(
                f"SELECT 1 {self.facts} AND destination_iata=? "
                f"AND {self.arrival}>=? AND {self.arrival}<=? "
                f"AND {self.arrival_upper}<=? LIMIT 1",
                (self.destination, utc_us(start), utc_us(end), utc_us(end)),
            ).fetchone()
            is not None
        )
