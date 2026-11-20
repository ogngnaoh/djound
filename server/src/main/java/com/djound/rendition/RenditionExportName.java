package com.djound.rendition;

import java.nio.charset.StandardCharsets;
import java.text.Normalizer;

final class RenditionExportName {
    private RenditionExportName() {}

    static String original(String title) {
        return recordingName(title) + " - Original.wav";
    }

    static String take(String title, long number, String label) {
        String name = recordingName(title) + " - Edit " + number;
        String description = component(label);
        return (description.isEmpty() ? name : name + " - " + description) + ".wav";
    }

    private static String recordingName(String title) {
        String name = component(title.replaceFirst("(?i)\\.(wav|mp3|m4a)$", ""));
        if (name.isEmpty()) return "Recording";
        // Windows treats a device name before the first dot as reserved even with more extensions.
        return name.replaceFirst("(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?= *\\.)", "_$1");
    }

    private static String component(String value) {
        String clean =
                Normalizer.normalize(value, Normalizer.Form.NFC)
                        .replaceAll("[\\p{Cc}\\p{Cf}\\p{Cs}\\p{Z}\\\\/:*?\"<>|]+", " ")
                        .replaceAll("^[. ]+|[. ]+$", "");
        byte[] bytes = clean.getBytes(StandardCharsets.UTF_8);
        // Reserve room for both names, the complete take number and the extension in 255 bytes.
        int end = 100;
        if (bytes.length <= end) return clean;
        while ((bytes[end] & 0xc0) == 0x80) end--;
        return new String(bytes, 0, end, StandardCharsets.UTF_8).stripTrailing();
    }
}
