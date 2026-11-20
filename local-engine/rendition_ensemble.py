"""Resolve competing melody lines locally without rewriting clear passages of the recording."""
from pathlib import Path

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


def resolve_players(source, work, events, mix, duration, deadline, *, pinned=False):
    from rendition_player_line import needs_player_analysis, select_player_line

    model = None
    revised = None
    cuts = _boundaries(mix, duration)
    for start, stop in zip(cuts, cuts[1:]):
        if not needs_player_analysis(_within(mix, start, stop), _within(events, start, stop), stop - start):
            continue
        if model is None:
            model = installed_model()
            if model is None:
                break
        left, right = max(0., start - 2), min(duration, stop + 2)
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
