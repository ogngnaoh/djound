"""Choose a bounded two-shelf EQ on one dry stereo stem at the render rate."""
import math
import subprocess

import numpy as np
from scipy.signal import welch

from common import EngineError


RATE = 44100


def band_ratio(samples):
    frequencies, spectrum = welch(samples, fs=RATE, axis=0, nperseg=min(4096, len(samples)))
    # Sum power across channels so opposite-polarity stereo remains measurable.
    body = spectrum[(frequencies >= 150) & (frequencies <= 800)].sum()
    treble = spectrum[(frequencies >= 2000) & (frequencies <= 8000)].sum()
    if min(body, treble) <= 1e-12:
        return None
    return 10 * math.log10(treble / body)


def shelves(pcm, gain_db):
    effect = f'bass=f=250:g={-gain_db / 2}:t=q:w=0.707,treble=f=3000:g={gain_db}:t=q:w=0.707'
    try:
        output = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1',
             '-f', 'f32le', '-ar', str(RATE), '-ac', '2', '-i', 'pipe:0', '-af', effect,
             '-f', 'f32le', '-c:a', 'pcm_f32le', 'pipe:1'],
            input=pcm, capture_output=True, check=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise EngineError('LOCAL_AUDIO_INVALID', 'Could not apply the reviewed track tone.') from error
    samples = np.frombuffer(output, dtype='<f4')
    if len(output) != len(pcm) or not np.isfinite(samples).all():
        raise EngineError('LOCAL_AUDIO_INVALID', 'The tone filter returned an invalid stereo signal.')
    return samples.reshape(-1, 2)


def shape(samples, tone):
    sign = {'bright': 1, 'warm': -1}[tone]
    source_ratio = band_ratio(samples)
    source_pcm = np.ascontiguousarray(samples, dtype='<f4').tobytes()
    best_error = math.inf
    for magnitude in (3, 6):
        gain = sign * magnitude
        candidate = shelves(source_pcm, gain)
        measured = band_ratio(candidate) if source_ratio is not None else None
        change = measured - source_ratio if measured is not None else None
        error = abs(change - sign * 2) if change is not None else math.inf
        if magnitude == 3 or error < best_error:
            chosen, chosen_gain, delta, best_error = candidate, gain, change, error
        if source_ratio is None:
            break
    # Match stem RMS so moving the tone control also respects the user's level setting.
    dry_energy = np.einsum('ij,ij->', samples, samples, dtype=np.float64)
    wet_energy = np.einsum('ij,ij->', chosen, chosen, dtype=np.float64)
    scale = math.sqrt(dry_energy / wet_energy) if wet_energy > 0 else 1.0
    return chosen.astype(np.float64) * scale, {
        'tone': tone, 'highShelfDb': chosen_gain, 'lowShelfDb': -chosen_gain / 2,
        'bandChangeDb': delta, 'levelCompensationDb': 20 * math.log10(scale),
    }
