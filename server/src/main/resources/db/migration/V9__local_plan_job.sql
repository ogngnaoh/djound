-- A workspace can have a newer manual or automatic plan when another tab polls
-- its completed preparation. Keep the job's immutable result addressable.
ALTER TABLE local_plan ADD COLUMN preparation_job_id text UNIQUE REFERENCES local_job(id);

-- Before this link existed both writes shared one transaction and PostgreSQL's now().
-- Leave any ambiguous historical pair unlinked rather than returning the wrong plan.
WITH matches AS (
    SELECT j.id AS job_id, p.id AS plan_id,
           count(*) OVER (PARTITION BY j.id) AS plan_count,
           count(*) OVER (PARTITION BY p.id) AS job_count
    FROM local_job j JOIN local_plan p ON p.exploration_id = j.exploration_id
        AND p.source_revision = j.source_revision AND p.note_revision = j.note_revision
        AND p.created_at = j.updated_at
    WHERE j.kind = 'PLAN' AND j.state = 'SUCCEEDED'
      AND p.submitted_text = (j.payload_json::jsonb ->> 'prompt')
      AND p.basis_recording_id = (j.payload_json::jsonb ->> 'basisRecordingId')
)
UPDATE local_plan p SET preparation_job_id = m.job_id FROM matches m
WHERE p.id = m.plan_id AND m.plan_count = 1 AND m.job_count = 1;
