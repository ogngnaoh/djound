"""Use timed instrument parts to resolve an ambiguous melodic line in a local passage."""

from bisect import bisect_left, bisect_right
import math

import numpy as np
from scipy.ndimage import median_filter

from rendition_foreground import FPS, KEYS, _harmonics


def _roll(notes, frames, with_confidence=False):
    roll = np.zeros((frames, KEYS), dtype=np.float32)
    for start, end, pitch, *confidence in notes:
        lo, hi = max(0, round(start * FPS)), min(frames, round(end * FPS))
        key = int(pitch) - 21
        if lo < hi and 0 <= key < KEYS:
            column = roll[lo:hi, key]
            np.maximum(column, confidence[0] if with_confidence else 1, out=column)
    return roll


def _leaps(notes):
    """Fraction of consecutive notes separated by more than a perfect fifth."""
    pitches = [note[2] for note in notes]
    return np.mean(np.abs(np.diff(pitches)) > 7) if len(pitches) >= 2 else 0.0


def needs_player_analysis(mix_notes, events, duration):
    """A simple line or sparse recording can keep its original acoustic transcription."""
    if duration <= 0 or len(mix_notes) < 8 or _leaps(mix_notes) < 1 / 3:
        return False
    frames = math.ceil(duration * FPS)
    return np.count_nonzero(_roll(events, frames).sum(axis=1) >= 3) >= frames / 2


def _unexplained_parts(programs, mix_notes, frames):
    lead = _roll(mix_notes, frames).astype(bool)
    voiced = lead.any(axis=1)
    parts = {}
    for program, notes in programs.items():
        if int(program) == 128 or len(notes) < 3:
            continue
        roll = _roll(notes, frames).astype(bool)
        count = roll.sum(axis=1)
        active = count > 0
        if active.mean() < 0.15 or np.mean(count[active] >= 3) > 0.25:
            continue
        # A quick tune, doubling, or handoff may really jump this much. Keep the
        # incumbent when an independent player follows it during a substantial passage.
        near = roll.copy()
        near[:, 1:] |= roll[:, :-1]
        near[:, :-1] |= roll[:, 1:]
        both = voiced & active
        if both.mean() > 0.2 and np.sum((lead & near).any(axis=1)) >= 0.72 * both.sum():
            return {}
        parts[program] = notes
    return parts


def _figuration(parts):
    """Identify an unusually busy outer voice and the side of the texture it occupies."""
    if len(parts) < 2:
        return None
    first, second = sorted(parts, key=lambda part: len(parts[part]), reverse=True)[:2]
    fast = parts[first]
    if len(fast) < 2 * len(parts[second]):
        return None
    pitches = np.array([note[2] for note in fast])
    low, high = np.quantile(pitches, [0.2, 0.8])
    # Instrument labels sometimes group two simultaneous voices. Estimate the two
    # register centers before removing the crowded register from consideration.
    for _ in range(8):
        lower = pitches <= (low + high) / 2
        if lower.all() or not lower.any():
            return None
        low, high = pitches[lower].mean(), pitches[~lower].mean()
    if high - low < 12:
        return None
    slower_center = np.median([n[2] for part, notes in parts.items() if part != first for n in notes])
    upper = np.median(pitches) > slower_center
    outside = ~lower if upper else lower
    lengths = [end - start for (start, end, *_), outer in zip(fast, outside) if outer]
    if len(lengths) <= len(fast) / 2 or np.median(lengths) > 0.25:
        return None
    return first, upper, (low + high) / 2


def _anchors(view, parts, rapid, upper, frames):
    anchors = []
    for part in parts:
        notes = view.get(part, [])
        if part != rapid:
            anchors.extend(notes)
            continue
        roll = _roll(notes, frames)
        # The busy player's label can contain a slower note from its neighbor.
        # Retain it when this same label also contains a separated outer pitch.
        for note in notes:
            start, end, pitch = note[:3]
            lo, hi = max(0, round(start * FPS)), min(frames, round(end * FPS))
            key = int(pitch) - 21
            simultaneous = roll[lo:hi, max(0, key + 6) :] if upper else roll[lo:hi, : max(0, key - 5)]
            if lo < hi and simultaneous.any(axis=1).mean() > 0.25:
                anchors.append(note)
    return anchors


def _trace(evidence):
    keys = np.arange(evidence.shape[1])
    cost = 1.1 + np.minimum(0.2 * np.abs(keys[:, None] - keys), 3.0)
    cost[-1, :] = cost[:, -1] = 1.0
    np.fill_diagonal(cost, 0.0)
    best = np.zeros(len(keys))
    previous = np.empty(evidence.shape, dtype=np.int8)
    for frame, observed in enumerate(evidence):
        choices = best[:, None] - cost
        previous[frame] = choices.argmax(axis=0)
        best = choices.max(axis=0) + observed
        best -= best.max()
    key = best.argmax()
    path = np.empty(len(evidence), dtype=np.int8)
    for frame in reversed(range(len(path))):
        path[frame] = key
        key = previous[frame, key]
    return path


def _perform(events, mix_notes, anchors, path, strength, duration):
    proposals = [[] for _ in range(KEYS)]
    for start, _, pitch, *_ in events:
        if 21 <= pitch < 21 + KEYS:
            proposals[pitch - 21].append(round(start * FPS))
    for times in proposals:
        times.sort()
    # A player attack or a waveform-validated repeat from the original transcription
    # has to agree with a fresh mixed-note onset. Vibrato alone can split a proposal.
    repeats = [
        now for last, now in zip(mix_notes, mix_notes[1:]) if last[2] == now[2] and now[0] - last[1] <= 0.04
    ]
    attacks = [set() for _ in range(KEYS)]
    for start, _, pitch, *_ in [*anchors, *repeats]:
        key, frame = int(pitch) - 21, round(start * FPS)
        if 0 <= key < KEYS:
            times = proposals[key]
            if bisect_left(times, frame - 5) < bisect_right(times, frame + 5):
                attacks[key].add(frame)
    attacks = [sorted(times) for times in attacks]
    boundaries = np.r_[0, np.flatnonzero(np.diff(path)) + 1, len(path)]
    notes = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        key = int(path[lo])
        if key >= KEYS or hi - lo < 3:
            continue
        cuts = [lo]
        times = attacks[key]
        for frame in times[bisect_left(times, lo + 4) : bisect_right(times, hi - 3)]:
            if frame >= cuts[-1] + 4:
                cuts.append(frame)
        for start, end in zip(cuts, [*cuts[1:], hi]):
            notes.append(
                (
                    float(start / FPS),
                    min(float(end / FPS), duration),
                    key + 21,
                    float(np.mean(strength[start:end, key])),
                )
            )
    return notes


def select_player_line(source, events, mix_notes, views, duration):
    """Decode one source-local passage. None means the mixed-audio melody should stand."""
    if not views or not needs_player_analysis(mix_notes, events, duration):
        return None
    frames = math.ceil(duration * FPS)
    parts = _unexplained_parts(views[0], mix_notes, frames)
    figuration = _figuration(parts)
    if figuration is None:
        return None
    rapid, upper, border = figuration
    anchors = [_anchors(view, parts, rapid, upper, frames) for view in views]
    model = sum(_roll(notes, frames) for notes in anchors)
    if not model.any():
        return None
    keys = np.arange(KEYS)
    order = keys if upper else keys[::-1]
    rank = median_filter(np.max(np.where(model > 0, order, 0), axis=1), size=9)
    mixed = _roll(events, frames, with_confidence=True)
    strength, _ = _harmonics(source, frames)
    supported = (mixed > 0) | ((model > 0) & (strength >= 0.15))
    inside = keys + 21 < round(border) if upper else keys + 21 > round(border)
    # Follow the edge of the quieter-moving texture nearest to the figuration.
    # A player-model dropout still permits a note heard in the actual recording.
    salience = 1.1 * mixed + strength + 0.5 * model / model.max()
    salience -= 0.35 * np.maximum(0, rank[:, None] - order - 1)
    salience[~(supported & inside)] = -5.0
    path = _trace(np.column_stack((salience, np.full(frames, 0.65))))
    notes = _perform(events, mix_notes, anchors[0], path, np.maximum(mixed, np.minimum(1, model)), duration)
    original_time = sum(end - start for start, end, *_ in mix_notes)
    new_time = sum(end - start for start, end, *_ in notes)
    if (
        new_time < 0.7 * original_time
        or len({n[2] for n in notes}) < 3
        or _leaps(notes) >= _leaps(mix_notes) / 2
    ):
        return None
    return notes
