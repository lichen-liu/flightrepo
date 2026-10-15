"""Observed itinerary-leg model and airport metadata helpers."""

import csv
import math
from datetime import datetime

from pydantic import BaseModel, Field


class FlightLegCandidate(BaseModel):
    icao_hex: str
    callsign: str | None
    aircraft_type_code: str | None
    origin_iata: str
    destination_iata: str
    departure_lower: datetime
    departure_upper: datetime
    arrival_lower: datetime
    arrival_upper: datetime
    evidence: str = "ground_brackets_and_nearby_airports"
    status: str = "inferred_candidate"
    review_reasons: list[str] = Field(default_factory=list)


def airport_points(path):
    with path.open(newline="", encoding="utf-8") as source:
        return [
            (
                row["iata_code"],
                float(row["latitude_deg"]),
                float(row["longitude_deg"]),
                float(row.get("elevation_ft") or 0),
            )
            for row in csv.DictReader(source)
            if len(row["iata_code"]) == 3
            and row["type"] not in {"closed", "heliport", "seaplane_base"}
        ]


def distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    chord = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(min(1, chord)))


def airport_timing_is_implausible(origin, destination, elapsed_seconds):
    """Use the widest retained event window and a generous civil-flight bound."""
    if origin is None or destination is None or elapsed_seconds <= 0:
        return False
    return distance_km(*origin, *destination) > 1500 * (elapsed_seconds / 3600)
