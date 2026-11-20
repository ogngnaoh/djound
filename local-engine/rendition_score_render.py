"""Perform timed General MIDI parts and publish one stereo recording."""
from importlib.util import find_spec
import math
import numbers
import os
from pathlib import Path
import subprocess
import tempfile
import time
import wave

from common import HOME


RATE = 44100
MAX_SECONDS = 600
BLOCK = 32768
MAX_NOTES = 65536
MAX_TRACKS = 32
PEAK = 0.91
REFLECTIONS = tuple((round(ms * RATE / 1000), gain) for ms, gain in
                    ((19, .09), (34, .067), (53, .053), (89, .038), (133, .024), (211, .014)))


class ScoreBalanceConflict(ValueError):
    """The edit cannot be heard at the kept players' saved levels."""


def score_available() -> bool:
    """Check the renderer protocol independently of any transcription or generation model."""
    if find_spec("numpy") is None or not (HOME / "FluidR3_GM.sf2").is_file():
        return False
    try:
        probe = subprocess.run([HOME / "score_renderer", "--version"], capture_output=True, timeout=2)
        return probe.returncode == 0 and probe.stdout.strip() == b"1"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("The scored performance exceeded its time limit.")
    return remaining


def _number(value, low, high, name, integer=False):
    kind = numbers.Integral if integer else numbers.Real
    if isinstance(value, bool) or not isinstance(value, kind) or not low <= value <= high:
        raise ValueError(f"Choose a valid {name} between {low} and {high}.")
    return int(value) if integer else float(value)


def _schedule(track, frames, path, remaining_notes):
    if not isinstance(track, dict):
        raise ValueError("Each score part needs an instrument and timed notes.")
    program = _number(track.get("program"), 0, 127, "instrument program", True)
    percussion = track.get("percussion", False)
    notes = track.get("notes")
    if not isinstance(percussion, bool) or not isinstance(notes, (list, tuple)) or len(notes) > remaining_notes:
        raise ValueError("Supply a percussion flag and a bounded list of timed notes for each part.")
    intervals = []
    for note in notes:
        if not isinstance(note, (list, tuple)) or len(note) != 4:
            raise ValueError("Each note needs a start, end, pitch and velocity.")
        start, end = [round(_number(t, 0, MAX_SECONDS, "note time") * RATE) for t in note[:2]]
        if not 0 <= start < end <= frames:
            raise ValueError("Every note must lie inside the scored recording.")
        pitch = _number(note[2], 0, 127, "MIDI pitch", True)
        velocity = _number(note[3], 1, 127, "note velocity", True)
        intervals.append((start, end, pitch, velocity))
    # A MIDI note-off otherwise releases the oldest instance of that pitch. Give
    # overlapping unisons different channels so every written duration is honored.
    occupied = {}
    events = []
    channels = 1
    for start, end, pitch, velocity in sorted(intervals):
        endings = occupied.setdefault(pitch, [])
        channel = next((i for i, finish in enumerate(endings) if finish <= start), len(endings))
        if channel == len(endings):
            if channel == 128:
                raise ValueError("Too many simultaneous notes of the same pitch in one part.")
            endings.append(end)
        else:
            endings[channel] = end
        channels = max(channels, channel + 1)
        events.extend(((start, velocity, channel, pitch), (end, 0, channel, pitch)))
    with path.open("w") as score:
        print(frames, 128 if percussion else 0, program, channels, len(events), file=score)
        for frame, vel, channel, pitch in sorted(events):  # Releases before attacks at a shared frame.
            print(frame, channel, pitch, vel, file=score)
    return len(notes)


def _add_part(mix, raw, schedule, frames, level, gain, pan, deadline):
    import numpy as np

    try:
        result = subprocess.run([HOME / "score_renderer", HOME / "FluidR3_GM.sf2", schedule, raw],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        raise TimeoutError("The scored performance exceeded its time limit.") from None
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(message[-240:] or "The local score renderer could not perform this part.")
    rms = float(result.stdout)
    if not math.isfinite(rms) or rms <= 0 or raw.stat().st_size != frames * 8:
        raise ValueError("A written part produced no usable audio with its selected instrument.")
    if level is not None:
        gain *= 10 ** (level / 20) / rms
    # Stereo balance preserves the bank's native stereo image at center.
    balance = np.array([min(1., 1 - pan), min(1., 1 + pan)], dtype=np.float32) * gain
    with raw.open("rb") as source:
        for frame in range(0, frames, BLOCK):
            _remaining(deadline)
            size = min(BLOCK, frames - frame) * 8
            part = np.frombuffer(source.read(size), dtype="<f4").reshape(-1, 2)
            mix.seek(frame * 8)
            previous = np.frombuffer(mix.read(size), dtype="<f4").reshape(-1, 2)
            mix.seek(frame * 8)
            mix.write(np.asarray(previous + part * balance, dtype="<f4").tobytes())
    raw.unlink()


def _apply_room(mix, frames, room, deadline):
    import numpy as np

    # One dry tail fits all the room's taps. Write the wet blocks back in place.
    delay = REFLECTIONS[-1][0]
    tail = np.zeros((delay, 2), dtype=np.float32)
    peak = 0.
    for frame in range(0, frames, BLOCK):
        _remaining(deadline)
        mix.seek(frame * 8)
        dry = np.frombuffer(mix.read(min(BLOCK, frames - frame) * 8), dtype="<f4").reshape(-1, 2)
        wet = dry.copy()
        if room:
            history = np.concatenate((tail, dry))
            for lag, strength in REFLECTIONS:
                begin = delay - lag
                wet += history[begin:begin + len(dry), ::-1] * (strength * room)
            tail = history[-delay:].copy()
        # Even a held note cut by the score boundary ends without a full-scale discontinuity.
        fade = min(441, frames)
        begin = max(0, frames - fade - frame)
        if begin < len(wet):
            left = frames - 1 - np.arange(frame + begin, frame + len(wet))
            wet[begin:] *= (left / max(1, fade - 1))[:, None]
        if not np.isfinite(wet).all():
            raise RuntimeError("The scored performance produced invalid audio.")
        peak = max(peak, float(np.max(np.abs(wet))))
        mix.seek(frame * 8)
        mix.write(wet.astype("<f4", copy=False).tobytes())
    mix.seek(0)
    return peak


def _headroom(mix, changed, limit, deadline, *, rounding_margin=0.):
    """Find the loudest constant gain for edited parts that leaves the other players fixed."""
    import numpy as np

    lower, upper = 0., 1.
    while block := mix.read(BLOCK * 8):
        _remaining(deadline)
        full = np.frombuffer(block, "<f4").astype(float)
        voice = np.frombuffer(changed.read(len(block)), "<f4").astype(float)
        backing = full - voice
        # If both endpoints fit, every gain from silence to the written lead fits too.
        at_risk = (abs(full) > limit + rounding_margin) | (abs(backing) > limit + rounding_margin)
        if not at_risk.any():
            continue
        v, b = voice[at_risk], backing[at_risk]
        if (v == 0).any():
            return None
        left, right = (-limit - b) / v, (limit - b) / v
        lower = max(lower, float(np.minimum(left, right).max()))
        upper = min(upper, float(np.maximum(left, right).min()))
        if upper < lower or upper <= 0:
            return None
    return upper


def _write_pcm(mix, frames, ready, master, deadline, legacy_lead=None, legacy_gain=1.):
    import numpy as np

    mix.seek(0)
    if legacy_lead is not None:
        legacy_lead.seek(0)
    with wave.open(str(ready), "wb") as wav:
        wav.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
        while chunk := mix.read(BLOCK * 8):
            _remaining(deadline)
            wet = np.frombuffer(chunk, dtype="<f4")
            if legacy_gain < 1:
                wet = wet - (1 - legacy_gain) * np.frombuffer(legacy_lead.read(len(chunk)), "<f4")
            pcm = np.rint(wet * (master * 32768)).astype("<i2")
            wav.writeframesraw(pcm.tobytes())


def render_score(tracks, duration: float, output: Path, *, deadline: float, room: float = 1.0,
                 master_gain: float | None = None, part_gains=None, protected_parts=None, receipt=None) -> float:
    """Perform (start, end, pitch, velocity) notes at each part's gain, pan and optional RMS level.

    A new mix inherits the master and individual gains of protected parts. A score
    with saved part gains and no protection list is a replay. Older master-only
    scores retain their original lead-only headroom calculation and PCM rounding.
    The optional receipt records the effective master and part gains for later edits.
    """
    _remaining(deadline)
    duration = _number(duration, 0, MAX_SECONDS, "score duration")
    room = _number(room, 0, 1, "room amount")
    if master_gain is not None:
        master_gain = _number(master_gain, 0, 1, "master gain")
        if master_gain == 0:
            raise ValueError("A saved master gain must be positive.")
    frames = round(duration * RATE)
    if not frames or not isinstance(tracks, (list, tuple)) or not 1 <= len(tracks) <= MAX_TRACKS:
        raise ValueError(f"Supply a positive duration and one to {MAX_TRACKS} scored parts.")
    if part_gains is not None and len(part_gains) != len(tracks):
        raise ValueError("Supply one saved gain per scored part.")
    gains = ([_number(value, 0, 1, "saved part gain") for value in part_gains]
             if part_gains is not None else [1.] * len(tracks))
    legacy = part_gains is None and protected_parts is None
    # A legacy saved master protected every part except the first. Explicit gains
    # replay as written unless the caller names the parts to protect on a new edit.
    protected = set()
    if protected_parts is None:
        editable = [0] if legacy and len(tracks) > 1 else []
    else:
        protected = {_number(i, 0, len(tracks) - 1, "protected part", True) for i in protected_parts}
        editable = [i for i in range(len(tracks)) if i not in protected]

    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Save the performance to a new WAV path.")
    if not score_available():
        raise RuntimeError("Install the score performer with bash local-engine/renditions.sh setup.")
    with tempfile.TemporaryDirectory(prefix=".score-", dir=output.parent) as directory:
        work = Path(directory)
        parts = []
        remaining_notes = MAX_NOTES
        for i, track in enumerate(tracks):
            _remaining(deadline)
            schedule = work / f"part-{i}.txt"
            count = _schedule(track, frames, schedule, remaining_notes)
            remaining_notes -= count
            gain = _number(track.get("gain", 1.), 0, 16, "part gain")
            pan = _number(track.get("pan", 0.), -1, 1, "part pan")
            level = track.get("level_db")
            if level is not None:
                level = _number(level, -96, 0, "part RMS level")
            parts.append((schedule, count, gain, pan, level))

        def perform(bus, indices):
            bus.truncate(0)
            bus.truncate(frames * 8)
            for i in indices:
                schedule, count, gain, pan, level = parts[i]
                if count and gain and gains[i]:
                    _add_part(bus, work / "part.f32", schedule, frames, level, gain * gains[i], pan, deadline)
            return _apply_room(bus, frames, room, deadline)

        ready = work / "score.wav"
        with tempfile.TemporaryFile(dir=work) as mix, tempfile.TemporaryFile(dir=work) as changed:
            peak = perform(mix, range(len(parts)))
            if peak == 0:
                raise ValueError("The score contains no audible performance.")
            master = min(1., PEAK / peak) if master_gain is None else master_gain
            legacy_gain = 1.
            if master * peak > PEAK + 1e-8:
                fit = None
                if editable:
                    changed_peak = perform(changed, editable)
                    limit = PEAK / master
                    # An old post-sum gain can put a preserved isolated sample a
                    # few float32 rounding units above the equivalent new mix.
                    fit = _headroom(mix, changed, limit, deadline,
                                    rounding_margin=0. if legacy else limit * 2e-7)
                if protected and fit is not None:
                    # A near-unison at just one attack can consume all remaining
                    # headroom, silencing the edited group's later, unrelated notes.
                    # Treat a reduction of over 30 dB to under eight PCM counts as
                    # effectively muted; intentionally faint written parts are valid.
                    written_peak = changed_peak * master
                    if fit < .0316 and written_peak >= 8 / 32768 > written_peak * fit:
                        raise ScoreBalanceConflict("The changed part would be too quiet to hear at this balance.")
                if fit is None:
                    # A standalone protected mix may itself exceed the limit, e.g.
                    # when removing a player that partially cancelled it.
                    master = PEAK / peak
                else:
                    for i in editable:
                        gains[i] *= fit
                    if legacy:
                        legacy_gain = fit
                    else:
                        # Publish from the same per-part gains we save. Applying this
                        # trim after summing the room instead would round differently
                        # on a future replay. The extra render only runs on overload.
                        peak = perform(mix, range(len(parts)))
                        if peak * master > PEAK + 1e-8:
                            master = PEAK / peak
                if protected and (master_gain - master) * peak > 1 / 32768:
                    raise ScoreBalanceConflict("Keeping the other players at this volume would overload the recording.")
            _write_pcm(mix, frames, ready, master, deadline, changed, legacy_gain)
        _remaining(deadline)
        os.link(ready, output)
        if receipt is not None:
            receipt.update(scoreMasterGain=master, scorePartGains=gains)
        return master
