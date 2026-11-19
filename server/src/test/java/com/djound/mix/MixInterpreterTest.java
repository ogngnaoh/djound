package com.djound.mix;

import static com.djound.mix.MixInterpreter.interpret;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class MixInterpreterTest {
    @Test
    void refinementsUseHeardDirectionAndExplicitTargets() {
        assertEquals(2, interpret("Bring the vocal forward here.", null, 0, 0, 0).gainDb());
        assertEquals(1, interpret("A little less.", "vocals", 2, 0, 2).gainDb());
        assertEquals(-1, interpret("A little less!", "drums", 1, -2, -2).gainDb());
        assertEquals(
                -3,
                interpret("Could you turn the drums down by one decibel?", "vocals", 1, -2, 1)
                        .gainDb());
        assertEquals(
                -3, interpret("I'd like to turn the kit down 1 dB.", "vocals", 1, -2, 1).gainDb());
        assertEquals(-1, interpret("Set the singer to -1 dB.", null, 2, 0, 0).gainDb());
        assertEquals(-4, interpret("Take the drums down", "vocals", 1, -2, 1).gainDb());
        assertEquals(-1, interpret("Bring the drums down to -1 dB.", "vocals", 1, -2, 1).gainDb());
        assertEquals(
                -1,
                interpret("Leave the vocals alone and lower the drums a little.", "vocals", 1, 0, 1)
                        .gainDb());
        assertEquals(-2, interpret("Duck the percussion by two decibels.", null, 0, 0, 0).gainDb());
        assertEquals(-4, interpret("Put the drums at minus four dB.", null, 0, 1, 0).gainDb());
        assertEquals(-2, interpret("Back off half of that last cut.", "drums", 0, -4, -4).gainDb());
        assertEquals(
                0, interpret("Return the voice to its initial level.", "vocals", 1, 0, 1).gainDb());
        assertEquals("CLARIFY", interpret("process the vocal four decibels", null, 0, 0, 0).kind());
        assertEquals(
                "COPY", interpret("Keep the drums from the last take.", "vocals", 1, -2, 1).kind());
        assertEquals("CLARIFY", interpret("a little less", null, 0, 0, 0).kind());
    }

    @Test
    void aFollowupCanSpecifyAFinalMagnitudeOrFractionOfTheLastMove() {
        assertEquals(1, interpret("Back that off to one dB.", "vocals", 2, 0, 2).gainDb());
        assertEquals(-1, interpret("Back that off to one dB.", "drums", 1, -3, -3).gainDb());
        assertEquals(1, interpret("Set it to +1 dB.", "drums", 1, -3, -3).gainDb());
        assertEquals(-1, interpret("Only half as much, please.", "drums", 0, -2, -2).gainDb());
        assertEquals(3, interpret("Only half as much, please.", "vocals", 4, -2, 2).gainDb());
        assertEquals("CLARIFY", interpret("Only half as much reverb.", "vocals", 4, -2, 2).kind());
    }

    private static void expectLevel(
            String text, String control, double level, double lastMove, double expected) {
        var decision =
                interpret(
                        text,
                        control,
                        "vocals".equals(control) ? level : 0,
                        "drums".equals(control) ? level : 0,
                        lastMove);
        assertEquals("SET", decision.kind(), text);
        assertEquals(control, decision.control(), text);
        assertEquals(expected, decision.gainDb(), .005, text);
    }

    @Test
    void separatesSignedReplacementsFromDirectionalDecibelMoves() {
        expectLevel("Make that +1 dB instead.", "vocals", 2, 2, 1);
        expectLevel("Actually, try minus one and a half decibels.", "drums", -4, -4, -1.5);
        expectLevel("How about +.25?", "vocals", 2, 2, .25);
        expectLevel("Drums: +1 dB.", "drums", -4, -4, 1);
        expectLevel("Turn that up by half a decibel.", "drums", -4, -4, -3.5);
        expectLevel("Could we go a little louder?", "drums", -4, -4, -3);
        expectLevel("A little more.", "drums", -4, -4, -5);
        expectLevel("Bring that up a bit more.", "drums", -4, -4, -3);
        expectLevel("Duck the singer a quarter of a decibel.", "vocals", 2, 2, 1.75);
        expectLevel("Nudge the drums down by ½ dB.", "drums", -2, -2, -2.5);
        expectLevel("Return the singing to its starting level.", "vocals", 3, 2, 0);
        expectLevel("Undo that last move.", "drums", -4, -2, -2);
    }

    @Test
    void proportionsApplyToTheSignedLastMoveFromItsActualStartingLevel() {
        expectLevel("Can we halve that change?", "drums", -4, -4, -2);
        expectLevel("Just a quarter of that adjustment, please.", "vocals", 5, 4, 2);
        expectLevel("Give me three quarters as much.", "drums", -6, -4, -5);
        expectLevel("Make the last change 25% smaller.", "vocals", 5, 4, 4);
        expectLevel("Undo three quarters of that cut.", "drums", -6, -4, -3);
        expectLevel("Back off 25% of the previous boost.", "vocals", 5, 4, 4);
        expectLevel("Could we double that last move?", "drums", -6, -4, -10);
        expectLevel("Use 1/3 of the previous increase.", "vocals", 5, 3, 3);
        expectLevel("Split the difference.", "vocals", 5, 4, 3);
        expectLevel("Repeat that last step.", "drums", -4, -2, -6);
    }

    @Test
    void insufficientHistoryConflictingMovementsAndUnsupportedActionsDoNotRender() {
        for (String text :
                new String[] {"halve that change", "A little less", "repeat the last step"})
            assertEquals("CLARIFY", interpret(text, "vocals", 2, 0, 0).kind(), text);
        for (String text :
                new String[] {
                    "halve the last drums change", "more triangle, please",
                            "resample that +1 dB instead",
                    "give the singer a 50% volume boost", "raise it by 50%",
                            "give me 2 or 3 dB more",
                    "take it up and down", "keep vocals alone and make it louder",
                            "vocals down a semitone",
                    "process vocals at +2 dB", "cut the vocals by 1/0 dB",
                            "copy the drums from take one and add a melody",
                    "give that a 3 dB lift in the first second", "leave it between -2 and +1 dB"
                }) assertEquals("CLARIFY", interpret(text, "vocals", 2, 0, 2).kind(), text);
    }

    @Test
    void bindsScopeToTheSelectedFramesAndClarifiesOtherRanges() {
        for (String scope :
                new String[] {
                    "for this section",
                    "across this phrase",
                    "throughout the highlighted bars",
                    "in the passage I just selected",
                    "over my selection",
                    "during the current excerpt",
                    "within what we highlighted"
                })
            expectLevel("Take the drums down two decibels " + scope + ".", "drums", 0, 0, -2);
        for (String scope :
                new String[] {
                    "in another section",
                    "across the next phrase",
                    "in the last half of this selection",
                    "in the first quarter of this passage",
                    "in the last four bars",
                    "in the third region",
                    "in the bridge"
                })
            assertEquals(
                    "CLARIFY",
                    interpret("Take the drums down two decibels " + scope, "drums", 0, 0, 0).kind(),
                    scope);
        assertEquals(
                "CLARIFY",
                interpret("Copy vocals from the last take for the next section", "vocals", 2, 0, 2)
                        .kind());
    }

    @Test
    void additiveDiscourseIntroducesOneMoreEdit() {
        expectLevel("Take the vocal down by one decibel as well.", "vocals", 0, 0, -1);
        expectLevel("Set the drums to minus one dB too.", "drums", 0, 0, -1);
        expectLevel("Also, nudge the vocal up.", "vocals", 0, 0, 1);
        expectLevel("For this bit bring the drums down.", "drums", 0, 0, -2);
        assertEquals(
                "CLARIFY",
                interpret("Lift the vocal as well as the drums.", "drums", 0, 0, 0).kind());
        assertEquals(
                "CLARIFY",
                interpret("Lift the vocal and also add a trumpet.", "vocals", 1, 0, 1).kind());
    }

    @Test
    void clarifyUnsupportedTargetsOrMultipleChanges() {
        for (var text :
                new String[] {
                    "more cowbell",
                    "turn the guitar up",
                    "vocals up and drums down",
                    "give the singer more reverb",
                    "raise just the snare",
                    "please don't lift the vocals",
                    "boost vocals by 45 dB",
                    "change tempo",
                    "raise the vocals an octave",
                    "gradually lower the drums",
                    "less guitar",
                    "lower the vocal in the chorus"
                }) {
            assertEquals("CLARIFY", interpret(text, "drums", 1, -2, 1).kind(), text);
        }
    }
}
