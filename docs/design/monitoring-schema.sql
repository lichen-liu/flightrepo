-- Design iteration 2: illustrative relational core, not a deployed migration.
-- SQLite-compatible ordinary SQL; server backends supply their own tested DDL.
-- Enable SQLite foreign_keys on every connection outside this portable schema.
-- All IDs are explicitly assigned strings; timestamps are UTC epoch milliseconds.

CREATE TABLE schema_migrations (
    version BIGINT PRIMARY KEY NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    applied_at_ms BIGINT NOT NULL
);

CREATE TABLE projects (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    title VARCHAR(256) NOT NULL,
    retired_at_ms BIGINT
);

CREATE TABLE sites (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    project_id VARCHAR(128) NOT NULL REFERENCES projects(id),
    title VARCHAR(256) NOT NULL,
    environment VARCHAR(64) NOT NULL,
    retired_at_ms BIGINT
);

CREATE INDEX sites_project ON sites(project_id, environment);

CREATE TABLE hosts (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    title VARCHAR(256) NOT NULL,
    retired_at_ms BIGINT
);

CREATE TABLE replicas (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    site_id VARCHAR(128) NOT NULL REFERENCES sites(id),
    host_id VARCHAR(128) NOT NULL REFERENCES hosts(id),
    slot VARCHAR(128) NOT NULL,
    expected_since_ms BIGINT NOT NULL,
    retired_at_ms BIGINT,
    UNIQUE (site_id, host_id, slot)
);

CREATE TABLE producer_runs (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    host_id VARCHAR(128) NOT NULL REFERENCES hosts(id),
    replica_id VARCHAR(128) REFERENCES replicas(id),
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('app', 'host', 'core')),
    revision VARCHAR(128) NOT NULL,
    adapter_version VARCHAR(64) NOT NULL,
    started_at_ms BIGINT NOT NULL,
    retired_at_ms BIGINT
    -- API validation also enforces replica/host membership and credential scope.
);

CREATE TABLE batches (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    run_id VARCHAR(128) NOT NULL REFERENCES producer_runs(id),
    sequence BIGINT NOT NULL CHECK (sequence >= 0),
    protocol_version BIGINT NOT NULL,
    observed_at_ms BIGINT NOT NULL,
    elapsed_ms BIGINT NOT NULL CHECK (elapsed_ms >= 0),
    received_at_ms BIGINT NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    quality VARCHAR(16) NOT NULL CHECK (quality IN ('full', 'partial', 'skewed')),
    UNIQUE (run_id, sequence)
);

CREATE INDEX batches_run_time ON batches(run_id, observed_at_ms);

CREATE TABLE producer_state (
    run_id VARCHAR(128) PRIMARY KEY NOT NULL REFERENCES producer_runs(id),
    last_sequence BIGINT NOT NULL,
    baseline_batch_id VARCHAR(128) NOT NULL REFERENCES batches(id),
    last_progress_received_at_ms BIGINT NOT NULL
    -- Preserve the referenced baseline batch/totals beyond ordinary raw retention.
    -- commit_snapshot validates that baseline_batch_id belongs to this run/sequence.
);

CREATE TABLE request_totals (
    batch_id VARCHAR(128) NOT NULL REFERENCES batches(id),
    stat_key VARCHAR(128) NOT NULL,
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('request', 'operation')),
    route_or_operation VARCHAR(256) NOT NULL,
    method VARCHAR(16) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    completed_count BIGINT NOT NULL CHECK (completed_count >= 0),
    duration_count BIGINT NOT NULL CHECK (duration_count >= 0),
    duration_sum_us BIGINT NOT NULL CHECK (duration_sum_us >= 0),
    histogram_version BIGINT NOT NULL,
    PRIMARY KEY (batch_id, stat_key),
    UNIQUE (batch_id, kind, route_or_operation, method, outcome)
    -- Cumulative within a run, not directly summed across snapshots.
    -- stat_key maps to the same validated dimensions for the entire run.
);

CREATE TABLE histogram_totals (
    batch_id VARCHAR(128) NOT NULL,
    stat_key VARCHAR(128) NOT NULL,
    bucket_index BIGINT NOT NULL CHECK (bucket_index >= 0),
    sample_count BIGINT NOT NULL CHECK (sample_count >= 0),
    PRIMARY KEY (batch_id, stat_key, bucket_index),
    FOREIGN KEY (batch_id, stat_key) REFERENCES request_totals(batch_id, stat_key)
    -- Versioned bucket bounds are code/configuration, not floating-point keys.
    -- Noncumulative buckets partition observations; their sum = duration_count.
    -- Validate that sum in commit_snapshot, within the same transaction.
);

CREATE TABLE metric_samples (
    batch_id VARCHAR(128) NOT NULL REFERENCES batches(id),
    metric_key VARCHAR(128) NOT NULL,
    resource_key VARCHAR(128) NOT NULL,
    value DOUBLE PRECISION,
    quality VARCHAR(16) NOT NULL CHECK (quality IN ('full', 'unsupported', 'partial')),
    PRIMARY KEY (batch_id, metric_key, resource_key),
    CHECK ((quality = 'unsupported' AND value IS NULL)
        OR (quality <> 'unsupported' AND value IS NOT NULL))
    -- Keys, units, aggregation and allowed resources come from a bounded registry.
    -- Integer counters requiring exactness use typed BIGINT records, not this table.
);

CREATE TABLE probe_results (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    site_id VARCHAR(128) NOT NULL REFERENCES sites(id),
    replica_id VARCHAR(128) REFERENCES replicas(id),
    probe_key VARCHAR(128) NOT NULL,
    observed_at_ms BIGINT NOT NULL,
    received_at_ms BIGINT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    duration_us BIGINT NOT NULL CHECK (duration_us >= 0),
    status_code BIGINT,
    error_code VARCHAR(64)
);

CREATE INDEX probes_site_time ON probe_results(site_id, observed_at_ms);

CREATE TABLE events (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    run_id VARCHAR(128) NOT NULL REFERENCES producer_runs(id),
    occurred_at_ms BIGINT NOT NULL,
    received_at_ms BIGINT NOT NULL,
    severity VARCHAR(16) NOT NULL,
    event_key VARCHAR(128) NOT NULL,
    summary VARCHAR(1024) NOT NULL,
    details_text VARCHAR(4096) NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL
    -- IDs remain stable across retries; conflicting contents are rejected.
    -- details_text is redacted JSON text; no DB-specific JSON operators required.
);

CREATE INDEX events_run_time ON events(run_id, occurred_at_ms);
CREATE INDEX events_severity_time ON events(severity, occurred_at_ms);

CREATE TABLE job_state (
    site_id VARCHAR(128) NOT NULL REFERENCES sites(id),
    job_key VARCHAR(128) NOT NULL,
    last_success_at_ms BIGINT,
    last_publication_at_ms BIGINT,
    last_progress_at_ms BIGINT,
    last_run_id VARCHAR(128) REFERENCES producer_runs(id),
    last_outcome VARCHAR(32) NOT NULL,
    PRIMARY KEY (site_id, job_key)
    -- Central transaction reconciles timestamps; delayed producers cannot regress
    -- last success/publication. Job outcome ordering uses the event timestamp.
);

-- Additional production tables described in the proposal: metric definitions,
-- probe/rule configuration, inventory snapshots, producer credential hashes,
-- interval deltas/rollups with coverage, incidents, silences and notification
-- attempts. Deletion order and retention preserve active baselines and FK parents.
