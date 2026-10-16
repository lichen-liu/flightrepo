"""Serving admission is separate from source evidence and manual verification."""

# Explicitly approved confidence categories; future unknown reasons fail closed.
SEARCHABLE_UNCERTAINTY_REASONS = frozenset(
    {
        "airborne_airport_estimate",
        "observation_gap",
        "callsign_variation",
        "implausible_airport_timing",
    }
)


def blocked_for_search(reasons, reviewed=False):
    return not reviewed and any(reason not in SEARCHABLE_UNCERTAINTY_REASONS for reason in reasons)


def route_verified_sql(alias=""):
    p = alias + "." if alias else ""
    v = p + "route_verification"
    return (
        f"CASE WHEN json_extract({v},'$.source')='vrs' "
        f"AND json_extract({v},'$.match_kind') IN ('exact','adjacent') "
        f"AND length(json_extract({v},'$.reference_sha256'))=64 "
        f"AND json_extract({v},'$.reference_sha256') NOT GLOB '*[^0-9a-f]*' "
        f"AND json_extract({v},'$.callsign')=upper(trim({p}callsign)) "
        f"AND json_extract({v},'$.origin_iata')={p}origin_iata "
        f"AND json_extract({v},'$.destination_iata')={p}destination_iata THEN 1 ELSE 0 END"
    )


def search_blocked_sql(alias=""):
    p = alias + "." if alias else ""
    allowed = ",".join("'" + reason + "'" for reason in sorted(SEARCHABLE_UNCERTAINTY_REASONS))
    return (
        f"({p}itinerary_reviewed=0 AND EXISTS (SELECT 1 FROM json_each({p}review_reasons) AS reason "
        f"WHERE reason.value IS NULL OR reason.value NOT IN ({allowed})))"
    )


SEARCH_BLOCKED_SQL = search_blocked_sql()
ADMITTED_SQL = "NOT " + SEARCH_BLOCKED_SQL
