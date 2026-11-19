package com.djound.rendition;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

import tools.jackson.databind.ObjectMapper;

import java.io.IOException;

class RenditionTimelineTest {
    private static final ObjectMapper json = new ObjectMapper();

    @Test
    void combinesTempoChangesFromBothPerformances() throws IOException {
        int[][] child = {{0, 0}, {100, 50}, {200, 200}};
        int[][] parent = {{0, 0}, {125, 400}, {200, 450}};
        assertArrayEquals(
                new int[][] {{0, 0}, {100, 160}, {150, 400}, {200, 450}},
                RenditionTimeline.toOriginal(child, parent));
        int[][] intro = {{0, 0}, {10, 0}, {20, 10}};
        assertArrayEquals(
                new int[][] {{0, 0}, {10, 0}, {15, 2}, {20, 10}},
                RenditionTimeline.toOriginal(intro, new int[][] {{0, 0}, {5, 2}, {10, 10}}));
    }

    @Test
    void rejectsUnmappedResizesInvalidLandmarksAndRetimedPassages() throws IOException {
        var none = json.readTree("null");
        assertNull(RenditionTimeline.workerMap(none, 100, 100, false));
        assertThrows(IOException.class, () -> RenditionTimeline.workerMap(none, 102, 100, false));
        var stretched = json.readTree("[[0,0],[30,20],[100,100]]");
        assertThrows(
                IOException.class, () -> RenditionTimeline.workerMap(stretched, 100, 100, true));
        assertArrayEquals(
                new int[][] {{0, 0}, {30, 20}, {100, 100}},
                RenditionTimeline.workerMap(stretched, 100, 100, false));
        for (String bad :
                new String[] {
                    "[[0,0],[20,90],[40,80],[100,100]]",
                    "[[0,0],[100,99]]",
                    "[[0,0],[0,5],[100,100]]",
                    "[[0,0],[40.5,20],[100,100]]",
                    "[[0,0],[100,4294967396]]"
                }) {
            assertThrows(
                    IOException.class,
                    () -> RenditionTimeline.workerMap(json.readTree(bad), 100, 100, false));
        }
    }

    @Test
    void boundsAndVersionsInternalPlans() throws IOException {
        assertEquals("{\"version\":1}", RenditionTimeline.plan(json.readTree("{\"version\":1}")));
        assertThrows(
                IOException.class, () -> RenditionTimeline.plan(json.readTree("{\"version\":2}")));
        assertThrows(
                IOException.class,
                () ->
                        RenditionTimeline.plan(
                                json.createObjectNode()
                                        .put("version", 1)
                                        .put("arrangement", "a".repeat(48 * 1024))));
    }
}
