package com.djound.mix;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * One level, fraction of a prior move, or no explicit quantity. Text retains the quantity's
 * position.
 */
record MixLevelQuantity(Unit unit, double value, boolean signed, String text) {
    enum Unit {
        NONE,
        DB,
        RATIO,
        INVALID
    }

    private static final List<String> WORDS =
            List.of(
                    "zero one two three four five six seven eight nine ten eleven twelve"
                            .split(" "));
    private static final String COUNT = String.join("|", WORDS);
    private static final String DECIMAL = "(?:[0-9]+(?:\\.[0-9]+)?|\\.[0-9]+)";
    private static final String ATOM = "(?:" + DECIMAL + "|" + COUNT + ")";
    private static final String LEVEL = "(?:decibels?|d\\s*b)";
    private static final String FRACTION =
            "(?:one[- ]quarter|a quarter|(?:one[- ])?third|two[- ]thirds|three[-"
                    + " ]quarters|quarter|half(?:way)?|halve|twice|double|[0-9]+\\s*/\\s*[0-9]+)";
    private static final Pattern MIXED =
            Pattern.compile("\\b(" + ATOM + ") and (?:a |one )?half(?=\\s+" + LEVEL + "\\b)");
    private static final Pattern QUANTITY =
            Pattern.compile(
                    "(?<![a-z0-9.])(?:(plus|minus|negative|positive)\\s+|([+-])\\s*)?("
                            + FRACTION
                            + "|"
                            + ATOM
                            + "|an?(?=\\s+"
                            + LEVEL
                            + "\\b))"
                            + "(?:\\s*(?:of\\s+)?(?:a\\s+|one\\s+)?("
                            + LEVEL
                            + ")\\b|\\s*(%|percent(?:age)?|per cent))?(?![a-z0-9]|\\.\\d)");
    private static final Pattern FRACTION_WORD = Pattern.compile(FRACTION);

    static MixLevelQuantity read(String input) {
        String s = MIXED.matcher(input).replaceAll(m -> Double.toString(number(m.group(1)) + .5));
        Matcher m = QUANTITY.matcher(s);
        if (!m.find()) return new MixLevelQuantity(Unit.NONE, 0, false, s);
        boolean signed = m.group(1) != null || m.group(2) != null;
        String token = m.group(3);
        boolean fraction = FRACTION_WORD.matcher(token).matches();
        double n = fraction ? fraction(token) : number(token);
        boolean percent = m.group(5) != null;
        Unit unit = percent || fraction && m.group(4) == null ? Unit.RATIO : Unit.DB;
        if (percent) n /= 100;
        if ("minus".equals(m.group(1)) || "negative".equals(m.group(1)) || "-".equals(m.group(2)))
            n = -n;
        String masked = s.substring(0, m.start()) + " amount " + s.substring(m.end());
        if (m.find() || !Double.isFinite(n)) unit = Unit.INVALID;
        return new MixLevelQuantity(unit, n, signed, masked.strip().replaceAll("\\s+", " "));
    }

    private static double number(String token) {
        if (token.equals("a") || token.equals("an")) return 1;
        int index = WORDS.indexOf(token);
        return index >= 0 ? index : Double.parseDouble(token);
    }

    private static double fraction(String token) {
        String value = token.replace('-', ' ');
        if (value.contains("/")) {
            String[] terms = value.split("/");
            return Double.parseDouble(terms[0].strip()) / Double.parseDouble(terms[1].strip());
        }
        return switch (value) {
            case "twice", "double" -> 2;
            case "quarter", "a quarter", "one quarter" -> .25;
            case "three quarters" -> .75;
            case "third", "one third" -> 1.0 / 3;
            case "two thirds" -> 2.0 / 3;
            default -> .5;
        };
    }
}
