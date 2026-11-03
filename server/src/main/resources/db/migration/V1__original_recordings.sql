CREATE TABLE workspace (
 id integer PRIMARY KEY CHECK(id=1), installation_id text NOT NULL, canonical_directory text NOT NULL,
 active_attempt_id text
);
CREATE TABLE exploration (
 id text PRIMARY KEY, title text NOT NULL, original_id text NOT NULL,
 source_revision bigint NOT NULL DEFAULT 1, draft_text text NOT NULL, draft_basis_id text NOT NULL,
 draft_revision bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE TABLE recording (
 id text PRIMARY KEY, exploration_id text NOT NULL REFERENCES exploration(id), title text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('ORIGINAL','RENDITION')), file_name text NOT NULL,
 storage_key text UNIQUE NOT NULL, content_type text NOT NULL, byte_size bigint NOT NULL CHECK(byte_size>0),
 duration_ms integer NOT NULL CHECK(duration_ms>0), sample_rate integer NOT NULL, channels integer NOT NULL,
 codec text NOT NULL, sha256 text NOT NULL, provenance text NOT NULL CHECK(provenance IN ('ORIGINAL','FIXTURE','LIVE')),
 kept boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL
);
ALTER TABLE exploration ADD CONSTRAINT original_recording_fk FOREIGN KEY(original_id) REFERENCES recording(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE exploration ADD CONSTRAINT draft_basis_fk FOREIGN KEY(draft_basis_id) REFERENCES recording(id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE command_receipt (
 command_key text PRIMARY KEY, operation text NOT NULL, fingerprint text NOT NULL,
 exploration_id text NOT NULL REFERENCES exploration(id), recording_id text NOT NULL REFERENCES recording(id),
 source_revision bigint NOT NULL, draft_revision bigint NOT NULL, created_at timestamptz NOT NULL
);
