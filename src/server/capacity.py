"""Bound expensive API work without queuing it ahead of health and static requests."""

from threading import BoundedSemaphore
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

from core.config import get_settings


class ReadCapacity:
    def __init__(self, app, *, limit=None):
        self.app = app
        self.slots = BoundedSemaphore(
            get_settings().max_concurrent_reads if limit is None else limit
        )
        self.statistics_slot = BoundedSemaphore(1)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        statistics = scope["path"] == "/v1/catalog"
        if scope["path"] == "/v1/search-availability":
            params = parse_qs(
                scope.get("query_string", b"").decode("ascii", "replace"), keep_blank_values=True
            )
            statistics = params.get("include_counts", ["true"])[-1].lower() not in (
                "false",
                "0",
                "off",
                "no",
            )
        # Optional exact totals share one slot, outside interactive capacity.
        slots = self.statistics_slot if statistics else self.slots
        if not slots.acquire(blocking=False):
            await JSONResponse(
                {
                    "detail": {
                        "code": "server_busy",
                        "message": "Server is busy. Try again shortly.",
                    }
                },
                status_code=503,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            slots.release()
