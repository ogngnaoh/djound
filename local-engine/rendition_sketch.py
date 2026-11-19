"""Build a melody-led sketch from a generated performance and recover its missing rhythm parts."""

from contextlib import contextmanager
from dataclasses import dataclass
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import wave

from common import EngineError, HERE, HOME


RATE = 44100
BLOCK = 32768
MAX_FRAMES = 600 * RATE
HEADROOM = 0.88
# Floors relative to the generated mix keep quiet separation bleed out of the restoration.
RHYTHM_FLOORS = (0.018, 0.032)
RHYTHM_TARGETS = (0.3, 0.48)
MAX_RHYTHM_GAIN = (3.0, 1.4)


class SeparatorUnavailable(RuntimeError):
    """The verified model or execution provider could not be opened."""


def backing_available() -> bool:
    """Cheap setup check. The separator verifies its model hash when it opens the CPU session."""
    if not all(find_spec(name) is not None for name in ("numpy", "onnxruntime", "scipy")):
        return False
    try:
        asset = json.loads((HERE / "finished-mix-assets.json").read_text())["assets"][0]
        path = Path(os.environ.get("DJOUND_MIX_MODEL", HOME / asset["path"]))
        return path.is_file() and path.stat().st_size == asset["bytes"]
    except (OSError, ValueError, KeyError, IndexError):
        return False


def _check(deadline):
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise RuntimeError("The arrangement accompaniment exceeded its time limit.")


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _frames(wav):
    if (
        wav.getparams()[:3] != (2, 2, RATE)
        or wav.getcomptype() != "NONE"
        or not 0 < wav.getnframes() <= MAX_FRAMES
    ):
        raise ValueError("The arrangement needs stereo 44.1 kHz PCM16 recordings up to ten minutes.")
    return wav.getnframes()


def _read(wav, start, count):
    import numpy as np

    wav.setpos(start)
    data = wav.readframes(count)
    if len(data) != count * 4:
        raise ValueError("The arrangement recording is incomplete.")
    return np.frombuffer(data, dtype="<i2").reshape(count, 2).astype("float32") / 32768


def _statistics(wav, deadline):
    import numpy as np

    frames = _frames(wav)
    mono_sum = mono_square = stereo_square = 0.0
    for i in range(0, frames, BLOCK):
        _check(deadline)
        x = _read(wav, i, min(BLOCK, frames - i))
        mono = np.mean(x, axis=1, dtype=np.float64)
        mono_sum += mono.sum()
        mono_square += np.vdot(mono, mono)
        stereo_square += np.sum(x * x, dtype=np.float64)
    mean = mono_sum / frames
    std = math.sqrt(max(0.0, mono_square / frames - mean * mean))
    return mean, std + 1e-8, math.sqrt(stereo_square / (2 * frames))


def _separated(wav, statistics, deadline, groups):
    """Crossfade adjacent eleven-second estimates in the interior of their real shared context."""
    import numpy as np
    import onnxruntime as ort
    import scnet

    _check(deadline)
    try:
        engine = scnet.session("CPUExecutionProvider")
    except EngineError as exc:
        raise SeparatorUnavailable(
            "Install the verified SCNet accompaniment model before arranging this take."
        ) from exc
    frames = _frames(wav)
    mean, _, _ = statistics
    size = scnet.SEGMENT
    last_start = max(0, frames - size)
    # Use complete real windows at both ends. Short padded windows can misclassify transients.
    intervals = math.ceil(last_start / (size - 2 * RATE))
    starts = [round(i * last_start / intervals) for i in range(intervals + 1)] if intervals else [0]
    boundaries = [(a + b + size) // 2 - RATE // 2 for a, b in zip(starts, starts[1:])]
    edges = [0, *boundaries, frames]
    rise = np.sin((np.arange(RATE) + 0.5) * (math.pi / (2 * RATE))) ** 2
    rise = rise.astype("float32")[:, None, None]
    tail = None
    run_options = ort.RunOptions()
    timeout = threading.Timer(
        max(0.0, deadline - time.monotonic()), lambda: setattr(run_options, "terminate", True)
    )
    timeout.daemon = True
    timeout.start()
    try:
        for index, start in enumerate(starts):
            _check(deadline)
            sample = _read(wav, start, min(frames - start, size))
            if len(sample) < size:
                sample = np.pad(sample, ((0, size - len(sample)), (0, 0)), mode="reflect")
            # SCNet was trained on standardized examples. Normalize each model input to keep
            # softly played passages in range even when another section is much louder.
            scale = sample.mean(axis=1, dtype=np.float64).std() + 1e-8
            inputs = {"mix_spec": scnet.spectrum(np.asarray((sample - mean) / scale, dtype="float32"))}
            try:
                planes = engine.run(["stems_spec"], inputs, run_options)[0][0]
            except Exception as exc:
                _check(deadline)
                raise RuntimeError("The accompaniment separator could not process this recording.") from exc
            window = np.stack(
                [scnet.waveform(planes[4 * k : 4 * k + 4]).astype("float32") for k in groups], axis=1
            )
            window *= scale
            if not np.isfinite(window).all():
                raise RuntimeError("The accompaniment separator returned invalid samples.")
            first, stop = edges[index], edges[index + 1]
            chunk = window[first - start : stop - start].copy()
            if tail is not None:
                chunk[:RATE] = tail * (1 - rise) + chunk[:RATE] * rise
            tail = window[stop - start : stop - start + RATE].copy()
            yield first, chunk
    finally:
        timeout.cancel()


def vocal_reference(source, output, deadline):
    """Write the estimated singer on the mix timeline and measure its level against the mix."""
    from contextlib import ExitStack

    import numpy as np
    from scnet import SOURCES

    source, output = Path(source), Path(output)
    _protect(output, source)
    if os.path.lexists(output):
        raise FileExistsError(f"The vocal reference already exists at {output}.")
    with tempfile.TemporaryDirectory(prefix=".vocal-", dir=output.parent) as tmp, ExitStack() as inputs:
        recording = inputs.enter_context(wave.open(str(source), "rb"))
        frames = recording.getnframes()
        # The separator expects stereo. A mono upload has identical left and right evidence.
        if recording.getnchannels() == 1:
            if (recording.getsampwidth(), recording.getframerate(), recording.getcomptype()) != (2, RATE, "NONE"):
                raise ValueError("The vocal reference needs 44.1 kHz PCM16 audio.")
            if not 0 < frames <= MAX_FRAMES:
                raise ValueError("The vocal reference needs a recording up to ten minutes.")
            stereo = Path(tmp) / "stereo.wav"
            with wave.open(str(stereo), "wb") as copy:
                copy.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
                for start in range(0, frames, BLOCK):
                    _check(deadline)
                    count = min(BLOCK, frames - start)
                    block = recording.readframes(count)
                    if len(block) != count * 2:
                        raise ValueError("The vocal reference recording is incomplete.")
                    copy.writeframesraw(np.repeat(np.frombuffer(block, "<i2"), 2).tobytes())
            recording = inputs.enter_context(wave.open(str(stereo), "rb"))

        stats = _statistics(recording, deadline)
        ready = Path(tmp) / "singer.wav"
        levels = []
        hop = RATE // 10
        with wave.open(str(ready), "wb") as singer:
            singer.setparams(recording.getparams())
            for start, grouped in _separated(recording, stats, deadline, (SOURCES["vocals"],)):
                if singer.tell() != start:
                    raise RuntimeError("The vocal reference did not cover the recording's complete timeline.")
                voice = grouped[:, 0]
                mix = _read(recording, start, len(voice))
                for i in range(0, len(voice), hop):
                    end = min(i + hop, len(voice))
                    v, m = voice[i:end], mix[i:end]
                    levels.append(((start + (i + end) / 2) / RATE,
                                   float(np.sqrt(v.var(axis=0).mean())), float(np.sqrt(m.var(axis=0).mean()))))
                singer.writeframesraw(np.rint(np.clip(voice, -1, 32767 / 32768) * 32768).astype("<i2").tobytes())
            if singer.tell() != frames:
                raise RuntimeError("The vocal reference did not cover the recording's complete timeline.")
        result = np.asarray(levels).T
        _check(deadline)
        # Publish the complete WAV only if another caller has not already claimed its name.
        os.link(ready, output)
    return result


@dataclass
class BackingCache:
    """Scratch file owned by one draft. Close it after refinement and restoration."""

    file: object
    identity: tuple
    frames: int
    rms: float
    stem_rms: tuple

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def read(self, start, count):
        import numpy as np

        # Three draft stems x two channels, IEEE float16. Retains over-range peaks.
        self.file.seek(start * 12)
        data = self.file.read(count * 12)
        if len(data) != count * 12:
            raise RuntimeError("The accompaniment cache is incomplete.")
        return np.frombuffer(data, dtype="<f2").reshape(count, 3, 2).astype("float32")


def _cache(performance, directory, deadline):
    import numpy as np

    identity = _identity(performance)
    scratch = tempfile.TemporaryFile(dir=directory)
    try:
        with wave.open(str(performance), "rb") as wav:
            frames = _frames(wav)
            stats = _statistics(wav, deadline)
            energy = np.zeros(3, dtype="float64")
            for _, stems in _separated(wav, stats, deadline, (0, 1, 2)):
                scratch.write(stems.astype("<f2").tobytes())
                energy += np.sum(stems * stems, axis=(0, 2), dtype=np.float64)
        if identity != _identity(performance) or scratch.tell() != frames * 12:
            raise RuntimeError("The generated performance changed during accompaniment preparation.")
        return BackingCache(scratch, identity, frames, stats[2], tuple(np.sqrt(energy / (frames * 2))))
    except BaseException:
        scratch.close()
        raise


def _protect(output, *inputs):
    for path in inputs:
        if output.resolve() == path.resolve() or (output.exists() and output.samefile(path)):
            raise ValueError("Save the arrangement separately from its input recordings.")


def _duck(other, guide):
    """Attenuate the draft's broad pitched stem at frequencies occupied by the guide."""
    import numpy as np
    from scipy.ndimage import gaussian_filter
    from scipy.signal import istft, stft

    orig = len(guide)
    pad = max(0, 2048 - orig)
    line = np.pad(guide.mean(axis=1), (0, pad))
    opts = {"fs": RATE, "nperseg": 2048, "noverlap": 1536}
    _, _, spec = stft(line, **opts)
    energy = gaussian_filter(np.abs(spec), (1.5, 2))
    floor = np.maximum(0.12 * energy.max(axis=0), 1e-8)
    mask = 1 - 0.72 * energy / (energy + floor)
    _, _, z = stft(np.pad(other.T, ((0, 0), (0, pad))), **opts)
    _, y = istft(z * mask, **opts)
    return y.T[:orig]


@contextmanager
def _publish(output, frames, target_rms, deadline, *, unchanged=None):
    """Spool floats to disk; apply one track-wide gain and publish atomic PCM16."""
    import numpy as np

    with (
        tempfile.TemporaryDirectory(prefix=".backing-", dir=output.parent) as tmp,
        tempfile.TemporaryFile(dir=tmp) as mix,
    ):
        peak = energy = 0.0

        def append(samples):
            nonlocal peak, energy
            _check(deadline)
            if not np.isfinite(samples).all():
                raise RuntimeError("The arrangement produced invalid samples.")
            peak = max(peak, float(np.max(np.abs(samples))))
            energy += np.sum(samples * samples, dtype=np.float64)
            mix.write(np.asarray(samples, dtype="<f4").tobytes())

        yield append
        if mix.tell() != frames * 8:
            raise RuntimeError("The arrangement did not cover the recording's complete timeline.")
        rms = math.sqrt(energy / (frames * 2))
        level_gain = 1.0 if target_rms is None else target_rms / max(rms, 1e-9)
        gain = 1.0 if unchanged and unchanged() else min(HEADROOM / max(peak, 1e-9), level_gain)
        mix.seek(0)
        ready = Path(tmp) / "ready.wav"
        with wave.open(str(ready), "wb") as out:
            out.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
            while data := mix.read(BLOCK * 8):
                _check(deadline)
                samples = np.frombuffer(data, dtype="<f4")
                out.writeframesraw(np.rint(samples * (gain * 32768)).astype("<i2").tobytes())
        _check(deadline)
        os.replace(ready, output)


def build_arrangement_sketch(
    guide: Path, performance: Path, output: Path, *, deadline: float
) -> BackingCache:
    """Combine the source melody guide with backing from the generated style performance."""
    _check(deadline)
    _protect(output, guide, performance)
    with wave.open(str(guide), "rb") as lead, wave.open(str(performance), "rb") as draft:
        frames = _frames(lead)
        if frames != _frames(draft):
            raise ValueError("The melody guide and generated performance must have matching timelines.")
        guide_rms = _statistics(lead, deadline)[2]
        if guide_rms < 1e-6:
            raise ValueError("The melody guide contains no audible notes.")
        cache = _cache(performance, output.parent, deadline)
        try:
            if cache.rms < 1e-6:
                raise ValueError("The generated performance contains no audible audio.")
            # Gains are upper bounds: an inaudible draft estimate never acquires an invented part.
            drums, bass, other = cache.stem_rms
            bass_gain = min(1.4, 0.65 * guide_rms / max(bass, 1e-9))
            drum_gain = (
                min(3.0, 0.29 * guide_rms / max(drums, 1e-9)) if drums > RHYTHM_FLOORS[0] * cache.rms else 0.0
            )
            chord_gain = min(0.7, 0.27 * guide_rms / max(other, 1e-9))
            with _publish(output, frames, cache.rms, deadline) as append:
                for i in range(0, frames, BLOCK):
                    end = min(frames, i + BLOCK)
                    # STFT context prevents a block edge becoming a new musical transient.
                    a, b = max(0, i - 8192), min(frames, end + 8192)
                    stems = cache.read(a, b - a)
                    line = _read(lead, a, b - a)
                    chord = _duck(stems[:, 2], line)
                    span = slice(i - a, end - a)
                    append(
                        line[span]
                        + stems[span, 0] * drum_gain
                        + stems[span, 1] * bass_gain
                        + chord[span] * chord_gain
                    )
            return cache
        except BaseException:
            cache.close()
            raise


def _rms(x, axis=None):
    import numpy as np

    return np.sqrt(np.mean(x * x, axis=axis, dtype=np.float64))


def mix_performers(lead: Path, accompaniment: Path, output: Path, *, deadline: float) -> None:
    """Balance two already isolated performances using fixed gains throughout the song."""
    import numpy as np

    _check(deadline)
    _protect(output, lead, accompaniment)
    inputs = (lead, accompaniment)
    identities = tuple(_identity(path) for path in inputs)
    with wave.open(str(lead), "rb") as front, wave.open(str(accompaniment), "rb") as back:
        frames = _frames(front)
        if _frames(back) != frames:
            raise ValueError("The lead and accompaniment must have matching timelines.")
        hop = RATE // 4
        counts = np.minimum(hop, frames - np.arange(0, frames, hop))
        levels = np.empty((len(counts), 2))
        for row, count in enumerate(counts):
            _check(deadline)
            levels[row] = [_rms(_read(wav, row * hop, int(count))) for wav in (front, back)]
        # Measure phrases where both perform. If they trade phrases with little overlap,
        # balance each performer's active level separately. Gating affects measurement only.
        active = levels > np.maximum(0.0005, levels.max(axis=0) * 0.05)
        if not np.all(active.any(axis=0)):
            raise ValueError("The lead and accompaniment must each contain audible audio.")
        together = active.all(axis=1)
        available = min(counts[active[:, lane]].sum() for lane in range(2))
        if counts[together].sum() >= 0.1 * available:
            active = np.column_stack((together, together))
        rms = [
            math.sqrt(np.average(levels[mask, lane] ** 2, weights=counts[mask]))
            for lane, mask in enumerate(active.T)
        ]
        backing_gain = 10 ** (-5 / 20) * rms[0] / rms[1]
        # Do not raise the overall level. Reduce both together only when a peak needs headroom.
        with _publish(output, frames, None, deadline) as append:
            for pos in range(0, frames, BLOCK):
                count = min(BLOCK, frames - pos)
                append(_read(front, pos, count) + backing_gain * _read(back, pos, count))
            if identities != tuple(_identity(path) for path in inputs):
                raise RuntimeError("A performance changed while mixing the two parts.")


def _restore(draft, refined, cache, output, deadline):
    import numpy as np

    stats = _statistics(refined, deadline)
    if stats[2] < 1e-6:
        raise ValueError("The refined take contains no audible audio.")
    if _frames(draft) != _frames(refined) or cache.frames != _frames(refined):
        raise ValueError("The refined take and generated performance must have matching timelines.")
    previous = np.zeros(2)
    stem_levels = np.asarray(cache.stem_rms[:2])
    active = stem_levels > cache.rms * np.array(RHYTHM_FLOORS)
    # No discernible rhythm in the draft: the refined PCM already contains the complete result.
    if not active.any():
        with tempfile.TemporaryDirectory(prefix=".backing-", dir=output.parent) as tmp:
            ready = Path(tmp) / "ready.wav"
            with wave.open(str(ready), "wb") as out:
                out.setparams(refined.getparams())
                refined.rewind()
                for pos in range(0, cache.frames, BLOCK):
                    _check(deadline)
                    out.writeframesraw(refined.readframes(min(BLOCK, cache.frames - pos)))
            os.replace(ready, output)
        return
    # Use track loudness to retain the drummer's original accents, including fills in a lead rest.
    # Per-beat normalization against the refined melody would duck those fills into silence.
    target_gains = np.minimum(
        0.85 * stats[2] / max(cache.rms, 1e-8),
        np.array(RHYTHM_TARGETS) * stats[2] / np.maximum(stem_levels, 1e-8),
    )
    changed = False
    with _publish(output, cache.frames, stats[2], deadline, unchanged=lambda: not changed) as append:
        for pos, found in _separated(refined, stats, deadline, (0, 1)):
            length = len(found)
            backing = cache.read(pos, length)[:, :2]
            target = _read(refined, pos, length)
            original = _read(draft, pos, length)
            # A new arrangement can be much louder than the draft in a particular phrase.
            # Recover its local balance over several seconds, never by following the lead's beat envelope.
            phrase_level = _rms(target)
            phrase_gains = np.minimum(
                0.85 * phrase_level / max(_rms(original), 1e-8),
                np.array(RHYTHM_TARGETS) * phrase_level / np.maximum(_rms(backing, axis=(0, 2)), 1e-8),
            )
            restore_gain = np.minimum(MAX_RHYTHM_GAIN, np.maximum(target_gains, phrase_gains))
            for idx in range(0, length, RATE // 4):
                span = slice(idx, min(idx + RATE // 4, length))
                dry, wet = original[span], target[span]
                old_level = _rms(backing[span], axis=(0, 2))
                new_level = _rms(found[span], axis=(0, 2))
                ratio = old_level / max(_rms(dry), 1e-8)
                desired = old_level * restore_gain
                # Add only the missing amount. A 3 dB tolerance avoids doubling a surviving part.
                gains = np.maximum(0, desired - new_level) / np.maximum(old_level, 1e-8)
                gains *= active & (ratio > RHYTHM_FLOORS) & (new_level < 0.707 * desired)
                changed |= bool(np.any(gains))
                # A long attack ramp suppresses the hit that justified restoring the drummer.
                ramp = min(len(wet), RATE // 200)
                gain_curve = np.broadcast_to(gains, (len(wet), 2)).astype("float32")
                gain_curve[:ramp] = np.linspace(previous, gains, ramp, dtype="float32")
                append(wet + np.sum(backing[span] * gain_curve[:, :, None], axis=1))
                previous = gains


def restore_backing(
    performance: Path, refined: Path, output: Path, *, deadline: float, cache: BackingCache | None = None
) -> None:
    """Reintroduce missing bass/drums from the generated draft; an unidentifiable stem stays out."""
    _check(deadline)
    _protect(output, performance, refined)
    with wave.open(str(performance), "rb") as draft, wave.open(str(refined), "rb") as improved:
        if _frames(draft) != _frames(improved):
            raise ValueError("The generated performance and refined take must have matching timelines.")
        if cache is None:
            with _cache(performance, output.parent, deadline) as scratch:
                _restore(draft, improved, scratch, output, deadline)
        else:
            if cache.file.closed or cache.identity != _identity(performance):
                raise ValueError("The accompaniment cache belongs to a different generated performance.")
            _restore(draft, improved, cache, output, deadline)
