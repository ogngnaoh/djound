"""Arrange an extracted tune on a new musical timeline and perform the director's parts."""
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from statistics import median

import numpy as np

from chart import CHORDS, rank_bar

RATE = 44100


def role_pan(role, index):
    return role.get("pan", (0., -.14, .18, -.22)[index % 4])


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
    return {"durationSeconds": duration, "bpm": bpm, "phrases": phrases,
            "melodyMedianPitch": median(event[2] for event in melody) if melody else None}


class Timeline:
    def __init__(self, analysis, plan):
        self.beat = 60 / plan["bpm"]
        self.meter = plan["meter"]
        play_form = plan["roles"] or plan.get("keepEmptyBars")
        self.intro = plan["introBars"] * self.meter if play_form else 0
        source_beat = 60 / analysis["bpm"]
        self.seconds = [0.]
        self.beats = [float(self.intro)]
        for phrase in analysis["phrases"]:
            intervals = (phrase["end"] - phrase["start"]) / source_beat
            # Round phrase boundaries to half-beats. Pickups and unequal phrase lengths survive.
            self.beats.append(self.beats[-1] + max(.5, round(2 * intervals) / 2))
            self.seconds.append(phrase["end"])
        self.release = .45
        self.last = self.beats[-1] + (plan["endingBars"] * self.meter if play_form else 0)
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


def lead_octave(plan, median_pitch):
    """Whole-octave displacement from the source melody, including a saved listener choice."""
    if "leadOctaveShift" in plan:
        shift = plan["leadOctaveShift"]
        if type(shift) is not int or not -2 <= shift <= 2:
            raise ValueError("The saved lead register is invalid.")
        return shift
    import pretty_midi
    natural = pretty_midi.INSTRUMENT_MAP[plan["leadProgram"]]
    center = {"Cello": 55, "Contrabass": 43, "Acoustic Bass": 44, "Tuba": 46, "Bassoon": 52,
              "Piccolo": 84, "Viola": 64, "French Horn": 58, "Trombone": 53}.get(natural)
    if center is None:
        return 0
    if median_pitch is None:
        raise ValueError("I need the selected melody's pitches to match this lead register.")
    return max(-2, min(2, round((center - median_pitch) / 12)))


def _lead_track(notes, timeline, plan):
    melody = notes["melody"]
    shift = 12 * lead_octave(plan, median(note[2] for note in melody))
    track = {"program": plan["leadProgram"], "level_db": -19., "notes": []}
    touch = plan.get("leadTouch")
    if touch:
        # Each curve runs from the first played attack to the last. A breath at
        # the end of the source phrase must not hide the written closing dynamic.
        onsets = [n[0] for n in melody]
        edges = [bisect_left(onsets, boundary) for boundary in timeline.seconds[:-1]] + [len(melody)]
        velocities = touch["velocity"]
        # The player balances each part to a target RMS. Carry the director's
        # overall touch into that balance as well as the attacks within the part.
        mean_velocity = (velocities[0] + 2 * velocities[1] + velocities[2]) / 4
        track["level_db"] += 20 * math.log10(mean_velocity / 80)
    positions = []
    expression = []
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
        anchor = index in plan["themeAnchors"] and start - timeline.seconds[index] < .12
        if touch:
            first, last = onsets[edges[index]], onsets[edges[index + 1] - 1]
            progress = (start - first) / (last - first) if last > first else 0.
            offset = 0. if anchor else progress ** 2 * touch["endOffsetMs"] / (1000 * timeline.beat)
            expression.append((index, offset, np.interp(progress, (0., .5, 1.), velocities) / 80))
        if anchor:
            pos = timeline.beats[index]
        pos = max(positions[-1][0] + .025 if positions else 0., pos)
        span = max(.1, (timeline.source_beats(end) - beat)
                   * {"articulated": .70, "laid_back": .92, "flowing": 1.06, "syncopated": .84}[phrase])
        positions.append((pos, span, max(0, min(127, pitch + shift)), confidence))
    if touch:
        timed = []
        for i, (pos, span, pitch, confidence) in enumerate(positions):
            index, offset, _ = expression[i]
            previous = positions[i - 1][0] if i else 0.
            following = positions[i + 1][0] if i + 1 < len(positions) else timeline.last
            # A late attack must leave room to sound, including at the very end.
            # Use nominal neighbors so one shifted note cannot displace the next phrase.
            ahead = max(0., pos - max(previous, timeline.beats[index]))
            behind = max(0., min(following, timeline.beats[index + 1], pos + span) - pos)
            pos += max(-.45 * ahead, min(offset, .45 * behind))
            timed.append((pos, span * touch["lengthPercent"] / 100, pitch, confidence))
        positions = timed
    for i, (pos, span, pitch, confidence) in enumerate(positions):
        next_onset = positions[i + 1][0] if i + 1 < len(positions) else timeline.last
        end = min(next_onset, pos + span, timeline.last)
        if end > pos:
            accent = 64 + 25 * confidence + 5 * math.cos(math.pi * pos)
            velocity = round(accent * (expression[i][2] if touch else 1.) * _level(plan, pos, timeline.last))
            track["notes"].append((pos * timeline.beat, end * timeline.beat, pitch, max(1, min(127, velocity))))
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


@dataclass(frozen=True)
class PhraseBacking:
    sparse: bool
    answers: tuple[float, ...]
    rest_end: float


def _phrase_backing(analysis, timeline, plan, attacks, releases):
    interaction = plan.get("phraseInteraction", "steady")
    if interaction == "steady":
        return []
    if interaction not in ("phrase_answers", "build_returns"):
        raise ValueError("The selected score has an unknown phrase interaction.")
    # Recognize a motif when its opening intervals return, even in another register.
    shapes = []
    for phrase in analysis["phrases"]:
        opening = phrase.get("anchorPitches", phrase.get("openingPitches", []))[:4]
        shape = tuple(b - a for a, b in zip(opening, opening[1:]))
        shapes.append(shape if len(shape) == 3 and any(shape) else None)
    counts = Counter(shapes)
    heard = set()
    sections = []
    for i, shape in enumerate(shapes):
        recurring = shape is not None and counts[shape] > 1
        returning = recurring and shape in heard
        heard.add(shape)
        trade = interaction == "phrase_answers"
        sparse = trade or (recurring and not returning)
        answers, rest_end = (), 0.
        if trade or returning:
            start, end = timeline.beats[i:i + 2]
            # Place a short reply after the last playable breath in this phrase.
            # Leave the next entrance clear, even when the rest spans several bars.
            left = bisect_right(releases, start)
            right = bisect_left(attacks, end)
            short_breath = None
            for j in reversed(range(left, right)):
                release = releases[j]
                next_onset = min(end, attacks[j + 1]) if j + 1 < len(attacks) else end
                space = next_onset - release
                if space < .85:
                    if (plan.get("phraseInteractionVersion", 1) >= 2 and space >= .36
                            and (short_breath is None or space > short_breath[0])):
                        short_breath = (space, release, next_onset)
                    continue
                rest_end = min(next_onset - .08, release + 1.75)
                slots = (_subdivide(n / 2, plan["subdivision"])
                         for n in range(math.floor(release * 2), math.ceil(rest_end * 2)))
                answers = tuple(t for t in slots if release + .10 <= t <= rest_end - .18)[:2]
                if answers:
                    break
            # A singer can take an audible breath shorter than an eighth-note reply.
            # Fit one plucked or articulated note into it, leaving both edges clear.
            if not answers and short_breath:
                _, release, next_onset = short_breath
                rest_end = next_onset - .05
                lo = release + .06
                on_grid = _subdivide(math.ceil(release * 2) / 2, plan["subdivision"])
                attack = on_grid if lo <= on_grid <= rest_end - .16 else lo
                answers = (attack,)
        sections.append(PhraseBacking(sparse, answers, rest_end))
    return sections


def _backing_events(role, timeline, plan, sections, attacks, releases):
    """Place the written figure, leaving the introduction and coda on their own clock."""
    if not role.get("followPhrases", True):
        sections = ()
    events = []
    kind, pattern = role["kind"], role["pattern"]
    bars = math.ceil(timeline.last / timeline.meter)
    for bar in range(bars):
        for idx, hit in enumerate(pattern):
            raw = bar * timeline.meter + hit["beat"]
            pos = _subdivide(raw, plan["subdivision"])
            end = min(timeline.last, pos + hit["duration"])
            if end <= pos:
                continue
            # These strokes have an explicit place in the bar, including the coda.
            if (raw >= timeline.beats[-1] and bar == bars - 1
                    and (kind, role.get("gesture")) not in (("chords", "offbeat"), ("percussion", "written"))):
                if idx:
                    continue
                pos = bar * timeline.meter
                end = min(timeline.last, pos + (1. if kind == "percussion" else .86 * timeline.meter))
            elif sections and timeline.intro <= pos < timeline.beats[-1]:
                section = sections[bisect_right(timeline.beats, pos) - 1]
                if section.answers:
                    if section.answers[0] <= pos < section.rest_end:
                        continue
                    if pos < section.answers[0]:
                        end = min(end, section.answers[0])
                next_note = bisect_right(releases, pos)
                if section.sparse and next_note < len(attacks) and attacks[next_note] < end:
                    # Keep bass and kit marking the half-bar. The other voices
                    # get one shorter attack beneath the lead before its reply.
                    half = kind in ("bass", "percussion") and hit["beat"] == timeline.meter / 2
                    if idx and not half:
                        continue
                    end = min(end, pos + 1.)
            events.append((pos, end, raw, hit))
    for section in sections:
        if not section.answers:
            continue
        slots = section.answers
        if kind in ("bass", "pedal"):
            slots = slots[:1]
        elif kind == "chords":
            slots = slots[-1:]
        for i, pos in enumerate(slots):
            # Answer with pitches and articulations from this player's own figure.
            hit = pattern[(len(pattern) - len(slots) + i) % len(pattern)]
            until = slots[i + 1] if i + 1 < len(slots) else section.rest_end
            end = min(until, pos + hit["duration"])
            events.append((pos, end, pos, hit))
    if sections:
        events.sort(key=lambda event: event[0])
    return events


def arrange(notes, analysis, plan):
    """Return timed voices, total duration, and the mapping from performed phrases to source frames."""
    timeline = Timeline(analysis, plan)
    palette = _chord_palette(notes, timeline, plan)
    lead = _lead_track(notes, timeline, plan)
    if "leadSoundProgram" in plan:
        lead["program"] = plan["leadSoundProgram"]
    if plan.get("leadBalanceDb"):
        lead["gain"] = 10 ** (plan["leadBalanceDb"] / 20)
    if plan.get("leadNoteDynamics"):
        lead["note_dynamics"] = plan["leadNoteDynamics"]
    cue = lead
    if timing := plan.get("backingTiming"):
        cue = _lead_track(notes, timeline, dict(plan, phrasing=timing["phrasing"], leadTouch=timing.get("leadTouch")))
    attacks = [a / timeline.beat for a, _, _, _ in cue["notes"]]
    releases = [b / timeline.beat for _, b, _, _ in cue["notes"]]
    sections = _phrase_backing(analysis, timeline, plan, attacks, releases)
    tracks = [lead]
    previous = {}
    for idx, role in enumerate(plan["roles"]):
        kind = role["kind"]
        drum = kind == "percussion"
        track = {"program": role.get("kit", 0) if drum else role.get("soundProgram", role["program"]), "percussion": drum,
                 "pan": role_pan(role, idx),
                 "level_db": {"bass": -21., "percussion": -22., "chords": -24., "arpeggio": -23.,
                              "counterline": -24., "pedal": -24.}[kind], "notes": []}
        if role.get("balanceDb"):
            track["gain"] = 10 ** (role["balanceDb"] / 20)
        for pos, end, raw_pos, hit in _backing_events(role, timeline, plan, sections, attacks, releases):
            cell = min(len(palette) - 1, int(raw_pos // (timeline.meter / 2)))
            root, tones = palette[cell]
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
                    if any(a < end * timeline.beat and b > pos * timeline.beat for a,b,_,_ in cue["notes"]):
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
