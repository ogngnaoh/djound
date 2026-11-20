"""Follow an audible instrumental line through concurrent pitches in the recorded mix."""
import math

import numpy as np
import soundfile as sf
from scipy.ndimage import maximum_filter1d, percentile_filter
from scipy.signal import find_peaks

from rendition_motifs import follow_supported_repeat

RATE = 44100
HOP = 882
FPS = RATE // HOP
KEYS = 88


def _harmonics(source, frames):
    """Measure partials of each piano key, comparing each harmonic with its spectral neighbors."""
    size, fft_size = 4096, 8192
    partials = np.arange(1, 11)
    fundamentals = 440 * 2 ** ((np.arange(21, 21 + KEYS) - 69) / 12)
    bins = fundamentals[:, None] * partials * (fft_size / RATE)
    weights = 1 / np.sqrt(partials)
    window = np.hanning(size)
    coordinates = []
    for semitones in (0, -.85, .85):
        position = bins * 2 ** (semitones / 12)
        valid = position < fft_size // 2
        index = np.minimum(position.astype(int), fft_size // 2 - 1)
        fraction = position - index
        coordinates.append((index, (1 - fraction) * valid, fraction * valid))

    strength = np.empty((frames, KEYS), np.float32)
    amplitude = np.empty_like(strength)
    with sf.SoundFile(source) as audio:
        # Keep the complex FFT and the harmonic samples bounded on full songs.
        for first in range(0, frames, 64):
            stop = min(frames, first + 64)
            left = first * HOP - size // 2
            count = (stop - first - 1) * HOP + size
            audio.seek(max(0, left))
            available = audio.read(count + min(0, left), dtype="float32", always_2d=True).mean(axis=1)
            block = np.zeros(count, np.float32)
            offset = max(0, -left)
            block[offset:offset + len(available)] = available
            windows = np.lib.stride_tricks.sliding_window_view(block, size)[::HOP]
            spectrum = np.abs(np.fft.rfft(windows * window, n=fft_size)).astype(np.float32)
            peaks = maximum_filter1d(spectrum, size=5, axis=1)
            center, lower, upper = (peaks[:, idx] * lo + peaks[:, idx + 1] * hi
                                    for idx, lo, hi in coordinates)
            contrast = np.maximum(0., center - .35 * (lower + upper))
            total = (contrast ** .7) @ weights
            strength[first:stop] = total / np.maximum(1.e-6, total.max(axis=1, keepdims=True))
            amplitude[first:stop] = np.linalg.norm(contrast, axis=2)
    return strength, amplitude


def _pitch_path(events, strength):
    frames = len(strength)
    evidence = np.full((frames, KEYS + 1), -6., np.float32)
    sources = np.full((frames, KEYS), -1, np.int32)
    for index, (start, end, pitch, confidence) in enumerate(events):
        lo, hi = max(0, round(start * FPS)), min(frames, round(end * FPS))
        key = pitch - 21
        if hi <= lo or confidence < .2 or not 0 <= key < KEYS:
            continue
        local = strength[lo:hi, key]
        evidence[lo:hi, key] = 2.5 * (.55 * local + .45 * np.median(local)) + .95 * confidence - .5
        sources[lo:hi, key] = index

    sounding = (sources >= 0).any(axis=1)
    if sounding.any():
        # The higher of two similarly prominent parts can provide the local register.
        # A quiet instrument above the tune does not establish where the tune belongs.
        pitched = evidence[:, :KEYS]
        prominent = (pitched >= pitched.max(axis=1, keepdims=True) - .60) & (sources >= 0)
        upper = KEYS - 1 - prominent[:, ::-1].argmax(axis=1)
        register = np.interp(np.arange(frames), np.flatnonzero(sounding), upper[sounding])
        register = percentile_filter(register, percentile=65, size=101, mode="nearest")
        distance = np.abs(np.arange(KEYS)[None, :] - register[:, None])
        pitched -= .11 * np.maximum(0., distance - 5)

    evidence[:, -1] = .22
    cost = np.full((KEYS + 1, KEYS + 1), 1.1)
    intervals = np.abs(np.arange(KEYS)[:, None] - np.arange(KEYS))
    cost[:KEYS, :KEYS] = 1.6 + np.minimum(2., intervals * .12)
    np.fill_diagonal(cost, 0.)
    best = np.zeros(KEYS + 1)
    previous = np.empty((frames, KEYS + 1), np.int8)
    for frame, observed in enumerate(evidence):
        choices = best[:, None] - cost
        previous[frame] = choices.argmax(axis=0)
        best = choices.max(axis=0) + observed
        best -= best.max()
    key = best.argmax()
    path = np.full(frames, -1, dtype=np.int8)
    for frame in reversed(range(frames)):
        if key < KEYS and sources[frame, key] >= 0:
            path[frame] = key
        key = previous[frame, key]
    return path, sources


def foreground_notes(source, events, duration):
    frames = math.ceil(duration * FPS)
    strength, amplitude = _harmonics(source, frames)
    path, sources = _pitch_path(events, strength)
    boundaries = np.r_[0, np.flatnonzero(np.diff(path)) + 1, frames]
    notes = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        key = int(path[lo])
        if key < 0 or hi - lo < 4:
            continue
        # Wind vibrato and sustained bowing can produce successive note proposals on
        # one held pitch. A dip and renewed attack in its partials can mark a repeat.
        level = 20 * np.log10(np.maximum(1.e-9, amplitude[lo:hi, key]))
        dips, properties = find_peaks(-level, prominence=7., distance=8)
        candidates = sources[lo:hi, key]
        proposed = np.flatnonzero(np.diff(candidates)) + 1
        attacks = [lo]
        for dip, drop in zip(dips, properties["prominences"]):
            if 5 <= dip <= hi - lo - 5 and (drop >= 14. or np.any(np.abs(proposed - dip) <= 5)):
                attacks.append(lo + dip)
        attacks.append(hi)
        for start, end in zip(attacks, attacks[1:]):
            confidence = np.median([events[i][3] for i in sources[start:end, key]])
            notes.append((float(start / FPS), min(float(end / FPS), duration), key + 21, float(confidence)))
    return follow_supported_repeat(notes, events, strength, FPS)
