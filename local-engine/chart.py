"""Reviewable major/minor triad proposals for a known 4/4 grid and confirmed MIDI notes."""

import math

from common import EngineError, notes as confirmed_notes

NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
CHORDS = {
    name + suffix: (root, (root + third) % 12, (root + 7) % 12)
    for root, name in enumerate(NAMES)
    for suffix, third in (("", 4), ("m", 3))
}


def frame(seconds):
    return math.floor(seconds * 44100 + 0.5)


def grid(duration, bpm, downbeat_ms):
    if type(bpm) is not int or not 40 <= bpm <= 200:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose a chord tempo between 40 and 200 BPM.')
    if duration is not None and (type(duration) not in (int, float) or not 0 < duration <= 12):
        raise EngineError('LOCAL_AUDIO_LIMIT', 'Choose a clip up to 12 seconds for the chord chart.')
    limit = 12 if duration is None else duration
    if type(downbeat_ms) is not int or not 0 <= downbeat_ms <= limit * 1000 - 10 + 1e-7:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Place the first chord beat inside the clip, with at least 10 ms remaining.')
    bars = []
    bar_seconds = 240 / bpm
    for index in range(10):
        start = downbeat_ms / 1000 + index * bar_seconds
        if frame(start) >= frame(limit):
            break
        end = min(limit, downbeat_ms / 1000 + (index + 1) * bar_seconds)
        bars.append((start, end))
    return bars


def validate_chart(chart, duration=None):
    if not isinstance(chart, dict) or set(chart) != {'bpm', 'downbeatMs', 'chords'}:
        raise EngineError('LOCAL_INVALID_RECIPE', 'A chord chart needs a tempo, first beat and a chord or rest for every bar.')
    bounds = grid(duration, chart['bpm'], chart['downbeatMs'])
    labels = chart['chords']
    if not isinstance(labels, list) or not 1 <= len(labels) <= 10:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Provide between one and ten chord bars.')
    if duration is not None and len(labels) != len(bounds):
        raise EngineError('LOCAL_INVALID_RECIPE', f'Provide exactly {len(bounds)} chord bars for this clip and grid.')
    if any(label is not None and (type(label) is not str or label not in CHORDS) for label in labels):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose a supported major or minor triad, or leave the bar at rest.')
    return {'bpm': chart['bpm'], 'downbeatMs': chart['downbeatMs'], 'chords': list(labels)}


def bars_of_evidence(notes, bounds, beat):
    """Attribute each note's sounding duration to every bar it intersects."""
    rows = []
    for start, end in bounds:
        weights = [0.0] * 12
        for n in notes:
            overlap = min(end, n["end"]) - max(start, n["start"])
            if overlap <= 0:
                continue
            # Beat-one and beat-three attacks provide restrained additional evidence.
            # A tie into a new measure contributes its actual duration there.
            offset = (n["start"] - start) / beat
            strong_attack = any(abs(offset - pulse) < 0.10 for pulse in (0, 2))
            weights[n["pitch"] % 12] += overlap * (1.20 if strong_attack else 1.0)
        rows.append((start, end, weights))
    return rows


def rank_bar(weights, context, previous, first_pc):
    total = sum(weights)
    scored = []
    for chord, pcs in CHORDS.items():
        coverage = sum(weights[pc] for pc in pcs) / total
        # Local sounding evidence dominates. Context and common tones only
        # separate near ties; an explicit edit never re-scores adjacent bars.
        phrase_fit = sum(context[pc] for pc in pcs) / sum(context)
        continuity = len(set(pcs).intersection(CHORDS.get(previous, ()))) / 3
        score = coverage + 0.04 * phrase_fit + 0.025 * continuity
        scored.append((chord, score, coverage))
    scored.sort(key=lambda row: (-round(row[1], 12), (CHORDS[row[0]][0] - first_pc) % 12, row[0].endswith("m")))
    return scored


def propose(notes, duration, bpm, downbeat_ms):
    """Label bars after the downbeat, leaving pickup and silent bars unaccompanied.

    Alternatives are chord names in descending order, excluding the primary
    suggestion. Ambiguity describes evidence from the guide, even after editing.
    """
    bounds = grid(duration, bpm, downbeat_ms)
    downbeat = downbeat_ms / 1000
    notes = confirmed_notes(notes, duration)
    notes = sorted(notes, key=lambda n: (n["start"], n["pitch"], n["end"]))
    rows = bars_of_evidence(notes, bounds, 60 / bpm)
    context = [sum(row[2][pc] for row in rows) for pc in range(12)]
    first_pc = next((n["pitch"] % 12 for n in notes if n["end"] > downbeat), 0)
    bars = []
    previous = None
    for start, end, weights in rows:
        bar = dict(start=start, end=end, chord=None, alternatives=[], ambiguous=False, origin="empty")
        if sum(weights):
            ranked = rank_bar(weights, context, previous, first_pc)
            chord, score, coverage = ranked[0]
            supported_classes = sum(w >= 0.05 * sum(weights) for w in weights)
            bar.update(chord=chord, origin="suggested", alternatives=[r[0] for r in ranked[1:4]],
                       ambiguous=supported_classes < 3 or score - ranked[1][1] < 0.10 or coverage < 0.70)
        previous = bar["chord"]
        bars.append(bar)
    return {'bpm': bpm, 'downbeatMs': downbeat_ms, 'chords': [bar['chord'] for bar in bars]}, bars


def edit_bar(chart, index, label, duration):
    if duration is None:
        raise EngineError('LOCAL_INVALID_RECIPE', 'A reviewed chord edit needs the confirmed clip duration.')
    result = validate_chart(chart, duration)
    if type(index) is not int or not 0 <= index < len(result['chords']):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose an existing chord bar.')
    result['chords'][index] = label
    return validate_chart(result, duration)


def backing(chart, duration, bass_enabled, sparse, soft):
    """One attack per bar. Sparse halves each hold and soft lowers note velocity."""
    bounds = grid(duration, chart['bpm'], chart['downbeatMs'])
    chords, bass = [], []
    for (start, end), label in zip(bounds, chart['chords'], strict=True):
        if label is None:
            continue
        # Keep prototype timestamps, except when legacy banker's rounding
        # would put a half-frame boundary on the opposite sample.
        if round(start * 44100) != frame(start):
            start = frame(start) / 44100
        length = end - start
        def release(fraction):
            released = end - fraction * length
            if sparse:
                released = start + (released - start) * 0.5
            return min(end, max(start + 1 / 44100, released))
        root, third, fifth = CHORDS[label]
        for pc in sorted((root, third, fifth), key=lambda p: (p - 55) % 12):
            chords.append(dict(start=start, end=release(.06), pitch=55 + (pc - 55) % 12,
                               velocity=34 if soft else 51))
        if bass_enabled:
            bass.append(dict(start=start, end=release(.16), pitch=36 + root, velocity=38 if soft else 56))
    return [dict(id=name, bank=0, program=program, notes=events)
            for name, program, events in (('chords', 4, chords), ('bass', 32, bass)) if events]
