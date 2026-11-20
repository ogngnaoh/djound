"""Recover a short neighboring-note figure masked by a held accompaniment note."""
from bisect import bisect_left, bisect_right
from functools import lru_cache

import numpy as np
import soundfile as sf

from rendition_sustains import _partials, _shape


def _neighboring_pairs(events, notes):
    attacks = sorted(event for event in events if event[3] >= .3 and 21 <= event[2] <= 108)
    onsets = [event[0] for event in attacks]
    for index in range(1, len(notes) - 1):
        previous, (start, end, held, _), following = notes[index - 1:index + 2]
        if not .22 <= end - start <= .62 or start - previous[1] > .09 or following[0] - end > .09:
            continue
        firsts = attacks[bisect_left(onsets, start - .045):bisect_right(onsets, start + .045)]
        seconds = attacks[bisect_left(onsets, start + .08):bisect_right(onsets, end - .075)]
        pairs = []
        for first in firsts:
            if first[2] == held or abs(first[2] - previous[2]) > 5:
                continue
            for second in seconds:
                a, b = first[2], second[2]
                if (b == held or not 1 <= abs(a - b) <= 3 or abs(b - following[2]) > 5
                        or first[1] < second[0] - .03 or second[1] < end - .05):
                    continue
                if previous[2] in (a, b) or following[2] in (a, b):
                    pairs.append((first, second))
        if pairs:
            yield index, pairs


def _change_in_sounding_partials(sounding, other):
    # Some instruments have very weak even harmonics. Compare the partials the
    # player actually produces against the same frequencies in the other interval.
    strongest = np.argsort(sounding)[-5:]
    return np.median((sounding - other)[strongest])


def _heard_turn(index, pairs, notes, profile):
    start, end, held, _ = notes[index]
    sides = (notes[max(0, index - 6):index], notes[index + 1:index + 7])
    neighbors = []
    for side in sides:
        shapes = [_shape(levels) for a, b, pitch, _ in side
                  if b - a >= .16 and b > start - 6 and a < end + 6
                  and (levels := profile(a, b, pitch)) is not None]
        if not shapes:
            return None
        neighbors.append(shapes)

    def distance(levels):
        shape = _shape(levels)
        return max(min(np.mean(abs(shape - other)) for other in side) for side in neighbors)

    supported = {}
    for first, second in pairs:
        split = second[0]
        fits = []
        for a, b, key, c, d in ((start, split, first[2], split, end), (split, end, second[2], start, split)):
            sounding, other, old = profile(a, b, key), profile(c, d, key), profile(a, b, held)
            if any(value is None for value in (sounding, other, old)):
                break
            fit = distance(sounding)
            if fit >= 5 or distance(old) - fit <= 2.5 or _change_in_sounding_partials(sounding, other) <= 6:
                break
            fits.append(fit)
        if len(fits) == 2:
            keys = (first[2], second[2])
            score = sum(fits)
            if keys not in supported or score < supported[keys][0]:
                supported[keys] = (score, [(max(start, first[0]), split, first[2], first[3]),
                                           (split, min(end, second[1]), second[2], second[3])])
    if len(supported) == 1:
        return next(iter(supported.values()))[1]
    return None


def recover_supported_turns(source, events, notes):
    """Replace a held note only where two detected attacks match the surrounding player's timbre."""
    disputes = dict(_neighboring_pairs(events, notes))
    if not disputes:
        return notes
    with sf.SoundFile(source) as audio:
        profile = lru_cache(maxsize=512)(lambda start, end, pitch: _partials(audio, start, end, pitch))
        result = []
        for index, note in enumerate(notes):
            replacement = _heard_turn(index, disputes[index], notes, profile) if index in disputes else None
            result.extend(replacement or [note])
        return result
