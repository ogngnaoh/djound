ALTER TABLE rendition_project ADD COLUMN last_activity_at timestamptz;

UPDATE rendition_project p
SET last_activity_at = greatest(p.created_at, (
    SELECT max(t.created_at) FROM rendition_take t WHERE t.project_id = p.id
));

ALTER TABLE rendition_project ALTER COLUMN last_activity_at SET NOT NULL,
    ALTER COLUMN last_activity_at SET DEFAULT now();

CREATE FUNCTION mark_rendition_activity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE rendition_project SET last_activity_at = greatest(last_activity_at, statement_timestamp())
    WHERE id = NEW.project_id;
    RETURN NULL;
END;
$$;

CREATE TRIGGER rendition_activity AFTER INSERT OR UPDATE OF state ON rendition_take
    FOR EACH ROW EXECUTE FUNCTION mark_rendition_activity();
