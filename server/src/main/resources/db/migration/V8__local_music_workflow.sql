ALTER TABLE recording DROP CONSTRAINT recording_provenance_check;
ALTER TABLE recording ADD CONSTRAINT recording_provenance_check CHECK(provenance IN ('ORIGINAL','FIXTURE','LIVE','LOCAL'));
ALTER TABLE generation_request DROP CONSTRAINT generation_request_provenance_check;
ALTER TABLE generation_request ADD CONSTRAINT generation_request_provenance_check CHECK(provenance IN ('FIXTURE','LIVE','LOCAL'));
ALTER TABLE generation_operation DROP CONSTRAINT generation_operation_provenance_check;
ALTER TABLE generation_operation ADD CONSTRAINT generation_operation_provenance_check CHECK(provenance IN ('FIXTURE','LIVE','LOCAL'));
CREATE TABLE local_note_revision (
 exploration_id text NOT NULL REFERENCES exploration(id), revision bigint NOT NULL,
 source_recording_id text NOT NULL REFERENCES recording(id), source_revision bigint NOT NULL,
 status text NOT NULL CHECK(status IN ('PROPOSED','CONFIRMED')), notes_json text NOT NULL,
 duration_seconds double precision NOT NULL CHECK(duration_seconds>0 AND duration_seconds<=12),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(exploration_id,revision)
);
CREATE TABLE local_plan (
 id text PRIMARY KEY, exploration_id text NOT NULL REFERENCES exploration(id),
 source_recording_id text NOT NULL REFERENCES recording(id), source_revision bigint NOT NULL,
 note_revision bigint NOT NULL, draft_revision bigint NOT NULL, basis_recording_id text NOT NULL REFERENCES recording(id),
 submitted_text text NOT NULL, recipe_json text NOT NULL, basis_recipe_json text NOT NULL, summary text NOT NULL,
 warnings_json text NOT NULL, can_generate boolean NOT NULL, model_json text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), FOREIGN KEY(exploration_id,note_revision) REFERENCES local_note_revision(exploration_id,revision)
);
ALTER TABLE generation_request ADD COLUMN local_plan_id text REFERENCES local_plan(id);
CREATE TABLE local_job (
 id text PRIMARY KEY, exploration_id text NOT NULL REFERENCES exploration(id), kind text NOT NULL CHECK(kind IN ('TRANSCRIBE','PREVIEW','PLAN')),
 state text NOT NULL CHECK(state IN ('ACCEPTED','RUNNING','SUCCEEDED','FAILED')), version bigint NOT NULL DEFAULT 1,
 source_revision bigint NOT NULL, note_revision bigint, payload_json text NOT NULL,
 result_json text, outcome_code text, outcome_detail text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_local_job ON local_job((true)) WHERE state IN ('ACCEPTED','RUNNING');
ALTER TABLE local_note_revision ADD COLUMN preview_job_id text REFERENCES local_job(id);
ALTER TABLE local_job ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;
ALTER TABLE generation_attempt ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;
