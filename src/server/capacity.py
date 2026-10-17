"""Bound expensive API work without queuing it ahead of health and static requests."""

from threading import BoundedSemaphore

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
        # Whole-dataset statistics must not consume the interactive read budget.
        slots = self.statistics_slot if scope["path"] == "/v1/catalog" else self.slots
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
