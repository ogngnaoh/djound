"""Check a brief octave excursion against periodicity in the recorded mixture."""
from collections import defaultdict

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks

from rendition_foreground import FPS, HOP, RATE


def _proposals(events):
    by_pitch = defaultdict(list)
    for start, end, pitch, confidence in sorted(events):
        if confidence < .22 or not 21 <= pitch <= 108:
            continue
        notes = by_pitch[pitch]
        if notes and round(start * FPS) - round(notes[-1][1] * FPS) <= 2:
            # Adjacent detections of one sounding pitch sometimes end a frame early.
            notes[-1][1] = max(notes[-1][1], end)
            notes[-1][3] = max(notes[-1][3], confidence)
        else:
            notes.append([start, end, pitch, confidence])
    return (note for notes in by_pitch.values() for note in notes)


def _period_matches(audio, lo, hi, expected):
    # Read only the disputed part, with 46 ms of context on each side.
    half = 2048
    left, right = lo * HOP - half, (hi - 1) * HOP + half
    samples = np.zeros(right - left)
    audio.seek(max(0, left))
    actual = audio.read(min(right, len(audio)) - max(0, left), always_2d=True).mean(axis=1)
    offset = max(0, -left)
    samples[offset:offset + len(actual)] = actual
    lags = np.arange(17, 300)  # 74–1297 Hz after downsampling to 22.05 kHz.
    hits = 0
    for window in np.lib.stride_tricks.sliding_window_view(samples, 2 * half)[::HOP, ::2]:
        wave = window - window.mean()
        squared = np.r_[0., np.cumsum(wave * wave)]
        norm = np.sqrt(np.maximum(1.e-9, (squared[-1] - squared[lags]) * squared[len(wave) - lags]))
        correlation = np.correlate(wave, wave, "full")[len(wave) - 1 + lags] / norm
        peaks = find_peaks(correlation)[0]
        # The shortest comparably strong period avoids reading integer multiples
        # of a higher sounding note as evidence for a new low note.
        strong = peaks[(correlation[peaks] > .55) & (correlation[peaks] >= .90 * correlation.max())]
        if len(strong):
            hz = RATE / (2 * lags[strong[0]])
            hits += abs(69 + 12 * np.log2(hz / 440) - expected) <= 1
    return hits >= .55 * (hi - lo)


def recover_octave_excursions(source, events, notes, duration):
    """Follow an actual octave-and-return gesture when another voice momentarily wins the mix."""
    path = np.full(int(np.ceil(duration * FPS)), -1, dtype=np.int16)
    for start, end, pitch, _ in notes:
        path[round(start * FPS):round(end * FPS)] = pitch
    disputed = []
    for start, end, pitch, confidence in _proposals(events):
        lo, hi = max(0, round(start * FPS)), min(len(path), round(end * FPS))
        if not 10 <= hi - lo <= 35:
            continue
        sides = (path[max(0, lo - 36):lo], path[hi:hi + 36])
        current = path[lo:hi]
        for neighbor in (pitch - 12, pitch + 12):
            if all(np.count_nonzero(side == neighbor) >= 6 for side in sides):
                # Preserve a plausible nearby alternate ending or an existing
                # statement of either octave. Check only a jump away from both.
                other = (current >= 0) & (abs(current - neighbor) >= 7) & (abs(current - pitch) >= 7)
                if np.mean(other) >= .6:
                    disputed.append((start, end, pitch, confidence))
                    break
    if not disputed:
        return notes
    with sf.SoundFile(source) as audio:
        confirmed = [n for n in sorted(disputed)
                     if _period_matches(audio, max(0, round(n[0] * FPS)), min(len(path), round(n[1] * FPS)), n[2])]
    # If two supported alternatives overlap, the recording does not settle which to use.
    changes = []
    latest_end = -1.
    for i, note in enumerate(confirmed):
        if latest_end <= note[0] and (i + 1 == len(confirmed) or confirmed[i + 1][0] >= note[1]):
            changes.append(note)
        latest_end = max(latest_end, note[1])
    if not changes:
        return notes
    kept, i = [], 0
    for start, end, pitch, confidence in notes:
        while i < len(changes) and changes[i][1] <= start:
            i += 1
        if i < len(changes) and changes[i][0] < end:
            # Avoid inventing a fresh attack on the displaced player's sustained tail.
            end = min(end, changes[i][0])
        if end - start >= .075:
            kept.append((start, end, pitch, confidence))
    return sorted(kept + changes)
