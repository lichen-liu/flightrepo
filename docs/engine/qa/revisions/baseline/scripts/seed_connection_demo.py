"""Seed a separate, ignored demo DB without modifying the normal local DB."""
import json
from pathlib import Path

from core.config import LOCAL_DATA_DIR, PROJECT_ROOT
from core.connections import Airport, AirportTransfer, RebuildRequest
from core.models import FlightRecord
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


def main():
    data = json.loads((PROJECT_ROOT / 'fixtures/connections-demo.json').read_text())
    path = LOCAL_DATA_DIR / 'connections-demo.db'
    updater = FlightUpdater(path)
    updater.initialize()
    updater.update_airports([Airport.model_validate(a) for a in data['airports']],
                            [AirportTransfer.model_validate(t) for t in data['transfers']])
    updater.upsert([FlightRecord.model_validate(f) for f in data['flights']])
    request_path = LOCAL_DATA_DIR / 'connection-request.json'
    request_path.write_text(json.dumps(data['request'], indent=2) + '\n')
    search_path = LOCAL_DATA_DIR / 'route-search-request.json'
    search_path.write_text(json.dumps({'origin': 'YYZ', 'destination': 'CDG',
                                      'departure_start': '2026-10-17T00:00:00Z',
                                      'departure_end': '2026-10-18T00:00:00Z',
                                      'horizon_end': '2026-10-24T00:00:00Z',
                                      'allow_airport_change': True, 'trace': True,
                                      'max_legs': 3, 'ranking': 'earliest_arrival'}, indent=2)+'\n')
    result = FlightReader(path).rebuild(RebuildRequest.model_validate(data['request']))
    print(json.dumps({'database': str(path), 'request': str(request_path),
                      'search_request': str(search_path),
                      'plan': [f.flight_number for f in result.legs],
                      'onward_options': len(result.options), 'complete': result.complete}, indent=2))


if __name__ == '__main__':
    main()
