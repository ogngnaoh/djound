package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.local.LocalModels.Note;
import com.djound.local.LocalMusic;

import org.junit.jupiter.api.Test;

import java.util.*;

class LocalContractTest {
    @Test
    void differentPitchLegatoRemainsEditableWithoutChangingTranscription() {
        var notes = List.of(new Note(.1, .8, 60, 80), new Note(.7, 1.1, 62, 90));
        assertDoesNotThrow(() -> LocalMusic.validateNotes(notes, 2));
        assertEquals(.8, notes.getFirst().end());
        assertEquals(.7, notes.getLast().start());
    }

    @Test
    void samePitchOverlapAndSubsampleNotesAreRejected() {
        var overlap = List.of(new Note(.1, .8, 60, 80), new Note(.7, 1.1, 60, 90));
        assertEquals(
                "NOTES_OVERLAP",
                assertThrows(ApiProblem.class, () -> LocalMusic.validateNotes(overlap, 2)).code());
        assertDoesNotThrow(
                () ->
                        LocalMusic.validateNotes(
                                List.of(new Note(.1, .8, 60, 80), new Note(.8, 1.1, 60, 90)), 2));
        assertEquals(
                "NOTES_INVALID",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        LocalMusic.validateNotes(
                                                List.of(new Note(.1, .1000001, 60, 80)), 2))
                        .code());
    }

    @Test
    void correctedRecipeRejectsNullAndFractionalProtocolTypes() {
        assertEquals(
                "PLAN_INVALID",
                assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(null)).code());
        Map<String, Object> recipe =
                new HashMap<>(
                        Map.of(
                                "instrument",
                                "piano",
                                "bass",
                                false,
                                "drums",
                                false,
                                "density",
                                "steady",
                                "backingVolume",
                                "normal",
                                "bpm",
                                100));
        assertDoesNotThrow(() -> LocalMusic.validateRecipe(recipe));
        recipe.put("bpm", 100.0);
        assertEquals(
                "PLAN_INVALID",
                assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
        recipe.put("bpm", 100);
        recipe.put("instrument", null);
        assertEquals(
                "PLAN_INVALID",
                assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
    }

    @Test
    void grooveRequiresACompletePatternAndIntegerTimingWhileLegacyRecipesRetainTheirTempo() {
        var recipe =
                new HashMap<String, Object>(
                        Map.of(
                                "instrument",
                                "recorded",
                                "bass",
                                false,
                                "drums",
                                true,
                                "density",
                                "steady",
                                "backingVolume",
                                "normal",
                                "bpm",
                                40));
        assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe));
        var latest = Map.of("pattern", "swing_ride", "downbeatMs", 11990);
        recipe.put("groove", latest);
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        recipe.put("bpm", 200);
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        for (Object invalidTempo : List.of(39, 201, 80.0, "125")) {
            recipe.put("bpm", invalidTempo);
            assertEquals(
                    "PLAN_INVALID",
                    assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
        }
        recipe.put("bpm", 100);
        for (Object invalid :
                List.of(
                        Map.of("pattern", "swing_ride"),
                        Map.of("pattern", "unknown", "downbeatMs", 0),
                        Map.of("pattern", "swing_ride", "downbeatMs", -1),
                        Map.of("pattern", "swing_ride", "downbeatMs", 11991),
                        Map.of("pattern", "swing_ride", "downbeatMs", Integer.MAX_VALUE),
                        Map.of("pattern", "swing_ride", "downbeatMs", 0.5),
                        Map.of(
                                "pattern",
                                "swing_ride",
                                "downbeatMs",
                                0,
                                "sourceAudio",
                                "/tmp/other"))) {
            recipe.put("groove", invalid);
            assertEquals(
                    "PLAN_INVALID",
                    assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
        }
        recipe.put("groove", null);
        assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe));
        recipe.remove("groove");
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
    }

    @Test
    void mixerAcceptsOnlyCompleteActiveFiniteStripsAndDropsUnitySettings() {
        var recipe =
                new HashMap<String, Object>(
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
                                100));
        var neutral = Map.of("gainDb", -0.0, "pan", 0, "muted", false);
        recipe.put("mix", Map.of("lead", neutral));
        assertFalse(LocalMusic.validateRecipe(recipe).containsKey("mix"));

        var lead = Map.of("gainDb", -60, "pan", -1, "muted", true);
        var bass = Map.of("gainDb", 12, "pan", 1, "muted", false);
        recipe.put("mix", Map.of("lead", lead, "bass", bass));
        assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        var explicitNeutral = Map.of("gainDb", 0, "pan", 0, "muted", false, "tone", "neutral");
        recipe.put("mix", Map.of("lead", explicitNeutral));
        assertFalse(LocalMusic.validateRecipe(recipe).containsKey("mix"));
        var unchangedBass = new LinkedHashMap<String, Object>(bass);
        unchangedBass.put("tone", "neutral");
        recipe.put("mix", Map.of("bass", unchangedBass));
        assertEquals(Map.of("bass", bass), LocalMusic.validateRecipe(recipe).get("mix"));
        for (var tone : List.of("warm", "bright")) {
            var colored = Map.of("gainDb", 0, "pan", 0, "muted", false, "tone", tone);
            recipe.put("mix", Map.of("bass", colored));
            assertEquals(recipe, LocalMusic.validateRecipe(recipe));
        }
        for (Object invalidTone : Arrays.asList(null, false, 1, List.of("warm"), "dark")) {
            var strip = new HashMap<String, Object>(neutral);
            strip.put("tone", invalidTone);
            recipe.put("mix", Map.of("lead", strip));
            assertEquals(
                    "PLAN_INVALID",
                    assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
        }
        for (Object invalid :
                List.of(
                        List.of(),
                        Map.of("vocals", neutral),
                        Map.of("drums", neutral),
                        Map.of("drums", explicitNeutral),
                        Map.of("lead", Map.of("gainDb", 0, "pan", 0)),
                        Map.of("lead", Map.of("gainDb", 0, "pan", 0, "muted", false, "reverb", 1)),
                        Map.of("lead", Map.of("gainDb", "0", "pan", 0, "muted", false)),
                        Map.of("lead", Map.of("gainDb", -60.01, "pan", 0, "muted", false)),
                        Map.of("lead", Map.of("gainDb", 12.01, "pan", 0, "muted", false)),
                        Map.of("lead", Map.of("gainDb", Double.NaN, "pan", 0, "muted", false)),
                        Map.of(
                                "lead",
                                Map.of(
                                        "gainDb",
                                        0,
                                        "pan",
                                        Double.POSITIVE_INFINITY,
                                        "muted",
                                        false)),
                        Map.of("lead", Map.of("gainDb", 0, "pan", 1.01, "muted", false)),
                        Map.of("lead", Map.of("gainDb", 0, "pan", 0, "muted", 0)),
                        Map.of("lead", lead, "bass", lead))) {
            recipe.put("mix", invalid);
            assertEquals(
                    "PLAN_INVALID",
                    assertThrows(
                                    ApiProblem.class,
                                    () -> LocalMusic.validateRecipe(recipe),
                                    invalid.toString())
                            .code());
        }
        recipe.put("mix", null);
        assertEquals(
                "PLAN_INVALID",
                assertThrows(ApiProblem.class, () -> LocalMusic.validateRecipe(recipe)).code());
    }
}
