"""Stereo-linked attenuation before quantization; no limiter or gain boost."""
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def estimated_peak(audio):
    """4x FIR oversampling estimate, not a certified true-peak meter."""
    oversampled = resample_poly(audio, 4, 1, axis=0)
    if not np.isfinite(oversampled).all():
        raise ValueError('Nonfinite oversampled audio')
    return max(float(np.abs(audio).max()), float(np.abs(oversampled).max()))


def save_safe_wav(path, channel_first, sample_rate=44100):
    audio = np.asarray(channel_first, dtype=np.float64).T
    if audio.ndim != 2 or audio.shape[1] != 2 or not audio.size:
        raise ValueError("Expected nonempty stereo audio")
    if not np.isfinite(audio).all():
        raise ValueError("Nonfinite generated audio")
    if np.abs(audio).max() > np.finfo(np.float32).max:
        raise ValueError('Generated audio cannot be preserved as finite float32 diagnostics')
    peak = estimated_peak(audio)
    ceiling = 10 ** (-1.2 / 20)  # Margin for quantization/estimator differences.
    gain = min(1.0, ceiling / peak) if peak else 1.0
    safe = audio * gain
    if not np.isfinite(gain) or not np.isfinite(safe).all():
        raise ValueError('Invalid export gain or audio')
    path = Path(path)
    sf.write(path.with_suffix('.raw.wav'), audio, sample_rate, subtype='FLOAT')
    sf.write(path, safe, sample_rate, subtype='PCM_16')
    decoded, _ = sf.read(path, always_2d=True)
    exported_peak = estimated_peak(decoded)
    if exported_peak > 10 ** (-1 / 20):
        raise ValueError("Export exceeded the -1 dB estimated peak acceptance ceiling")
    stats = {
        'method': 'stereo-linked attenuation only; 4x FIR peak estimate',
        'sample_rate': sample_rate,
        'raw_sample_peak': float(np.abs(audio).max()),
        'raw_estimated_peak_4x': peak,
        'gain': gain,
        'gain_db': 20 * float(np.log10(gain)),
        'exported_sample_peak': float(np.abs(decoded).max()),
        'exported_estimated_peak_4x': exported_peak,
        'ceiling_db': -1.2,
        'raw_float_file': path.with_suffix('.raw.wav').name,
    }
    path.with_suffix('.export.json').write_text(json.dumps(stats, indent=2) + '\n')
    return stats
