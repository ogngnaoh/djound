-- Earlier takes share the upload clock. A null map retains that identity.
ALTER TABLE rendition_take
    ADD COLUMN song_time_map jsonb,
    ADD COLUMN arrangement_plan jsonb;
