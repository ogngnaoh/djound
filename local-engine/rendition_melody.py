"""Extract a likely lead from a canonical recording and synthesize a timed instrumental guide."""

from collections import deque
from importlib.util import find_spec
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import wave

from common import HOME

RATE = 44100
MAX_FRAMES = 300 * RATE
MODEL = Path("basic-pitch/basic_pitch/saved_models/icassp_2022/nmp.onnx")
BANK = "FluidR3_GM.sf2"
NO_LINE = 3


def guide_available() -> bool:
    """Cheap readiness check. Inference libraries are loaded only in the extraction subprocess."""
    if not all((HOME / p).is_file() for p in (MODEL, BANK, "basic-pitch/basic_pitch/note_creation.py")):
        return False
    if not all(find_spec(p) for p in ("numpy", "onnxruntime", "scipy", "librosa", "pretty_midi", "soundfile")):
        return False
    try:
        probe = subprocess.run([HOME / "renderer", "--guide-version"], capture_output=True, timeout=2)
        return probe.returncode == 0 and probe.stdout.strip() == b"2"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _frame_count(source):
    with wave.open(str(source), "rb") as wav:
        if (
            wav.getframerate() != RATE
            or wav.getsampwidth() != 2
            or wav.getnchannels() not in (1, 2)
            or wav.getcomptype() != "NONE"
            or not 0 < wav.getnframes() <= MAX_FRAMES
        ):
            raise ValueError("The melody guide requires a PCM16 44.1 kHz recording up to five minutes.")
        return wav.getnframes()


def build_melody_guide(
    source: Path, output: Path, *, program: int, deadline: float, preserve_register: bool = False
) -> bool:
    """Write the complete guide atomically. False means the recording lacks a reliable musical line."""
    if type(preserve_register) is not bool:
        raise ValueError("Specify whether to keep the recorded octave.")
    if type(program) is not int or not 0 <= program <= 127:
        raise ValueError("Choose a General MIDI instrument between 0 and 127.")
    if not math.isfinite(deadline) or deadline <= time.monotonic():
        raise TimeoutError("Melody extraction exceeded its time limit.")
    source, output = Path(source).resolve(strict=True), Path(output).absolute()
    if source == output.resolve() or (output.exists() and source.samefile(output)):
        raise ValueError("Choose a separate output for the melody guide.")
    frames = _frame_count(source)
    if not guide_available():
        raise RuntimeError("Set up the local melody transcriber, renderer and instrument bank.")
    with tempfile.TemporaryDirectory(prefix=".melody-", dir=output.parent) as folder:
        work = Path(folder)
        # All model/JIT scratch belongs to this request. The parent can interrupt native libraries and
        # the renderer together even when either is blocked inside a single long-running call.
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "NUMBA_CACHE_DIR": str(work / "numba"),
            "DJOUND_LOCAL_ENGINE_HOME": str(HOME),
            "MPLCONFIGDIR": str(work / "matplotlib"),
            "XDG_CACHE_HOME": str(work / "cache"),
            "OPENBLAS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
            "TMPDIR": str(work),
        }
        env.pop("LIBROSA_CACHE_DIR", None)
        with (work / "extract.log").open("wb") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    Path(__file__).resolve(),
                    source,
                    work,
                    str(program),
                    str(int(preserve_register)),
                ],
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                result = child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise TimeoutError("Melody extraction exceeded its time limit.") from None
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
        if result == NO_LINE:
            return False
        if result != 0:
            detail = (work / "extract.log").read_text(errors="replace").splitlines()
            raise RuntimeError(
                "Could not render the melody guide." + (" " + detail[-1][:240] if detail else "")
            )
        staged = work / "guide.wav"
        if _frame_count(staged) != frames:
            raise ValueError("The melody guide has an incomplete timeline.")
        staged.replace(output)
        return True


def _predictions(wav, model):
    import numpy as np
    from scipy.signal import resample_poly

    # The pinned Basic Pitch graph uses 43844 input samples (22.05 kHz) and 30 overlapping
    # output frames. Retain the center of each window, aligned to absolute source time.
    window, edge = 43844, 3840
    stride = window - 2 * edge
    for start in range(0, (len(wav) + 1) // 2, stride):
        left = start - edge
        first = max(0, left)
        wav.seek(2 * first)
        offset = first - left
        count = min(2 * (window - offset), len(wav) - wav.tell())
        chunk = wav.read(count, dtype="float32", always_2d=True).mean(axis=1)
        samples = np.zeros(window, dtype="float32")
        downsampled = resample_poly(chunk, 1, 2)
        samples[offset : offset + len(downsampled)] = downsampled
        notes, attacks = model.run(
            ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
            {"serving_default_input_2:0": samples[None, :, None]},
        )
        notes, attacks = notes[0, 15:-15], attacks[0, 15:-15]
        times = (start + np.arange(len(notes)) * stride / len(notes)) / 22050
        keep = times < len(wav) / RATE
        yield times[keep], notes[keep], attacks[keep]


def _candidates(source):
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf

    sys.path.insert(0, str(HOME / "basic-pitch"))
    from basic_pitch.note_creation import output_to_notes_polyphonic

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = opts.inter_op_num_threads = 1
    model = ort.InferenceSession(str(HOME / MODEL), sess_options=opts, providers=["CPUExecutionProvider"])
    events = []
    windows = deque()
    processed = 0.0

    def decode(end):
        times, notes, attacks = (np.concatenate(part) for part in zip(*windows))
        if len(times) < 7:
            return
        found = output_to_notes_polyphonic(
            frames=notes,
            onsets=attacks,
            onset_thresh=0.44,
            frame_thresh=0.27,
            min_note_len=6,
            infer_onsets=True,
            min_freq=None,
            max_freq=None,
            melodia_trick=True,
        )
        for a, b, p, c in found:
            if processed <= times[a] < end:
                events.append((float(times[a]), float(times[min(b, len(times) - 1)]), int(p), float(c)))

    with sf.SoundFile(source) as wav:
        for prediction in _predictions(wav, model):
            windows.append(prediction)
            if len(windows) == 8:
                end = windows[-1][0][0]
                decode(end)
                processed = end
                for _ in range(6):
                    windows.popleft()
        if windows:
            decode(math.inf)
    return sorted(events)


def _select_polyphonic(events, duration):
    """Trace the prominent upper voice; favor sustained notes, strong attacks and small intervals."""
    import numpy as np

    n = math.ceil(duration * 50)
    active = np.zeros((n, 88), np.float32)
    onset = np.zeros_like(active)
    age = np.zeros_like(active)
    event_id = np.full((n, 88), -1, np.int32)
    for i, (start, end, pitch, confidence) in enumerate(events):
        lo, hi = max(0, round(start * 50)), min(n, round(end * 50))
        if confidence < 0.2 or hi <= lo:
            continue
        k = pitch - 21
        elapsed = np.arange(hi - lo) / 50
        active[lo:hi, k] = confidence
        onset[lo:hi, k] = np.exp(-elapsed / 0.16)
        age[lo:hi, k] = elapsed
        event_id[lo:hi, k] = i
    # 88 piano keys plus rest. A five-minute performance needs 15000 x 89 back-pointers.
    keys = np.arange(88)
    costs = np.full((89, 89), 0.35)
    costs[:88, :88] = 0.68 + np.minimum(np.abs(keys[:, None] - keys) * 0.025, 0.32)
    np.fill_diagonal(costs, 0.0)
    score = np.zeros(89)
    previous = np.zeros((n, 89), np.int8)
    for i in range(n):
        valid = np.flatnonzero(active[i] > 0.2)
        evidence = np.full(89, -4.0)
        evidence[-1] = 0.12
        if len(valid):
            below = np.minimum((valid[-1] - valid) / 12, 2)
            evidence[valid] = (
                0.44
                + active[i, valid]
                - 0.48 * below
                + 0.14 * onset[i, valid]
                - 0.12 * np.minimum(age[i, valid], 1.5)
            )
        choices = score[:, None] - costs
        previous[i] = choices.argmax(axis=0)
        score = choices.max(axis=0) + evidence
        score -= score.max()
    state = int(score.argmax())
    path = np.full(n, -1, np.int32)
    for i in reversed(range(n)):
        if state < 88:
            path[i] = event_id[i, state]
        state = previous[i, state]
    boundaries = np.r_[0, np.flatnonzero(np.diff(path)) + 1, n]
    selected = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        if path[lo] >= 0 and hi - lo >= 4:
            a, b, p, c = events[path[lo]]
            selected.append((max(a, lo / 50 - 0.01), min(b, hi / 50 + 0.01, duration), p, c))
    return selected


def _fundamental(source):
    import librosa
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    tracks = []
    with sf.SoundFile(source) as wav:
        duration = len(wav) / RATE
        for k in np.arange(0, duration, 12.0):
            lo, hi = max(0, k - 1), min(duration, k + 13)
            wav.seek(round(lo * RATE))
            mono = wav.read(round((hi - lo) * RATE), dtype="float32", always_2d=True).mean(axis=1)
            down = resample_poly(mono, 1, 2)
            f, _, confidence = librosa.pyin(
                down,
                sr=22050,
                fmin=65.0,
                fmax=1400.0,
                frame_length=2048,
                hop_length=441,
                resolution=0.25,
                max_transition_rate=50,
                fill_na=np.nan,
            )
            t = lo + np.arange(len(f)) * 0.02
            keep = (t >= k) & (t < k + 12) & (t < duration)
            tracks.append((t[keep], librosa.hz_to_midi(f[keep]), confidence[keep]))
    return tuple(np.concatenate(part) for part in zip(*tracks))


def _single_voice(times, pitches, confidence, events):
    import numpy as np

    count = np.zeros(len(times), int)
    for start, end, _, strength in events:
        if strength >= 0.32:
            lo, hi = times.searchsorted((start, end))
            count[lo:hi] += 1
    voiced = np.isfinite(pitches) & (confidence > 0.15)
    return voiced.mean() > 0.35 and (count[voiced] > 1).mean() < 0.25


def _voiced_notes(source, times, pitch, confidence):
    import numpy as np
    import soundfile as sf
    from scipy.ndimage import binary_closing, median_filter
    from scipy.signal import find_peaks

    # Use sustained pitch changes and syllable energy dips, avoiding a new note for each vibrato cycle.
    with sf.SoundFile(source) as wav:
        levels = [
            10 * np.log10(1.0e-8 + np.mean(block.mean(axis=1) ** 2))
            for block in wav.blocks(blocksize=882, dtype="float32", always_2d=True)
        ]
    envelope = median_filter(np.interp(times, np.arange(len(levels)) * 0.02 + 0.01, levels), 3)
    use = np.isfinite(pitch) & (confidence > 0.05)
    voiced = binary_closing(use, structure=np.ones(3))
    line = median_filter(np.interp(times, times[use], pitch[use]), 5)
    energy_valleys = find_peaks(-envelope, prominence=5.5, distance=8)[0]
    confidence_valleys = find_peaks(-confidence, prominence=0.48, distance=9)[0]
    edges = np.flatnonzero(np.diff(np.r_[False, voiced, False])).reshape(-1, 2)
    notes = []
    for a, b in edges:
        if b - a < 6:
            continue
        motion = np.zeros(b - a)
        for i in range(a + 4, b - 4):
            motion[i - a] = abs(np.median(line[max(a, i - 7) : i]) - np.median(line[i : min(b, i + 7)]))
        cuts = list(find_peaks(motion, height=1.65, distance=7)[0] + a)
        for i in energy_valleys[(energy_valleys > a + 5) & (energy_valleys < b - 5)]:
            if all(abs(c - i) >= 8 for c in cuts):
                cuts.append(i)
        for i in confidence_valleys[(confidence_valleys > a + 8) & (confidence_valleys < b - 8)]:
            if (
                confidence[i] < 0.14
                and max(envelope[i - 5 : i]) - envelope[i] > 2
                and all(abs(c - i) >= 10 for c in cuts)
            ):
                cuts.append(i)
        bounds = [a, *sorted(cuts), b]
        for lo, hi in zip(bounds, bounds[1:]):
            valid = use[lo:hi]
            if valid.sum() < 5:
                continue
            pitches, weights = pitch[lo:hi][valid], confidence[lo:hi][valid]
            order = pitches.argsort()
            mid = np.searchsorted(np.cumsum(weights[order]), weights.sum() / 2)
            notes.append(
                (times[lo], times[hi - 1] + 0.02, round(float(pitches[order[mid]])), float(weights.mean()))
            )
    return notes


def _reliable(notes, duration):
    confident = [(end - start, p) for start, end, p, c in notes if c >= 0.32 and end - start >= 0.10]
    pitched_time = sum(span for span, _ in confident)
    return (
        len(confident) >= 3
        and len({pitch for _, pitch in confident}) >= 2
        and pitched_time >= min(1.5, duration * 0.4)
        and pitched_time >= duration * 0.05
    )


def _synthesize(notes, path, program, frames, preserve_register=False):
    events = []
    for start, end, pitch, confidence in notes:
        lo, hi = max(0, round(start * RATE)), min(frames, round(end * RATE))
        if hi > lo:
            velocity = round(65 + 25 * min(1.0, max(0.0, confidence)))
            events.extend(((lo, 1, pitch, velocity), (hi, 0, pitch, velocity)))
    work = path.parent
    schedule = work / "notes.txt"
    with schedule.open("w") as file:
        print(frames, program, len(events), file=file)
        for event in sorted(events):
            print(*event, file=file)
    raw = work / "guide.pcm"
    mode = "--guide-absolute" if preserve_register else "--guide"
    subprocess.run([HOME / "renderer", mode, HOME / BANK, schedule, raw], check=True)
    if raw.stat().st_size != 4 * frames:
        raise RuntimeError("The guide renderer returned incomplete audio.")
    with raw.open("rb") as samples, wave.open(str(path), "wb") as out:
        out.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
        while block := samples.read(262144):
            out.writeframesraw(block)


def _extract(source, work, program, preserve_register):
    import numpy as np
    import soundfile as sf

    frames = _frame_count(source)
    with sf.SoundFile(source) as wav:
        if not any(np.max(np.abs(block)) > 0.0003 for block in wav.blocks(blocksize=RATE, dtype="float32")):
            return False
    events = _candidates(source)
    if not events:
        return False
    times, pitch, confidence = _fundamental(source)
    notes = (
        _voiced_notes(source, times, pitch, confidence)
        if _single_voice(times, pitch, confidence, events)
        else _select_polyphonic(events, frames / RATE)
    )
    if not _reliable(notes, frames / RATE):
        return False
    _synthesize(notes, work / "guide.wav", program, frames, preserve_register)
    return True


if __name__ == "__main__":
    ok = _extract(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), bool(int(sys.argv[4])))
    sys.exit(0 if ok else NO_LINE)
