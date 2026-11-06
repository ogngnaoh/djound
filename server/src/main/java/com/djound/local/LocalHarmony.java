package com.djound.local;

import com.djound.ApiProblem;

import java.util.*;
import java.util.stream.Collectors;

/** A reviewed 4/4 chord chart. Bar positions use the same 44.1 kHz clock as the renderer. */
final class LocalHarmony {
    private static final Set<String> CHORDS =
            Set.of(
                    "C", "Cm", "Db", "Dbm", "D", "Dm", "Eb", "Ebm", "E", "Em", "F", "Fm", "F#",
                    "F#m", "G", "Gm", "Ab", "Abm", "A", "Am", "Bb", "Bbm", "B", "Bm");
    private static final int MAX_BARS = 10;
    private static final int SAMPLE_RATE = 44_100;

    private LocalHarmony() {}

    static Map<String, Object> canonical(Object value) {
        if (!(value instanceof Map<?, ?> grid)
                || !grid.keySet().equals(Set.of("bpm", "downbeatMs", "chords"))
                || !(grid.get("bpm") instanceof Integer bpm)
                || bpm < 40
                || bpm > 200
                || !(grid.get("downbeatMs") instanceof Integer downbeat)
                || downbeat < 0
                || downbeat > 11990)
            throw invalid(
                    "Choose a chord chart from 40 to 200 BPM and a first beat inside the clip.");
        if (!(grid.get("chords") instanceof List<?> chords)
                || chords.isEmpty()
                || chords.size() > MAX_BARS)
            throw invalid("Choose one chord or rest for each bar in the clip.");
        for (Object chord : chords) {
            if (chord != null && (!(chord instanceof String) || !CHORDS.contains(chord)))
                throw invalid("Choose a supported major chord, minor chord or rest for each bar.");
        }
        // List.copyOf rejects null, which represents an intentional rest.
        return Map.of(
                "bpm",
                bpm,
                "downbeatMs",
                downbeat,
                "chords",
                Collections.unmodifiableList(new ArrayList<>(chords)));
    }

    static void validateTiming(Map<String, Object> recipe, double seconds) {
        if (!(recipe.get("harmony") instanceof Map<?, ?> grid)) return;
        int downbeat = (int) grid.get("downbeatMs");
        if (downbeat + 10L > seconds * 1000 + 1e-7)
            throw invalid(
                    "Place the first chord beat inside the recording with at least 10 ms left to"
                        + " play.");
        double barSeconds = 240.0 / (int) grid.get("bpm");
        long samples = Math.round(SAMPLE_RATE * seconds);
        int bars = 0;
        while (bars < MAX_BARS
                && Math.round(SAMPLE_RATE * (downbeat / 1000.0 + bars * barSeconds)) < samples)
            bars++;
        if (((List<?>) grid.get("chords")).size() != bars)
            throw invalid(
                    "Choose exactly one chord or rest for each of the clip's " + bars + " bars.");
    }

    static boolean sounds(Map<String, Object> recipe) {
        return recipe.get("harmony") instanceof Map<?, ?> grid
                && ((List<?>) grid.get("chords")).stream().anyMatch(Objects::nonNull);
    }

    static String describe(Map<String, Object> recipe, Map<String, Object> previous) {
        if (!(recipe.get("harmony") instanceof Map<?, ?> grid))
            return previous.containsKey("harmony") ? " Removed the chord chart." : "";
        String chart =
                ((List<?>) grid.get("chords"))
                        .stream()
                                .map(chord -> Objects.toString(chord, "rest"))
                                .collect(Collectors.joining(" | "));
        return " Chord chart: "
                + chart
                + ". "
                + grid.get("bpm")
                + " BPM, first beat at "
                + grid.get("downbeatMs")
                + " ms.";
    }

    private static ApiProblem invalid(String detail) {
        return new ApiProblem(409, "PLAN_INVALID", detail, "EDIT_TEXT");
    }
}
