"""Exact endpoint matching after conservative candidate reuse and pruning."""

import math
import random
import weakref

from ingestion.adsblol import movements
from ingestion.adsblol.legs import distance_km
from ingestion.adsblol.movements import AIRPORT_RADIUS_KM, AirportLookup


class OriginalAirportLookup(AirportLookup):
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
        matches = {}
        for point in candidates:
            code, airport_lat, airport_lon = point[:3]
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


def test_reused_cells_still_resolve_each_exact_position_and_radius():
    lookup = AirportLookup([("AAA", 0.1, 0.1)])
    assert lookup.match_result(0.3248, 0.1) == ("AAA", "matched")
    hits = lookup._candidate_points.cache_info().hits
    assert lookup.match_result(0.3249, 0.1) == (None, "unmatched_airport")
    assert lookup._candidate_points.cache_info().hits > hits
    assert lookup.match_result(0.3, 0.1, radius_km=21) == (None, "unmatched_airport")
    assert lookup.match_result(0.3, 0.1, radius_km=23) == ("AAA", "matched")


def test_heading_and_ambiguity_are_not_cached_with_the_neighborhood():
    lookup = AirportLookup([("AAA", 40, -70), ("CCC", 40.2, -70)])
    assert lookup.match_result(40.15, -70, track=0, departing=True) == ("AAA", "matched")
    assert lookup.match_result(40.15, -70, track=0, departing=False) == ("CCC", "matched")
    assert lookup._candidate_points.cache_info().hits > 0
    lookup = AirportLookup([("AAA", 40, -70), ("CCC", 40.1, -70)])
    assert lookup.match_result(40.04, -70, ambiguity_km=1) == ("AAA", "matched")
    assert lookup.match_result(40.04, -70, ambiguity_km=3) == (None, "ambiguous_airport")


def test_candidate_cache_is_bounded_per_catalog_and_does_not_retain_lookup():
    lookup = AirportLookup([])
    for latitude in range(-70, -30):
        for longitude in range(-170, 171, 10):
            lookup.match_result(latitude, longitude)
    info = lookup._candidate_points.cache_info()
    assert info.currsize == info.maxsize == 1024
    first = AirportLookup([("AAA", 0, 0)])
    second = AirportLookup([("BBB", 0, 0)])
    assert first.match(0, 0) == "AAA"
    assert second.match(0, 0) == "BBB"
    reference = weakref.ref(first)
    del first
    assert reference() is None


def test_conservative_latitude_bound_keeps_threshold_cases_exact():
    for radius in [0.001, 1, 25, 100]:
        margin = math.degrees(radius / 6371)
        for latitude in [-89.999, -70, -0.0, 0.0, 10, 70, 89.999]:
            for sign in [-1, 1]:
                for shift in [-1e-6, -1e-7, -1e-9, 0, 1e-9, 1e-7, 1e-6]:
                    airport_lat = latitude + sign * (margin + shift)
                    if not -90 <= airport_lat <= 90:
                        continue
                    points = [("AAA", airport_lat, 0.0)]
                    lookup, original = AirportLookup(points), OriginalAirportLookup(points)
                    assert lookup.match_result(
                        latitude, 0.0, radius_km=radius
                    ) == original.match_result(latitude, 0.0, radius_km=radius)


def test_dateline_poles_and_duplicate_airport_codes_keep_original_results():
    points = [
        ("AAA", 0, -179.999),
        ("AAA", 0, 179.99),
        ("BBB", 89.999, 90),
        ("CCC", -89.999, -90),
        ("DDD", 90, 0),
        ("EEE", -90, 180),
    ]
    lookup, original = AirportLookup(points), OriginalAirportLookup(points)
    for latitude, longitude in [
        (0, -180),
        (0, 180),
        (0, 179.9999),
        (0, -179.9999),
        (89.999, -90),
        (-89.999, 90),
        (90, 180),
        (-90, -180),
    ]:
        for radius in [1, 25, 100]:
            for track in [None, 0, 90, 180, 270]:
                for departing in [False, True]:
                    options = dict(radius_km=radius, track=track, departing=departing)
                    assert lookup.match_result(
                        latitude, longitude, **options
                    ) == original.match_result(latitude, longitude, **options)


def test_policy_and_position_matrix_exactly_matches_original():
    rng = random.Random(2307)
    points = []
    for i in range(250):
        code = chr(65 + i // 676) + chr(65 + (i // 26) % 26) + chr(65 + i % 26)
        points.append((code, rng.uniform(-89.9, 89.9), rng.uniform(-180, 180)))
    points.extend(
        (f"Z{chr(65 + i // 26)}{chr(65 + i % 26)}", 40 + i / 100, -70 + i / 200) for i in range(80)
    )
    lookup, original = AirportLookup(points), OriginalAirportLookup(points)
    for _ in range(4000):
        _, airport_lat, airport_lon = rng.choice(points)
        latitude = max(-90, min(90, airport_lat + rng.uniform(-1.5, 1.5)))
        longitude = (airport_lon + rng.uniform(-1.5, 1.5) + 180) % 360 - 180
        options = dict(
            radius_km=rng.choice([0.001, 1, 25, 100]),
            ambiguity_km=rng.choice([0, 1, 10]),
            track=rng.choice([None, 0, 45, 90, 180, 270, 359.999]),
            departing=rng.choice([False, True]),
            heading_tolerance_degrees=rng.choice([0.001, 45, 180]),
            heading_ignore_radius_km=rng.choice([0, 3, 10]),
        )
        assert lookup.match_result(latitude, longitude, **options) == original.match_result(
            latitude, longitude, **options
        )


def test_provably_far_latitudes_skip_distance_but_nearby_points_still_use_it(monkeypatch):
    points = [("AAA", 10.11, 50.1), ("BBB", 10.9, 50.1)]
    calls = []

    def counted(*args):
        calls.append(args)
        return distance_km(*args)

    monkeypatch.setattr(movements, "distance_km", counted)
    lookup = AirportLookup(points)
    assert lookup.match_result(10.1, 50.1) == OriginalAirportLookup(points).match_result(10.1, 50.1)
    assert calls and all(args[2] == 10.11 for args in calls)
    # A reused candidate set must still evaluate the current coordinates.
    calls.clear()
    lookup.match_result(10.100001, 50.1)
    assert calls and all(args[:2] == (10.100001, 50.1) for args in calls)
