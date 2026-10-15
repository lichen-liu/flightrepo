-- Pipeline-owned SQLite state. These tables never publish to serving.
CREATE TABLE IF NOT EXISTS ingestion_store_metadata (
    id INTEGER PRIMARY KEY CHECK(id=1),
    version INTEGER NOT NULL CHECK(version=1),
    store_id TEXT NOT NULL UNIQUE,
    generation INTEGER NOT NULL CHECK(generation>0)
) STRICT;
INSERT OR IGNORE INTO ingestion_store_metadata VALUES(1,1,lower(hex(randomblob(16))),1);

CREATE TABLE IF NOT EXISTS ingestion_profiles (
    source TEXT PRIMARY KEY,
    data TEXT NOT NULL CHECK(json_valid(data)),
    last_used INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS ingestion_documents (
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    kind TEXT NOT NULL CHECK(kind IN ('route','schedule','airport','aircraft','validation')),
    source_record_key TEXT NOT NULL CHECK(length(source_record_key)>0),
    source_group TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_us INTEGER NOT NULL,
    valid_from_us INTEGER,
    valid_to_us INTEGER,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    PRIMARY KEY(source_id,kind,source_record_key),
    CHECK(valid_from_us IS NULL OR valid_to_us IS NULL OR valid_from_us<valid_to_us)
) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ingestion_documents_validity
ON ingestion_documents(kind,valid_from_us,valid_to_us);

-- Private publication range index includes cancelled and incomplete facts.
CREATE INDEX IF NOT EXISTS ingestion_publication_time ON flight_legs (CASE
    WHEN departure_scheduled_us IS NOT NULL AND arrival_scheduled_us IS NOT NULL THEN departure_scheduled_us
    WHEN departure_observed_lower_us IS NOT NULL THEN departure_observed_lower_us
    WHEN departure_actual_us IS NOT NULL OR arrival_actual_us IS NOT NULL THEN COALESCE(departure_actual_us,arrival_actual_us)
    ELSE COALESCE(departure_scheduled_us,arrival_scheduled_us)
END);
