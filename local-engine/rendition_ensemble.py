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


def _silent_edges(samples, rate, start, stop):
    """Keep model context on the same side of a recording cut as the disputed phrase."""
    silent = np.all(samples == 0, axis=1)
    gaps = np.flatnonzero(np.diff(np.r_[False, silent, False])).reshape(-1, 2)
    # A zero crossing or brief instrument attack cannot establish a cut. Look
    # only around the two edges; silence within the ensemble is part of its music.
    gaps = gaps[gaps[:, 1] - gaps[:, 0] >= round(.08 * rate)]
    left = max((end for _, end in gaps if abs(end - start) <= rate / 2 and end < len(samples)), default=0)
    right = min((begin for begin, _ in gaps if abs(begin - stop) <= rate / 2 and begin > 0), default=len(samples))
    return (left, right) if left < right else (0, len(samples))


def _resolve(source, work, baseline, duration, deadline, passages, select, context_supported, *, honor_cuts):
    if not passages:
        return None
    model = installed_model()
    if model is None:
        return None
    revised = None
    for start, stop in passages:
        left, right = max(0., start - 2), min(duration, stop + 2)
        if (left != start or right != stop) and not context_supported(left, right):
            left, right = start, stop
        path = source
        if left > 0 or right < duration:
            path = Path(work) / "players-passage.wav"
            with sf.SoundFile(source) as recording:
                begin = round(left * recording.samplerate)
                end = round(right * recording.samplerate)
                recording.seek(begin)
                samples = recording.read(end - begin, dtype="int16", always_2d=True)
                if honor_cuts:
                    lo, hi = _silent_edges(samples, recording.samplerate,
                                           round(start * recording.samplerate) - begin,
                                           round(stop * recording.samplerate) - begin)
                    samples = samples[lo:hi]
                    begin, end = begin + lo, begin + hi
                sf.write(path, samples, recording.samplerate, subtype="PCM_16")
                left, right = begin / recording.samplerate, end / recording.samplerate
                if honor_cuts:
                    start, stop = max(start, left), min(stop, right)
                    if stop <= start:
                        continue
        views = instrument_tracks(path, model, right - left, deadline)
        selected = select(path, left, right, views)
        if selected:
            aligned = [(a + left, b + left, key, confidence) for a, b, key, confidence in selected]
            if any(b > start and a < stop for a, b, _, _ in aligned):
                revised = _replace(baseline if revised is None else revised, aligned, start, stop)
    return revised


def resolve_players(source, work, events, mix, duration, deadline, *, pinned=False, localized=False):
    from rendition_player_line import needs_player_analysis, select_player_line

    def select(path, left, right, views):
        return select_player_line(path, _within(events, left, right), _within(mix, left, right), views, right - left)

    def context_supported(left, right):
        return not localized or needs_player_analysis(_within(mix, left, right), _within(events, left, right), right - left)

    revised = _resolve(source, work, mix, duration, deadline, _passages(events, mix, duration, localized),
                       select, context_supported, honor_cuts=localized)
    if revised is None and pinned:
        raise ValueError("This take used the local instrument transcriber. Restore its verified model to recover the saved tune.")
    return revised


def resolve_separated_player(source, work, events, separated, mix, duration, deadline, *, pinned=False):
    from rendition_voice_player import needs_voice_player, select_voice_player

    def agrees(left, right):
        return needs_voice_player(_within(separated, left, right), _within(mix, left, right), right - left)

    def select(path, left, right, views):
        return select_voice_player(path, _within(events, left, right), _within(separated, left, right),
                                   _within(mix, left, right), views, right - left)

    # Keep complete short performances together so an instrument's repeated
    # phrases help identify it. Longer performances may change lead at a rest.
    cuts = [0., duration] if duration <= 48 else _boundaries(separated, duration)
    spans = [(a, b) for a, b in zip(cuts, cuts[1:]) if agrees(a, b)]
    revised = _resolve(source, work, separated, duration, deadline, spans, select, agrees, honor_cuts=True)
    if pinned and revised is None:
        raise ValueError("This take needs its saved separated instrument melody. Restore the verified local models and retry.")
    return revised
