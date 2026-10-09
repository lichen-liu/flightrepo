from datetime import datetime, timedelta

from flight_engine.core.models import Itinerary
from flight_engine.core.contracts import FlightQuery



def build_itineraries(store: FlightQuery, origin: str, destination: str, start: datetime,
                      end: datetime, max_legs: int = 2, min_connection_minutes: int = 45,
                      max_connection_minutes: int = 360, limit: int = 20) -> list[Itinerary]:
    flights = store.search(start, end + timedelta(hours=max_connection_minutes), limit=5000)
    by_origin: dict[str, list] = {}
    for flight in flights:
        by_origin.setdefault(flight.origin_iata, []).append(flight)
    found: list[Itinerary] = []

    def walk(airport: str, legs: list) -> None:
        if len(legs) >= max_legs or len(found) >= limit * 5:
            return
        for flight in by_origin.get(airport, []):
            if not legs:
                if not start <= flight.departure_scheduled < end:
                    continue
            else:
                wait = (flight.departure_scheduled - legs[-1].arrival_scheduled).total_seconds() / 60
                if not min_connection_minutes <= wait <= max_connection_minutes:
                    continue
                if flight.destination_iata in {leg.origin_iata for leg in legs}:
                    continue
            next_legs = legs + [flight]
            if flight.destination_iata == destination:
                duration = int((flight.arrival_scheduled - next_legs[0].departure_scheduled).total_seconds() / 60)
                found.append(Itinerary(legs=next_legs, departure=next_legs[0].departure_scheduled,
                                       arrival=flight.arrival_scheduled, duration_minutes=duration))
            else:
                walk(flight.destination_iata, next_legs)

    walk(origin.upper(), [])
    return sorted(found, key=lambda x: (len(x.legs), x.duration_minutes, x.departure))[:limit]

