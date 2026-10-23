"""Resolve local wall clocks with explicit DST semantics; no persistence or I/O."""

from datetime import UTC, datetime

from core.routing.models import RoutingError
from core.tzdb import zone as ZoneInfo


def resolve_local(
    value: datetime,
    zone_name: str,
    *,
    fold: int | None = None,
    field: str = "local_time",
) -> datetime:
    if value.tzinfo is not None:
        raise RoutingError(
            "local_time_has_offset",
            "Local time must not contain a UTC offset.",
            context={"field": field, "timezone": zone_name},
        )
    if fold not in (None, 0, 1):
        raise ValueError("fold must be 0 or 1")
    zone = ZoneInfo(zone_name)
    candidates = {}
    for choice in (0, 1):
        try:
            instant = value.replace(tzinfo=zone, fold=choice).astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise RoutingError(
                "local_time_out_of_range",
                "Local time cannot be represented in UTC.",
                context={"field": field, "timezone": zone_name},
            ) from error
        # Round-trip rejects nonexistent spring-forward clocks. Dedup ordinary clocks.
        if instant.astimezone(zone).replace(tzinfo=None) == value:
            candidates[choice] = instant
    distinct = sorted(set(candidates.values()))
    context = {"field": field, "timezone": zone_name, "local_time": value.isoformat()}
    if not distinct:
        raise RoutingError(
            "nonexistent_local_time",
            "This local time does not exist because the clock changes.",
            context=context,
        )
    if len(distinct) > 1 and fold is None:
        raise RoutingError(
            "ambiguous_local_time",
            "This local time occurs twice. Choose the earlier or later occurrence.",
            context={**context, "utc_choices": [x.isoformat() for x in distinct]},
        )
    return candidates[fold] if fold is not None else distinct[0]
