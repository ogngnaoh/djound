package com.djound;

import java.time.Instant;
import java.util.List;

public final class Models {
    private Models() {}

    public record Recording(
            String id,
            String explorationId,
            String kind,
            String title,
            String fileName,
            String contentType,
            long byteSize,
            int durationMs,
            int sampleRate,
            int channels,
            String codec,
            String sha256,
            String mediaUrl,
            String provenance,
            boolean kept,
            String direction,
            String requestId,
            String attemptId,
            Instant createdAt) {}

    public record RecordingUpdate(Boolean kept) {}

    public record Draft(String text, String basisRecordingId, long revision) {}

    public record DraftUpdate(String text, String basisRecordingId, Long expectedDraftRevision) {}

    public record Exploration(
            String id,
            String title,
            long sourceRevision,
            boolean canReplaceSource,
            Recording original,
            List<Recording> renditions,
            Draft draft,
            Attempt latestAttempt,
            Instant createdAt,
            Instant updatedAt) {}

    public record Summary(
            String id,
            String title,
            Recording original,
            int renditionCount,
            List<Recording> keptRecordings,
            Instant updatedAt) {}

    public record Receipt(
            String explorationId,
            String originalRecordingId,
            long sourceRevision,
            long draftRevision,
            String location,
            boolean replayed) {}

    public record CreateRequestCommand(
            String text,
            String basisRecordingId,
            Long expectedSourceRevision,
            Long expectedDraftRevision,
            String acknowledgedUnknownAttemptId) {
        public CreateRequestCommand(
                String text,
                String basisRecordingId,
                Long expectedSourceRevision,
                Long expectedDraftRevision) {
            this(text, basisRecordingId, expectedSourceRevision, expectedDraftRevision, null);
        }
    }

    public record CreationReceipt(
            String requestId,
            String attemptId,
            String statusUrl,
            long consumedDraftRevision,
            long nextDraftRevision,
            boolean replayed) {}

    public record GenerationRequest(
            String id,
            String explorationId,
            String sourceRecordingId,
            long sourceRevision,
            String basisRecordingId,
            String text,
            String inheritedDirection,
            String direction,
            String assembledInstructions,
            String assemblerVersion,
            String adapterConfigurationId,
            String provenance,
            Instant createdAt) {}

    public record AttemptOutcome(String code, String detail) {}

    public record Attempt(
            String id,
            String explorationId,
            String requestId,
            String state,
            long version,
            GenerationRequest request,
            AttemptOutcome outcome,
            String resultRecordingId,
            String previousAttemptId,
            boolean canRetry,
            List<String> permittedActions,
            Instant createdAt,
            Instant updatedAt) {}

    public record RetryCommand(
            String expectedPreviousAttemptId, String acknowledgedUnknownAttemptId) {
        public RetryCommand(String expectedPreviousAttemptId) {
            this(expectedPreviousAttemptId, null);
        }
    }

    public record RetryReceipt(
            String requestId, String attemptId, String statusUrl, boolean replayed) {}

    public record FixtureControls(String nextOutcome, String unavailableRecordingId) {}

    public record FixtureOutcomeControl(String outcome) {}

    public record FixturePlaybackControl(String recordingId, Boolean unavailable) {}

    public record UnknownOutcome(
            String attemptId, String detail, Instant occurredAt, String provenance) {}

    public record FixtureAvailabilityControl(String scenario) {}
}
