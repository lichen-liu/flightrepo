"""Explicit semantic checks in addition to SQLite's physical integrity checks."""

from core.storage import TIME_FIELDS

# Match the public FlightRecord contract without imposing aircraft/callsign lists.
FACT_VALID_SQL = """
length(trim(source_record_key))>0
AND origin_iata GLOB '[A-Z][A-Z][A-Z]' AND destination_iata GLOB '[A-Z][A-Z][A-Z]'
AND origin_iata<>destination_iata
AND status IN ('unknown','scheduled','active','landed','cancelled')
AND evidence_kind IN ('source_reported','inferred_ground_brackets','inferred_airport_proximity','manual')
AND itinerary_reviewed IN (0,1) AND revision>=1 AND last_seen_us>=first_seen_us
AND (flight_number IS NULL OR length(trim(flight_number))>0)
AND (callsign IS NULL OR length(trim(callsign))>0)
AND (airline_iata IS NULL OR airline_iata GLOB '[A-Z0-9][A-Z0-9]')
AND (aircraft_type_code IS NULL OR (length(aircraft_type_code) BETWEEN 2 AND 8 AND aircraft_type_code NOT GLOB '*[^A-Z0-9]*'))
AND (departure_scheduled_us IS NULL OR arrival_scheduled_us IS NULL OR departure_scheduled_us<arrival_scheduled_us)
AND (departure_actual_us IS NULL OR arrival_actual_us IS NULL OR departure_actual_us<arrival_actual_us)
AND ((departure_actual_us IS NULL AND departure_actual_kind IS NULL) OR (departure_actual_us IS NOT NULL AND departure_actual_kind IN ('gate_out','takeoff','unknown')))
AND ((arrival_actual_us IS NULL AND arrival_actual_kind IS NULL) OR (arrival_actual_us IS NOT NULL AND arrival_actual_kind IN ('gate_in','landing','unknown')))
AND ((departure_observed_lower_us IS NULL AND departure_observed_upper_us IS NULL AND arrival_observed_lower_us IS NULL AND arrival_observed_upper_us IS NULL)
 OR (departure_observed_lower_us IS NOT NULL AND departure_observed_upper_us IS NOT NULL AND arrival_observed_lower_us IS NOT NULL AND arrival_observed_upper_us IS NOT NULL
 AND departure_observed_lower_us<=departure_observed_upper_us AND departure_observed_upper_us<arrival_observed_lower_us AND arrival_observed_lower_us<=arrival_observed_upper_us))
AND (departure_scheduled_us IS NOT NULL OR arrival_scheduled_us IS NOT NULL OR departure_actual_us IS NOT NULL OR arrival_actual_us IS NOT NULL OR departure_observed_lower_us IS NOT NULL)
AND (evidence_kind NOT IN ('inferred_ground_brackets','inferred_airport_proximity') OR departure_observed_lower_us IS NOT NULL)
AND CASE WHEN json_valid(review_reasons) THEN json_type(review_reasons)='array' ELSE 0 END
"""
FACT_VALID_SQL += "".join(
    f" AND ({field}_us IS NULL OR {field}_us BETWEEN -62135596800000000 AND 253402300799999999)"
    for field in TIME_FIELDS
)


def semantic_health(connection):
    invalid_facts = connection.execute(
        f"SELECT COUNT(*) FROM flight_legs WHERE COALESCE(({FACT_VALID_SQL}),0)=0"
    ).fetchone()[0]
    invalid_airports = connection.execute("""SELECT COUNT(*) FROM airports WHERE COALESCE((iata GLOB '[A-Z][A-Z][A-Z]'
        AND ((latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))),0)=0""").fetchone()[
        0
    ]
    invalid_transfers = connection.execute("""SELECT COUNT(*) FROM airport_transfers t JOIN airports a ON a.iata=t.arrival_iata JOIN airports b ON b.iata=t.departure_iata
        WHERE a.city_id IS NULL OR b.city_id IS NULL OR a.city_id<>b.city_id OR t.arrival_iata=t.departure_iata OR t.minimum_minutes NOT BETWEEN 1 AND 1440""").fetchone()[
        0
    ]
    return dict(
        invalid_flight_contract_records=invalid_facts,
        invalid_airport_records=invalid_airports,
        invalid_transfer_records=invalid_transfers,
    )


def require_semantic_health(connection):
    health = semantic_health(connection)
    if any(health.values()):
        raise ValueError("Retained flight/airport contract validation failed: " + str(health))
    return health
