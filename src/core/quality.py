"""Shared labels distinguish provenance notes, uncertainty and conflicting evidence."""


def describe_quality(
    *,
    reasons,
    evidence_kind,
    reference_result=None,
    reviewed=False,
    observed=False,
    aircraft_type=None,
    provider=None,
):
    labels = []

    def add(code, severity, label, detail):
        labels.append(dict(code=code, severity=severity, label=label, detail=detail))

    matched = reference_result in {"exact", "adjacent"}
    if "implausible_airport_timing" in reasons:
        add(
            "timing_conflict",
            "warning",
            "Timing conflict",
            "The retained airports and time window imply an implausible speed. This itinerary may be wrong.",
        )
    if "observation_gap" in reasons:
        add(
            "observation_gap",
            "info" if matched or reviewed else "caution",
            "Observation gap",
            "Observations have a gap over 30 minutes; an intermediate stop may be unobserved.",
        )
    if "callsign_variation" in reasons:
        add(
            "callsign_variation",
            "caution",
            "Callsign changed",
            "More than one callsign was observed; flight identity is uncertain.",
        )
    if reference_result == "different":
        add(
            "vrs_route_differs",
            "caution",
            "Reference route differs",
            "The undated VRS callsign association lists different airports. Historical route changes or inference errors are possible.",
        )
    if (
        "airborne_airport_estimate" in reasons or evidence_kind == "inferred_airport_proximity"
    ) and not matched:
        add(
            "estimated_airports",
            "caution",
            "Estimated airports",
            "At least one airport was inferred from airborne observations.",
        )
    if matched:
        add(
            "vrs_route_match",
            "info",
            "VRS route match",
            "The directed airport pair matches an undated VRS route. This does not verify historical timing or continuous flight.",
        )
    elif reference_result == "incomplete":
        add(
            "vrs_reference_incomplete",
            "info",
            "Reference incomplete",
            "Some airports in the VRS association could not be mapped; no directed match was established.",
        )
    elif reference_result == "missing":
        add(
            "vrs_reference_missing",
            "info",
            "No VRS reference",
            "VRS has no exact callsign association. Absence is not evidence that the flight is wrong.",
        )
    elif reference_result is None and provider == "adsblol":
        add(
            "vrs_not_checked",
            "info",
            "Reference not checked",
            "This record has not been audited against a pinned VRS snapshot.",
        )
    if observed:
        add(
            "observed_time_window",
            "info",
            "Observed time window",
            "Times are observation bounds, not exact takeoff or landing timestamps.",
        )
    if reviewed:
        add(
            "manually_reviewed",
            "info",
            "Manually reviewed",
            "A person explicitly reviewed this record. Source uncertainty and conflicts remain visible.",
        )
    if not aircraft_type and (observed or provider == "adsblol"):
        add(
            "aircraft_type_unknown",
            "info",
            "Aircraft type unknown",
            "No aircraft type code was retained.",
        )
    severity = max(
        ({"info": 0, "caution": 1, "warning": 2}[x["severity"]] for x in labels), default=-1
    )
    return dict(
        level={-1: "standard", 0: "noted", 1: "uncertain", 2: "conflict"}[severity], labels=labels
    )


def review_recommended_sql(alias=""):
    """Same uncertainty classes as describe_quality; independent of serving admission."""
    from core.admission import review_required_sql, route_verified_sql

    p = alias + "." if alias else ""
    v = p + "route_verification"
    current_difference = (
        f"json_extract({v},'$.source')='vrs' AND json_extract({v},'$.match_kind')='different' "
        f"AND json_extract({v},'$.callsign') IS nullif(upper(trim({p}callsign)),'') "
        f"AND json_extract({v},'$.origin_iata')={p}origin_iata "
        f"AND json_extract({v},'$.destination_iata')={p}destination_iata"
    )
    return (
        f"({review_required_sql(alias)} OR ({p}itinerary_reviewed=0 AND ("
        f"((({p}evidence_kind='inferred_airport_proximity' OR EXISTS(SELECT 1 FROM json_each({p}review_reasons) reason WHERE reason.value='airborne_airport_estimate')) "
        f"AND NOT ({route_verified_sql(alias)}))) OR ({current_difference}))))"
    )
