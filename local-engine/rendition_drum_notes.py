"""Read explicit, counted kit notes without assigning another player's rhythm to the drummer."""
from functools import lru_cache
import re


@lru_cache(maxsize=1)
def _kit_names():
    from pretty_midi import DRUM_MAP

    names = {name.lower().replace("-", " "): key for key, name in enumerate(DRUM_MAP, 35)}
    names.update({"kick": 36, "kick drum": 36, "snare": 38, "snare drum": 38, "rim": 37,
                  "rim click": 37, "rim tap": 37, "cross stick": 37, "sidestick": 37,
                  "clap": 39, "hi hat": 42, "ride cymbal": 51, "crash cymbal": 49})
    pattern = r"\b(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")s?\b"
    return names, re.compile(pattern)


BEATS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
COUNT = r"(?:one|two|three|four|five|six|[1-6])\b"
SEPARATOR = r"(?:\s+and\s+|\s*&\s*|,\s*(?:and\s+)?)"
PLACEMENT = re.compile(r"\s+(?:only\s+)?on\s+(?:(?:the\s+)?beats?\s+)?("
                       + COUNT + "(?:" + SEPARATOR + COUNT + r")*|(?:every|each) beat|(?:the )?offbeats?)\b")
NEGATIVE = re.compile(r"\b(?:no|not|never|without|avoid|omit|remove|instead of)\b|n['’]t\b")


def counted_drum_notes(text, meter):
    """Return (beat, GM key) pairs only when the listener actually scored the kit."""
    names, pattern = _kit_names()
    events = set()
    for clause in re.split(r"[.!?;]|\bbut\b", text.casefold().replace("-", " ")):
        last_end = 0
        for hit in pattern.finditer(clause):
            prefix = clause[last_end:hit.start()]
            last_end = hit.end()
            if NEGATIVE.search(prefix):
                continue
            placement = PLACEMENT.match(clause, hit.end())
            if not placement:
                # Do not make a partial chart when another requested drum still
                # needs an arrangement. Let the conductor consider the whole kit.
                return []
            name = hit.group()
            pitch = names[name] if name in names else names[name[:-1]]
            words = placement[1]
            if words in ("every beat", "each beat"):
                beats = range(meter)
            elif "offbeat" in words:
                beats = [n + .5 for n in range(meter)]
            else:
                counts = [BEATS[word] if word in BEATS else int(word) for word in re.findall(COUNT, words)]
                if max(counts) > meter:
                    return []
                beats = [n - 1 for n in counts]
            events.update((beat, pitch) for beat in beats)
    return sorted(events)
