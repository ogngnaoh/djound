ALTER TABLE rendition_take
    ADD COLUMN clarification_kind text,
    ADD COLUMN answer_to text,
    ADD COLUMN passage_choice text,
    ADD CHECK (clarification_kind IS NULL OR
               (state = 'clarify' AND clarification_kind = 'retained_passage')),
    ADD CHECK ((answer_to IS NULL) = (passage_choice IS NULL) AND
               (passage_choice IS NULL OR passage_choice IN ('keep', 'develop'))),
    ADD FOREIGN KEY (project_id, answer_to) REFERENCES rendition_take(project_id, id);
