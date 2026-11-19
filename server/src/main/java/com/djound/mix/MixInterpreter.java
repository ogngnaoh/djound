package com.djound.mix;

import java.math.BigDecimal;
import java.text.Normalizer;
import java.util.Locale;
import java.util.regex.Pattern;

/** Interprets one level edit against the settings and signed last move of the auditioned take. */
public final class MixInterpreter {
    public record Decision(String kind, String control, double gainDb, String message) {}

    private enum Action {
        SET_LEVEL,
        ADD_DB,
        SCALE_LAST,
        NUDGE_LAST
    }

    private record Move(Action action, double amount) {
        double resolve(double level, double lastMove) {
            return switch (action) {
                case SET_LEVEL -> amount;
                case ADD_DB -> level + amount;
                // The prior move began at level - lastMove, even when it was itself a refinement.
                case SCALE_LAST -> level + (amount - 1) * lastMove;
                case NUDGE_LAST -> level + Math.signum(lastMove) * amount;
            };
        }
    }

    private static Pattern words(String alternatives) {
        return Pattern.compile("\\b(?:" + alternatives + ")\\b");
    }

    private static final String VOICE = "vocals?|vox|singer|singing|voices?|vocalists?";
    private static final String KIT = "drums?|drum ?kit|percussion|drummer|kit";
    private static final Pattern VOCALS = words(VOICE);
    private static final Pattern DRUMS = words(KIT);
    private static final Pattern PRESERVE =
            words(
                    "(?:leave|keep) (?:the )?(?:"
                            + VOICE
                            + "|"
                            + KIT
                            + ") (?:alone|untouched|unchanged|as (?:they|it) (?:are|is)|where"
                            + " (?:they|it) (?:are|is))");
    // The caller supplies exactly one selected range. Resolve mentions of that range before
    // interpreting musical targets, quantities, or historical takes, all of which have their own
    // scope.
    private static final String RANGE_NOUN =
            "section|passage|part|phrase|selection|range|region|excerpt|segment|bars";
    private static final String SELECTED = "selected|highlighted|marked|chosen";
    private static final Pattern CURRENT_RANGE =
            words(
                    "(?:(?:in|on|for|over|across|throughout|during|within|through|inside) )?"
                            + "(?:(?:this|(?:the |my )?(?:currently )?(?:"
                            + SELECTED
                            + "|current)) "
                            + "(?:"
                            + RANGE_NOUN
                            + "|bit)|(?:this|the|my|our) selection|(?:the )?(?:"
                            + RANGE_NOUN
                            + ") "
                            + "(?:i|we) (?:just )?(?:"
                            + SELECTED
                            + ")|what (?:i|we) (?:just )?(?:"
                            + SELECTED
                            + "))");
    private static final Pattern UNRESOLVED_RANGE =
            words(
                    RANGE_NOUN
                            + "|first half|second half|last half|beginning of|end of|first"
                            + " quarter|final quarter");
    // A discourse marker at the edge adds this turn to the conversation. The same words
    // between clauses can introduce another edit, so leave those for the compound request check.
    private static final Pattern ADDITIVE_FRAME =
            Pattern.compile(
                    "^(?:(?:and )?also|additionally|in addition)[, ]+|(?:[, ]+(?:as"
                        + " well|too|also))(?=[.!?]*$)");
    private static final Pattern REFERENCE =
            words(
                    "(?:last|previous|earlier|prior|other|old|that) (?:take|version|preview|one)|as"
                            + " before|from before");
    private static final Pattern COPY =
            words("keep|copy|reuse|retain|match|restore|use|borrow|bring back|same|from|like");
    private static final Pattern UNSUPPORTED =
            words(
                    "not|never|don't|dont|except|without|unless|instead"
                        + " of|guitars?|pianos?|bass|kick|snare|cymbals?|hi[- ]?hats?|backing"
                        + " vocals?|background"
                        + " vocals?|reverb|delay|distort(?:ion)?|compress(?:ion)?|eq|tempo|pitch|octaves?|semitones?|fade|fading|gradually|pan(?:ning)?|stereo|lyrics?|brighter|warmer|darker|mute|isolate|solo|remove|replace|denoise|echo|chorus|verse|bridge|intro|outro|whole"
                        + " (?:song|track)|everywhere");
    private static final Pattern MULTIPLE =
            words("and|then|or|between|through|from amount to|amount to amount");
    private static final Pattern UP =
            words(
                    "turn up|up|higher|louder|raise|boost|lift|increase|amplify|forward|foreground|front|closer|stronger|prominent|prominence|above|stand"
                        + " out|more present|more presence|more audible|too (?:soft|quiet|low)|hard"
                        + " to hear|buried|lost in the mix|push through");
    private static final Pattern DOWN =
            words(
                    "down|lower|quieter|softer|reduce|decrease|cut|drop|soften|attenuate|duck|tame|tuck|recede|behind|back|too"
                        + " (?:loud|strong|high|far forward|forward)|overpowering|overpowers|less"
                        + " prominent");
    private static final Pattern LESS =
            words(
                    "less|smaller|gentler|weaker|back (?:it |that |this )?off|ease"
                            + " (?:(?:it|that|this) )?(?:back|off)|dial (?:it|that|this) back|pull"
                            + " (?:it|that|this) back|scale (?:it|that|this) back|back off|too"
                            + " much|went too far|undo some|notch back");
    private static final Pattern MORE =
            words("more|further|farther|bigger|stronger|another|extra|keep going");
    private static final Pattern REPEAT = words("repeat|again|same direction|same amount");
    private static final Pattern MOVE_NOUN =
            words(
                    "change|move|adjustment|step|boost|lift|cut|increase|decrease|reduction|push|bump|dip|amount");
    private static final Pattern BASELINE =
            words(
                    "unity|neutral|reset|(?:starting|initial|original|unmodified|default|baseline)"
                            + " (?:gain|level|volume|setting)|back to baseline");
    private static final Pattern UNDO = words("undo|reverse|revert|roll back");
    private static final Pattern SMALL =
            words(
                    "slightly|slight|subtly|subtle|touch|tiny|tad|bit|little|hair|smidge|gently|nudge|hint|notch");
    private static final Pattern ASSIGN = words("set|target|at amount|to amount|amount instead");
    private static final Pattern RELATIVE =
            words("by amount|another amount|additional amount|extra amount");
    private static final Pattern REFERENT = words("it|that|this|them|those|last|previous|prior");
    // Conversational framing carries no musical action or target. Action and magnitude must still
    // parse.
    private static final Pattern FRAMING =
            words(
                    "on second thought|on reflection|let me hear|i think|i'd like to|i would like"
                        + " to|would you mind|how about|let's|shall"
                        + " we|can|could|would|please|thanks|thank you|"
                        + "i|we|you|me|my|us|a|an|the|of|for|by|as|much|than|to|at|in|on|from|with|"
                        + "its|it's|is|are|was|were|them|they|those|this|it|that|before|earlier|last|prior|previous|time|"
                        + "just|only|instead|actually|maybe|rather|even|still|there|here|some|really|now|right|out|off|"
                        + "make|put|give|get|go|do|let|try|turn|take|bring|return|restore|dial|ease|pull|push|hear|heard|want|person|lead|level|levels|gain|volume|setting|direction|same|other|way|as"
                        + " much|as far|while it's highlighted|while highlighted");
    private static final Pattern RATIO_CONTEXT =
            words(
                    "as much|as"
                        + " far|difference|halfway|back|off|less|more|smaller|bigger|stronger|weaker|meet|between|undo|scale|keep|use|times");
    private static final Pattern SPLIT =
            words("split the difference|meet (?:me |us )?(?:halfway|half way)|half way");

    private static final Pattern LAST_MOVE =
            words(
                    "change|move|adjustment|step|previous|last|prior|that"
                            + " (?:boost|cut|lift|increase|reduction|bump)|as much|as far");
    private static final Pattern RELATIVE_FRACTION =
            words(
                    "amount"
                        + " (?:less|smaller|weaker|more|bigger|stronger)|(?:less|smaller|weaker|more|bigger|stronger)"
                        + " by amount");
    private static final Pattern REMOVE_FRACTION =
            words(
                    "back (?:it |that )?off (?:by )?amount|ease (?:it |that )?back by amount|take"
                            + " amount off|reduce .*? by amount");
    private static final Pattern TWO_TAKE_OPTIONS =
            Pattern.compile(
                    "\\b(?:and|or)\\s+(?:(?:the )?(?:take|version|preview)\\b|(?:the"
                        + " )?(?:first|second|third|previous|earlier) (?:take|version|preview))");
    private static final Pattern MOVE_REFERENCE =
            words(
                    "(?:last|previous|prior|that) (?:amount"
                        + " )?(?:boost|lift|cut|drop|increase|decrease|reduction|push|bump|dip)");
    private static final Pattern LAST = words("last|previous|prior");
    private static final Pattern PHYSICAL =
            words(
                    "up|down|higher|lower|louder|quieter|softer|forward|behind|more present|more"
                            + " audible|less prominent");

    private static boolean has(Pattern pattern, String text) {
        return pattern.matcher(text).find();
    }

    private static String remove(String text, Pattern... patterns) {
        for (Pattern pattern : patterns) text = pattern.matcher(text).replaceAll(" ");
        return text;
    }

    private static boolean fullyUnderstood(String parsed) {
        return remove(
                        parsed,
                        SMALL,
                        REPEAT,
                        UP,
                        DOWN,
                        LESS,
                        MORE,
                        BASELINE,
                        UNDO,
                        ASSIGN,
                        RELATIVE,
                        MOVE_NOUN,
                        RATIO_CONTEXT,
                        VOCALS,
                        DRUMS,
                        FRAMING)
                .replaceAll("[\\s,.!?'’:+-]", "")
                .isEmpty();
    }

    private static Decision clarify(String message) {
        return new Decision("CLARIFY", null, 0, message);
    }

    public static Decision interpret(
            String text, String lastControl, double vocalsDb, double drumsDb, double lastChangeDb) {
        if (text == null || text.isBlank() || text.length() > 1000)
            return clarify("Describe one level change for the selected passage.");
        String normalized =
                Normalizer.normalize(
                                text.replaceAll("(?<=[0-9])½", " and a half"), Normalizer.Form.NFKC)
                        .toLowerCase(Locale.ROOT)
                        .replace('’', '\'')
                        .replace('−', '-')
                        .replace('⁄', '/')
                        .strip()
                        .replaceAll("\\s+", " ");
        String scoped =
                CURRENT_RANGE.matcher(normalized).replaceAll(" ").replaceAll("\\s+", " ").strip();
        if (has(UNRESOLVED_RANGE, scoped))
            return clarify(
                    "Select the passage you want to change, then describe the level adjustment.");
        var preserve = PRESERVE.matcher(ADDITIVE_FRAME.matcher(scoped).replaceAll(" ").strip());
        boolean preserveVoice = false, preserveDrums = false;
        while (preserve.find()) {
            preserveVoice |= has(VOCALS, preserve.group());
            preserveDrums |= has(DRUMS, preserve.group());
        }
        String request =
                preserve.replaceAll(" ")
                        .replaceAll("^[\\s,]*(?:and\\b)?|(?:\\band)?[\\s,.!?]*$", "")
                        .strip();
        boolean voice = has(VOCALS, request), drums = has(DRUMS, request);
        boolean named = voice || drums;
        String control = voice ? "vocals" : drums ? "drums" : lastControl;
        if (voice && drums
                || preserveVoice && "vocals".equals(control)
                || preserveDrums && "drums".equals(control))
            return clarify("Which should change first, vocals or drums?");
        if (has(UNSUPPORTED, request))
            return clarify(
                    "Which vocal or drum level change would you like in the selected passage?");
        if (!"vocals".equals(control) && !"drums".equals(control))
            return clarify("Which level should change, vocals or drums?");
        if ((has(REFERENCE, request) || MixTakeReference.mentionsName(request))
                && has(COPY, request)) {
            if (!named) return clarify("Which setting should I copy from the other take?");
            // A choice of two take names is resolved by the conversation using actual history.
            if (has(MULTIPLE, TWO_TAKE_OPTIONS.matcher(request).replaceAll(" ")))
                return clarify("Which single setting should I copy?");
            return new Decision("COPY", control, 0, "Copy " + control + " from an earlier take.");
        }

        MixLevelQuantity quantity =
                MixLevelQuantity.read(SPLIT.matcher(request).replaceAll("half as much"));
        String parsed = quantity.text();
        if (quantity.unit() == MixLevelQuantity.Unit.INVALID || has(MULTIPLE, parsed))
            return clarify("Give one final level or one adjustment for the selected passage.");
        if (!fullyUnderstood(parsed))
            return clarify("What level change should I make to the vocals or drums?");

        double level = "vocals".equals(control) ? vocalsDb : drumsDb;
        boolean hasHistory =
                control.equals(lastControl) && Double.isFinite(lastChangeDb) && lastChangeDb != 0;
        Move move = parseMove(parsed, quantity, named, hasHistory, level, lastChangeDb);
        if (move == null)
            return clarify(
                    "Should the level go up or down, or should I revise the previous change?");
        double result = move.resolve(level, lastChangeDb);
        if (!Double.isFinite(result) || Math.abs(result) > 12)
            return clarify("Choose a level between −12 and +12 dB.");
        result = Math.rint(result * 100) / 100;
        String label = "vocals".equals(control) ? "Vocals" : "Drums";
        return new Decision(
                "SET",
                control,
                result,
                label
                        + " "
                        + (result > 0 ? "+" : "")
                        + BigDecimal.valueOf(result).stripTrailingZeros().toPlainString()
                        + " dB");
    }

    private static Move parseMove(
            String s,
            MixLevelQuantity q,
            boolean named,
            boolean hasHistory,
            double level,
            double lastMove) {
        boolean db = q.unit() == MixLevelQuantity.Unit.DB;
        boolean ratio = q.unit() == MixLevelQuantity.Unit.RATIO;
        boolean less = has(LESS, s), more = has(MORE, s);
        boolean base = has(BASELINE, s), undo = has(UNDO, s);
        if (less && more || base && (ratio || db && q.value() != 0)) return null;
        if (base) return new Move(Action.SET_LEVEL, 0);
        if (ratio) {
            // Percentages of a level, a dB move, and linear audio amplitude are different requests.
            // Resolve a proportion only when the user refers to the move itself.
            boolean prior =
                    has(LAST_MOVE, s)
                            || !named
                                    && (has(REFERENT, s)
                                            || has(RATIO_CONTEXT, s)
                                            || s.equals("amount"));
            if (!hasHistory
                    || q.signed()
                    || !prior
                    || (has(UP, s) || has(DOWN, s)) && !has(LAST_MOVE, s) && !has(LESS, s)
                    || has(ASSIGN, s) && !has(LAST_MOVE, s)) return null;
            double factor = q.value();
            if (has(RELATIVE_FRACTION, s)) factor = 1 + (less ? -factor : factor);
            else if (undo || has(REMOVE_FRACTION, s)) factor = 1 - factor;
            if (factor < 0) return null;
            return new Move(Action.SCALE_LAST, factor);
        }
        if (undo) return hasHistory && !db ? new Move(Action.SCALE_LAST, 0) : null;

        // A listener's description of excess already contains its direction (e.g. "too soft").
        String directions =
                s.replace("too far forward", "quieter")
                        .replace("too forward", "quieter")
                        .replace("less prominent", "quieter")
                        .replace("too soft", "louder")
                        .replace("too quiet", "louder");
        boolean up = has(UP, directions), down = has(DOWN, directions);
        if (up && down) return null;
        boolean assign =
                db && !has(RELATIVE, s) && (has(ASSIGN, s) || shorthand(s, named, q.signed()));
        if (assign) {
            double value = q.value();
            if (!q.signed() && less && hasHistory) value = Math.copySign(value, level);
            return new Move(Action.SET_LEVEL, value);
        }
        boolean repeats = has(REPEAT, s);
        boolean refersToMove = !named || has(LAST, s);
        if (repeats && refersToMove && !db && !up && !down && !less && !more)
            return hasHistory ? new Move(Action.SCALE_LAST, 2) : null;
        double step = db ? Math.abs(q.value()) : has(SMALL, s) ? 1 : 2;
        // "A bit more" continues a previous cut, while "a bit louder" physically raises the level.
        if (refersToMove
                && (less || more)
                && !has(PHYSICAL, MOVE_REFERENCE.matcher(s).replaceAll(" "))) {
            if (!hasHistory) return null;
            return new Move(
                    Action.NUDGE_LAST,
                    (db ? step : Math.min(1, Math.abs(lastMove))) * (less ? -1 : 1));
        }
        if ((up && q.signed() && q.value() < 0) || (down && q.signed() && q.value() > 0))
            return null;
        if (up || down || named && (more || less))
            return new Move(Action.ADD_DB, step * (down || !up && less ? -1 : 1));
        return null;
    }

    private static boolean shorthand(String s, boolean named, boolean signed) {
        // A bare signed number is a replacement, including the common "make that +1" ellipsis.
        String rest = remove(s, VOCALS, DRUMS, FRAMING).replaceAll("[\\s,.!?'’:]", "");
        return (named || signed) && rest.equals("amount");
    }

    private MixInterpreter() {}
}
