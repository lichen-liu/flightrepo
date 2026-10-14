"""Package normalized itinerary facts into immutable chunks for backend downloads."""

import gzip
import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.itinerary_catalog import (
    MAX_CHUNK_RECORDS,
    ItineraryCatalog,
    ItineraryChunk,
    ItineraryPayload,
    http_url,
)
from core.models import FlightRecord


def write_itinerary_catalog(records, airports, transfers, output, base_url):
    http_url(base_url)
    by_code = {airport.iata: airport for airport in airports}
    groups, identities = defaultdict(list), set()
    for record in records:
        record = FlightRecord.model_validate(record.model_dump())
        identity = record.provider, record.provider_id
        if identity in identities:
            raise ValueError("Duplicate flight identity in source records")
        identities.add(identity)
        departure = record.departure_scheduled
        groups[(record.provider, record.origin_iata, departure.year, departure.month)].append(
            record
        )
    chunks, files = [], {}
    windows = []
    for (provider, origin, year, month), flights in sorted(groups.items()):
        flights.sort(key=lambda f: (f.departure_scheduled, f.provider_id))
        start = datetime(year, month, 1, tzinfo=UTC)
        end = (
            datetime(year, month + 1, 1, tzinfo=UTC)
            if month < 12
            else datetime(year + 1, 1, 1, tzinfo=UTC)
            if year < 9999
            else datetime.max.replace(tzinfo=UTC)
        )
        offset = 0
        while offset < len(flights):
            stop = min(offset + MAX_CHUNK_RECORDS, len(flights))
            # Windows are disjoint; equal departure instants must stay together.
            while stop < len(flights) and (
                flights[stop - 1].departure_scheduled == flights[stop].departure_scheduled
            ):
                stop -= 1
                if stop == offset:
                    raise ValueError("Too many flights at one provider/origin departure instant")
            boundary = flights[stop].departure_scheduled if stop < len(flights) else end
            windows.append((provider, origin, start, boundary, flights[offset:stop]))
            start, offset = boundary, stop
    for provider, origin, start, end, flights in windows:
        content = gzip.compress(
            ItineraryPayload(flights=flights).model_dump_json().encode(), mtime=0
        )
        digest = hashlib.sha256(content).hexdigest()
        active = [flight for flight in flights if flight.status != "cancelled"]
        timezone = by_code[origin].timezone if origin in by_code else None
        local = ZoneInfo(timezone) if timezone else None
        chunks.append(
            ItineraryChunk(
                sha256=digest,
                url=base_url.rstrip("/") + "/" + digest + ".json.gz",
                bytes=len(content),
                provider=provider,
                origin=origin,
                destinations=sorted({f.destination_iata for f in flights}),
                departure_start=start,
                departure_end=end,
                flight_count=len(flights),
                cancelled_count=len(flights) - len(active),
                first_departure=min((f.departure_scheduled for f in active), default=None),
                last_departure=max((f.departure_scheduled for f in active), default=None),
                oldest_fact_refresh=min(f.fetched_at for f in flights),
                newest_fact_refresh=max(f.fetched_at for f in flights),
                origin_timezone=timezone,
                dates_utc=sorted({f.departure_scheduled.date() for f in active}),
                dates_local=sorted({f.departure_scheduled.astimezone(local).date() for f in active})
                if local
                else [],
            )
        )
        files[digest + ".json.gz"] = content
    catalog = ItineraryCatalog(chunks=chunks, airports=airports, transfers=transfers)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (output / name).write_bytes(content)
    (output / "catalog.json").write_text(catalog.model_dump_json(indent=2) + "\n")
    return catalog
