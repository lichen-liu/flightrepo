"""JSON replay documents; pure functions, no filesystem or database dependency."""

from core.connections import RebuildRequest
from core.planning import rebuild_from_snapshot
from core.routing.models import ENGINE_VERSION, RouteSearchRequest, RoutingError
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot


def replay_document(kind, request, snapshot, result):
    return {
        "format_version": 1,
        "engine_version": ENGINE_VERSION,
        "kind": kind,
        "request": request.model_dump(mode="json"),
        "snapshot": snapshot.to_data(),
        "result": result.model_dump(mode="json"),
    }


def replay(data):
    if not isinstance(data, dict):
        raise RoutingError("invalid_replay", "replay document must be a JSON object")
    if data.get("format_version") != 1 or data.get("engine_version") != ENGINE_VERSION:
        raise RoutingError("incompatible_replay", "replay format or engine version is incompatible")
    if data.get("kind") not in ("search", "rebuild"):
        raise RoutingError("invalid_replay_kind", "replay kind must be search or rebuild")
    missing = {"request", "snapshot", "result"} - data.keys()
    if missing:
        raise RoutingError(
            "invalid_replay",
            f"replay document is missing: {', '.join(sorted(missing))}",
        )
    snapshot = RoutingSnapshot.from_data(data["snapshot"])
    if data["kind"] == "search":
        result = search_routes(RouteSearchRequest.model_validate(data["request"]), snapshot)
    elif data["kind"] == "rebuild":
        result = rebuild_from_snapshot(RebuildRequest.model_validate(data["request"]), snapshot)
    return {
        "matches_saved_result": result.model_dump(mode="json") == data["result"],
        "result": result.model_dump(mode="json"),
    }
