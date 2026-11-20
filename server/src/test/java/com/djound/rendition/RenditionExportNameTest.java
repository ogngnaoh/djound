package com.djound.rendition;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;

class RenditionExportNameTest {
    @Test
    void unsafePathAndHeaderCharactersCannotHideOrRenameTheDownload() {
        assertEquals(
                "Nuit Étude - Edit 3 - Café Trio.wav",
                RenditionExportName.take(
                        "..Nuit :\r\nÉtude.WAV", 3, "Cafe\u0301/\\\"\u202eTrio...?"));
        assertEquals("Recording - Edit 7.wav", RenditionExportName.take(".mp3", 7, "../:*?"));
        assertEquals("_CON.1 - Original.wav", RenditionExportName.original("CON.1.m4a"));
        assertEquals("Op. 27 - Original.wav", RenditionExportName.original("Op. 27"));
    }

    @Test
    void multibyteNamesKeepTheCompleteTakeNumberAndExtension() {
        String title = "曲".repeat(60);
        String label = "🎹".repeat(60);
        String name = RenditionExportName.take(title + ".mp3", 91, label);
        assertEquals("曲".repeat(33) + " - Edit 91 - " + "🎹".repeat(25) + ".wav", name);
        assertTrue(name.getBytes(StandardCharsets.UTF_8).length <= 255);
    }
}
