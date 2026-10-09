from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLIGHT_", env_file=".env", extra="ignore")

    database_path: Path = Path("var/flights.db")
    provider: str = "aerodatabox"
    provider_api_key: str = ""
    provider_base_url: str = "https://aerodatabox.p.rapidapi.com"
    provider_host: str = "aerodatabox.p.rapidapi.com"
    admin_token: str = "change-me"
    max_query_days: int = 31


@lru_cache
def get_settings() -> Settings:
    return Settings()

