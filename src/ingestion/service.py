"""Provider fetching and normalization orchestration."""
from datetime import date

from core.models import IngestResult
from ingestion.providers.base import FlightProvider
from ingestion.updater import FlightUpdater


class IngestionService:
    def __init__(self, store: FlightUpdater, provider: FlightProvider, max_days: int):
        self.store, self.provider, self.max_days = store, provider, max_days

    async def ingest(self, airports: list[str], start: date, end: date) -> IngestResult:
        if (end - start).days + 1 > self.max_days:
            raise ValueError(f"date range exceeds configured maximum of {self.max_days} days")
        unique = {}
        for airport in airports:
            for record in await self.provider.flights_for_airport(airport, start, end):
                unique[(record.provider, record.provider_id)] = record
        count = self.store.upsert(unique.values())
        return IngestResult(provider=self.provider.name, fetched=len(unique), upserted=count,
                            airports=airports, start=start, end=end)
