"""Conservative research extraction of complete ground-to-ground flight candidates.

This does not produce scheduled FlightRecords. Airport matches and movement-time
bounds are inferred; production historical routing needs an explicit observed-time model.
"""

import csv
import math
from collections import Counter
from datetime import UTC, datetime

from pydantic import BaseModel


class FlightLegCandidate(BaseModel):
    icao_hex: str
    callsign: str
    aircraft_type_code: str
    origin_iata: str
    destination_iata: str
    departure_lower: datetime
    departure_upper: datetime
    arrival_lower: datetime
    arrival_upper: datetime
    evidence: str = "ground_brackets_and_nearby_airports"
    status: str = "inferred_candidate"


def airport_points(path):
    with path.open(newline="", encoding="utf-8") as source:
        return [
            (row["iata_code"], float(row["latitude_deg"]), float(row["longitude_deg"]))
            for row in csv.DictReader(source)
            if len(row["iata_code"]) == 3
            and row["scheduled_service"] == "yes"
            and row["type"] not in {"closed", "heliport", "seaplane_base"}
        ]


def distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    chord = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(min(1, chord)))


def nearby_airport(row, airports, *, radius_km=5):
    matches = [(distance_km(row[1], row[2], lat, lon), code) for code, lat, lon in airports]
    nearby = sorted((distance, code) for distance, code in matches if distance <= radius_km)
    # Reject ambiguous endpoints rather than choosing among nearby airports.
    return nearby[0][1] if len(nearby) == 1 else None


def flight_candidates(data, airports, rules, *, max_bracket_seconds=120):
    flags = data.get("dbFlags")
    if data.get("t") not in rules.fixed_wing_types or isinstance(flags, int) and flags & 1:
        return []
    candidates, previous_ground, airborne, callsigns, current_call = [], None, [], Counter(), ""

    def instant(row):
        return datetime.fromtimestamp(data["timestamp"] + row[0], UTC)

    def finish(ground):
        if not airborne or previous_ground is None:
            return
        first, last = airborne[0], airborne[-1]
        if (
            first[0] - previous_ground[0] > max_bracket_seconds
            or ground[0] - last[0] > max_bracket_seconds
            or last[0] - first[0] < 300
        ):
            return
        origin = nearby_airport(previous_ground, airports)
        destination = nearby_airport(ground, airports)
        if not origin or not destination or origin == destination or not callsigns:
            return
        callsign = callsigns.most_common(1)[0][0]
        if not any(
            callsign.startswith(prefix) and callsign[len(prefix) : len(prefix) + 1].isdigit()
            for prefix in rules.callsign_prefixes
        ):
            return
        candidates.append(
            FlightLegCandidate(
                icao_hex=data["icao"],
                callsign=callsign,
                aircraft_type_code=data["t"],
                origin_iata=origin,
                destination_iata=destination,
                departure_lower=instant(previous_ground),
                departure_upper=instant(first),
                arrival_lower=instant(last),
                arrival_upper=instant(ground),
            )
        )

    previous_time = None
    for row in data["trace"]:
        if previous_time is not None and row[0] < previous_time:
            raise ValueError("Trace timestamps must be ordered")
        if previous_time is not None and row[0] - previous_time > max_bracket_seconds:
            previous_ground, airborne, callsigns = None, [], Counter()
            current_call = ""
        previous_time = row[0]
        if row[6] & 1 or row[3] is None:
            # Missing/stale positions break evidence continuity.
            previous_ground, airborne, callsigns = None, [], Counter()
            current_call = ""
            continue
        extra = row[8] if len(row) > 8 and isinstance(row[8], dict) else {}
        current_call = extra.get("flight", "").strip() or current_call
        if row[3] == "ground":
            finish(row)
            previous_ground, airborne, callsigns = row, [], Counter()
        elif isinstance(row[3], (int, float)):
            airborne.append(row)
            if current_call:
                callsigns[current_call] += 1
    # A trailing airborne run has no observed arrival bracket and is excluded.
    return candidates
