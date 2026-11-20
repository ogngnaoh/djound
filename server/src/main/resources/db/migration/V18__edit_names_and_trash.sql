ALTER TABLE rendition_take ADD COLUMN display_label text,
    ADD COLUMN trashed_at timestamptz;

CREATE TRIGGER rendition_edit_activity AFTER UPDATE OF display_label, trashed_at ON rendition_take
    FOR EACH ROW WHEN (OLD.display_label IS DISTINCT FROM NEW.display_label OR
                       OLD.trashed_at IS DISTINCT FROM NEW.trashed_at)
    EXECUTE FUNCTION mark_rendition_activity();
