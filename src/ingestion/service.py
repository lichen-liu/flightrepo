"""Provider fetching and normalization orchestration."""

from datetime import date
from pathlib import Path

from core.database_roles import ingestion_target
from core.file_lock import file_mutex
from core.models import IngestRequest, IngestResult
from ingestion.providers.base import FlightProvider
from ingestion.store import IngestionStore


class IngestionService:
    def __init__(self, store: IngestionStore, provider: FlightProvider, max_days: int):
        if not isinstance(store, IngestionStore):
            raise ValueError(
                "Provider ingestion requires an IngestionStore, never a serving writer"
            )
        self.store, self.provider, self.max_days = store, provider, max_days

    async def ingest(self, airports: list[str], start: date, end: date) -> IngestResult:
        request = IngestRequest(airports=airports, start=start, end=end)
        airports, start, end = request.airports, request.start, request.end
        if (end - start).days + 1 > self.max_days:
            raise ValueError(f"date range exceeds configured maximum of {self.max_days} days")
        ingestion_target(self.store.path)
        with file_mutex(Path(str(self.store.path) + ".job.lock")):
            unique = {}
            for airport in airports:
                for record in await self.provider.flights_for_airport(airport, start, end):
                    unique[(record.provider, record.provider_id)] = record
            self.store.initialize()
            count = self.store.upsert(unique.values(), restore=False)
            return IngestResult(
                provider=self.provider.name,
                fetched=len(unique),
                upserted=count,
                airports=airports,
                start=start,
                end=end,
            )
