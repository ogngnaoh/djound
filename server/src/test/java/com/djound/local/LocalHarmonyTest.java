package com.djound.local;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.ApiProblem;

import org.junit.jupiter.api.Test;

import java.util.*;

class LocalHarmonyTest {
    private final Map<String, Object> legacy =
            Map.of(
                    "instrument",
                    "piano",
                    "bass",
                    true,
                    "drums",
                    false,
                    "density",
                    "steady",
                    "backingVolume",
                    "normal",
                    "bpm",
                    100);

    private static Map<String, Object> chart(int bpm, int downbeat, String... chords) {
        return Map.of("bpm", bpm, "downbeatMs", downbeat, "chords", Arrays.asList(chords));
    }

    @Test
    void chartHasItsOwnStrictGridAndOnlyCanonicalTriadsOrRests() {
        var recipe = new HashMap<>(legacy);
        assertEquals(legacy, LocalMusic.validateRecipe(legacy));
        var valid = chart(73, 40, "C", "Dbm", null, "F#m");
        recipe.put("harmony", valid);
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        for (Object invalid :
                Arrays.asList(
                        null,
                        List.of(),
                        chart(73, 40),
                        chart(39, 0, "C"),
                        chart(201, 0, "C"),
                        chart(73, -1, "C"),
                        chart(73, 11991, "C"),
                        chart(73, 0, "C#"),
                        chart(73, 0, "D7"),
                        Map.of("bpm", 100.0, "downbeatMs", 0, "chords", List.of("C")),
                        Map.of("bpm", 100, "downbeatMs", 2.0, "chords", List.of("C")),
                        Map.of("bpm", 100, "downbeatMs", 0, "chords", "C"),
                        Map.of("bpm", 100, "downbeatMs", 0, "chords", List.of(0)),
                        Map.of("bpm", 100, "downbeatMs", 0, "chords", List.of("C"), "extra", true),
                        chart(200, 0, new String[11]))) {
            recipe.put("harmony", invalid);
            assertEquals(
                    "PLAN_INVALID",
                    assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
        }
    }

    @Test
    void barAtTheEndSampleIsExcludedAndTenMillisecondsMustRemainAtTheDownbeat() {
        // At 200 BPM the next bar starts 1.2 seconds (52920 samples) later.
        var one = Map.<String, Object>of("harmony", chart(200, 10, "C"));
        var two = Map.<String, Object>of("harmony", chart(200, 10, "C", null));
        for (double end : List.of(1.21, 1.21 + 0.4 / 44100)) {
            assertDoesNotThrow(() -> LocalHarmony.validateTiming(one, end));
            assertThrows(ApiProblem.class, () -> LocalHarmony.validateTiming(two, end));
        }
        assertDoesNotThrow(() -> LocalHarmony.validateTiming(two, 1.21 + 0.6 / 44100));
        assertThrows(ApiProblem.class, () -> LocalHarmony.validateTiming(one, .019));
        assertDoesNotThrow(() -> LocalHarmony.validateTiming(one, .020));
        assertDoesNotThrow(
                () ->
                        LocalHarmony.validateTiming(
                                Map.of("harmony", chart(200, 0, new String[10])), 12));
    }

    @Test
    void allRestChartSilencesItsBassAndChordsButLeavesLeadAndDrumsAvailable() {
        var recipe = new HashMap<>(legacy);
        var muted = Map.of("gainDb", 0, "pan", 0, "muted", true);
        var warm = Map.of("gainDb", -1, "pan", 0, "muted", false, "tone", "warm");
        recipe.put("harmony", chart(100, 0, (String) null));
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        for (var mix :
                List.of(Map.of("chords", warm), Map.of("bass", warm), Map.of("lead", muted))) {
            recipe.put("mix", mix);
            assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe));
        }
        recipe.put("drums", true);
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        recipe.put("drums", false);
        recipe.put("harmony", chart(100, 0, null, "Am"));
        recipe.put("mix", Map.of("lead", muted, "bass", muted, "chords", warm));
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        recipe.remove("harmony");
        assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe));
    }
}
