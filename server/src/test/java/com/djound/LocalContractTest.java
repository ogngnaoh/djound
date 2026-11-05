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
}
