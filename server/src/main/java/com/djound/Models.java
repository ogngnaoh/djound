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
            Instant createdAt) {}

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
}
