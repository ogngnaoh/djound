package com.djound.local;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class LocalModels {
    private LocalModels() {}

    public record Note(double start, double end, int pitch, int velocity) {}

    public record Notes(
            long revision,
            long sourceRevision,
            String status,
            List<Note> notes,
            double durationSeconds,
            String previewUrl) {}

    public record Job(
            String id,
            String kind,
            String state,
            long sourceRevision,
            Long noteRevision,
            String outcomeCode,
            String detail,
            Instant updatedAt) {}

    public record Plan(
            String id,
            long noteRevision,
            long sourceRevision,
            long draftRevision,
            String text,
            String basisRecordingId,
            Map<String, Object> recipe,
            Map<String, Object> basisRecipe,
            String summary,
            boolean canGenerate,
            List<String> warnings,
            Map<String, Object> model) {}

    public record JobResult(Job job, Plan plan) {}

    public record State(
            boolean available,
            String unavailableReason,
            Job activeJob,
            Job latestJob,
            Notes notes,
            Plan plan) {}

    public record SourceCommand(Long expectedSourceRevision) {}

    public record RevisionCommand(Long expectedSourceRevision, Long expectedNoteRevision) {}

    public record NotesCommand(
            Long expectedSourceRevision,
            Long expectedNoteRevision,
            List<Note> notes,
            boolean confirm) {}

    public record PlanCommand(
            Long expectedSourceRevision,
            Long expectedNoteRevision,
            Long expectedDraftRevision,
            String text,
            String basisRecordingId) {}

    public record CorrectionCommand(
            Long expectedSourceRevision,
            Long expectedNoteRevision,
            Long expectedDraftRevision,
            String text,
            String basisRecordingId,
            Map<String, Object> recipe) {}
}
