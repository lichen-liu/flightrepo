"""Reduce observations to itinerary evidence, retaining no position history."""

import math
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from functools import lru_cache, partial

from pydantic import BaseModel, Field

from ingestion.adsblol.diagnostics import count
from ingestion.adsblol.legs import FlightLegCandidate, distance_km

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
AIRPORT_RADIUS_KM = 25
EXCLUDED_CATEGORIES = {"A7", "B1", "B2", "B3", "B4", "B6", "B7", "C1", "C2", "C3"}


class Terminal(BaseModel):
    time_us: int
    airport: str | None = None
    match_reason: str | None = None
    inferred: bool = False


class MovementState(BaseModel):
    aircraft_type_code: str | None
    last_us: int | None = None
    ground: Terminal | None = None
    first_airborne_us: int | None = None
    last_airborne_us: int | None = None
    arrival_hint: Terminal | None = None
    origin_hint: Terminal | None = None
    previous_altitude: float | None = None
    current_call: str = ""
    callsigns: dict[str, int] = Field(default_factory=dict)
    max_observation_gap_us: int = 0

    def interrupt(self):
        self.ground = None
        self.first_airborne_us = self.last_airborne_us = None
        self.current_call = ""
        self.callsigns.clear()
        self.max_observation_gap_us = 0
        self.arrival_hint = None
        self.origin_hint = None
        self.previous_altitude = None


class LeadingMovement(BaseModel):
    """Only timing/callsign/airport evidence before a day's first ground observation."""

    aircraft_type_code: str | None
    first_us: int | None = None
    last_us: int | None = None
    first_airborne_us: int | None = None
    last_airborne_us: int | None = None
    first_altitude: float | None = None
    arrival_hint: Terminal | None = None
    previous_altitude: float | None = None
    current_call: str = ""
    callsigns: dict[str, int] = Field(default_factory=dict)
    max_observation_gap_us: int = 0
    arrival: Terminal | None = None
    valid: bool = True


def candidate_rejection(state, arrival, rules):
    ground = state.ground
    first, last = state.first_airborne_us, state.last_airborne_us
    if first is None or last is None:
        return "no_airborne_observations"
    if ground is None:
        return "missing_origin_ground"
    if first - ground.time_us > rules.max_terminal_seconds * 1_000_000:
        return "departure_bracket_too_wide"
    if arrival.time_us - last > rules.max_terminal_seconds * 1_000_000:
        return "arrival_bracket_too_wide"
    if last - first < rules.min_airborne_seconds * 1_000_000:
        return "insufficient_airborne_duration"
    if ground.airport is None:
        return "origin_" + (ground.match_reason or "unmatched_airport")
    if arrival.airport is None:
        return "destination_" + (arrival.match_reason or "unmatched_airport")
    if ground.airport == arrival.airport:
        return "same_airport"
    return None


def make_candidate(
    hex_id, state, arrival, rules, *, diagnostics=None, stage="local", defer_origin=False
):
    ground = state.ground
    first, last = state.first_airborne_us, state.last_airborne_us
    call = max(state.callsigns, key=lambda value: (state.callsigns[value], value), default=None)
    reason = candidate_rejection(state, arrival, rules)
    if reason and defer_origin and ground and ground.inferred and ground.airport == arrival.airport:
        reason = "missing_origin_ground"  # First low sample can be yesterday's arrival.
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
    if state.aircraft_type_code is None:
        count(diagnostics, "metadata", "accepted_without_type")
    if not state.callsigns:
        count(diagnostics, "metadata", "accepted_without_callsign")
    elif len(state.callsigns) > 1:
        count(diagnostics, "metadata", "accepted_with_callsign_variations")
    review_reasons = []
    if ground.inferred or arrival.inferred:
        review_reasons.append("airborne_airport_estimate")
    if state.max_observation_gap_us > 120_000_000:
        count(diagnostics, "metadata", "accepted_with_observation_gaps")
    if state.max_observation_gap_us > rules.review_gap_seconds * 1_000_000:
        review_reasons.append("observation_gap")
    if not review_reasons and (
        state.max_observation_gap_us > 120_000_000 or len(state.callsigns) > 1
    ):
        count(diagnostics, "metadata", "accepted_under_relaxed_review")
    for review_reason in review_reasons:
        count(diagnostics, "review_reasons", review_reason)
    return FlightLegCandidate(
        icao_hex=hex_id,
        review_reasons=review_reasons,
        callsign=call,
        aircraft_type_code=state.aircraft_type_code,
        origin_iata=ground.airport,
        destination_iata=arrival.airport,
        departure_lower=EPOCH + timedelta(microseconds=ground.time_us),
        departure_upper=EPOCH + timedelta(microseconds=first),
        arrival_lower=EPOCH + timedelta(microseconds=last),
        arrival_upper=EPOCH + timedelta(microseconds=arrival.time_us),
        evidence="airport_proximity_and_time_window"
        if ground.inferred or arrival.inferred
        else "ground_brackets_and_nearby_airports",
    )


def advance_boundary(hex_id, previous, leading, tail, rules, *, diagnostics=None):
    """Join adjacent UTC days from compact summaries, including a whole airborne day."""
    if (
        previous is None
        or previous.last_us is None
        or leading is None
        or not leading.valid
        or leading.first_us is None
        or (
            leading.aircraft_type_code
            and previous.aircraft_type_code
            and leading.aircraft_type_code != previous.aircraft_type_code
        )
        or not 0 < leading.first_us - previous.last_us <= rules.max_gap_seconds * 1_000_000
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
    merged.max_observation_gap_us = max(
        previous.max_observation_gap_us,
        leading.max_observation_gap_us,
        leading.first_us - previous.last_us,
    )
    merged.last_us = leading.last_us
    merged.aircraft_type_code = previous.aircraft_type_code or leading.aircraft_type_code
    merged.arrival_hint = leading.arrival_hint or previous.arrival_hint
    if (
        previous.origin_hint is not None
        and previous.previous_altitude is not None
        and leading.first_altitude is not None
        and leading.first_altitude >= previous.previous_altitude + rules.min_climb_feet
    ):
        merged.ground = previous.origin_hint
        merged.origin_hint = None
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


def _bucket_candidates(buckets, south, north, west, east):
    """Gather the same coarse cells once; exact geometry remains per observation."""
    longitudes = {(longitude + 180) % 360 - 180 for longitude in range(west, east + 1)}
    return tuple(
        point
        for latitude in range(south, north + 1)
        for longitude in longitudes
        for point in buckets.get((latitude, longitude), ())
    )


class AirportLookup:
    """Coarse in-memory buckets keep endpoint matching independent of airport count."""

    def __init__(self, airports):
        self.airports = airports
        self.buckets = defaultdict(list)
        self.elevations = {point[0]: point[3] if len(point) > 3 else 0 for point in airports}
        self.max_elevation = max(self.elevations.values(), default=0)
        for point in airports:
            _, lat, lon = point[:3]
            self.buckets[math.floor(lat), math.floor((lon + 180) % 360 - 180)].append(point)
        # The extraction catalog is fixed for this lookup's lifetime. Cache only
        # integer-cell candidate lists, never final matches or rounded positions.
        # Capturing the bucket mapping rather than self avoids a reference cycle.
        self._candidate_points = lru_cache(maxsize=1024)(partial(_bucket_candidates, self.buckets))

    def match_result(
        self,
        lat,
        lon,
        radius_km=AIRPORT_RADIUS_KM,
        ambiguity_km=1,
        *,
        track=None,
        departing=False,
        heading_tolerance_degrees=45,
        heading_ignore_radius_km=3,
    ):
        angular = radius_km / 6371
        lat_margin = math.degrees(angular)
        cosine = math.cos(math.radians(lat))
        lon_margin = (
            180
            if abs(lat) + lat_margin >= 90
            else math.degrees(math.asin(min(1, math.sin(angular) / cosine)))
        )
        candidates = self._candidate_points(
            math.floor(lat - lat_margin),
            math.floor(lat + lat_margin),
            math.floor(lon - lon_margin),
            math.floor(lon + lon_margin),
        )
        matches = {}
        for point in candidates:
            code, airport_lat, airport_lon = point[:3]
            # Great-circle distance cannot be smaller than latitude separation.
            # A conservative ~1 cm pad keeps numerical boundary cases in the
            # exact calculation; acceptance still uses the original distance.
            if abs(airport_lat - lat) > lat_margin + 1e-7:
                continue
            distance = distance_km(lat, lon, airport_lat, airport_lon)
            if track is not None and distance > heading_ignore_radius_km:
                # An airborne endpoint should lie along the observed approach or
                # behind the departure, rather than merely near its position.
                a, b = math.radians(lat), math.radians(airport_lat)
                delta = math.radians(airport_lon - lon)
                bearing = math.degrees(
                    math.atan2(
                        math.sin(delta) * math.cos(b),
                        math.cos(a) * math.sin(b) - math.sin(a) * math.cos(b) * math.cos(delta),
                    )
                )
                expected = (bearing + 180) % 360 if departing else bearing
                if abs((track - expected + 180) % 360 - 180) > heading_tolerance_degrees:
                    continue
            if distance <= radius_km:
                matches[code] = min(matches.get(code, math.inf), distance)
        nearest = sorted(matches, key=lambda code: (matches[code], code))
        if not nearest:
            return None, "unmatched_airport"
        if len(nearest) > 1 and matches[nearest[1]] - matches[nearest[0]] <= ambiguity_km:
            return None, "ambiguous_airport"
        return nearest[0], "matched"

    def match(self, lat, lon, radius_km=AIRPORT_RADIUS_KM):
        return self.match_result(lat, lon, radius_km)[0]


def reduce_movement(
    data, state, airports, rules, start_us, end_us, *, leading=None, diagnostics=None
):
    """Return legs and small carry state for the supplied UTC day, without schedules."""
    hex_id = data.get("icao", "")
    raw_kind = data.get("t")
    kind = (raw_kind.strip().upper() or None) if isinstance(raw_kind, str) else None
    if kind and not re.fullmatch(r"[A-Z0-9]{2,8}", kind):
        kind = None
        count(diagnostics, "metadata", "invalid_type_ignored")
    if kind is None:
        count(diagnostics, "metadata", "missing_type_seen")
    flags = data.get("dbFlags")
    # Position freshness does not invalidate aircraft classification metadata.
    non_fixed_wing = (
        any(
            isinstance(row, list)
            and len(row) > 8
            and isinstance(row[8], dict)
            and isinstance(row[8].get("category"), str)
            and row[8]["category"] in EXCLUDED_CATEGORIES
            for row in data.get("trace", [])
        )
        if isinstance(data.get("trace"), list)
        else False
    )
    count(diagnostics, "aircraft", "examined")
    reason = (
        "invalid_identity"
        if not isinstance(hex_id, str) or not re.fullmatch(r"[0-9a-f]{6}", hex_id)
        else "military"
        if isinstance(flags, int) and flags & 1
        else "non_fixed_wing_category"
        if non_fixed_wing
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
    if state is None or (state.aircraft_type_code and kind and state.aircraft_type_code != kind):
        if state is not None:
            interrupt("aircraft_type_changed")
        state = MovementState(aircraft_type_code=kind)
    legs = []
    if not state.aircraft_type_code:
        state.aircraft_type_code = kind
    if leading is not None:
        leading.aircraft_type_code = kind

    def complete(arrival):
        if leading is not None and leading.arrival is None:
            leading.arrival = arrival
            leading.first_airborne_us = state.first_airborne_us
            leading.last_airborne_us = state.last_airborne_us
            leading.max_observation_gap_us = state.max_observation_gap_us
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
        # readsb marks a newly accepted reliable position after a reception gap.
        # Bit 0 describes the preceding interval, not invalid coordinates here.
        if row[6] & 1:
            count(diagnostics, "observations", "reception_gap_markers")
        extra = row[8] if len(row) > 8 and isinstance(row[8], dict) else {}
        if row[3] != "ground" and not (
            isinstance(row[3], (int, float))
            and not isinstance(row[3], bool)
            and math.isfinite(row[3])
        ):
            count(
                diagnostics,
                "ignored_observations",
                "missing_altitude" if row[3] is None else "invalid_altitude",
            )
            continue
        if not (
            isinstance(row[1], (int, float))
            and not isinstance(row[1], bool)
            and isinstance(row[2], (int, float))
            and not isinstance(row[2], bool)
            and math.isfinite(row[1])
            and math.isfinite(row[2])
            and -90 <= row[1] <= 90
            and -180 <= row[2] <= 180
        ):
            count(diagnostics, "ignored_observations", "invalid_position")
            continue
        if state.last_us is not None and stamp <= state.last_us:
            count(diagnostics, "ignored_observations", "duplicate_timestamp")
            continue
        gap = stamp - state.last_us if state.last_us is not None else 0
        if gap > 120_000_000:
            bucket = (
                "120_to_300_seconds"
                if gap <= 300_000_000
                else "300_to_600_seconds"
                if gap <= 600_000_000
                else "600_to_1800_seconds"
                if gap <= 1_800_000_000
                else "1800_to_21600_seconds"
                if gap <= 21_600_000_000
                else "over_21600_seconds"
            )
            count(diagnostics, "coverage_gaps", bucket)
        if gap > rules.max_gap_seconds * 1_000_000:
            if state.arrival_hint is not None:
                complete(state.arrival_hint)
            if leading is not None and leading.arrival is None:
                leading.valid = False
            interrupt("coverage_gap")
        if state.first_airborne_us is not None:
            state.max_observation_gap_us = max(state.max_observation_gap_us, gap)
        state.last_us = stamp
        if leading is not None and leading.arrival is None:
            if leading.first_us is None:
                leading.first_us = stamp
                leading.valid = stamp - start_us <= rules.max_gap_seconds * 1_000_000
            leading.last_us = stamp
        flight = extra.get("flight")
        callsign = flight.strip().upper() if isinstance(flight, str) else ""
        state.current_call = callsign or state.current_call
        if row[3] == "ground":
            count(diagnostics, "observations", "ground")
            airport, match_reason = airports.match_result(
                row[1], row[2], rules.airport_radius_km, rules.airport_ambiguity_km
            )
            count(diagnostics, "airport_matches", match_reason)
            arrival = Terminal(
                time_us=stamp,
                airport=airport,
                match_reason=match_reason if airport is None else None,
            )
            complete(arrival)
            state.ground = arrival
            state.first_airborne_us = state.last_airborne_us = None
            state.callsigns.clear()
            state.max_observation_gap_us = 0
            state.arrival_hint = None
            state.previous_altitude = None
        elif isinstance(row[3], (int, float)) and math.isfinite(row[3]):
            count(diagnostics, "observations", "airborne")
            altitude = row[3]
            if leading is not None and leading.arrival is None and leading.first_altitude is None:
                leading.first_altitude = altitude
            if row[6] & 2 and state.first_airborne_us is not None:
                if state.arrival_hint is not None:
                    complete(state.arrival_hint)
                if leading is not None and leading.arrival is None:
                    leading.valid = False
                interrupt("source_leg_boundary")
                state.current_call = callsign
            airport = None
            track = row[5] if isinstance(row[5], (int, float)) and math.isfinite(row[5]) else None
            needs_origin = (
                state.ground is None
                or state.ground.airport is None
                or (
                    state.first_airborne_us is None
                    and stamp - state.ground.time_us > rules.max_terminal_seconds * 1_000_000
                )
            )
            if (
                rules.allow_airborne_endpoints
                and altitude <= airports.max_elevation + rules.terminal_height_feet
            ):
                airport, _ = airports.match_result(
                    row[1],
                    row[2],
                    rules.airport_radius_km,
                    rules.airport_ambiguity_km,
                    track=track,
                    departing=needs_origin,
                    heading_tolerance_degrees=rules.heading_tolerance_degrees,
                    heading_ignore_radius_km=rules.heading_ignore_radius_km,
                )
            near_terminal = (
                airport is not None
                and -500 <= altitude - airports.elevations[airport] <= rules.terminal_height_feet
            )
            if (
                near_terminal
                and state.first_airborne_us is None
                and (
                    state.ground is None
                    or state.ground.airport is None
                    or stamp - state.ground.time_us > rules.max_terminal_seconds * 1_000_000
                )
            ):
                state.origin_hint = Terminal(
                    time_us=stamp - rules.max_terminal_seconds * 1_000_000,
                    airport=airport,
                    inferred=True,
                )
            climbing = (
                state.previous_altitude is not None
                and altitude >= state.previous_altitude + rules.min_climb_feet
            ) or (
                len(row) > 7
                and isinstance(row[7], (int, float))
                and math.isfinite(row[7])
                and row[7] >= rules.min_climb_rate_fpm
            )
            if needs_origin and state.origin_hint is not None and climbing:
                state.ground = state.origin_hint
                state.origin_hint = None
                count(diagnostics, "endpoint_inference", "origin_from_low_airborne")
            if state.first_airborne_us is None:
                if state.ground is not None and not state.ground.inferred:
                    state.max_observation_gap_us = max(
                        state.max_observation_gap_us, stamp - state.ground.time_us
                    )
                state.first_airborne_us = stamp
            state.last_airborne_us = stamp
            descending = state.previous_altitude is not None and altitude <= state.previous_altitude
            if (
                near_terminal
                and descending
                and (state.ground is None or airport != state.ground.airport)
            ):
                state.arrival_hint = Terminal(
                    time_us=stamp + rules.max_terminal_seconds * 1_000_000,
                    airport=airport,
                    inferred=True,
                )
            else:
                state.arrival_hint = None
            state.previous_altitude = altitude
            if state.current_call:
                state.callsigns[state.current_call] = state.callsigns.get(state.current_call, 0) + 1
        else:
            if leading is not None and leading.arrival is None:
                leading.valid = False
            count(diagnostics, "ignored_observations", "invalid_altitude")
    if leading is not None and leading.arrival is None:
        leading.first_airborne_us = state.first_airborne_us
        leading.last_airborne_us = state.last_airborne_us
        leading.max_observation_gap_us = state.max_observation_gap_us
        leading.callsigns = dict(state.callsigns)
        leading.current_call = state.current_call
        leading.arrival_hint = state.arrival_hint
    if (
        state.arrival_hint is not None
        and state.last_us < end_us - rules.max_terminal_seconds * 1_000_000
    ):
        complete(state.arrival_hint)
        count(diagnostics, "endpoint_inference", "destination_from_low_airborne")
        state.interrupt()
    if not legs:
        count(diagnostics, "aircraft", "without_accepted_local_leg")
    if state.first_airborne_us is not None:
        if state.last_us >= end_us - rules.max_gap_seconds * 1_000_000:
            count(diagnostics, "segments", "local_deferred_next_day")
            if state.ground is not None and state.ground.airport is not None:
                count(diagnostics, "segments", "local_deferred_next_day_with_origin")
        else:
            count(diagnostics, "abandoned_segments", "coverage_ended_before_midnight")
    if state.last_us is None or state.last_us < end_us - rules.max_gap_seconds * 1_000_000:
        state = None
    return legs, state, "processed"
