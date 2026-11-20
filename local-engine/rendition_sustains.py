"""Keep an instrumental sustain when a polyphonic tracker moves to a competing octave."""
from bisect import bisect_right
from collections import defaultdict
from functools import lru_cache

import numpy as np
import soundfile as sf
from scipy.ndimage import maximum_filter1d

from rendition_octaves import _proposals


def _disputed_pairs(events, notes):
    spans = defaultdict(list)
    for start, end, pitch, _ in _proposals(events):
        spans[pitch].append((start, end))
    starts = {pitch: [start for start, _ in rows] for pitch, rows in spans.items()}
    for index, (first, second) in enumerate(zip(notes, notes[1:])):
        start, split, low, _ = first
        attack, end, high, _ = second
        if (abs(high - low) != 12 or abs(split - attack) > .045 or
                min(split - start, end - attack) < .15 or end - start < .70):
            continue
        held = []
        for pitch in (low, high):
            candidate = bisect_right(starts.get(pitch, ()), split) - 1
            if candidate >= 0:
                onset, release = spans[pitch][candidate]
                pad = min(.19, .10 * (end - start))
                if onset <= start + pad and release >= end - pad and onset < split - .08 and release > split + .08:
                    held.append(pitch)
        if held:
            yield index, held


def _partials(audio, start, end, pitch):
    """Median levels of the first twelve harmonics, above their local spectral background."""
    size, fft = 4096, 16384
    sr = audio.samplerate
    harmonics = np.arange(1, 13) * 440 * 2 ** ((pitch - 69) / 12)
    bins = np.rint(harmonics * fft / sr).astype(int)
    left, right = (np.rint(bins * 2 ** n).astype(int) for n in (-1 / 16, 1 / 16))
    if right[-1] >= fft // 2:
        return None
    observations = []
    window = np.hanning(size)[:, None]
    for when in np.linspace(start + .2 * (end - start), end - .2 * (end - start), 5):
        first = round(when * sr) - size // 2
        audio.seek(max(0, first))
        samples = audio.read(size + min(0, first), always_2d=True)
        block = np.zeros((size, audio.channels))
        offset = max(0, -first)
        block[offset:offset + len(samples)] = samples
        spectrum = abs(np.fft.rfft(window * block, axis=0, n=fft))
        peaks = maximum_filter1d(spectrum, size=5, axis=0)[bins]
        contrast = peaks - .20 * (spectrum[left] + spectrum[right])
        observations.append(20 * np.log10(np.maximum(.01, contrast).mean(axis=1)))
    return np.median(observations, axis=0)


def _shape(levels):
    return np.maximum(-45., levels - levels.max())


def _sustained_pitch(index, held, notes, profile):
    start, split, first, _ = notes[index]
    attack, end, second, _ = notes[index + 1]
    pitches = (first, second)
    neighbors = []
    for side in (notes[max(0, index - 5):index], notes[index + 2:index + 7]):
        voices = [profile(a, b, k) for a, b, k, _ in side if a < end + 6 and b > start - 6
                  and b - a >= .15 and k not in pitches]
        neighbors.append([_shape(v) for v in voices if v is not None])
    if not all(neighbors):
        return None
    fits = {}
    gains = {}
    for pitch in pitches:
        before, after = profile(start, split, pitch), profile(attack, end, pitch)
        if before is None or after is None:
            return None
        # Odd partials of one octave do not coincide with odd partials of the other.
        gains[pitch] = np.median((after - before)[::2])
        before, after = _shape(before), _shape(after)
        timbre = (before + after) / 2
        context = [min(np.mean(abs(timbre - n)) for n in side) for side in neighbors]
        fits[pitch] = (np.mean(abs(before - after)), max(context), np.mean(context))
    if gains[second] - gains[first] > 6:
        return None  # The recording supports a real arrival in the new register.
    for pitch in held:
        other = first if pitch == second else second
        change, farthest_side, distance = fits[pitch]
        if change < 6 and farthest_side < 5 and fits[other][2] - distance > 4:
            return pitch
    return None


def keep_supported_sustains(source, events, notes):
    """Merge only a pair with one continuous proposed pitch and the same player heard on both sides."""
    disputes = list(_disputed_pairs(events, notes))
    if not disputes:
        return notes
    with sf.SoundFile(source) as audio:
        profile = lru_cache(maxsize=512)(lambda start, end, pitch: _partials(audio, start, end, pitch))
        corrections = {index: pitch for index, held in disputes
                       if (pitch := _sustained_pitch(index, held, notes, profile)) is not None}
    # More than one adjacent disputed jump can be a deliberate octave-and-return figure.
    resolved = {i: p for i, p in corrections.items() if i - 1 not in corrections and i + 1 not in corrections}
    result = []
    for index, note in enumerate(notes):
        if index in resolved:
            pitch = resolved[index]
            following = notes[index + 1]
            result.append((note[0], following[1], pitch, (note if note[2] == pitch else following)[3]))
        elif index - 1 not in resolved:
            result.append(note)
    return result
