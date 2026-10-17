"""Quality limits for inference; aircraft and callsign whitelists are never used."""

from pydantic import BaseModel, ConfigDict, Field


class ExtractionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_gap_seconds: int = Field(default=21600, ge=1, le=86400)
    review_gap_seconds: int = Field(default=1800, ge=1, le=86400)
    max_terminal_seconds: int = Field(default=1800, ge=1, le=7200)
    min_airborne_seconds: int = Field(default=60, ge=1)
    airport_radius_km: float = Field(default=25, gt=0, le=100)
    airport_ambiguity_km: float = Field(default=1, ge=0, le=10)
    terminal_height_feet: int = Field(default=3500, ge=0, le=10000)
    heading_tolerance_degrees: float = Field(default=45, gt=0, le=180)
    heading_ignore_radius_km: float = Field(default=3, ge=0, le=10)
    min_climb_feet: int = Field(default=100, ge=1)
    min_climb_rate_fpm: int = Field(default=300, ge=1)
    allow_airborne_endpoints: bool = True
