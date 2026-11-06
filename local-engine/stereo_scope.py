"""Detect explicitly requested stereo positioning, which the arrangement schema cannot represent."""
import re

# Check command clauses, not isolated words such as "left", "pan" or "right".
# The intentionally narrow match lets other phrasings go to the normal planner.
_PREFIX = r"(?:(?:please|could you|can you|would you|let's|also|then)\s+)*"
_SOUND = r"(?:bass(?:\s+line)?|drums?|percussion|beat|piano|keys|guitar|lead|melody|backing|accompaniment|it)"
_OBJECT = r"(?:(?:the|my|that|this|all|both)\s+)*" + _SOUND
_SIDE = r"(?:left|right|cent(?:er|re))"
_DEST = r"(?:(?:over|hard|far|slightly|a little|more|across|to|towards?|on|in|into|out of|from|the|stereo)\s+)*" + _SIDE + r"(?=$|\s+(?:speaker|ear|channel|side|please)\b)"
_MOVE = re.compile(_PREFIX + r"(?:pan|shift|move|place|put|push|send|position|spread|split|route)\s+" +
                   _OBJECT + r"\s+" + _DEST)
_PAN = re.compile(_PREFIX + r"(?:pan\s+" + _OBJECT + r"\b|(?:add|apply|use|adjust)\s+(?:the |some )?(?:stereo )?panning\b)")
_SPEAKER = re.compile(_PREFIX + r"(?:i want(?: to hear)?|let me hear|make|have)\s+" + _OBJECT +
                      r"\s+(?:(?:play|come|sound|only|out|of|from|on|in|through|the)\s+)*" + _SIDE +
                      r"(?:[- ]hand)?\s+(?:channel|speaker|ear)\b")
_CLAUSES = re.compile(r"[,;.!?]|\b(?:and|but|then|while)\b")


def requests_stereo_change(text):
    text = " ".join(text.casefold().replace("’", "'").split())
    for clause in _CLAUSES.split(text):
        clause = clause.strip()
        if re.search(r"\b(?:not|never|without|except|unless|instead of|rather than)\b|n't\b", clause):
            continue
        if _MOVE.match(clause) or _PAN.match(clause) or _SPEAKER.match(clause):
            return True
    return False
