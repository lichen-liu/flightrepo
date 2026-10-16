"""Serving admission is separate from source evidence and manual verification."""


def requires_review(reasons, reviewed=False, route_verified=False):
    allowed = {"airborne_airport_estimate"}
    if route_verified:
        allowed.add("observation_gap")
    return not reviewed and any(reason not in allowed for reason in reasons)


def route_verified_sql(alias=""):
    p = alias + "." if alias else ""
    v = p + "route_verification"
    return (
        f"COALESCE((json_extract({v},'$.source')='vrs' "
        f"AND json_extract({v},'$.match_kind') IN ('exact','adjacent') "
        f"AND length(json_extract({v},'$.reference_sha256'))=64 "
        f"AND json_extract({v},'$.reference_sha256') NOT GLOB '*[^0-9a-f]*' "
        f"AND json_extract({v},'$.callsign')=upper(trim({p}callsign)) "
        f"AND json_extract({v},'$.origin_iata')={p}origin_iata "
        f"AND json_extract({v},'$.destination_iata')={p}destination_iata),0)"
    )


def review_required_sql(alias=""):
    prefix = alias + "." if alias else ""
    return (
        f"({prefix}itinerary_reviewed = 0 AND EXISTS "
        f"(SELECT 1 FROM json_each({prefix}review_reasons) AS reason "
        "WHERE reason.value IS NOT 'airborne_airport_estimate' "
        f"AND (reason.value IS NOT 'observation_gap' OR NOT ({route_verified_sql(alias)}))))"
    )


REVIEW_REQUIRED_SQL = review_required_sql()
ADMITTED_SQL = "NOT " + REVIEW_REQUIRED_SQL
