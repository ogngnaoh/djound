"""Resolve a single sounding instrument when the note model writes its overtones as simultaneous players."""
from bisect import bisect_left
from collections import defaultdict
import math

import numpy as np
import soundfile as sf
from scipy.ndimage import maximum_filter1d, median_filter

RATE = 44100
HOP = 441
WINDOW = 3072
FFT = 8192
KEYS = np.arange(21, 109)
PARTIALS = np.arange(1, 12)
RATIOS = 2 ** ((np.arange(128)[None, :] - np.arange(128)[:, None]) / 12)
HARMONIC = (RATIOS >= .96) & (RATIOS <= 11.4) & (abs(RATIOS - np.rint(RATIOS)) < .04 * RATIOS)


def _sounding_keys(source, frames):
    frequencies = 440 * 2 ** ((KEYS - 69) / 12)
    targets = frequencies[:, None] * PARTIALS * FFT / RATE
    bins = np.minimum(np.rint(targets).astype(int), FFT // 2)
    weights = (targets < FFT // 2) / PARTIALS ** .6
    window = np.hanning(WINDOW)
    keys = np.empty(frames, dtype=int)
    energy = np.empty(frames)
    certainty = np.empty(frames)
    with sf.SoundFile(source) as audio:
        for first in range(0, frames, 64):
            stop = min(first + 64, frames)
            left = first * HOP - WINDOW // 2
            block = np.zeros((stop - first - 1) * HOP + WINDOW, np.float32)
            audio.seek(max(0, left))
            offset = max(0, -left)
            data = audio.read(len(block) - offset, always_2d=True, dtype="float32").mean(axis=1)
            block[offset:offset + len(data)] = data
            windows = np.lib.stride_tricks.sliding_window_view(block, WINDOW)[::HOP]
            energy[first:stop] = np.sqrt(np.mean(windows ** 2, axis=1))
            spectrum = abs(np.fft.rfft(windows * window, n=FFT))
            peaks = maximum_filter1d(spectrum, size=3, axis=1)
            # Compression keeps a bright reed's louder second partial from drowning out its fundamental.
            evidence = ((peaks[:, bins] ** .4) * weights).sum(axis=2)
            order = np.argsort(evidence, axis=1)
            keys[first:stop] = KEYS[order[:, -1]]
            strengths = np.take_along_axis(evidence, order[:, -2:], axis=1)
            certainty[first:stop] = strengths[:, 1] / np.maximum(1e-8, strengths.sum(axis=1))
    return keys, energy, certainty


def _independent_attacks(events, keys):
    fundamentals = defaultdict(list)
    upper = []
    for attack, end, pitch, confidence in sorted(events):
        if confidence < .25:
            continue
        left = min(len(keys) - 1, round((attack + .025) * RATE / HOP))
        right = max(left + 1, round(min(end, attack + .12) * RATE / HOP))
        fundamental = round(float(np.median(keys[left:right])))
        if pitch == fundamental:
            fundamentals[pitch].append(attack)
        elif pitch > fundamental and HARMONIC[fundamental, pitch]:
            upper.append((attack, fundamental))
    independent = 0
    for attack, fundamental in upper:
        onsets = fundamentals[fundamental]
        index = bisect_left(onsets, attack - .09)
        if index == len(onsets) or onsets[index] > attack + .09:
            independent += 1
    # Two players may share an octave for the entire phrase but enter separately.
    return independent >= max(3, len(upper) * .25)


def harmonic_solo(source, events, duration):
    """Return a physical pitch line only when simultaneous proposals belong to one harmonic family."""
    frames = math.ceil(duration * RATE / HOP)
    candidates = np.zeros((frames, 128), bool)
    for start, end, pitch, confidence in events:
        if confidence >= .25:
            candidates[round(start * RATE / HOP):round(end * RATE / HOP), pitch] = True
    counts = candidates.sum(axis=1)
    active, simultaneous = counts > 0, counts > 1
    if simultaneous.sum() < max(15, active.sum() * .15):
        return None
    # Chord tones from an ensemble need its existing foreground selector.
    # Reject them before taking another spectrum of the recording.
    low = np.argmax(candidates[simultaneous], axis=1)
    if np.mean((~candidates[simultaneous] | HARMONIC[low]).all(axis=1)) < .9:
        return None

    keys, energy, quality = _sounding_keys(source, frames)
    agrees = (~candidates | HARMONIC[keys]).all(axis=1)
    if agrees[active].mean() < .90 or candidates[np.flatnonzero(active), keys[active]].mean() < .6:
        return None
    if _independent_attacks(events, keys):
        return None
    # A later duet must not be discarded just because there was a long exposed solo up front.
    for start in range(0, frames, 200):
        window = slice(start, start + 200)
        if active[window].sum() >= 35 and agrees[window][active[window]].mean() < .75:
            return None
    audible = energy > max(.0003, .035 * np.percentile(energy, 98))
    keys = median_filter(np.where(audible, keys, 0), size=5, mode="nearest")
    edges = np.r_[True, np.diff(keys) != 0, True]
    # Equal adjacent pitches have no pitch boundary. Split only at an actual
    # decay and reattack corroborated by a written onset of that same pitch.
    for attack, _, key, confidence in events:
        i = round(attack * RATE / HOP)
        if confidence < .25 or i < 12 or i + 8 >= frames or not np.all(keys[i - 6:i + 7] == key):
            continue
        valley = i - 5 + int(np.argmin(energy[i - 5:i + 2]))
        flank = min(max(energy[valley - 7:valley]), max(energy[valley + 1:valley + 8]))
        if flank > 2 * energy[valley]:
            edges[valley] = True
    edges = np.flatnonzero(edges)
    notes = []
    for left, right in zip(edges, edges[1:]):
        if keys[left] and right - left >= 5:
            confidence = float(np.mean(quality[left:right]))
            # A near tie between spectral roots during a breath or attack
            # transient cannot establish a separate note.
            if confidence < .51:
                continue
            start = max(0., (left - .5) * HOP / RATE)
            stop = min(duration, (right - .5) * HOP / RATE)
            notes.append((start, stop, int(keys[left]), confidence))
    return notes or None
