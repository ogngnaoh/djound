package com.djound.mix;

import java.util.List;

public final class MixModels {
    private MixModels() {}

    public record Selection(int startFrame, int endFrame) {}

    public record Select(Integer startFrame, Integer endFrame, String basisTakeId) {}

    public record Edit(
            String text,
            String basisTakeId,
            Integer startFrame,
            Integer endFrame,
            String referenceTakeId,
            Double passageTrimDb) {}

    public record Target(String takeId) {}

    public record Keep(String takeId, String sha256) {}

    public record Take(
            String id,
            int number,
            String parentId,
            String sourceTakeId,
            String selectionBaseId,
            int startFrame,
            int endFrame,
            double vocalsDb,
            double drumsDb,
            double passageTrimDb,
            String lastControl,
            double lastChangeDb,
            String direction,
            String label,
            String text,
            String audioUrl,
            String sha256,
            boolean kept,
            boolean heard) {}

    public record State(
            String id,
            String title,
            int sampleRate,
            int frames,
            int durationMs,
            String importUrl,
            String sourceUrl,
            long revision,
            Selection selection,
            String selectionBaseId,
            String preparation,
            String error,
            String heardTakeId,
            String keptTakeId,
            List<Take> takes) {}

    public record Reply(
            String status,
            String message,
            Take take,
            List<Take> choices,
            String control,
            Double suggestedTrimDb,
            Double suggestedGainDb) {
        Reply(String status, String message, Take take, List<Take> choices) {
            this(status, message, take, choices, null, null, null);
        }
    }
}
