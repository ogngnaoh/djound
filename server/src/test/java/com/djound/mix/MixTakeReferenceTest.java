package com.djound.mix;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

import java.util.Set;

class MixTakeReferenceTest {
    @Test
    void namedReferencesCannotSilentlyFallBackToTheLatestDrumTake() {
        for (String phrase :
                new String[] {
                    "Use drums from version 2",
                    "Drums like preview #2",
                    "Use the second take's drums",
                    "Keep the drums from take two",
                    "Match the 2nd version for drums"
                }) {
            assertEquals("COPY", MixInterpreter.interpret(phrase, "vocals", 1, 0, 1).kind());
            assertEquals(new MixTakeReference(Set.of(2), false), MixTakeReference.parse(phrase));
        }
        assertEquals(
                Set.of(2, 3),
                MixTakeReference.parse("Use the drums from version 2 or preview 3").named());
        assertTrue(MixTakeReference.parse("Use drums from take twenty-three").unclear());
        assertTrue(MixTakeReference.parse("Use drums from the second-to-last take").unclear());
        assertTrue(MixTakeReference.parse("Use drums from version B").unclear());
        assertEquals(
                new MixTakeReference(Set.of(), false),
                MixTakeReference.parse("Use drums from the last take"));
    }
}
