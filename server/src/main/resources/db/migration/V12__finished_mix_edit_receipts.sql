CREATE TABLE mix_edit_receipt (
 project_id text NOT NULL REFERENCES mix_project(id), command_key text NOT NULL,
 request_json text NOT NULL, take_id text NOT NULL REFERENCES mix_take(id),
 PRIMARY KEY (project_id,command_key)
);
