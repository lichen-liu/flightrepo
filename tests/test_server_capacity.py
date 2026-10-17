import asyncio
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.capacity import ReadCapacity
from server.reader import FlightReader, ReadQueryTimeout


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_capacity_rejects_excess_work_keeps_health_available_and_releases_slots(outcome):
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def slow(scope, receive, send):
            if scope["path"] == "/health":
                await PlainTextResponse("ready")(scope, receive, send)
                return
            entered.set()
            await finish.wait()
            if outcome == "error":
                raise RuntimeError("read failed")
            await PlainTextResponse("done")(scope, receive, send)

        gate = ReadCapacity(slow, limit=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gate), base_url="http://test"
        ) as client:
            first = asyncio.create_task(client.get("/v1/itineraries/search"))
            await entered.wait()
            busy = await client.get("/v1/itineraries/search")
            assert busy.status_code == 503 and busy.headers["retry-after"] == "1"
            assert busy.json()["detail"]["code"] == "server_busy"
            assert (await client.get("/health")).status_code == 200
            if outcome == "cancel":
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            elif outcome == "error":
                finish.set()
                with pytest.raises(RuntimeError, match="read failed"):
                    await first
            else:
                finish.set()
                assert (await first).status_code == 200
            finish.set()
            if outcome == "error":
                with pytest.raises(RuntimeError, match="read failed"):
                    await client.get("/v1/itineraries/search")
            else:
                assert (await client.get("/v1/itineraries/search")).status_code == 200

    asyncio.run(run())


def test_statistics_have_their_own_capacity():
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def application(scope, receive, send):
            if scope["path"] == "/v1/catalog":
                entered.set()
                await finish.wait()
            await PlainTextResponse("ok")(scope, receive, send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ReadCapacity(application, limit=1)),
            base_url="http://test",
        ) as client:
            statistics = asyncio.create_task(client.get("/v1/catalog"))
            await entered.wait()
            assert (await client.get("/v1/catalog")).status_code == 503
            assert (await client.get("/v1/itineraries/search")).status_code == 200
            finish.set()
            assert (await statistics).status_code == 200

    asyncio.run(run())


def test_read_deadline_interrupts_sql_and_does_not_change_data(tmp_path):
    writer = FlightUpdater(tmp_path / "serving.db")
    writer.initialize()
    reader = FlightReader(writer.path, serving_only=True, query_timeout_seconds=0.001)
    with pytest.raises(ReadQueryTimeout):
        with reader.connect() as connection:
            connection.execute(
                "WITH RECURSIVE work(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM work "
                "WHERE n<10000000) SELECT SUM(n) FROM work"
            ).fetchone()
    assert reader.readiness()["status"] == "ready"
    with writer.connect() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with reader.connect() as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM airports")


def test_read_deadline_has_a_retryable_public_error():
    class SlowReader:
        def readiness(self):
            raise ReadQueryTimeout("internal SQL details")

    app.dependency_overrides[get_store] = SlowReader
    try:
        with TestClient(app) as client:
            result = client.get("/health/ready")
            assert result.status_code == 503
            assert result.headers["retry-after"] == "1"
            assert result.json()["detail"]["code"] == "query_timeout"
            assert "internal SQL" not in result.text
    finally:
        app.dependency_overrides.clear()
