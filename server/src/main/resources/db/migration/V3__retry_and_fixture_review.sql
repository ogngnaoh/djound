ALTER TABLE generation_attempt ADD COLUMN previous_attempt_id text REFERENCES generation_attempt(id);
ALTER TABLE generation_attempt ADD COLUMN fixture_outcome text NOT NULL DEFAULT 'SUCCESS' CHECK(fixture_outcome IN ('SUCCESS','FAILURE'));
ALTER TABLE exploration ADD COLUMN latest_attempt_id text REFERENCES generation_attempt(id);
UPDATE exploration e SET latest_attempt_id=(SELECT a.id FROM generation_attempt a WHERE a.exploration_id=e.id ORDER BY a.created_at DESC,a.id DESC LIMIT 1);
ALTER TABLE workspace ADD COLUMN fixture_next_outcome text NOT NULL DEFAULT 'SUCCESS' CHECK(fixture_next_outcome IN ('SUCCESS','FAILURE'));
ALTER TABLE workspace ADD COLUMN fixture_unavailable_recording_id text REFERENCES recording(id);
