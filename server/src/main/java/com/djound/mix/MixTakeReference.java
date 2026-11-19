package com.djound.mix;

import java.text.Normalizer;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/** Explicit take numbers and unresolved names in a musician's request. */
record MixTakeReference(Set<Integer> named, boolean unclear) {
    private static final List<String> CARDINALS =
            List.of(
                    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
                            .split(" "));
    private static final List<String> ORDINALS =
            List.of(
                    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth"
                            .split(" "));
    private static final String NUMBER =
            "(?:[0-9]+(?:st|nd|rd|th)?|"
                    + String.join("|", CARDINALS)
                    + "|"
                    + String.join("|", ORDINALS)
                    + "|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)";
    private static final String TAKE_NOUN = "(?:take|version|preview)";
    private static final Pattern TAKE_NUMBER =
            Pattern.compile(
                    "\\b(?:"
                            + TAKE_NOUN
                            + "\\s*(?:#\\s*|(?:no\\.?|number)\\s+)?("
                            + NUMBER
                            + ")|("
                            + NUMBER
                            + ")\\s+"
                            + TAKE_NOUN
                            + ")\\b");
    private static final Pattern UNRESOLVED_NUMBER = Pattern.compile("\\b" + NUMBER + "\\b");
    private static final Pattern OTHER_NAME =
            Pattern.compile("\\b" + TAKE_NOUN + "\\s+(?:[ab]|named|called|labeled|labelled)\\b");

    static boolean mentionsName(String text) {
        return TAKE_NUMBER.matcher(text).find() || OTHER_NAME.matcher(text).find();
    }

    static MixTakeReference parse(String request) {
        String text = Normalizer.normalize(request, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT);
        var matcher = TAKE_NUMBER.matcher(text);
        var named = new LinkedHashSet<Integer>();
        StringBuilder remainder = new StringBuilder();
        boolean unclear = false;
        while (matcher.find()) {
            String token = matcher.group(1) == null ? matcher.group(2) : matcher.group(1);
            int n = Math.max(CARDINALS.indexOf(token), ORDINALS.indexOf(token));
            if (Character.isDigit(token.charAt(0)))
                try {
                    n = Integer.parseInt(token.replaceFirst("(?:st|nd|rd|th)$", ""));
                } catch (NumberFormatException e) {
                    unclear = true;
                }
            if (n > 0) named.add(n);
            else unclear = true;
            matcher.appendReplacement(remainder, " ");
        }
        matcher.appendTail(remainder);
        // A partially matched reference (e.g. "take twenty-three") must never select Take 20.
        String remaining =
                remainder
                        .toString()
                        .replaceAll("\\b(?:last|old|previous|earlier|other) one\\b", " ");
        return new MixTakeReference(
                named,
                unclear
                        || UNRESOLVED_NUMBER.matcher(remaining).find()
                        || OTHER_NAME.matcher(remaining).find());
    }
}
