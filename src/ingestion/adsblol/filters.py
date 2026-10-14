"""Explicit aircraft-type and commercial-callsign selection for itinerary extraction."""

from pydantic import BaseModel, ConfigDict, Field


class CommercialFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixed_wing_types: set[str] = Field(min_length=1)
    callsign_prefixes: set[str] = Field(min_length=1)
