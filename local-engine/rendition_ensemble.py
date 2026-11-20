"""Resolve competing melody lines locally without rewriting clear passages of the recording."""
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from rendition_instrument_runtime import installed_model, instrument_tracks


def _boundaries(notes, duration):
    # Long songs are checked locally. Prefer a breath near the cut so that a
    # player can change at a musical boundary and the model has useful context.
    cuts = [0.]
    while duration - cuts[-1] > 32:
        target = cuts[-1] + 24
        gaps = [(b[0] - a[1], (a[1] + b[0]) / 2) for a, b in zip(notes, notes[1:])
                if b[0] > a[1] and abs((a[1] + b[0]) / 2 - target) <= 2]
        cuts.append(max(gaps)[1] if gaps else target)
    return cuts + [duration]


def _within(notes, start, stop):
    return [(max(a, start) - start, min(b, stop) - start, key, confidence)
            for a, b, key, confidence in notes if b > start and a < stop]


def _passages(events, mix, duration, localized):
    from rendition_player_line import FPS, _roll, needs_player_analysis

    def disputed(start, stop):
        return needs_player_analysis(_within(mix, start, stop), _within(events, start, stop), stop - start)

    cuts = _boundaries(mix, duration)
    coarse = [(a, b) for a, b in zip(cuts, cuts[1:]) if disputed(a, b)]
    if not localized or duration <= 10 or (duration <= 32 and coarse):
        return coarse

    # A busy phrase can straddle any fixed cut. Look for local evidence before
    # batching nearby disputes into bounded model calls.
    spans = []
    for start in range(0, math.ceil(duration), 2):
        stop = min(start + 10., duration)
        if disputed(start, stop):
            if spans and start <= spans[-1][1]:
                spans[-1][1] = stop
            else:
                spans.append([start, stop])
    passages = []
    for start, stop in spans:
        start, stop = max(0., start - 1.), min(duration, stop + 1.)
        count = math.ceil((stop - start) * FPS)
        polyphonic = np.flatnonzero(_roll(_within(events, start, stop), count).sum(axis=1) >= 3)
        # Exclude surrounding solo passages. Leave a little context for attacks
        # and note releases at the point where the texture changes.
        for frames in np.split(polyphonic, np.flatnonzero(np.diff(polyphonic) > 2 * FPS) + 1):
            if not len(frames):
                continue
            left = max(start, start + frames[0] / FPS - .5)
            right = min(stop, start + (frames[-1] + 1) / FPS + .5)
            cuts = _boundaries(_within(mix, left, right), right - left)
            passages.extend((left + a, left + b) for a, b in zip(cuts, cuts[1:]) if disputed(left + a, left + b))
    # A slow-moving dispute may need more than ten seconds to show the
    # repeated jumps that justify the model. Keep those independent spans too.
    return sorted(passages + [(a, b) for a, b in coarse
                              if not any(a < end and b > begin for begin, end in passages)])


def _replace(notes, replacement, start, stop):
    # Join a note across a seam only when both transcriptions heard it sustain.
    pieces = []
    for lo, hi, events in ((0., start, notes), (start, stop, replacement), (stop, float("inf"), notes)):
        if hi <= lo:
            continue
        for a, b, pitch, confidence in events:
            if b <= lo or a >= hi:
                continue
            left, right = max(lo, a), min(hi, b)
            if pieces and a < lo and pieces[-1][1] == lo and pieces[-1][2] == pitch and pieces[-1][4]:
                pieces[-1][1] = right
                pieces[-1][4] = b > hi
            else:
                pieces.append([left, right, pitch, confidence, b > hi])
    return [tuple(note[:4]) for note in pieces]


def resolve_players(source, work, events, mix, duration, deadline, *, pinned=False, localized=False):
    from rendition_player_line import needs_player_analysis, select_player_line

    model = None
    revised = None
    for start, stop in _passages(events, mix, duration, localized):
        if model is None:
            model = installed_model()
            if model is None:
                break
        left, right = max(0., start - 2), min(duration, stop + 2)
        if localized and (left != start or right != stop):
            if not needs_player_analysis(_within(mix, left, right), _within(events, left, right), right - left):
                left, right = start, stop
        path = source
        if left > 0 or right < duration:
            path = Path(work) / "players-passage.wav"
            with sf.SoundFile(source) as recording:
                begin = round(left * recording.samplerate)
                end = round(right * recording.samplerate)
                recording.seek(begin)
                sf.write(path, recording.read(end - begin, dtype="int16", always_2d=True),
                         recording.samplerate, subtype="PCM_16")
                left, right = begin / recording.samplerate, end / recording.samplerate
        views = instrument_tracks(path, model, right - left, deadline)
        selected = select_player_line(path, _within(events, left, right), _within(mix, left, right),
                                      views, right - left)
        if selected:
            aligned = [(a + left, b + left, key, confidence) for a, b, key, confidence in selected]
            if any(b > start and a < stop for a, b, _, _ in aligned):
                revised = _replace(mix if revised is None else revised, aligned, start, stop)
    if revised is None and pinned:
        raise ValueError("This take used the local instrument transcriber. Restore its verified model to recover the saved tune.")
    return revised
