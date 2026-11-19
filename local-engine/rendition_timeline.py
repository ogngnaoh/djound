"""Map performance frames through the uploaded song's musical landmarks."""
from bisect import bisect_left, bisect_right
from fractions import Fraction
import json
import math
from pathlib import Path
import tempfile
import time
import wave

from rendition_melody import transcribe_recording
from rendition_score_render import MAX_NOTES, MAX_SECONDS, RATE, render_score


def _lerp(x, a, b, lo, hi):
    return lo + Fraction(x - a, b - a) * (hi - lo)


def _round_frame(x):
    # Match Java Math.round on nonnegative positions, including exact half frames.
    return math.floor(x + Fraction(1, 2))


class _Timeline:
    def __init__(self, pairs):
        if not isinstance(pairs, (tuple, list)) or not 2 <= len(pairs) <= 16384:
            raise ValueError("The performance needs valid frame landmarks.")
        self.file = []
        self.song = []
        for point in pairs:
            if not isinstance(point, (tuple, list)) or len(point) != 2 or any(
                type(n) is not int or not 0 <= n <= MAX_SECONDS * RATE for n in point
            ):
                raise ValueError("Song time landmarks must contain valid integer frames.")
            x, y = point
            if self.file and (x <= self.file[-1] or y < self.song[-1]):
                raise ValueError("Song time landmarks must advance in performance order.")
            self.file.append(x)
            self.song.append(y)
        if self.file[0] != 0 or self.song[0] != 0 or not self.song[-1]:
            raise ValueError("Song time landmarks must cover the complete upload.")

    def original(self, frame):
        index = bisect_left(self.file, frame)
        if self.file[index] == frame:
            return self.song[index]
        return _lerp(frame, self.file[index-1], self.file[index], self.song[index-1], self.song[index])

    def inverse(self, song_frame):
        """All file frames corresponding to a source position, including both ends of a hold."""
        index = bisect_left(self.song, song_frame)
        if self.song[index] == song_frame:
            return self.file[index], self.file[bisect_right(self.song, song_frame) - 1]
        frame = _lerp(song_frame, self.song[index-1], self.song[index], self.file[index-1], self.file[index])
        return frame, frame


def relative_map(take_to_original, parent_to_original):
    """Compose take→upload with upload→parent, preserving both ends and every tempo breakpoint."""
    if take_to_original is None and parent_to_original is None:
        return None
    if parent_to_original is None:
        take = _Timeline(take_to_original)
        parent = _Timeline([[0, 0], [take.song[-1], take.song[-1]]])
    else:
        parent = _Timeline(parent_to_original)
        take = _Timeline(take_to_original if take_to_original is not None
                         else [[0, 0], [parent.song[-1], parent.song[-1]]])
    if take.song[-1] != parent.song[-1]:
        raise ValueError("Both performances must refer to the same upload timeline.")
    if take.file == parent.file and take.song == parent.song:
        return [[0, 0], [take.file[-1], parent.file[-1]]]

    times = set(take.file)
    for y in set(parent.song):
        left, right = take.inverse(y)
        # A change of tempo may fall between two representable PCM frames.
        times.update((math.floor(left), math.ceil(left), math.floor(right), math.ceil(right)))
        before, after = parent.inverse(y)
        if left == right and before != after:
            # A parent-only interlude has no duration in the child. Step across it
            # within one frame; never stretch the surrounding phrase to cover it.
            times.update((max(0, math.floor(left)-1), min(take.file[-1], math.ceil(left)+1)))
    result = []
    for x in sorted(times):
        y = take.original(x)
        a, b = parent.inverse(y)
        if a != b:
            start, end = take.inverse(y)
            if start != end:
                a = _lerp(x, start, end, a, b)  # Stay in the same fraction of a shared intro or coda.
            elif x == take.file[-1]:
                a = b
        value = _round_frame(a)
        while len(result) >= 2:
            (x0, y0), (x1, y1) = result[-2:]
            if (y1-y0) * (x-x1) != (value-y1) * (x1-x0):
                break
            result.pop()
        result.append([x, value])
    if len(result) > 4096:
        raise ValueError("The relative performance timeline exceeds the supported number of landmarks.")
    return result


def align_original_guide(original: Path, output: Path, parent_map, *, deadline: float) -> bool:
    """Perform the upload's extracted pitches and durations on the selected parent's clock."""
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("Aligning the original melody exceeded its time limit.")
    original, output = Path(original), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Save the aligned melody guide to a new WAV path.")
    timeline = _Timeline(parent_map)
    with wave.open(str(original), "rb") as wav:
        if wav.getframerate() != RATE or wav.getnframes() != timeline.song[-1]:
            raise ValueError("The performance timeline does not match the uploaded recording.")
    holds = sorted({y for y, following in zip(timeline.song, timeline.song[1:]) if y == following})
    with tempfile.TemporaryDirectory(prefix=".align-", dir=output.parent) as directory:
        score = Path(directory) / "notes.json"
        if not transcribe_recording(original, score, deadline=deadline):
            return False
        transcription = json.loads(score.read_text())
        if transcription["frames"] != timeline.song[-1]:
            raise ValueError("The transcribed recording does not match the performance timeline.")
        notes = []
        for start, stop, pitch, confidence in transcription["melody"]:
            if time.monotonic() >= deadline:
                raise TimeoutError("Aligning the original melody exceeded its time limit.")
            start, stop = round(start * RATE), round(stop * RATE)
            if not 0 <= start <= stop <= timeline.song[-1]:
                raise ValueError("The transcribed melody lies outside the uploaded recording.")
            velocity = round(65 + 25 * min(1., max(0., confidence)))
            # A newly introduced interlude carries no original song time. End a
            # crossing note before it and resume the original pitch after it.
            cuts = [start, *holds[bisect_right(holds, start):bisect_left(holds, stop)], stop]
            for begin, end in zip(cuts, cuts[1:]):
                attack = _round_frame(timeline.inverse(begin)[1])
                release = _round_frame(timeline.inverse(end)[0])
                if release > attack:
                    notes.append((attack / RATE, release / RATE, pitch, velocity))
                if len(notes) > MAX_NOTES:
                    raise ValueError("The aligned melody has too many notes for this performance.")
        if not notes:
            return False
        render_score([{"program": 0, "level_db": -20., "notes": notes}],
                     timeline.file[-1] / RATE, output, deadline=deadline, room=0.)
    return True
