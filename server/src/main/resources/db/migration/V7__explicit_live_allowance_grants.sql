-- Authorizations are explicit operator actions. Migration/startup grants nothing.
ALTER TABLE workspace ADD CONSTRAINT workspace_installation_id_unique UNIQUE(installation_id);
ALTER TABLE live_submission_reservation DROP CONSTRAINT live_submission_reservation_installation_id_fkey;
ALTER TABLE live_submission_reservation ADD CONSTRAINT live_submission_reservation_installation_id_fkey
 FOREIGN KEY(installation_id) REFERENCES workspace(installation_id);

CREATE TABLE live_submission_grant (
 grant_id text PRIMARY KEY CHECK(grant_id ~ '^[A-Za-z0-9._:-]{8,128}$'),
 installation_id text NOT NULL REFERENCES workspace(installation_id),
 additional_submissions integer NOT NULL CHECK(additional_submissions > 0),
 reason text NOT NULL CHECK(length(btrim(reason)) > 0 AND length(reason) <= 1000),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER grant_immutable BEFORE UPDATE OR DELETE ON live_submission_grant
 FOR EACH ROW EXECUTE FUNCTION reject_live_ledger_mutation();

-- Used only by the local operator CLI, never by application startup or an HTTP endpoint.
-- Preview and apply share validation. The workspace lock serializes grants with reservations.
CREATE FUNCTION grant_live_submissions(
 requested_installation text, requested_directory text, requested_grant text,
 requested_count integer, requested_reason text, apply_grant boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
 installation workspace%ROWTYPE;
 existing live_submission_grant%ROWTYPE;
 authorized bigint;
 spent bigint;
 added integer;
 replayed boolean;
BEGIN
 SELECT * INTO STRICT installation FROM workspace WHERE id=1 FOR UPDATE;
 IF requested_installation IS DISTINCT FROM installation.installation_id
    OR requested_directory IS DISTINCT FROM installation.canonical_directory THEN
  RAISE EXCEPTION 'Installation ID and canonical data directory must match the database';
 END IF;
 IF requested_grant IS NULL OR requested_grant !~ '^[A-Za-z0-9._:-]{8,128}$'
    OR requested_count IS NULL OR requested_count <= 0
    OR requested_reason IS NULL OR length(btrim(requested_reason))=0
    OR length(requested_reason)>1000 OR apply_grant IS NULL THEN
  RAISE EXCEPTION 'Provide a valid grant ID, positive submission count, and reason';
 END IF;
 SELECT * INTO existing FROM live_submission_grant WHERE grant_id=requested_grant;
 replayed := FOUND;
 IF replayed AND (existing.installation_id IS DISTINCT FROM requested_installation
    OR existing.additional_submissions IS DISTINCT FROM requested_count
    OR existing.reason IS DISTINCT FROM requested_reason) THEN
  RAISE EXCEPTION 'Grant ID already belongs to a different authorization';
 END IF;
 SELECT coalesce((SELECT submission_limit FROM live_submission_allowance
                  WHERE installation_id=requested_installation),0)
        + coalesce((SELECT sum(additional_submissions) FROM live_submission_grant
                    WHERE installation_id=requested_installation),0) INTO authorized;
 SELECT count(*) INTO spent FROM live_submission_reservation WHERE installation_id=requested_installation;
 added := CASE WHEN replayed THEN 0 ELSE requested_count END;
 IF apply_grant AND NOT replayed THEN
  INSERT INTO live_submission_grant(grant_id,installation_id,additional_submissions,reason)
   VALUES(requested_grant,requested_installation,requested_count,requested_reason);
 END IF;
 RETURN jsonb_build_object(
  'installationId',requested_installation,'grantId',requested_grant,
  'applied',apply_grant AND NOT replayed,'replayed',replayed,
  'preview',NOT apply_grant,'additionalSubmissions',added,
  'before',jsonb_build_object('limit',authorized,'used',spent,'remaining',greatest(0,authorized-spent)),
  'after',jsonb_build_object('limit',authorized+added,'used',spent,'remaining',greatest(0,authorized+added-spent))
 );
END $$;
