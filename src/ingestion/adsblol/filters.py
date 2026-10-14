"""Explicit aircraft-type and commercial-callsign selection for itinerary extraction."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_FILTER_PATH = Path(__file__).with_name("commercial-filter.json")


class CommercialFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixed_wing_types: set[str] = Field(min_length=1)
    callsign_prefixes: set[str] = Field(min_length=1)
