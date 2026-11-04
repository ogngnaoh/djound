CREATE TABLE fixture_operation (
 id text PRIMARY KEY, attempt_id text NOT NULL UNIQUE REFERENCES generation_attempt(id),
 fixture_outcome text NOT NULL CHECK(fixture_outcome IN ('SUCCESS','FAILURE','UNKNOWN')),
 fixture_name text NOT NULL, result_url text,
 ready_at timestamptz NOT NULL, created_at timestamptz NOT NULL
);
ALTER TABLE generation_attempt ADD COLUMN operation_handle text REFERENCES fixture_operation(id);
ALTER TABLE generation_attempt ADD COLUMN acknowledged_unknown_attempt_id text REFERENCES generation_attempt(id);
ALTER TABLE generation_attempt ADD COLUMN acknowledged_by_attempt_id text REFERENCES generation_attempt(id);
ALTER TABLE generation_attempt ADD COLUMN acknowledged_at timestamptz;
ALTER TABLE workspace ADD COLUMN unknown_outcome_attempt_id text REFERENCES generation_attempt(id);
ALTER TABLE generation_attempt DROP CONSTRAINT generation_attempt_fixture_outcome_check;
ALTER TABLE generation_attempt ADD CONSTRAINT generation_attempt_fixture_outcome_check CHECK(fixture_outcome IN ('SUCCESS','FAILURE','UNKNOWN'));
ALTER TABLE workspace DROP CONSTRAINT workspace_fixture_next_outcome_check;
ALTER TABLE workspace ADD CONSTRAINT workspace_fixture_next_outcome_check CHECK(fixture_next_outcome IN ('SUCCESS','FAILURE','UNKNOWN'));
CREATE TABLE publication_journal (
 attempt_id text PRIMARY KEY REFERENCES generation_attempt(id), attempt_version bigint NOT NULL,
 phase text NOT NULL CHECK(phase IN ('DOWNLOADING','VALIDATED','LINKED')),
 recording_id text NOT NULL UNIQUE, temporary_name text NOT NULL UNIQUE, storage_key text NOT NULL UNIQUE,
 file_name text NOT NULL, content_type text, byte_size bigint, sha256 text,
 duration_ms integer, sample_rate integer, channels integer, codec text,
 created_at timestamptz NOT NULL
);
