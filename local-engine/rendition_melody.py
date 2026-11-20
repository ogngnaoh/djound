"""Extract a likely lead from a canonical recording and synthesize a timed instrumental guide."""

from collections import deque
from functools import cache
from importlib.util import find_spec
import json
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
MELODY_TRACE_VERSION = 7
TRACE_SOURCES = ("single", "mix", "separated_voice", "instrument_tracks", "separated_player")


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


def _frame_count(source, limit=MAX_FRAMES):
    with wave.open(str(source), "rb") as wav:
        if (
            wav.getframerate() != RATE
            or wav.getsampwidth() != 2
            or wav.getnchannels() not in (1, 2)
            or wav.getcomptype() != "NONE"
            or not 0 < wav.getnframes() <= limit
        ):
            raise ValueError("Choose a PCM16 44.1 kHz recording within the supported duration.")
        return wav.getnframes()


def build_melody_guide(
    source: Path, output: Path, *, program: int, deadline: float, preserve_register: bool = False
) -> bool:
    """Write the complete guide atomically. False means the recording lacks a reliable musical line."""
    return _extract_to_file(source, output, program, deadline, preserve_register, notes_only=False)


def transcribe_recording(source: Path, output: Path, *, deadline: float, trace_version=MELODY_TRACE_VERSION,
                         trace_source=None) -> bool:
    """Extract the tune and other pitched events into a private score input."""
    return _extract_to_file(source, output, 0, deadline, False, notes_only=True, trace_version=trace_version,
                            trace_source=trace_source)


def _extract_to_file(source, output, program, deadline, preserve_register, *, notes_only,
                     trace_version=MELODY_TRACE_VERSION, trace_source=None):
    if type(trace_version) is not int or not 1 <= trace_version <= MELODY_TRACE_VERSION:
        raise ValueError("This saved take uses an unsupported melody transcription.")
    if trace_source is not None and (trace_version < 3 or trace_source not in TRACE_SOURCES
                                     or (trace_source == "instrument_tracks" and trace_version < 6)
                                     or (trace_source == "separated_player" and trace_version < 7)):
        raise ValueError("This saved take has an unknown melody source.")
    if type(preserve_register) is not bool:
        raise ValueError("Specify whether to keep the recorded octave.")
    if type(program) is not int or not 0 <= program <= 127:
        raise ValueError("Choose a General MIDI instrument between 0 and 127.")
    if not math.isfinite(deadline) or deadline <= time.monotonic():
        raise TimeoutError("Melody extraction exceeded its time limit.")
    source, output = Path(source).resolve(strict=True), Path(output).absolute()
    if source == output.resolve() or (output.exists() and source.samefile(output)):
        raise ValueError("Choose a separate output for the melody guide.")
    frames = _frame_count(source, 600 * RATE if notes_only else MAX_FRAMES)
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
                    "notes" if notes_only else "audio",
                    str(trace_version),
                    trace_source or "auto",
                    str(deadline),
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
        staged = work / ("notes.json" if notes_only else "guide.wav")
        if not notes_only and _frame_count(staged) != frames:
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


def _melodic_register(events, frames):
    """Follow the local upper voice through brief spaces between its attacks."""
    import numpy as np
    from scipy.ndimage import percentile_filter

    upper_voice = np.full(frames, np.nan)
    for start, end, pitch, confidence in events:
        if end - start >= .15 and confidence >= .29:
            segment = upper_voice[max(0, round(start * 50)):min(frames, round(end * 50))]
            np.fmax(segment, pitch, out=segment)
    present = np.flatnonzero(np.isfinite(upper_voice))
    if not len(present):
        return None
    filled = np.interp(np.arange(frames), present, upper_voice[present])
    return percentile_filter(filled, percentile=65, size=121, mode="nearest")


def _select_polyphonic(events, duration, *, trace_version=MELODY_TRACE_VERSION):
    """Trace the prominent upper voice; favor sustained notes, strong attacks and small intervals."""
    import numpy as np

    n = math.ceil(duration * 50)
    register = _melodic_register(events, n) if trace_version >= 2 else None
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
    distances = np.abs(keys[:, None] - keys)
    leap_cost = np.minimum(distances * .035, .62) if register is not None else np.minimum(distances * .025, .32)
    costs[:88, :88] = .68 + leap_cost
    np.fill_diagonal(costs, 0.0)
    score = np.zeros(89)
    previous = np.zeros((n, 89), np.int8)
    for i in range(n):
        valid = np.flatnonzero(active[i] > 0.2)
        evidence = np.full(89, -4.0)
        evidence[-1] = .18 if register is not None else .12
        if len(valid):
            below = np.minimum((valid[-1] - valid) / 12, 2)
            evidence[valid] = (
                0.44
                + active[i, valid]
                - 0.48 * below
                + 0.14 * onset[i, valid]
                - 0.12 * np.minimum(age[i, valid], 1.5)
            )
            if register is not None:
                # When a theme pauses, an isolated low accompaniment or high overtone
                # can be the strongest pitch in a frame. The lead can rest there.
                offset = valid + 21 - register[i]
                evidence[valid] -= .095 * np.maximum(-offset - 4, 0) + .13 * np.maximum(offset - 7, 0)
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
            if trace_version >= 2 and lo / 50 - a > .13:
                # Switching to a note's sustained tail would invent another attack.
                continue
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

    use = np.isfinite(pitch) & (confidence > 0.05)
    if not use.any():
        return []
    # Use sustained pitch changes and syllable energy dips, avoiding a new note for each vibrato cycle.
    with sf.SoundFile(source) as wav:
        levels = [
            10 * np.log10(1.0e-8 + np.mean(block.mean(axis=1) ** 2))
            for block in wav.blocks(blocksize=882, dtype="float32", always_2d=True)
        ]
    envelope = median_filter(np.interp(times, np.arange(len(levels)) * 0.02 + 0.01, levels), 3)
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


def _singer_in_mix(source, work, events, duration, deadline, *, require_foreground=False):
    """Use the estimated voice only while there is audible and pitched vocal evidence."""
    import numpy as np
    from rendition_sketch import SeparatorUnavailable, backing_available, vocal_reference

    if not backing_available():
        return []
    stem = work / "singer.wav"
    try:
        centers, vocal_level, mix_level = vocal_reference(source, stem, deadline)
    except SeparatorUnavailable:
        return []
    # A separator can leave pitched traces of an instrument in its vocal output.
    # Require sustained energy relative to the actual recording as well as a coherent line.
    share = vocal_level / np.maximum(mix_level, .0005)
    present = (share >= .16) & (vocal_level >= .0008)
    audible_time = np.count_nonzero(present) * .1
    if audible_time < min(1.5, duration * .35):
        return []
    if require_foreground:
        # A genuine phrase can occur anywhere in a mostly instrumental recording.
        # Isolated leaks should not establish a singer by accumulating across the track.
        foreground = present & (share >= .5)
        window = np.ones(min(15, len(present)), dtype=np.int16)
        foreground_bins = np.convolve(foreground, window, "valid")
        phrase = foreground_bins * 3 >= len(window) * 2
        if not phrase.any():
            return []
    times, pitch, confidence = _fundamental(stem)
    heard = (np.interp(times, centers, share) >= .16) & (np.interp(times, centers, vocal_level) >= .0008)
    confidence = np.where(heard, confidence, 0.)
    voiced = np.isfinite(pitch) & (confidence >= .15)
    if voiced.sum() * .02 < min(1.5, duration * .35):
        return []
    supported = np.zeros(len(times), bool)
    for start, end, key, strength in events:
        if strength >= .22:
            lo, hi = times.searchsorted((start, end))
            supported[lo:hi] |= abs(pitch[lo:hi] - key) <= 1
    if supported[voiced].mean() < .35:
        return []
    if require_foreground:
        # Loud unpitched sounds elsewhere cannot corroborate a weak pitched leak.
        agreeing = np.interp(centers, times, voiced & supported) >= .5
        agreed_bins = np.convolve(foreground & agreeing, window, "valid")
        if not np.any(phrase & (agreed_bins >= .35 * foreground_bins)):
            return []
    line = _voiced_notes(stem, times, pitch, confidence)
    return line if _reliable(line, audible_time if require_foreground else duration) else []


def _with_instrumental_phrases(voice, events, duration, *, mix_line=None):
    """Return the lead to an instrument when it plays a distinct phrase in a vocal rest."""
    from bisect import bisect_left
    from statistics import median

    note_length = median(end - start for start, end, _, _ in voice)
    gaps = []
    for i in range(len(voice) + 1):
        start = voice[i - 1][1] if i else 0.
        end = voice[i][0] if i < len(voice) else duration
        if end - start >= max(2., 4 * note_length):
            gaps.append((i, start, end))
    if not gaps:
        return voice
    written = mix_line() if mix_line else _select_polyphonic(events, duration, trace_version=2)
    onsets = [note[0] for note in written]
    result = list(voice)
    for i, start, end in gaps:
        neighbors = voice[max(0, i - 3):i + 3]
        vocal_register = median(note[2] for note in neighbors)
        phrases = []
        # Allow an early pickup within onset uncertainty. A held accompaniment
        # from under the singer should not acquire a new attack at the boundary.
        for a, b, pitch, confidence in written[bisect_left(onsets, start - .12):bisect_left(onsets, end)]:
            if a < start and i and pitch == voice[i - 1][2]:
                continue
            a, b = max(start, a), min(end, b)
            if b - a < .08:
                continue
            if (not phrases or a - phrases[-1][-1][1] > max(.65, 2 * note_length)
                    or abs(pitch - phrases[-1][-1][2]) > 12):
                phrases.append([])
            phrases[-1].append((a, b, pitch, confidence))
        for phrase in phrases:
            if not _reliable(phrase, phrase[-1][1] - phrase[0][0]):
                continue
            pitches = [p for a, b, p, c in phrase if b - a >= .1 and c >= .32]
            if median(pitches) <= vocal_register - 7:
                # A lower instrument may answer in its own register. Require it
                # to develop beyond an alternating bass or a cycling arpeggio.
                if len(set(pitches)) < 3 or any(
                    pitches[period:] == pitches[:-period] for period in range(1, min(4, len(pitches) // 2) + 1)
                ):
                    continue
            result.extend(phrase)
    return sorted(result)


def _recorded_line(source, work, events, duration, trace_version, trace_source, deadline):
    @cache
    def mix_line():
        if trace_version >= 4:
            from rendition_foreground import foreground_notes
            notes = foreground_notes(source, events, duration)
            if trace_version >= 5:
                from rendition_octaves import recover_octave_excursions
                return recover_octave_excursions(source, events, notes, duration)
            return notes
        return _select_polyphonic(events, duration, trace_version=trace_version)

    def instrumental_line():
        notes = mix_line()
        if trace_version >= 6 and trace_source != "mix":
            from rendition_ensemble import resolve_players
            players = resolve_players(source, work, events, notes, duration, deadline,
                                      pinned=trace_source == "instrument_tracks", localized=trace_version >= 7)
            if players is not None:
                return players, "instrument_tracks"
        return notes, "mix"

    if trace_source in ("mix", "instrument_tracks"):
        return instrumental_line()
    if trace_source not in ("separated_voice", "separated_player"):
        times, pitch, confidence = _fundamental(source)
        if trace_source == "single" or _single_voice(times, pitch, confidence, events):
            return _voiced_notes(source, times, pitch, confidence), "single"
    if trace_version >= 3:
        voice = _singer_in_mix(source, work, events, duration, deadline, require_foreground=trace_version >= 4)
        if voice:
            separated = _with_instrumental_phrases(voice, events, duration,
                                                   mix_line=mix_line if trace_version >= 4 else None)
            if trace_version >= 7 and trace_source != "separated_voice" and (
                    trace_source == "separated_player" or (HOME / "instrument-transcriber").exists()):
                from rendition_ensemble import resolve_separated_player
                selected = resolve_separated_player(source, work, events, separated, mix_line(), duration,
                                                    deadline, pinned=trace_source == "separated_player")
                if selected is not None:
                    return selected, "separated_player"
            return separated, "separated_voice"
        if trace_source == "separated_player":
            raise ValueError("The saved instrument melody could not be recovered. Check the local separator installation.")
        if trace_source == "separated_voice":
            raise ValueError("The saved vocal melody could not be recovered. Check the local separator installation.")
    return instrumental_line()


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


def _extract(source, work, program, preserve_register, *, notes_only=False, trace_version=MELODY_TRACE_VERSION,
             trace_source=None, deadline=None):
    import numpy as np
    import soundfile as sf

    if deadline is None:
        from rendition_audio import rendition_deadline
        deadline = rendition_deadline()
    frames = _frame_count(source, 600 * RATE if notes_only else MAX_FRAMES)
    with sf.SoundFile(source) as wav:
        if not any(np.max(np.abs(block)) > 0.0003 for block in wav.blocks(blocksize=RATE, dtype="float32")):
            return False
    events = _candidates(source)
    if not events:
        return False
    notes, chosen = _recorded_line(source, work, events, frames / RATE, trace_version, trace_source, deadline)
    if not _reliable(notes, frames / RATE):
        return False
    if notes_only:
        with (work / "notes.json").open("w") as file:
            data = {"frames": frames, "melody": notes, "candidates": events}
            if trace_version >= 3:
                data["melodySource"] = chosen
            json.dump(data, file, allow_nan=False)
    else:
        _synthesize(notes, work / "guide.wav", program, frames, preserve_register)
    return True


if __name__ == "__main__":
    ok = _extract(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), bool(int(sys.argv[4])),
                  notes_only=len(sys.argv) > 5 and sys.argv[5] == "notes",
                  trace_version=int(sys.argv[6]) if len(sys.argv) > 6 else MELODY_TRACE_VERSION,
                  trace_source=None if len(sys.argv) <= 7 or sys.argv[7] == "auto" else sys.argv[7],
                  deadline=float(sys.argv[8]) if len(sys.argv) > 8 else time.monotonic() + 1200)
    sys.exit(0 if ok else NO_LINE)
