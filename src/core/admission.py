"""Serving admission is separate from estimated evidence and manual verification."""


def requires_review(reasons, reviewed=False):
    return not reviewed and any(reason != "airborne_airport_estimate" for reason in reasons)


def review_required_sql(alias=""):
    prefix = alias + "." if alias else ""
    return (
        f"({prefix}itinerary_reviewed = 0 AND EXISTS "
        f"(SELECT 1 FROM json_each({prefix}review_reasons) AS reason "
        "WHERE reason.value IS NOT 'airborne_airport_estimate'))"
    )


REVIEW_REQUIRED_SQL = review_required_sql()
ADMITTED_SQL = "NOT " + REVIEW_REQUIRED_SQL
