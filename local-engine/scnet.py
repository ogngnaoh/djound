"""Offline SCNet small ONNX inference. Model and preprocessing sources are in THIRD_PARTY_NOTICES."""
import json
from pathlib import Path
import os

import numpy as np

from common import EngineError, HERE, HOME, digest

RATE = 44100
SEGMENT = 485100
STEP = SEGMENT // 4
FFT = 4096
HOP = 1024
FRAMES = 476
# Graph output groups are drums, bass, other and vocals, with two complex stereo channels each.
SOURCES = {'vocals': 3, 'drums': 0}
MANIFEST = json.loads((HERE / 'finished-mix-assets.json').read_text())
MODEL_HASH = MANIFEST['assets'][0]['sha256']


def session(provider):
    import onnxruntime as ort
    path = Path(os.environ.get('DJOUND_MIX_MODEL', HOME / MANIFEST['assets'][0]['path']))
    if not path.is_file() or digest(path) != MODEL_HASH:
        raise EngineError('MIX_SETUP_REQUIRED', 'Install the verified separator with local-engine/setup_localengine.sh --finished-mix.')
    if provider not in ort.get_available_providers():
        raise EngineError('MIX_SETUP_REQUIRED', f'The local engine does not provide {provider}.')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    engine = ort.InferenceSession(str(path), sess_options=options, providers=[provider])
    if engine.get_inputs()[0].shape != [1, 4, 2049, FRAMES] or engine.get_outputs()[0].shape != [1, 16, 2049, FRAMES]:
        raise EngineError('MIX_SETUP_REQUIRED', 'The installed separator has an unexpected audio format.')
    return engine


def spectrum(segment):
    padded = np.pad(segment, ((0, (FRAMES - 1) * HOP - SEGMENT), (0, 0)))
    centered = np.pad(padded, ((FFT // 2, FFT // 2), (0, 0)), mode='reflect')
    frames = np.lib.stride_tricks.sliding_window_view(centered.T, FFT, axis=1)[:, ::HOP, :]
    transformed = (np.fft.rfft(frames, axis=-1, norm='ortho')).transpose(0, 2, 1)
    return np.ascontiguousarray(np.stack((transformed.real, transformed.imag), axis=1)
                                .reshape(1, 4, FFT // 2 + 1, FRAMES), dtype=np.float32)


def waveform(planes):
    """Invert the rectangular, centered STFT with exact squared-window overlap normalization."""
    values = planes[0::2] + 1j * planes[1::2]
    frames = np.fft.irfft(values.transpose(0, 2, 1), n=FFT, axis=-1, norm='ortho')
    size = (FRAMES - 1) * HOP + FFT
    samples = np.zeros((2, size))
    weights = np.zeros(size)
    for index in range(FRAMES):
        span = slice(index * HOP, index * HOP + FFT)
        samples[:, span] += frames[:, index]
        weights[span] += 1
    span = slice(FFT // 2, FFT // 2 + SEGMENT)
    return (samples[:, span] / weights[span]).T


def estimate(audio, provider):
    """Replicate the published wrapper's standardization, reflection, segments and linear fades."""
    engine = session(provider)
    mono = audio.mean(axis=1, dtype=np.float64)
    scale = mono.std(ddof=1) + 1e-8 if len(mono) > 1 else 1e-8
    normalized = np.asarray((audio - mono.mean()) / scale, dtype=np.float32)
    border = SEGMENT - STEP if len(audio) > 2 * (SEGMENT - STEP) else 0
    signal = np.pad(normalized, ((border, border), (0, 0)), mode='reflect') if border else normalized
    collected = {name: np.zeros(signal.shape, dtype=np.float64) for name in SOURCES}
    weights = np.zeros(len(signal))
    fade = SEGMENT // 10
    edge = np.linspace(0, 1, fade)
    for start in range(0, len(signal), STEP):
        segment = signal[start:start + SEGMENT]
        length = len(segment)
        last = start + SEGMENT >= len(signal)
        if length < SEGMENT:
            mode = 'reflect' if length > SEGMENT // 2 else 'constant'
            segment = np.pad(segment, ((0, SEGMENT - length), (0, 0)), mode=mode)
        output = engine.run(['stems_spec'], {'mix_spec': spectrum(segment)})[0][0]
        gain = np.ones(SEGMENT)
        if start > 0:
            gain[:fade] = edge
        if not last:
            gain[-fade:] = edge[::-1]
        span = slice(start, start + length)
        weights[span] += gain[:length]
        for name, index in SOURCES.items():
            samples = waveform(output[index * 4:(index + 1) * 4])
            collected[name][span] += samples[:length] * gain[:length, None]
        if last:
            break
    take = slice(border, border + len(audio))
    return {name: np.asarray(values[take] / weights[take, None] * scale, dtype='<f4')
            for name, values in collected.items()}
