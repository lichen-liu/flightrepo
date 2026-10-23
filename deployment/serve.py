"""Serve an explicitly configured dataset; credentials stay in its tunnel job."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "fyi.boboji.fly"
MAX_REQUEST_BODY = 128_000


def read_settings(root: Path) -> dict:
    data = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("settings.json must contain an object.")
    hostname = data.get("hostname", "fly.boboji.fyi")
    if not isinstance(hostname, str):
        raise ValueError("Use a valid public hostname in settings.json.")
    hostname = hostname.strip().lower()
    if (
        len(hostname) > 253
        or "." not in hostname
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        )
    ):
        raise ValueError("Use a valid public hostname in settings.json.")
    port = data.get("port", 8014)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError("Use a port between 1024 and 65535 in settings.json.")
    database = data.get("database_path")
    if not isinstance(database, str) or not database or not Path(database).is_absolute():
        raise ValueError("database_path must be an absolute path in settings.json.")
    data_mode = data.get("data_mode", "synthetic")
    if data_mode not in ("synthetic", "real"):
        raise ValueError("data_mode must be synthetic or real")
    return {
        "hostname": hostname,
        "port": port,
        "database_path": str(Path(database).resolve()),
        "data_mode": data_mode,
    }


def verify_application_overlay(root: Path):
    """Reject a wheel replacement that silently discards recorded application fixes."""
    receipt = root / "installation.json"
    if not receipt.exists():
        return
    import server

    installed = Path(server.__file__).resolve().parent.parent
    overlay = json.loads(receipt.read_text()).get("application_overlay", {})
    for name, expected in overlay.get("files_sha256", {}).items():
        path = (installed / name).resolve()
        if not path.is_relative_to(installed):
            raise ValueError("Invalid application overlay path in installation receipt")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(
                f"Installed application differs from its deployment receipt: {name}. "
                "Install a build containing the recorded application fixes before starting."
            )


def validate_database(database: Path, *, data_mode="synthetic") -> int:
    from server.reader import FlightReader

    stats = FlightReader(database, serving_only=True).stats()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        schema = connection.execute(
            "SELECT contract,version FROM schema_metadata WHERE id=1"
        ).fetchone()
    if schema != ("flight-legs", 1):
        raise ValueError("Hosted database requires the shared flight-legs schema version 1")
    if data_mode == "synthetic" and (not stats["flights"] or set(stats["providers"]) != {"demo"}):
        raise ValueError(
            "Hosted preview requires a nonempty database containing only demo providers."
        )
    if data_mode == "real" and "demo" in stats["providers"]:
        raise ValueError("Real serving mode cannot contain demo flight records")
    return stats["flights"]


def seed_database(root: Path) -> dict:
    """Publish an absent DB atomically. Existing facts are never refreshed or replaced."""
    from core.connections import Airport, AirportTransfer
    from core.models import FlightRecord
    from ingestion.updater import FlightUpdater

    settings = read_settings(root)
    database = Path(settings["database_path"])
    if database.exists() or database.is_symlink():
        return {
            "database_path": str(database),
            "seeded": False,
            "flight_count": validate_database(database, data_mode=settings["data_mode"]),
        }
    if settings["data_mode"] == "real":
        raise ValueError(
            "Real mode never seeds demo data; initialize the selected database with admin first"
        )
    data = json.loads((root / "synthetic-data.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
        not isinstance(data.get(field), list) for field in ("flights", "airports", "transfers")
    ):
        raise ValueError("synthetic-data.json must contain the installed demo fixture.")
    flights = [FlightRecord.model_validate(row) for row in data["flights"]]
    if not flights or any(f.provider != "demo" for f in flights):
        raise ValueError("Synthetic seed must contain only nonempty demo flight records.")
    if len({(f.provider, f.provider_id) for f in flights}) != len(flights):
        raise ValueError("Synthetic seed must contain distinct flight identities.")
    airports = [Airport.model_validate(row) for row in data["airports"]]
    transfers = [AirportTransfer.model_validate(row) for row in data["transfers"]]
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".flyji-seed-", dir=database.parent) as temporary:
        staged = Path(temporary) / "flights.db"
        updater = FlightUpdater(staged)
        updater.initialize()
        updater.update_airports(airports, transfers)
        updater.upsert(flights)
        with updater.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        staged.chmod(0o600)
        validate_database(staged)
        try:
            # Same-filesystem hard link is atomic and refuses an existing destination.
            os.link(staged, database)
        except FileExistsError:
            return {
                "database_path": str(database),
                "seeded": False,
                "flight_count": validate_database(database),
            }
    return {"database_path": str(database), "seeded": True, "flight_count": len(flights)}


class BoundedBody:
    """Cap actual streamed bytes and total body-read time, including chunked requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None:
            try:
                declared = int(length)
                if declared < 0:
                    raise ValueError
            except ValueError:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                    scope, receive, send
                )
                return
            if declared > MAX_REQUEST_BODY:
                await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                    scope, receive, send
                )
                return
        body = bytearray()
        too_large = False
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_REQUEST_BODY:
                        too_large = True
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await JSONResponse({"detail": "Request body timed out"}, status_code=408)(
                scope, receive, send
            )
            return
        if too_large:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                scope, receive, send
            )
            return
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


def create_server(root: Path, *, verify_receipt=True, monitoring=True):
    from fastapi import FastAPI
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.routing import Mount

    from server.api import app as original
    from server.api import get_store
    from server.reader import FlightReader

    if verify_receipt:
        verify_application_overlay(root)
    settings = read_settings(root)
    database = Path(settings["database_path"])
    validate_database(database, data_mode=settings["data_mode"])
    app = FastAPI(title=original.title, description=original.description, version=original.version)
    app.user_middleware = list(original.user_middleware)
    app.exception_handlers = dict(original.exception_handlers)
    reader = FlightReader(database, serving_only=True)
    app.dependency_overrides[get_store] = lambda: reader
    # include_router binds overrides to this app without changing original routes/caches.
    app.include_router(original.router)
    app.router.routes.extend(route for route in original.routes if isinstance(route, Mount))
    wrapped = TrustedHostMiddleware(
        BoundedBody(app),
        allowed_hosts=[settings["hostname"], "localhost", "127.0.0.1", "::1", "[::1]"],
        www_redirect=False,
    )
    if monitoring:
        from server.monitoring import wrap_application

        routes = tuple(route.path for route in original.routes if hasattr(route, "response_model"))
        # The adapter deliberately bounds endpoint dimensions; remaining templates
        # and unknown routes still count toward the aggregate unmatched category.
        receipt = root / "installation.json"
        revision = (
            json.loads(receipt.read_text()).get("source_commit", "unknown")
            if receipt.exists()
            else "unknown"
        )
        wrapped = wrap_application(
            wrapped, root / "settings.json", routes=routes[:20], revision=revision
        )
    options = {
        "host": "127.0.0.1",
        "port": settings["port"],
        "workers": 1,
        "reload": False,
        "limit_concurrency": 32,
        "backlog": 32,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 15,
        "h11_max_incomplete_event_size": 16_384,
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1",
        "server_header": False,
        "access_log": False,
        "ws": "none",
    }
    return wrapped, options


async def check_installation(root: Path) -> None:
    import httpx

    # The installer validates its new wheel before replacing the prior receipt.
    app, _ = create_server(
        root, verify_receipt=not (root / "installing").exists(), monitoring=False
    )
    settings = read_settings(root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        for path in (
            "/",
            "/assets/app.css",
            "/assets/app.mjs",
            "/assets/quality.mjs",
            "/assets/map.mjs",
            "/assets/vendor/d3-7.9.0.min.js",
            "/assets/vendor/world-110m.geojson",
            "/health/ready",
        ):
            response = await client.get(path)
            if response.status_code != 200:
                raise RuntimeError("The installed application, database or assets are unavailable.")
        if settings["data_mode"] == "real":
            catalog = (await client.get("/v1/catalog")).json()
            dataset = catalog["dataset"]
            if not any(dataset["time_bases"].values()):
                return
            route = catalog["suggested_route"]
            first = datetime.fromisoformat(dataset["first_departure"].replace("Z", "+00:00"))
            start = first.replace(hour=0, minute=0, second=0, microsecond=0)
            horizon = (start + timedelta(days=7)).isoformat()
            response = await client.post(
                "/v1/itineraries/search",
                json={
                    "origin": route["origin_iata"],
                    "destination": route["destination_iata"],
                    "departure_start": start.isoformat(),
                    "departure_end": (start + timedelta(days=2)).isoformat(),
                    "horizon_end": horizon,
                    "time_basis": dataset["default_time_basis"],
                },
            )
            if response.status_code != 200 or not response.json().get("items"):
                raise RuntimeError("The installed real itinerary search failed")
            leg = response.json()["items"][0]["legs"][0]
            response = await client.post(
                "/v1/plans/rebuild",
                json={
                    "prefix": [{"flight_id": leg["flight_id"]}],
                    "destination": route["destination_iata"],
                    "horizon_end": horizon,
                    "time_basis": dataset["default_time_basis"],
                },
            )
            if response.status_code != 200 or not response.json().get("complete"):
                raise RuntimeError("The installed real itinerary rebuild failed")
            return
        response = await client.post(
            "/v1/itineraries/search",
            json={
                "origin": "YYZ",
                "destination": "CDG",
                "via_airport": "AMS",
                "departure_start": "2026-10-17T00:00:00Z",
                "departure_end": "2026-10-18T00:00:00Z",
                "horizon_end": "2026-10-24T00:00:00Z",
                "allow_airport_change": True,
            },
        )
        if response.status_code != 200 or not response.json()["items"]:
            raise RuntimeError("The installed constrained synthetic route search failed.")
        items = response.json()["items"]
        if any(
            "AMS"
            not in {f["destination_iata"] for f in item["legs"][:-1]}
            | {f["origin_iata"] for f in item["legs"][1:]}
            for item in items
        ):
            raise RuntimeError("The installed route search did not retain its required airport.")
        first = items[0]["legs"][0]
        response = await client.post(
            "/v1/plans/rebuild",
            json={
                "prefix": [{"flight_id": first["flight_id"]}],
                "destination": "CDG",
                "via_airport": "AMS",
                "next_airport": "AMS",
                "horizon_end": "2026-10-24T00:00:00Z",
                "allow_airport_change": True,
            },
        )
        if response.status_code != 200 or not response.json()["complete"]:
            raise RuntimeError("The installed synthetic connection calculation failed.")
        plan = response.json()
        if (
            len(plan["legs"]) < 2
            or plan["legs"][0] != first
            or plan["legs"][1]["destination_iata"] != "AMS"
        ):
            raise RuntimeError(
                "The installed connection calculation did not retain its flight choices."
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the installed Flyji dataset.")
    default_root = (
        RUNTIME_ROOT
        if Path(__file__).resolve().parent == REPO / "deployment"
        else Path(__file__).resolve().parent
    )
    parser.add_argument(
        "--root", type=Path, default=default_root, help="installed runtime directory"
    )
    parser.add_argument(
        "--seed", action="store_true", help="seed only an absent synthetic database"
    )
    parser.add_argument(
        "--check", action="store_true", help="check installed application without listening"
    )
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.seed:
            result = seed_database(args.root)
            print(f"Database ready: {result['flight_count']} flights; existing facts preserved.")
        if args.check:
            asyncio.run(check_installation(args.root))
            print("Installed application, assets and routing are ready.")
        if args.seed or args.check:
            return
        import uvicorn

        app, options = create_server(args.root)
        uvicorn.run(app, **options)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        raise SystemExit(f"Flyji server failed: {error}") from error


if __name__ == "__main__":
    main()
