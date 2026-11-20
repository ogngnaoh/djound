"""Use an earlier phrase to compare competing, acoustically supported continuations in the mix."""
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from operator import itemgetter

import numpy as np


def _phrase_notes(notes):
    # Coalesce adjacent observations of one pitch for contour matching only.
    phrase = []
    for start, end, key, confidence in notes:
        if phrase and phrase[-1][2] == key and start - phrase[-1][1] < .10:
            phrase[-1][1] = max(phrase[-1][1], end)
        else:
            phrase.append([start, end, key, confidence])
    return phrase


def _heard_note(pitch, begin, finish, notes, by_pitch, strength, fps):
    candidates = by_pitch.get(pitch, ())
    onset = itemgetter(0)
    first = bisect_left(candidates, begin - .16, key=onset)
    stop = bisect_right(candidates, begin + .16, key=onset)
    if first == stop:
        return None
    index = min(range(first, stop), key=lambda i: abs(candidates[i][0] - begin))
    start, end, _, confidence = candidates[index]
    # Pitch detectors can split a sustained note into touching proposals.
    while index + 1 < len(candidates) and end < finish and candidates[index + 1][0] <= end + 1 / fps:
        index += 1
        end = max(end, candidates[index][1])
    end = min(end, finish + .10)
    if end - start < .15:
        return None
    envelope = strength[round(start * fps):round(end * fps), pitch - 21]
    if not len(envelope) or np.median(envelope) < .4:
        return None

    # Near-register alternate endings keep their own contour. Intervene only when
    # a substantially different register displaced most of this sounding note.
    first = max(0, bisect_right(notes, start, key=onset) - 1)
    stop = bisect_left(notes, end, key=onset)
    displaced = sum(max(0., min(end, b) - max(start, a)) for a, b, key, _ in notes[first:stop]
                    if abs(key - pitch) >= 10)
    if displaced < .55 * (end - start):
        return None
    return start, end, pitch, confidence


def _overlaps_change(changes, start, end):
    i = bisect_right(changes, start, key=itemgetter(1))
    return i < len(changes) and changes[i][0] < end


def follow_supported_repeat(notes, events, strength, fps):
    """Recover a two-note continuation only if both attacks exist in the current recording.

    Notes and candidates use (start seconds, end seconds, sounding MIDI, confidence).
    The ordered foreground notes are monophonic. Strength is framewise harmonic
    salience, normalized across pitches, with MIDI 21 in its first column.
    """
    phrase = _phrase_notes(notes)
    if len(phrase) < 10:
        return notes
    by_pitch = defaultdict(list)
    for event in sorted(events):
        if event[3] >= .22 and 0 <= event[2] - 21 < strength.shape[1]:
            by_pitch[event[2]].append(event)
    history = defaultdict(deque)
    changes = []
    for i in range(3, len(phrase)):
        current = phrase[i - 3:i + 1]
        keys = tuple(note[2] for note in current)
        now = np.array([note[0] for note in current])
        if len(set(keys)) < 3 or now[-1] - now[0] < 1. or _overlaps_change(changes, now[0], current[-1][1]):
            continue
        previous = history[keys]
        while previous and now[-1] - phrase[previous[0]][0] > 30.:
            previous.popleft()
        for j in reversed(previous):
            # Complete independent statements of the motif may serve as examples.
            if j + 2 >= i - 3 or phrase[j + 2][1] > now[0]:
                continue
            if _overlaps_change(changes, phrase[j - 3][0], phrase[j + 2][1]):
                continue
            before = np.array([note[0] for note in phrase[j - 3:j + 1]])
            scale = (now[-1] - now[0]) / (before[-1] - before[0])
            shift = np.median(now - scale * before)
            if not .85 <= scale <= 1.18 or np.max(np.abs(now - scale * before - shift)) > .16:
                continue
            heard = []
            for start, end, pitch, _ in phrase[j + 1:j + 3]:
                begin = scale * start + shift
                if begin - current[-1][1] > 1.8 or abs(pitch - np.median(keys)) > 7:
                    break
                note = _heard_note(pitch, begin, scale * end + shift, notes, by_pitch, strength, fps)
                if note is None:
                    break
                heard.append(note)
            if len(heard) != 2:
                continue
            first, second = heard
            if (first[0] < current[-1][1] - .08 or second[0] - first[0] < .15
                    or (changes and first[0] < changes[-1][1])):
                continue
            changes.extend(((first[0], min(first[1], second[0]), *first[2:]), second))
            break
        previous.append(i)
    if not changes:
        return notes

    # Keep attacks outside each correction. Resuming the displaced instrument's
    # sustained tail would create a new attack absent from the audio.
    kept = []
    j = 0
    for start, end, pitch, confidence in notes:
        while j < len(changes) and changes[j][1] <= start:
            j += 1
        if j < len(changes) and changes[j][0] < end:
            end = min(end, changes[j][0])
        if end - start >= .075:
            kept.append((start, end, pitch, confidence))
    return sorted(kept + changes)
