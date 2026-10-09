import json
from datetime import datetime, timezone

import pytest

from flight_engine.admin import client
from flight_engine.core.config import Settings
from flight_engine.core.models import FlightRecord
from flight_engine.server.reader import FlightReader
from flight_engine.ingestion.updater import FlightUpdater


def test_local_management_roundtrip_and_reset(tmp_path, monkeypatch):
    local = tmp_path / 'var'
    settings = Settings(database_path=local / 'flights.db')
    monkeypatch.setattr(client, 'get_settings', lambda: settings)
    monkeypatch.setattr(client, 'LOCAL_DATA_DIR', local)
    record = FlightRecord(provider='fixture', provider_id='one', flight_number='AC1',
                          origin_iata='YYZ', destination_iata='YUL',
                          departure_scheduled=datetime(2026, 10, 10, 10, tzinfo=timezone.utc),
                          arrival_scheduled=datetime(2026, 10, 10, 11, tzinfo=timezone.utc),
                          fetched_at=datetime.now(timezone.utc))
    source = tmp_path / 'input.json'
    source.write_text(json.dumps([record.model_dump(mode='json')]))
    client.run(['import-json', str(source)])
    exported = tmp_path / 'export.json'
    client.run(['export-json', str(exported)])
    assert json.loads(exported.read_text())[0]['provider_id'] == 'one'
    store = FlightReader(settings.database_path)
    assert store.stats()['flights'] == 1
    source.write_text('[{"invalid": true}]')
    with pytest.raises(ValueError):
        client.run(['import-json', str(source)])
    assert store.stats()['flights'] == 1
    client.run(['delete', 'fixture', 'one'])
    assert store.stats()['flights'] == 0
    client.run(['import-json', str(exported)])
    with pytest.raises(ValueError):
        client.run(['reset'])
    assert store.stats()['flights'] == 1
    client.run(['reset', '--yes'])
    assert store.stats()['flights'] == 0
    outside = tmp_path / 'outside.json'
    outside.write_text('keep')
    with pytest.raises(ValueError):
        client.reset_database(outside)
    assert outside.read_text() == 'keep'


def test_client_timestamp_normalizes_offsets():
    assert client.timestamp('2026-10-10T05:00:00-04:00').hour == 9
    with pytest.raises(ValueError):
        client.timestamp('2026-10-10T05:00:00')
