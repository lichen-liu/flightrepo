"""Reduce observations to itinerary evidence, retaining no position history."""

import math
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from ingestion.adsblol.diagnostics import count
from ingestion.adsblol.legs import FlightLegCandidate, distance_km

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_GAP_US = 120_000_000


class Terminal(BaseModel):
    time_us: int
    airport: str | None = None
    match_reason: str | None = None


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


def candidate_rejection(state, arrival, rules):
    ground = state.ground
    first, last = state.first_airborne_us, state.last_airborne_us
    if first is None or last is None:
        return "no_airborne_observations"
    if ground is None:
        return "missing_origin_ground"
    if first - ground.time_us > MAX_GAP_US:
        return "departure_bracket_too_wide"
    if arrival.time_us - last > MAX_GAP_US:
        return "arrival_bracket_too_wide"
    if last - first < 300_000_000:
        return "insufficient_airborne_duration"
    if ground.airport is None:
        return "origin_" + (ground.match_reason or "unmatched_airport")
    if arrival.airport is None:
        return "destination_" + (arrival.match_reason or "unmatched_airport")
    if ground.airport == arrival.airport:
        return "same_airport"
    if not state.callsigns:
        return "missing_callsign"
    if len(state.callsigns) != 1:
        return "changing_callsign"
    call = next(iter(state.callsigns))
    if not any(
        re.fullmatch(re.escape(prefix) + r"[0-9][A-Z0-9]*", call)
        for prefix in rules.callsign_prefixes
    ):
        return "callsign_not_selected"
    return None


def make_candidate(
    hex_id, state, arrival, rules, *, diagnostics=None, stage="local", defer_origin=False
):
    ground = state.ground
    first, last = state.first_airborne_us, state.last_airborne_us
    call = next(iter(state.callsigns)) if len(state.callsigns) == 1 else None
    reason = candidate_rejection(state, arrival, rules)
    if reason == "no_airborne_observations":
        return None  # Repeated ground samples are not rejected flights.
    count(diagnostics, "segments", stage + "_evaluated")
    if reason is not None:
        if reason == "missing_origin_ground" and defer_origin:
            count(diagnostics, "segments", "local_deferred_previous_day")
        else:
            count(diagnostics, "segments", stage + "_rejected")
            count(diagnostics, stage + "_rejections", reason)
        return None
    count(diagnostics, "segments", stage + "_accepted")
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


def advance_boundary(hex_id, previous, leading, tail, rules, *, diagnostics=None):
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
        if previous is not None and previous.first_airborne_us is not None:
            reason = (
                "missing_or_invalid_head"
                if leading is None or not leading.valid or leading.first_us is None
                else "aircraft_type_changed"
                if leading.aircraft_type_code != previous.aircraft_type_code
                else "observation_gap"
            )
            count(diagnostics, "boundary", reason)
        elif (
            previous is None
            and leading is not None
            and leading.valid
            and leading.first_airborne_us is not None
        ):
            count(diagnostics, "boundary", "missing_previous_tail")
        return None, tail
    count(diagnostics, "boundary", "joined_summaries")
    merged = previous.model_copy(deep=True)
    merged.last_us = leading.last_us
    merged.first_airborne_us = previous.first_airborne_us or leading.first_airborne_us
    merged.last_airborne_us = leading.last_airborne_us or previous.last_airborne_us
    for call, frequency in leading.callsigns.items():
        merged.callsigns[call] = merged.callsigns.get(call, 0) + frequency
    if not merged.callsigns and previous.current_call and leading.first_airborne_us is not None:
        merged.callsigns[previous.current_call] = 1
    merged.current_call = leading.current_call or previous.current_call
    if leading.arrival is not None:
        return make_candidate(
            hex_id, merged, leading.arrival, rules, diagnostics=diagnostics, stage="boundary"
        ), tail
    return None, merged if tail is not None else None


class AirportLookup:
    """Coarse in-memory buckets keep endpoint matching independent of airport count."""

    def __init__(self, airports):
        self.airports = airports
        self.buckets = defaultdict(list)
        for point in airports:
            _, lat, lon = point
            self.buckets[math.floor(lat), math.floor((lon + 180) % 360 - 180)].append(point)

    def match_result(self, lat, lon, radius_km=5):
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
        if len(matches) == 1:
            return next(iter(matches)), "matched"
        return None, "ambiguous_airport" if matches else "unmatched_airport"

    def match(self, lat, lon, radius_km=5):
        return self.match_result(lat, lon, radius_km)[0]


def reduce_movement(
    data, state, airports, rules, start_us, end_us, *, leading=None, diagnostics=None
):
    """Return legs and small carry state for the supplied UTC day, without schedules."""
    hex_id = data.get("icao", "")
    kind = data.get("t")
    flags = data.get("dbFlags")
    count(diagnostics, "aircraft", "examined")
    reason = (
        "invalid_identity"
        if not isinstance(hex_id, str) or not re.fullmatch(r"[0-9a-f]{6}", hex_id)
        else "military"
        if isinstance(flags, int) and flags & 1
        else "missing_type"
        if not kind
        else "invalid_type"
        if not isinstance(kind, str)
        else "type_not_selected"
        if kind not in rules.fixed_wing_types
        else None
    )
    if reason:
        count(diagnostics, "aircraft", "excluded")
        count(diagnostics, "aircraft_exclusions", reason)
        return [], None, "excluded_aircraft"
    count(diagnostics, "aircraft", "processed")

    def interrupt(reason):
        count(diagnostics, "interruptions", reason)
        if state.first_airborne_us is not None:
            count(diagnostics, "abandoned_segments", reason)
        state.interrupt()

    def invalid(reason, message):
        count(diagnostics, "invalid_records", reason)
        raise ValueError(message)

    if not isinstance(data.get("trace"), list):
        invalid("invalid_trace_shape", "Aircraft trace must be an array")
    if not isinstance(data.get("timestamp"), (int, float)) or not math.isfinite(data["timestamp"]):
        invalid("invalid_base_timestamp", "Aircraft timestamp must be finite")
    if state is None or state.aircraft_type_code != kind:
        if state is not None:
            interrupt("aircraft_type_changed")
        state = MovementState(aircraft_type_code=kind)
    legs = []
    base_us = round(data["timestamp"] * 1_000_000)
    previous_input_us = None
    for index, row in enumerate(data["trace"]):
        if diagnostics is not None:
            diagnostics.observation_index = index
        count(diagnostics, "observations", "examined")
        if not isinstance(row, list) or len(row) < 7 or not isinstance(row[6], int):
            invalid("malformed_observation", "Malformed aircraft observation")
        if not isinstance(row[0], (int, float)) or not math.isfinite(row[0]):
            invalid("invalid_time_offset", "Aircraft observation offset must be finite")
        stamp = base_us + round(row[0] * 1_000_000)
        if previous_input_us is not None and stamp < previous_input_us:
            invalid("unordered_observations", "Aircraft observations must be ordered")
        previous_input_us = stamp
        if not start_us <= stamp < end_us:
            count(diagnostics, "ignored_observations", "outside_day")
            continue
        count(diagnostics, "observations", "within_day")
        if state.last_us is not None and stamp <= state.last_us:
            count(diagnostics, "ignored_observations", "duplicate_timestamp")
            continue
        if state.last_us is not None and stamp - state.last_us > MAX_GAP_US:
            if leading is not None and leading.arrival is None:
                leading.valid = False
            interrupt("coverage_gap")
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
            reason = (
                "stale_position"
                if row[6] & 1
                else "missing_altitude"
                if row[3] is None
                else "excluded_category"
            )
            count(diagnostics, "ignored_observations", reason)
            interrupt(reason)
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
            count(diagnostics, "ignored_observations", "invalid_position")
            interrupt("invalid_position")
            continue
        if row[3] == "ground":
            count(diagnostics, "observations", "ground")
            airport, match_reason = airports.match_result(row[1], row[2])
            count(diagnostics, "airport_matches", match_reason)
            arrival = Terminal(
                time_us=stamp,
                airport=airport,
                match_reason=match_reason if airport is None else None,
            )
            if leading is not None and leading.arrival is None:
                leading.arrival = arrival
                leading.first_airborne_us = state.first_airborne_us
                leading.last_airborne_us = state.last_airborne_us
                leading.callsigns = dict(state.callsigns)
                leading.current_call = state.current_call
            candidate = make_candidate(
                hex_id,
                state,
                arrival,
                rules,
                diagnostics=diagnostics,
                defer_origin=leading is not None and leading.valid and leading.arrival == arrival,
            )
            if candidate:
                legs.append(candidate)
            state.ground = arrival
            state.first_airborne_us = state.last_airborne_us = None
            state.callsigns.clear()
        elif isinstance(row[3], (int, float)) and math.isfinite(row[3]):
            count(diagnostics, "observations", "airborne")
            if state.first_airborne_us is None:
                state.first_airborne_us = stamp
            state.last_airborne_us = stamp
            if state.current_call:
                state.callsigns[state.current_call] = state.callsigns.get(state.current_call, 0) + 1
                if len(state.callsigns) > 8:
                    if leading is not None and leading.arrival is None:
                        leading.valid = False
                    interrupt("excessive_callsign_changes")
        else:
            if leading is not None and leading.arrival is None:
                leading.valid = False
            count(diagnostics, "ignored_observations", "invalid_altitude")
            interrupt("invalid_altitude")
    if leading is not None and leading.arrival is None:
        leading.first_airborne_us = state.first_airborne_us
        leading.last_airborne_us = state.last_airborne_us
        leading.callsigns = dict(state.callsigns)
        leading.current_call = state.current_call
    if not legs:
        count(diagnostics, "aircraft", "without_accepted_local_leg")
    if state.first_airborne_us is not None:
        if state.last_us >= end_us - MAX_GAP_US:
            count(diagnostics, "segments", "local_deferred_next_day")
            if state.ground is not None and state.ground.airport is not None:
                count(diagnostics, "segments", "local_deferred_next_day_with_origin")
        else:
            count(diagnostics, "abandoned_segments", "coverage_ended_before_midnight")
    if state.last_us is None or state.last_us < end_us - MAX_GAP_US:
        state = None
    return legs, state, "processed"
