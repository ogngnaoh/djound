"""Arrange an extracted tune on a new musical timeline and perform the director's parts."""
import math
from statistics import median

import numpy as np

from chart import CHORDS, rank_bar

RATE = 44100


def analyze(notes):
    """Locate phrases using sounding notes, rests and the estimated pulse."""
    melody = notes["melody"]
    duration = notes["frames"] / RATE
    starts = [event[0] for event in melody]
    gaps = np.diff(starts)
    gaps = gaps[(gaps >= .14) & (gaps <= 2.2)]
    subdivisions = np.array([.25, .5, .75, 1., 1.5, 2., 3., 4.])
    def pulse_error(bpm):
        error = np.min(abs(gaps[:, None] * bpm / 60 - subdivisions), axis=1)
        # Quarter and eighth readings can fit the same attacks. A modest prior resolves ties.
        return np.minimum(.2, error).mean() + .05 * abs(math.log2(bpm / 100))
    bpm = min(range(50, 181), key=pulse_error) if len(gaps) else 96
    beat = 60 / bpm
    cuts = [0.]
    for previous, current in zip(melody, melody[1:]):
        elapsed = (current[0] - cuts[-1]) / beat
        rest = (current[0] - previous[1]) / beat
        if elapsed >= 12 or (elapsed >= 4 and rest >= .65):
            if duration - current[0] > 1.5 * beat:
                cuts.append(current[0])
    phrases = []
    for start, end in zip(cuts, [*cuts[1:], duration]):
        pitches = [p for at, _, p, _ in melody if start <= at < end]
        phrases.append({"id": len(phrases), "start": start, "end": end, "notes": len(pitches),
                        "anchorPitches": pitches[:6]})
    return {"durationSeconds": duration, "bpm": bpm, "phrases": phrases}


class Timeline:
    def __init__(self, analysis, plan):
        self.beat = 60 / plan["bpm"]
        self.meter = plan["meter"]
        self.intro = plan["introBars"] * self.meter if plan["roles"] else 0
        source_beat = 60 / analysis["bpm"]
        self.seconds = [0.]
        self.beats = [float(self.intro)]
        for phrase in analysis["phrases"]:
            intervals = (phrase["end"] - phrase["start"]) / source_beat
            # Round phrase boundaries to half-beats. Pickups and unequal phrase lengths survive.
            self.beats.append(self.beats[-1] + max(.5, round(2 * intervals) / 2))
            self.seconds.append(phrase["end"])
        self.release = .45
        self.last = self.beats[-1] + (plan["endingBars"] * self.meter if plan["roles"] else 0)
        self.duration = self.last * self.beat + self.release
        if self.duration > 600:
            raise ValueError("This arrangement would exceed ten minutes. Try a faster or shorter interpretation.")

    def source_beats(self, seconds):
        return float(np.interp(seconds, self.seconds, self.beats))

    def source_seconds(self, beat):
        return float(np.interp(beat, self.beats, self.seconds))

    def frames(self):
        anchors = [[0, 0]]
        for beat, seconds in zip(self.beats, self.seconds):
            point = [round(beat * self.beat * RATE), round(seconds * RATE)]
            if point[0] > anchors[-1][0]:
                anchors.append(point)
        anchors.append([round(self.duration * RATE), anchors[-1][1]])
        return anchors


def _subdivide(position, feel):
    whole = math.floor(position)
    fraction = position - whole
    middle = {"swing": 2 / 3, "triplet": .5, "straight": .5}[feel]
    if fraction <= .5:
        return whole + fraction * 2 * middle
    return whole + middle + (fraction - .5) * 2 * (1 - middle)


def _chord_palette(notes, timeline, plan):
    # Estimate harmony from all sounding pitches, including lower voices excluded from the tune.
    events = np.asarray(notes["candidates"] or notes["melody"], dtype=float)
    pitches = events[:, 2].astype(int)
    weights = events[:, 3] * (1 + .65 * (pitches < np.median(pitches)))
    context = np.bincount(pitches % 12, (events[:, 1] - events[:, 0]) * weights, minlength=12)
    stride = timeline.meter / 2
    previous = None
    palette = []
    for half in range(max(1, math.ceil(timeline.last / stride))):
        position = min(max(half * stride, timeline.intro), max(timeline.intro, timeline.beats[-1] - stride))
        lo, hi = timeline.source_seconds(position), timeline.source_seconds(position + stride)
        overlap = np.maximum(0, np.minimum(hi, events[:, 1]) - np.maximum(lo, events[:, 0]))
        local = np.bincount(pitches % 12, weights * overlap, minlength=12)
        if local.sum():
            nearest = int(pitches[max(0, np.searchsorted(events[:, 0], lo) - 1)] % 12)
            previous = rank_bar(local, context, previous, nearest)[0][0]
        root, third, _ = CHORDS[previous or max(CHORDS, key=lambda label: context[CHORDS[label][0]])]
        seventh = 10 if context[(root + 10) % 12] >= context[(root + 11) % 12] else 11
        tones = [0, (third - root) % 12, 7]
        if plan["harmony"] == "open_fifths":
            tones = [0, 7, 12]
        elif plan["harmony"] in ("sevenths", "extensions"):
            tones.append(seventh)
            if plan["harmony"] == "extensions":
                tones.append(14)
        palette.append((root, tones))
    return palette


def _level(plan, position, limit):
    phase = min(1., position / max(limit, 1.))
    if plan["phraseDynamics"] == "arch":
        return 0.88 + .16 * math.sin(math.pi * phase)
    if plan["phraseDynamics"] == "contrast":
        return 1 if int(position // (plan["meter"] * 2)) % 2 == 0 else .83
    return 1.


def _lead_track(notes, timeline, plan):
    melody = notes["melody"]
    # For a low lead, transpose whole octaves to bring the theme into the instrument's range.
    import pretty_midi
    natural = pretty_midi.INSTRUMENT_MAP[plan["leadProgram"]]
    center = {"Cello": 55, "Contrabass": 43, "Acoustic Bass": 44, "Tuba": 46, "Bassoon": 52,
              "Piccolo": 84, "Viola": 64, "French Horn": 58, "Trombone": 53}.get(natural)
    center_pitch = median(note[2] for note in melody)
    shift = 12 * round((center - center_pitch) / 12) if center is not None else 0
    shift = max(-24, min(24, shift))
    track = {"program": plan["leadProgram"], "level_db": -19., "notes": []}
    positions = []
    for start, end, pitch, confidence in melody:
        beat = timeline.source_beats(start)
        # The arranger's interpretation keeps small rubato and deliberately shifts weak subdivisions.
        quantum = 1 / 3 if plan["subdivision"] == "triplet" else .25
        snapped = round(beat / quantum) * quantum
        pos = _subdivide(snapped, plan["subdivision"]) + .16 * (beat - snapped)
        phrase = plan["phrasing"]
        if phrase == "laid_back":
            pos += .055
        elif phrase == "syncopated" and (round(snapped * 2) % 4 == 2):
            pos -= .12
        index = int(np.searchsorted(timeline.seconds, start, side="right") - 1)
        if index in plan["themeAnchors"] and start - timeline.seconds[index] < .12:
            pos = timeline.beats[index]
        pos = max(positions[-1][0] + .025 if positions else 0., pos)
        span = max(.1, (timeline.source_beats(end) - beat)
                   * {"articulated": .70, "laid_back": .92, "flowing": 1.06, "syncopated": .84}[phrase])
        velocity = round((64 + 25 * confidence + 5 * math.cos(math.pi * pos)) * _level(plan, pos, timeline.last))
        positions.append((pos, span, max(0, min(127, pitch + shift)), max(1, min(127, velocity))))
    for i, (pos, span, pitch, velocity) in enumerate(positions):
        next_onset = positions[i + 1][0] if i + 1 < len(positions) else timeline.last
        end = min(next_onset, pos + span, timeline.last)
        if end > pos:
            track["notes"].append((pos * timeline.beat, end * timeline.beat, pitch, velocity))
    return track


def _voicing(root, tones, last):
    pcs = [(root + interval) % 12 for interval in tones]
    # Fit inversions inside a comping register and prefer a short move from the previous chord.
    candidates = []
    for bottom in range(48, 63):
        if bottom % 12 not in pcs:
            continue
        pitches = sorted(bottom + (pc - bottom) % 12 for pc in set(pcs))
        movement = sum(min(abs(p - previous) for previous in last) for p in pitches) if last else 0
        candidates.append((movement + abs(np.mean(pitches) - 60) * .55, pitches))
    return min(candidates)[1]


def arrange(notes, analysis, plan):
    """Return timed voices, total duration, and the mapping from performed phrases to source frames."""
    timeline = Timeline(analysis, plan)
    palette = _chord_palette(notes, timeline, plan)
    lead = _lead_track(notes, timeline, plan)
    tracks = [lead]
    previous = {}
    for idx, role in enumerate(plan["roles"]):
        kind = role["kind"]
        drum = kind == "percussion"
        track = {"program": role.get("kit", 0) if drum else role["program"], "percussion": drum,
                 "pan": (0., -.14, .18, -.22)[idx % 4],
                 "level_db": {"bass": -21., "percussion": -22., "chords": -24., "arpeggio": -23.,
                              "counterline": -24., "pedal": -24.}[kind], "notes": []}
        for bar in range(math.ceil(timeline.last / timeline.meter)):
            for hit in role["pattern"]:
                raw_pos = bar * timeline.meter + hit["beat"]
                if raw_pos >= timeline.last:
                    continue
                pos = _subdivide(raw_pos, plan["subdivision"])
                end = min(timeline.last, pos + hit["duration"])
                if end <= pos:
                    continue
                cell = min(len(palette) - 1, int(raw_pos // (timeline.meter / 2)))
                root, tones = palette[cell]
                # Resolve the final written coda measure together, with a single release.
                cadence = (raw_pos >= timeline.beats[-1] and
                           bar == math.ceil(timeline.last / timeline.meter) - 1)
                if cadence:
                    if hit is not role["pattern"][0]:
                        continue
                    pos = bar * timeline.meter
                    end = min(timeline.last, pos + (1. if drum else .86 * timeline.meter))
                if drum:
                    pitches = [hit["pitch"]]
                elif kind == "chords":
                    pitches = _voicing(root, tones, previous.get(idx))
                    previous[idx] = pitches
                else:
                    degree = hit["degree"]
                    interval = tones[degree % len(tones)] + 12 * (degree // len(tones))
                    base = 36 if kind in ("bass", "pedal") else 55
                    pitch = base + (root - base) % 12 + interval
                    if kind == "counterline":
                        # Let the response occupy space between theme notes.
                        if any(a < end * timeline.beat and b > pos * timeline.beat for a,b,_,_ in lead["notes"]):
                            continue
                    pitches = [pitch]
                velocity = max(1, min(127, round(hit["velocity"] * _level(plan, pos, timeline.last))))
                for voice, pitch in enumerate(pitches):
                    attack = pos * timeline.beat + voice * .005
                    if end * timeline.beat > attack:
                        track["notes"].append((attack, end * timeline.beat, min(127, pitch), velocity))
        if track["notes"]:
            tracks.append(track)
    return tracks, timeline.duration, timeline.frames()
