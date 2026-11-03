CREATE TABLE generation_request (
 id text PRIMARY KEY, exploration_id text NOT NULL REFERENCES exploration(id),
 source_recording_id text NOT NULL REFERENCES recording(id), source_revision bigint NOT NULL,
 basis_recording_id text NOT NULL REFERENCES recording(id), submitted_text text NOT NULL,
 inherited_direction text NOT NULL, direction text NOT NULL, assembled_instructions text NOT NULL,
 assembler_version text NOT NULL, adapter_configuration_id text NOT NULL,
 provenance text NOT NULL CHECK(provenance IN ('FIXTURE','LIVE')), created_at timestamptz NOT NULL
);
CREATE TABLE generation_attempt (
 id text PRIMARY KEY, request_id text NOT NULL REFERENCES generation_request(id),
 exploration_id text NOT NULL REFERENCES exploration(id),
 state text NOT NULL CHECK(state IN ('ACCEPTED','SUBMITTING','RUNNING','SAVING','RECONCILING','SUCCEEDED','FAILED')),
 version bigint NOT NULL DEFAULT 1, result_recording_id text UNIQUE REFERENCES recording(id),
 outcome_code text, outcome_detail text, execution_count integer NOT NULL DEFAULT 0 CHECK(execution_count BETWEEN 0 AND 1),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX one_locally_active_attempt ON generation_attempt ((true))
 WHERE state IN ('ACCEPTED','SUBMITTING','RUNNING','SAVING','RECONCILING');
CREATE INDEX attempts_exploration_created ON generation_attempt(exploration_id,created_at DESC);
CREATE INDEX recording_exploration_kind ON recording(exploration_id,kind);
ALTER TABLE recording ADD COLUMN direction text;
ALTER TABLE recording ADD COLUMN request_id text REFERENCES generation_request(id);
ALTER TABLE recording ADD COLUMN attempt_id text UNIQUE REFERENCES generation_attempt(id);
ALTER TABLE command_receipt ADD COLUMN request_id text REFERENCES generation_request(id);
ALTER TABLE command_receipt ADD COLUMN attempt_id text REFERENCES generation_attempt(id);
ALTER TABLE command_receipt ADD COLUMN consumed_draft_revision bigint;
