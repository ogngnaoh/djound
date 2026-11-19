"""Retain an explicitly requested lead using the revoiced melody guide on the take's timeline."""
import math
import os
from pathlib import Path
import tempfile
import wave

import numpy as np

from rendition_audio import _remaining, rendition_deadline
from rendition_sketch import HEADROOM, RATE, _frames, _protect, _read


WINDOW = 4096
LEAD_RATIO = 0.70 / 0.65
MAX_GUIDE_GAIN = 4.0


def _spectral_match(guide, take, window):
    """Estimate how much of the guide's audible partials the take could contain."""
    g, r = (np.linalg.norm(np.fft.rfft(x * window[:, None], axis=0), axis=1) for x in (guide, take))
    g_sum, r_sum = g.sum(), r.sum()
    if not g_sum or not r_sum:
        return False, 0.
    # Near-identical spectra also cover a phase-shifted copy. Merely sharing
    # the fundamental pitch is insufficient for a different instrument.
    same = np.abs(g / g_sum - r / r_sum).sum() < .045
    middle = g[1:-1]
    peaks = np.flatnonzero((middle > g[:-2]) & (middle >= g[2:]) & (middle >= .04 * g.max())) + 1
    if not len(peaks):
        return same, 0.
    # Use each distinct strong partial once, so one loud fundamental cannot
    # disguise missing upper harmonics. Accompaniment may obscure some bins.
    ratios = r[peaks] / g[peaks]
    return same, float(np.quantile(ratios, .25, method="lower"))


def _missing_gain(guide_energy, take_energy, cross, same_spectrum, partial_gain, gain):
    if same_spectrum:
        return 0.
    if guide_energy == 0 or take_energy == 0:
        return gain
    # Projection estimates the amount of this particular waveform already in
    # the performance. Keep it only when the two waveforms are coherent.
    projection = cross / guide_energy
    correlation = abs(cross) / math.sqrt(guide_energy * take_energy)
    trust = min(1., max(0., (correlation - .25) / .35))
    present = min(abs(projection), partial_gain)
    backing_energy = max(0., take_energy - present * present * guide_energy)
    target = min(gain, LEAD_RATIO * math.sqrt(backing_energy / guide_energy))
    # An existing lead within 1 dB of the target needs no reinforcement.
    missing = 0. if present >= .89 * target else target - present
    return gain + trust * (missing - gain)


def retain_requested_lead(guide: Path, refined: Path, destination: Path, *, deadline: float) -> None:
    """Blend the instrument-rendered guide with the refined performance, preserving its phrasing."""
    deadline = rendition_deadline(deadline)
    _protect(destination, guide, refined)
    with wave.open(str(guide), "rb") as line, wave.open(str(refined), "rb") as take:
        frames = _frames(line)
        if frames != _frames(take):
            raise ValueError("The revoiced lead and the refined performance must have matching timelines.")

        # Calibrate once across sounding phrases. Normalizing each note would
        # erase its dynamics and boost quiet tails. The floor affects only the
        # calibration; the actual mix includes the full attack and decay.
        stats = []
        active_line = active_take = 0.
        taper = np.hanning(WINDOW)
        for pos in range(0, frames, WINDOW):
            _remaining(deadline)
            count = min(WINDOW, frames - pos)
            g, r = _read(line, pos, count), _read(take, pos, count)
            gg = float(np.sum(g * g, dtype="float64"))
            rr = float(np.sum(r * r, dtype="float64"))
            cross = float(np.sum(g * r, dtype="float64"))
            same, partial = False, 0.
            if gg and rr:
                same, partial = _spectral_match(g, r, taper if count == WINDOW else np.hanning(count))
            stats.append((gg, rr, cross, same, partial))
            if gg > 2 * count * 1e-8:
                active_line += gg
                active_take += rr
        gain = 1.
        if active_line and active_take:
            gain = min(MAX_GUIDE_GAIN, LEAD_RATIO * math.sqrt(active_take / active_line))
        gains = [_missing_gain(*values, gain) for values in stats]

        def mixed_blocks():
            for i, pos in enumerate(range(0, frames, WINDOW)):
                _remaining(deadline)
                count = min(WINDOW, frames - pos)
                g, r = _read(line, pos, count), _read(take, pos, count)
                mixed = r + gains[i] * g
                if i and stats[i - 1][0] and gains[i] != gains[i - 1]:
                    ramp = min(count, RATE // 200)
                    curve = np.linspace(gains[i - 1], gains[i], ramp, dtype="float32")
                    mixed[:ramp] = r[:ramp] + curve[:, None] * g[:ramp]
                yield mixed

        # One gain for the whole take. A lead rest retains the refined samples
        # exactly unless headroom elsewhere requires a common level change.
        peak = max(float(np.max(np.abs(block))) for block in mixed_blocks())
        level = min(1., HEADROOM / max(peak, 1e-9))
        with tempfile.TemporaryDirectory(prefix=".lead-", dir=destination.parent) as temp:
            output = Path(temp) / "ready.wav"
            with wave.open(str(output), "wb") as writer:
                writer.setparams(take.getparams())
                for block in mixed_blocks():
                    writer.writeframesraw(np.rint(block * (level * 32768)).astype("<i2").tobytes())
            _remaining(deadline)
            os.replace(output, destination)
