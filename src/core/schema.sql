-- Shared flight-leg contract. Initialized only by an explicit writer.
-- All *_us values are signed INTEGER Unix microseconds in UTC.
PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS schema_metadata (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    contract TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0)
) STRICT;
INSERT OR IGNORE INTO schema_metadata VALUES (1, 'flight-legs', 1);

CREATE TABLE IF NOT EXISTS database_policy (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    quota_bytes INTEGER CHECK(quota_bytes >= 0)
) STRICT;
INSERT OR IGNORE INTO database_policy VALUES (1, 21474836480);

CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK(length(trim(code)) > 0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    source_url TEXT,
    license_expression TEXT,
    license_url TEXT,
    attribution TEXT
) STRICT;

-- A stub airport with just its code is valid. Missing metadata stays NULL.
CREATE TABLE IF NOT EXISTS airports (
    iata TEXT PRIMARY KEY CHECK(iata GLOB '[A-Z][A-Z][A-Z]'),
    name TEXT,
    city_id TEXT,
    timezone TEXT,
    latitude REAL CHECK(latitude BETWEEN -90 AND 90),
    longitude REAL CHECK(longitude BETWEEN -180 AND 180),
    metadata_source_id INTEGER REFERENCES data_sources(id),
    metadata_updated_us INTEGER,
    CHECK((latitude IS NULL) = (longitude IS NULL))
) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS airports_city ON airports(city_id, iata) WHERE city_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS airport_transfers (
    arrival_iata TEXT NOT NULL REFERENCES airports(iata),
    departure_iata TEXT NOT NULL REFERENCES airports(iata),
    minimum_minutes INTEGER NOT NULL CHECK(minimum_minutes BETWEEN 1 AND 1440),
    source TEXT NOT NULL CHECK(length(trim(source)) > 0),
    PRIMARY KEY(arrival_iata, departure_iata),
    CHECK(arrival_iata <> departure_iata)
) STRICT, WITHOUT ROWID;

-- One attempt per requested input day, or per bounded non-archive import.
-- Failed attempts are observable but never count as completed source coverage.
CREATE TABLE IF NOT EXISTS ingestion_batches (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    input_start_us INTEGER NOT NULL,
    input_end_us INTEGER NOT NULL,
    started_us INTEGER NOT NULL,
    finished_us INTEGER,
    state TEXT NOT NULL CHECK(state IN ('running', 'committed', 'failed', 'interrupted')),
    pipeline_version TEXT NOT NULL,
    config_sha256 TEXT NOT NULL CHECK(
        length(config_sha256) = 64 AND config_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    config_json TEXT NOT NULL CHECK(json_valid(config_json)),
    inputs_json TEXT NOT NULL CHECK(json_valid(inputs_json)),
    diagnostics_json TEXT NOT NULL CHECK(json_valid(diagnostics_json)),
    inserted_count INTEGER NOT NULL DEFAULT 0 CHECK(inserted_count >= 0),
    updated_count INTEGER NOT NULL DEFAULT 0 CHECK(updated_count >= 0),
    duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_count >= 0),
    error_summary TEXT,
    UNIQUE(id, source_id),
    CHECK(input_start_us < input_end_us),
    CHECK(finished_us IS NULL OR finished_us >= started_us),
    CHECK((state = 'running' AND finished_us IS NULL) OR
          (state <> 'running' AND finished_us IS NOT NULL)),
    CHECK(state = 'committed' OR (inserted_count = 0 AND updated_count = 0))
) STRICT;
CREATE INDEX IF NOT EXISTS ingestion_batches_coverage
ON ingestion_batches(source_id, input_start_us, input_end_us)
WHERE state = 'committed';

-- One source assertion about one origin-to-destination movement, not a journey,
-- an aircraft-day, or a globally deduplicated physical flight.
CREATE TABLE IF NOT EXISTS flight_legs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    source_record_key TEXT NOT NULL CHECK(length(trim(source_record_key)) > 0),
    origin_iata TEXT NOT NULL REFERENCES airports(iata),
    destination_iata TEXT NOT NULL REFERENCES airports(iata),
    flight_number TEXT CHECK(flight_number IS NULL OR length(trim(flight_number)) > 0),
    airline_iata TEXT CHECK(airline_iata IS NULL OR airline_iata GLOB '[A-Z0-9][A-Z0-9]'),
    callsign TEXT CHECK(callsign IS NULL OR length(trim(callsign)) > 0),
    aircraft_type_code TEXT CHECK(aircraft_type_code IS NULL OR (
        length(aircraft_type_code) BETWEEN 2 AND 8 AND
        aircraft_type_code NOT GLOB '*[^A-Z0-9]*'
    )),
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(status IN ('unknown', 'scheduled', 'active', 'landed', 'cancelled')),
    evidence_kind TEXT NOT NULL
        CHECK(evidence_kind IN ('source_reported', 'inferred_ground_brackets', 'inferred_airport_proximity', 'manual')),
    review_reasons TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(review_reasons) AND json_type(review_reasons) = 'array'),
    itinerary_reviewed INTEGER NOT NULL DEFAULT 0 CHECK(itinerary_reviewed IN (0,1)),
    route_verification TEXT CHECK(route_verification IS NULL OR
        (json_valid(route_verification) AND json_type(route_verification)='object')),
    departure_scheduled_us INTEGER,
    arrival_scheduled_us INTEGER,
    departure_actual_us INTEGER,
    arrival_actual_us INTEGER,
    departure_actual_kind TEXT
        CHECK(departure_actual_kind IN ('gate_out', 'takeoff', 'unknown')),
    arrival_actual_kind TEXT
        CHECK(arrival_actual_kind IN ('gate_in', 'landing', 'unknown')),
    departure_observed_lower_us INTEGER,
    departure_observed_upper_us INTEGER,
    arrival_observed_lower_us INTEGER,
    arrival_observed_upper_us INTEGER,
    first_seen_us INTEGER NOT NULL,
    last_seen_us INTEGER NOT NULL,
    source_updated_us INTEGER,
    first_batch_id INTEGER,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    locally_modified_us INTEGER,
    UNIQUE(source_id, source_record_key),
    FOREIGN KEY(first_batch_id, source_id) REFERENCES ingestion_batches(id, source_id),
    CHECK(origin_iata <> destination_iata),
    CHECK(last_seen_us >= first_seen_us),
    CHECK(departure_scheduled_us IS NULL OR arrival_scheduled_us IS NULL OR
          departure_scheduled_us < arrival_scheduled_us),
    CHECK(departure_actual_us IS NULL OR arrival_actual_us IS NULL OR
          departure_actual_us < arrival_actual_us),
    CHECK((departure_actual_us IS NULL AND departure_actual_kind IS NULL) OR
          (departure_actual_us IS NOT NULL AND departure_actual_kind IS NOT NULL)),
    CHECK((arrival_actual_us IS NULL AND arrival_actual_kind IS NULL) OR
          (arrival_actual_us IS NOT NULL AND arrival_actual_kind IS NOT NULL)),
    CHECK(
        (departure_observed_lower_us IS NULL AND departure_observed_upper_us IS NULL AND
         arrival_observed_lower_us IS NULL AND arrival_observed_upper_us IS NULL) OR
        (departure_observed_lower_us IS NOT NULL AND departure_observed_upper_us IS NOT NULL AND
         arrival_observed_lower_us IS NOT NULL AND arrival_observed_upper_us IS NOT NULL AND
         departure_observed_lower_us <= departure_observed_upper_us AND
         departure_observed_upper_us < arrival_observed_lower_us AND
         arrival_observed_lower_us <= arrival_observed_upper_us)
    ),
    -- Partial schedule/actual facts may be stored, but are not route-searchable.
    CHECK(departure_scheduled_us IS NOT NULL OR arrival_scheduled_us IS NOT NULL OR
          departure_actual_us IS NOT NULL OR arrival_actual_us IS NOT NULL OR
          departure_observed_lower_us IS NOT NULL),
    CHECK(evidence_kind NOT IN ('inferred_ground_brackets', 'inferred_airport_proximity') OR
          departure_observed_lower_us IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS flight_codeshares (
    leg_id INTEGER NOT NULL REFERENCES flight_legs(id) ON DELETE CASCADE,
    flight_number TEXT NOT NULL CHECK(length(trim(flight_number)) > 0),
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
    PRIMARY KEY(leg_id, flight_number)
) STRICT, WITHOUT ROWID;

-- Preserve deletion intent when an upstream chunk/day is fetched again.
CREATE TABLE IF NOT EXISTS flight_tombstones (
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    source_record_key TEXT NOT NULL CHECK(length(trim(source_record_key)) > 0),
    deleted_us INTEGER NOT NULL,
    PRIMARY KEY(source_id, source_record_key)
) STRICT, WITHOUT ROWID;

-- Two partial indexes per time basis: onward routing and global date windows.
-- Empty bases have no index entries. Do not index every possible filter.
CREATE INDEX IF NOT EXISTS flight_legs_scheduled_origin
ON flight_legs(origin_iata, departure_scheduled_us)
WHERE status <> 'cancelled' AND departure_scheduled_us IS NOT NULL
    AND arrival_scheduled_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_scheduled_departure
ON flight_legs(departure_scheduled_us)
WHERE status <> 'cancelled' AND departure_scheduled_us IS NOT NULL
    AND arrival_scheduled_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_actual_origin
ON flight_legs(origin_iata, departure_actual_us)
WHERE status <> 'cancelled' AND departure_actual_us IS NOT NULL
    AND arrival_actual_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_actual_departure
ON flight_legs(departure_actual_us)
WHERE status <> 'cancelled' AND departure_actual_us IS NOT NULL
    AND arrival_actual_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_observed_origin
ON flight_legs(origin_iata, departure_observed_lower_us)
WHERE status <> 'cancelled' AND departure_observed_lower_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_observed_departure
ON flight_legs(departure_observed_lower_us)
WHERE status <> 'cancelled' AND departure_observed_lower_us IS NOT NULL;

-- A common interval contract. Consumers select exactly one time_basis per query;
-- a leg with several kinds of evidence must not become several returned flights.
-- Cancelled/deleted flights remain auditable but cannot power routing/calendar.
CREATE VIEW IF NOT EXISTS searchable_legs AS
SELECT f.id AS leg_id, f.source_id, f.source_record_key,
       f.origin_iata, f.destination_iata, f.aircraft_type_code,
       'scheduled' AS time_basis,
       f.departure_scheduled_us AS departure_lower_us,
       f.departure_scheduled_us AS departure_upper_us,
       f.arrival_scheduled_us AS arrival_lower_us,
       f.arrival_scheduled_us AS arrival_upper_us,
       'scheduled_departure' AS departure_event,
       'scheduled_arrival' AS arrival_event
FROM flight_legs f
WHERE NOT (f.itinerary_reviewed=0 AND EXISTS (SELECT 1 FROM json_each(f.review_reasons) AS reason WHERE reason.value IS NULL OR reason.value NOT IN ('airborne_airport_estimate','callsign_variation','implausible_airport_timing','observation_gap')))
    AND f.status <> 'cancelled' AND f.departure_scheduled_us IS NOT NULL
    AND f.arrival_scheduled_us IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM flight_tombstones t
                    WHERE t.source_id = f.source_id AND t.source_record_key = f.source_record_key)
UNION ALL
SELECT f.id, f.source_id, f.source_record_key,
       f.origin_iata, f.destination_iata, f.aircraft_type_code,
       'actual', f.departure_actual_us, f.departure_actual_us,
       f.arrival_actual_us, f.arrival_actual_us,
       f.departure_actual_kind, f.arrival_actual_kind
FROM flight_legs f
WHERE NOT (f.itinerary_reviewed=0 AND EXISTS (SELECT 1 FROM json_each(f.review_reasons) AS reason WHERE reason.value IS NULL OR reason.value NOT IN ('airborne_airport_estimate','callsign_variation','implausible_airport_timing','observation_gap')))
    AND f.status <> 'cancelled' AND f.departure_actual_us IS NOT NULL
    AND f.arrival_actual_us IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM flight_tombstones t
                    WHERE t.source_id = f.source_id AND t.source_record_key = f.source_record_key)
UNION ALL
SELECT f.id, f.source_id, f.source_record_key,
       f.origin_iata, f.destination_iata, f.aircraft_type_code,
       'observed', f.departure_observed_lower_us, f.departure_observed_upper_us,
       f.arrival_observed_lower_us, f.arrival_observed_upper_us,
       'ground_to_air_transition', 'air_to_ground_transition'
FROM flight_legs f
WHERE NOT (f.itinerary_reviewed=0 AND EXISTS (SELECT 1 FROM json_each(f.review_reasons) AS reason WHERE reason.value IS NULL OR reason.value NOT IN ('airborne_airport_estimate','callsign_variation','implausible_airport_timing','observation_gap')))
    AND f.status <> 'cancelled' AND f.departure_observed_lower_us IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM flight_tombstones t
                    WHERE t.source_id = f.source_id AND t.source_record_key = f.source_record_key);
