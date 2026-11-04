-- No live allowance is provisioned by migrations or application code.
CREATE TABLE live_submission_allowance (
 installation_id text PRIMARY KEY, submission_limit integer NOT NULL CHECK(submission_limit=2),
 provisioned_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE live_submission_reservation (
 attempt_id text PRIMARY KEY REFERENCES generation_attempt(id),
 installation_id text NOT NULL REFERENCES live_submission_allowance(installation_id),
 reserved_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION reject_live_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'The live submission allowance and reservations are append-only'; END $$;
CREATE TRIGGER allowance_immutable BEFORE UPDATE OR DELETE ON live_submission_allowance FOR EACH ROW EXECUTE FUNCTION reject_live_ledger_mutation();
CREATE TRIGGER reservation_immutable BEFORE UPDATE OR DELETE ON live_submission_reservation FOR EACH ROW EXECUTE FUNCTION reject_live_ledger_mutation();
CREATE TABLE generation_operation (
 id text PRIMARY KEY, attempt_id text NOT NULL UNIQUE REFERENCES generation_attempt(id),
 adapter_configuration_id text NOT NULL, provenance text NOT NULL CHECK(provenance IN ('FIXTURE','LIVE')),
 upload_path text, event_id text, observation_started_at timestamptz, result_url text,
 created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO generation_operation(id,attempt_id,adapter_configuration_id,provenance,result_url,created_at)
 SELECT f.id,f.attempt_id,r.adapter_configuration_id,'FIXTURE',f.result_url,f.created_at
 FROM fixture_operation f JOIN generation_attempt a ON a.id=f.attempt_id JOIN generation_request r ON r.id=a.request_id;
ALTER TABLE generation_attempt DROP CONSTRAINT generation_attempt_operation_handle_fkey;
ALTER TABLE generation_attempt ADD CONSTRAINT generation_attempt_operation_handle_fkey FOREIGN KEY(operation_handle) REFERENCES generation_operation(id);
-- Fixture observations retain their existing workspace columns. Live observations have a separate context.
CREATE TABLE live_availability (
 id integer PRIMARY KEY CHECK(id=1), availability_state text NOT NULL DEFAULT 'UNKNOWN' CHECK(availability_state IN ('ELIGIBLE','BLOCKED','UNKNOWN')),
 availability_reason_code text NOT NULL DEFAULT 'CAPACITY_UNCONFIRMED', availability_observed_at timestamptz,
 availability_provider_reset_at timestamptz, availability_next_probe_at timestamptz,
 availability_supports_status_check boolean NOT NULL DEFAULT false CHECK(NOT availability_supports_status_check),
 availability_version bigint NOT NULL DEFAULT 0
);
INSERT INTO live_availability(id) VALUES(1);
