"""Reduce observations to itinerary evidence, retaining no position history."""

import math
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from ingestion.adsblol.legs import FlightLegCandidate, distance_km

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_GAP_US = 120_000_000


class Terminal(BaseModel):
    time_us: int
    airport: str | None = None


class MovementState(BaseModel):
    aircraft_type_code: str
    last_us: int | None = None
    ground: Terminal | None = None
    first_airborne_us: int | None = None
    last_airborne_us: int | None = None
    current_call: str = ""
    callsigns: dict[str, int] = Field(default_factory=dict)

    def interrupt(self):
        self.ground = None
        self.first_airborne_us = self.last_airborne_us = None
        self.current_call = ""
        self.callsigns.clear()


class LeadingMovement(BaseModel):
    """Only timing/callsign/airport evidence before a day's first ground observation."""

    aircraft_type_code: str
    first_us: int | None = None
    last_us: int | None = None
    first_airborne_us: int | None = None
    last_airborne_us: int | None = None
    current_call: str = ""
    callsigns: dict[str, int] = Field(default_factory=dict)
    arrival: Terminal | None = None
    valid: bool = True


def make_candidate(hex_id, state, arrival, rules):
    ground = state.ground
    first, last = state.first_airborne_us, state.last_airborne_us
    call = next(iter(state.callsigns)) if len(state.callsigns) == 1 else None
    if not (
        ground is not None
        and first is not None
        and last is not None
        and first - ground.time_us <= MAX_GAP_US
        and arrival.time_us - last <= MAX_GAP_US
        and last - first >= 300_000_000
        and ground.airport is not None
        and arrival.airport is not None
        and ground.airport != arrival.airport
        and call
        and any(
            re.fullmatch(re.escape(prefix) + r"[0-9][A-Z0-9]*", call)
            for prefix in rules.callsign_prefixes
        )
    ):
        return None
    return FlightLegCandidate(
        icao_hex=hex_id,
        callsign=call,
        aircraft_type_code=state.aircraft_type_code,
        origin_iata=ground.airport,
        destination_iata=arrival.airport,
        departure_lower=EPOCH + timedelta(microseconds=ground.time_us),
        departure_upper=EPOCH + timedelta(microseconds=first),
        arrival_lower=EPOCH + timedelta(microseconds=last),
        arrival_upper=EPOCH + timedelta(microseconds=arrival.time_us),
    )


def advance_boundary(hex_id, previous, leading, tail, rules):
    """Join adjacent UTC days from compact summaries, including a whole airborne day."""
    if (
        previous is None
        or previous.last_us is None
        or leading is None
        or not leading.valid
        or leading.first_us is None
        or leading.aircraft_type_code != previous.aircraft_type_code
        or not 0 < leading.first_us - previous.last_us <= MAX_GAP_US
    ):
        return None, tail
    merged = previous.model_copy(deep=True)
    merged.last_us = leading.last_us
    merged.first_airborne_us = previous.first_airborne_us or leading.first_airborne_us
    merged.last_airborne_us = leading.last_airborne_us or previous.last_airborne_us
    for call, count in leading.callsigns.items():
        merged.callsigns[call] = merged.callsigns.get(call, 0) + count
    if not merged.callsigns and previous.current_call and leading.first_airborne_us is not None:
        merged.callsigns[previous.current_call] = 1
    merged.current_call = leading.current_call or previous.current_call
    if leading.arrival is not None:
        return make_candidate(hex_id, merged, leading.arrival, rules), tail
    return None, merged if tail is not None else None


class AirportLookup:
    """Coarse in-memory buckets keep endpoint matching independent of airport count."""

    def __init__(self, airports):
        self.airports = airports
        self.buckets = defaultdict(list)
        for point in airports:
            _, lat, lon = point
            self.buckets[math.floor(lat), math.floor((lon + 180) % 360 - 180)].append(point)

    def match(self, lat, lon, radius_km=5):
        angular = radius_km / 6371
        lat_margin = math.degrees(angular)
        cosine = math.cos(math.radians(lat))
        lon_margin = (
            180
            if abs(lat) + lat_margin >= 90
            else math.degrees(math.asin(min(1, math.sin(angular) / cosine)))
        )
        candidates = []
        for latitude in range(math.floor(lat - lat_margin), math.floor(lat + lat_margin) + 1):
            longitudes = {
                (longitude + 180) % 360 - 180
                for longitude in range(
                    math.floor(lon - lon_margin), math.floor(lon + lon_margin) + 1
                )
            }
            for longitude in longitudes:
                candidates.extend(self.buckets.get((latitude, longitude), ()))
        matches = {
            code
            for code, airport_lat, airport_lon in candidates
            if distance_km(lat, lon, airport_lat, airport_lon) <= radius_km
        }
        return next(iter(matches)) if len(matches) == 1 else None


def reduce_movement(data, state, airports, rules, start_us, end_us, *, leading=None):
    """Return legs and small carry state for the supplied UTC day, without schedules."""
    hex_id = data.get("icao", "")
    kind = data.get("t")
    flags = data.get("dbFlags")
    if (
        not isinstance(hex_id, str)
        or not re.fullmatch(r"[0-9a-f]{6}", hex_id)
        or kind not in rules.fixed_wing_types
        or isinstance(flags, int)
        and flags & 1
    ):
        return [], None, "excluded_aircraft"
    if state is None or state.aircraft_type_code != kind:
        state = MovementState(aircraft_type_code=kind)
    legs = []
    base_us = round(data["timestamp"] * 1_000_000)
    previous_input_us = None
    for row in data["trace"]:
        if len(row) < 7 or not isinstance(row[6], int):
            raise ValueError("Malformed aircraft observation")
        stamp = base_us + round(row[0] * 1_000_000)
        if previous_input_us is not None and stamp < previous_input_us:
            raise ValueError("Aircraft observations must be ordered")
        previous_input_us = stamp
        if not start_us <= stamp < end_us:
            continue
        if state.last_us is not None and stamp <= state.last_us:
            continue
        if state.last_us is not None and stamp - state.last_us > MAX_GAP_US:
            if leading is not None and leading.arrival is None:
                leading.valid = False
            state.interrupt()
        state.last_us = stamp
        if leading is not None and leading.arrival is None:
            if leading.first_us is None:
                leading.first_us = stamp
                leading.valid = stamp - start_us <= MAX_GAP_US
            leading.last_us = stamp
        extra = row[8] if len(row) > 8 and isinstance(row[8], dict) else {}
        category = extra.get("category")
        if (
            row[6] & 1
            or row[3] is None
            or category in {"A7", "B1", "B2", "B3", "B4", "B6", "B7", "C1", "C2", "C3"}
        ):
            if leading is not None and leading.arrival is None:
                leading.valid = False
            state.interrupt()
            continue
        flight = extra.get("flight")
        callsign = flight.strip().upper() if isinstance(flight, str) else ""
        state.current_call = callsign or state.current_call
        if not (
            isinstance(row[1], (int, float))
            and isinstance(row[2], (int, float))
            and math.isfinite(row[1])
            and math.isfinite(row[2])
            and -90 <= row[1] <= 90
            and -180 <= row[2] <= 180
        ):
            if leading is not None and leading.arrival is None:
                leading.valid = False
            state.interrupt()
            continue
        if row[3] == "ground":
            airport = airports.match(row[1], row[2])
            arrival = Terminal(time_us=stamp, airport=airport)
            if leading is not None and leading.arrival is None:
                leading.arrival = arrival
                leading.first_airborne_us = state.first_airborne_us
                leading.last_airborne_us = state.last_airborne_us
                leading.callsigns = dict(state.callsigns)
                leading.current_call = state.current_call
            candidate = make_candidate(hex_id, state, arrival, rules)
            if candidate:
                legs.append(candidate)
            state.ground = arrival
            state.first_airborne_us = state.last_airborne_us = None
            state.callsigns.clear()
        elif isinstance(row[3], (int, float)) and math.isfinite(row[3]):
            if state.first_airborne_us is None:
                state.first_airborne_us = stamp
            state.last_airborne_us = stamp
            if state.current_call:
                state.callsigns[state.current_call] = state.callsigns.get(state.current_call, 0) + 1
                if len(state.callsigns) > 8:
                    if leading is not None and leading.arrival is None:
                        leading.valid = False
                    state.interrupt()
        else:
            if leading is not None and leading.arrival is None:
                leading.valid = False
            state.interrupt()
    if leading is not None and leading.arrival is None:
        leading.first_airborne_us = state.first_airborne_us
        leading.last_airborne_us = state.last_airborne_us
        leading.callsigns = dict(state.callsigns)
        leading.current_call = state.current_call
    if state.last_us is None or state.last_us < end_us - MAX_GAP_US:
        state = None
    return legs, state, "processed"
