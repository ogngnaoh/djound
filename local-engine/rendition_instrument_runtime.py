"""Run a local instrument transcription model one short segment at a time."""
import json
import time

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from common import HERE, HOME, digest

SEGMENT = 32767
RATE = 16000
CHANNELS = 13
LAYERS = 8
STEPS = 256


def installed_model():
    directory = HOME / "instrument-transcriber"
    if not directory.exists():
        return None
    manifest = json.loads((HERE / "instrument-transcriber-assets.json").read_text())
    repair = "Restore the verified instrument transcriber with the local rendition setup."
    try:
        identity = json.loads((directory / ".djound-verified.json").read_text())
        if identity != {key: manifest[key] for key in ("model", "revision")}:
            raise ValueError(repair)
        for asset in manifest["assets"]:
            path = directory / asset["path"]
            if path.stat().st_size != asset["bytes"] or digest(path) != asset["sha256"]:
                raise ValueError(repair)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(repair) from exc
    return directory


def _check_time(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Instrument transcription exceeded its time limit.")


def _segments(path, lead_in):
    """Convert canonical PCM to aligned model windows without retaining a full song."""
    up, down = 160, 441
    with sf.SoundFile(path) as wav:
        if wav.samplerate != 44100:
            raise ValueError("Instrument transcription requires the canonical 44.1 kHz recording.")
        length = (len(wav) * up + down - 1) // down
        for position in range(0, length + lead_in, SEGMENT):
            samples = np.zeros(SEGMENT, dtype="float32")
            begin = max(0, position - lead_in)
            stop = min(length, position + SEGMENT - lead_in)
            if stop > begin:
                # The cuts share the resampler phase and have filter context on both
                # sides. This gives the same samples as resampling the complete file.
                left = max(0, (begin // up - 1) * down)
                right = min(len(wav), ((stop + up - 1) // up + 1) * down)
                wav.seek(left)
                block = wav.read(right - left, dtype="float32", always_2d=True).mean(axis=1)
                resampled = resample_poly(block, up, down)
                offset = begin - left // down * up
                dest = begin + lead_in - position
                samples[dest:dest + stop - begin] = resampled[offset:offset + stop - begin]
            yield samples


def _spectrum(samples):
    size = 2048
    wave = np.pad(samples.astype(np.float64), size // 2, mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(wave, size)[::300]
    window = .5 - .5 * np.cos(2 * np.pi * np.arange(size) / size)
    spectrum = np.log(np.abs(np.fft.rfft(frames * window))[:, 1:] + 1.e-5)
    return ((spectrum - spectrum.min()) / (np.ptp(spectrum) + .008)).astype(np.float32)[None]


def _decode(step, cross, deadline):
    # The encoder's 13 rows are instrument families, with independent self-attention
    # key/value caches. Drop each row as soon as its end token appears.
    active = np.arange(CHANNELS)
    tokens = [[] for _ in active]
    ids = np.zeros(CHANNELS, dtype="int64")
    capacity = 64
    keys = [np.zeros((CHANNELS, 6, 64, capacity), dtype="float32") for _ in range(LAYERS)]
    values = [np.zeros((CHANNELS, 6, capacity, 64), dtype="float32") for _ in range(LAYERS)]
    for pos in range(STEPS):
        _check_time(deadline)
        if pos == keys[0].shape[-1]:
            keys = [np.pad(k, ((0, 0), (0, 0), (0, 0), (0, pos))) for k in keys]
            values = [np.pad(v, ((0, 0), (0, 0), (0, pos), (0, 0))) for v in values]
        inputs = {"ids": ids, "pos": np.array([pos], dtype="int64")}
        for i in range(LAYERS):
            inputs.update({f"pk_{i}": keys[i], f"pv_{i}": values[i],
                           f"ck_{i}": cross[i], f"cv_{i}": cross[LAYERS + i]})
        result = step.run(None, inputs)
        ids = result[0]
        for row, token in zip(active, ids):
            tokens[row].append(int(token))
        keep = (ids != 1) | (pos == 0)
        if not keep.any():
            break
        for i in range(LAYERS):
            keys[i][:, :, :, pos] = result[2 + i]
            values[i][:, :, pos, :] = result[2 + LAYERS + i]
        if not keep.all():
            active, ids = active[keep], ids[keep]
            cross = [c[keep] for c in cross]
            keys = [k[keep] for k in keys]
            values = [v[keep] for v in values]
    return tokens


def _sessions(model):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 3
    options.inter_op_num_threads = 1
    return [ort.InferenceSession(str(model / name), options, providers=["CPUExecutionProvider"])
            for name in ("encoder.onnx", "decoder_step.onnx")]


def _tokenize(source, encoder, decoder, lead_in, deadline):
    for samples in _segments(source, lead_in):
        _check_time(deadline)
        cross = encoder.run(None, {"spec": _spectrum(samples)})
        yield _decode(decoder, cross, deadline)


def instrument_tracks(source, model, duration, deadline):
    """Return the instruments heard in each view, all on the upload's pitch and clock."""
    from rendition_instrument_notes import decode_segments

    _check_time(deadline)
    vocabulary = json.loads((model / "vocab.json").read_text())
    encoder, decoder = _sessions(model)
    parts = []
    # The model emits packets about two seconds long. A half-packet shift gives
    # attacks near a boundary another chance in the middle of a packet.
    for lead_in in (0, (SEGMENT + 1) // 2):
        delay = lead_in / RATE
        rows = list(_tokenize(source, encoder, decoder, lead_in, deadline))
        decoded = decode_segments(rows, vocabulary, duration + delay)
        aligned = {}
        for program, notes in decoded.items():
            events = []
            for start, end, pitch, confidence in notes:
                start, end = max(0., start - delay), min(duration, end - delay)
                if end > start:
                    events.append((start, end, pitch, confidence))
            if events:
                aligned[program] = events
        parts.append(aligned)
    return parts
