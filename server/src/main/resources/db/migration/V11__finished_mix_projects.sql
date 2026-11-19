CREATE TABLE mix_project (
 id text PRIMARY KEY, title text NOT NULL, import_key text UNIQUE, import_sha text NOT NULL,
 duration_ms integer NOT NULL, frames integer, source_path text, source_sha text,
 preparation text NOT NULL CHECK (preparation IN ('DECODING','IDLE','PREPARING','READY','FAILED')),
 error text, job_id text, passage_id text, heard_take text NOT NULL DEFAULT 'original',
 kept_take text NOT NULL DEFAULT 'original', revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mix_passage (
 id text PRIMARY KEY, project_id text NOT NULL REFERENCES mix_project(id),
 base_id text NOT NULL, start_frame integer NOT NULL CHECK(start_frame>=0),
 end_frame integer NOT NULL CHECK(end_frame>start_frame),
 prep_path text, prep_sha text, cache_key text,
 UNIQUE(project_id,base_id,start_frame,end_frame)
);
ALTER TABLE mix_project ADD FOREIGN KEY (passage_id) REFERENCES mix_passage(id);
CREATE TABLE mix_take (
 id text PRIMARY KEY, project_id text NOT NULL REFERENCES mix_project(id),
 number integer NOT NULL, passage_id text NOT NULL REFERENCES mix_passage(id),
 parent_id text NOT NULL, reference_id text REFERENCES mix_take(id),
 vocals_db double precision NOT NULL CHECK(vocals_db BETWEEN -12 AND 12),
 drums_db double precision NOT NULL CHECK(drums_db BETWEEN -12 AND 12),
 passage_trim_db double precision NOT NULL DEFAULT 0 CHECK(passage_trim_db BETWEEN -12 AND 0),
 last_control text NOT NULL CHECK(last_control IN ('vocals','drums')),
 last_change_db double precision NOT NULL, request_text text NOT NULL, label text NOT NULL,
 audio_path text NOT NULL UNIQUE, sha256 text NOT NULL,
 heard boolean NOT NULL DEFAULT false, kept boolean NOT NULL DEFAULT false,
 UNIQUE(project_id,number)
);
CREATE TABLE mix_keep_receipt (
 project_id text NOT NULL REFERENCES mix_project(id), command_key text NOT NULL,
 take_id text NOT NULL REFERENCES mix_take(id), sha256 text NOT NULL,
 PRIMARY KEY (project_id,command_key)
);
