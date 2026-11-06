package com.djound.local;

import com.djound.ApiProblem;

import java.math.BigDecimal;
import java.util.*;

/** Sparse mixer settings embedded in the immutable arrangement recipe. */
final class LocalMix {
    private static final List<String> TRACKS = List.of("lead", "bass", "drums");
    private static final Set<String> CONTROLS = Set.of("gainDb", "pan", "muted");
    private static final Set<String> CONTROLS_WITH_TONE = Set.of("gainDb", "pan", "muted", "tone");
    private static final Set<String> TONES = Set.of("neutral", "warm", "bright");

    private LocalMix() {}

    static Map<String, Object> canonical(Map<String, Object> recipe) {
        if (!recipe.containsKey("mix")) return Map.of();
        if (!(recipe.get("mix") instanceof Map<?, ?> mix) || !TRACKS.containsAll(mix.keySet()))
            throw invalid("Use mixer settings only for lead, bass and drums.");
        Map<String, Object> strips = new LinkedHashMap<>();
        boolean audible = false;
        for (String track : TRACKS) {
            boolean active = active(recipe, track);
            if (!mix.containsKey(track)) {
                audible |= active;
                continue;
            }
            if (!active) throw invalid("Enable " + track + " before changing its mix.");
            if (!(mix.get(track) instanceof Map<?, ?> strip)
                    || !(strip.keySet().equals(CONTROLS)
                            || strip.keySet().equals(CONTROLS_WITH_TONE))
                    || !(strip.get("muted") instanceof Boolean muted))
                throw invalid("Each mix strip needs gainDb, pan and a boolean muted value.");
            Number gain = bounded(strip.get("gainDb"), -60, 12, "gainDb");
            Number pan = bounded(strip.get("pan"), -1, 1, "pan");
            Object value = strip.containsKey("tone") ? strip.get("tone") : "neutral";
            if (!(value instanceof String tone) || !TONES.contains(tone))
                throw invalid("Choose neutral, warm or bright track tone.");
            audible |= !muted;
            if (!tone.equals("neutral"))
                strips.put(track, Map.of("gainDb", gain, "pan", pan, "muted", muted, "tone", tone));
            else if (gain.doubleValue() != 0 || pan.doubleValue() != 0 || muted)
                strips.put(track, Map.of("gainDb", gain, "pan", pan, "muted", muted));
        }
        if (!audible) throw invalid("Unmute at least one active track before creating a version.");
        return strips;
    }

    private static Number bounded(Object input, int min, int max, String name) {
        if (!(input instanceof Number number)
                || !Double.isFinite(number.doubleValue())
                || number.doubleValue() < min
                || number.doubleValue() > max)
            throw invalid(name + " must be a finite number between " + min + " and " + max + ".");
        double value = number.doubleValue();
        if (value == Math.rint(value)) return (int) value;
        return value;
    }

    private static boolean active(Map<String, Object> recipe, String track) {
        return track.equals("lead") || Boolean.TRUE.equals(recipe.get(track));
    }

    static String describe(Map<String, Object> recipe, Map<String, Object> previous) {
        Map<?, ?> mix = (Map<?, ?>) recipe.getOrDefault("mix", Map.of());
        Map<?, ?> prior = (Map<?, ?>) previous.getOrDefault("mix", Map.of());
        List<String> settings = new ArrayList<>();
        for (String track : TRACKS) {
            if (mix.get(track) instanceof Map<?, ?> strip) {
                double pan = ((Number) strip.get("pan")).doubleValue();
                String position =
                        pan == 0
                                ? "centered"
                                : BigDecimal.valueOf(Math.abs(pan))
                                                .movePointRight(2)
                                                .stripTrailingZeros()
                                                .toPlainString()
                                        + (pan < 0 ? "% left" : "% right");
                settings.add(
                        track
                                + " "
                                + strip.get("gainDb")
                                + " dB, "
                                + position
                                + (Boolean.TRUE.equals(strip.get("muted"))
                                        ? ", muted"
                                        : ", unmuted")
                                + describeTone(strip, prior.get(track)));
            } else if (prior.containsKey(track) && active(recipe, track)) {
                settings.add(
                        track
                                + " reset to 0 dB, centered and unmuted"
                                + describeTone(Map.of(), prior.get(track)));
            }
        }
        return settings.isEmpty() ? "" : " Reviewed mix: " + String.join("; ", settings) + ".";
    }

    private static String describeTone(Map<?, ?> strip, Object previous) {
        if (strip.containsKey("tone")) return ", " + strip.get("tone") + " tone";
        if (previous instanceof Map<?, ?> prior && prior.containsKey("tone"))
            return ", tone reset to neutral";
        return "";
    }

    private static ApiProblem invalid(String detail) {
        return new ApiProblem(409, "PLAN_INVALID", detail, "EDIT_TEXT");
    }
}
