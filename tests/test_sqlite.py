import json
import subprocess
import sys

from flight_engine.server.reader import FlightReader
from flight_engine.ingestion.updater import FlightUpdater
from test_engine import flight


def test_wal_reader_snapshot_during_separate_process_write(tmp_path):
    updater = FlightUpdater(tmp_path / 'flights.db')
    updater.initialize()
    store = FlightReader(updater.path)
    record = flight('1', 'AC1', 'YYZ', 'YUL', '2026-10-10T10:00:00+00:00', '2026-10-10T11:00:00+00:00')
    updater.upsert([record])
    with store.connect() as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT COUNT(*) FROM flights').fetchone()[0] == 1
        second = record.model_copy(update={'provider_id': '2'})
        code = '''import sys
from pathlib import Path
from flight_engine.core.models import FlightRecord
from flight_engine.server.reader import FlightReader
from flight_engine.ingestion.updater import FlightUpdater
updater = FlightUpdater(Path(sys.argv[1]))
updater.initialize()
updater.upsert([FlightRecord.model_validate_json(sys.argv[2])])
'''
        subprocess.run([sys.executable, '-c', code, str(store.path), second.model_dump_json()],
                       check=True, timeout=15)
        assert reader.execute('SELECT COUNT(*) FROM flights').fetchone()[0] == 1
    assert store.stats()['flights'] == 2
    assert store.stats()['journal_mode'] == 'wal'


def test_failed_batch_leaves_existing_records_unchanged(tmp_path):
    import pytest
    updater = FlightUpdater(tmp_path / 'flights.db')
    updater.initialize()
    store = FlightReader(updater.path)
    record = flight('1', 'AC1', 'YYZ', 'YUL', '2026-10-10T10:00:00+00:00', '2026-10-10T11:00:00+00:00')
    updater.upsert([record])
    with pytest.raises(ValueError):
        updater.upsert([record.model_copy(update={'provider_id': '2'}),
                      record.model_copy(update={'destination_iata': 'YYZ'})])
    assert store.all_records() == [record]


def test_reader_cannot_write_or_create_database(tmp_path):
    import sqlite3
    import pytest
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        FlightReader(missing).stats()
    assert not missing.exists()
    updater = FlightUpdater(tmp_path / 'flights.db')
    updater.initialize()
    reader = FlightReader(updater.path)
    assert not hasattr(reader, 'upsert')
    assert not hasattr(reader, 'reset')
    with reader.connect() as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('DELETE FROM flights')


def test_serving_api_only_exposes_reads(tmp_path):
    from fastapi.testclient import TestClient
    from flight_engine.server.api import app, get_store
    updater = FlightUpdater(tmp_path / 'flights.db')
    updater.initialize()
    app.dependency_overrides[get_store] = lambda: FlightReader(updater.path)
    try:
        with TestClient(app) as client:
            assert client.get('/v1/flights', params={
                'start': '2026-10-10T00:00:00Z', 'end': '2026-10-11T00:00:00Z'}).json() == []
            assert client.post('/v1/admin/ingestions').status_code == 404
    finally:
        app.dependency_overrides.clear()
