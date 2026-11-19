CREATE TABLE rendition_project (
    id text PRIMARY KEY,
    title text NOT NULL,
    import_key text UNIQUE,
    upload_sha text NOT NULL,
    source_sha text NOT NULL,
    frames integer NOT NULL CHECK(frames BETWEEN 1 AND 13230000),
    favorite_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE rendition_take (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES rendition_project(id),
    parent_id text,
    request_id text NOT NULL,
    prompt text NOT NULL,
    start_seconds double precision,
    end_seconds double precision,
    applied_start_seconds double precision,
    applied_end_seconds double precision,
    state text NOT NULL CHECK(state IN ('queued','running','ready','failed','clarify')),
    label text NOT NULL,
    caption text NOT NULL DEFAULT '',
    error text,
    sha256 text,
    frames integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(project_id, id),
    UNIQUE(project_id, request_id),
    FOREIGN KEY(project_id, parent_id) REFERENCES rendition_take(project_id, id),
    CHECK ((start_seconds IS NULL AND end_seconds IS NULL) OR
           (start_seconds IS NOT NULL AND end_seconds IS NOT NULL AND start_seconds >= 0 AND end_seconds > start_seconds)),
    CHECK (state <> 'ready' OR (frames > 0 AND frames IS NOT NULL AND sha256 IS NOT NULL))
);
ALTER TABLE rendition_project ADD FOREIGN KEY (id, favorite_id) REFERENCES rendition_take(project_id,id);
CREATE INDEX rendition_queue ON rendition_take(created_at,id) WHERE state='queued';
