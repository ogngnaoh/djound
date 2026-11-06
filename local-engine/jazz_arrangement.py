"""Deterministic swing-combo comping and walking bass for a reviewed 4/4 chord chart."""
from chart import CHORDS, frame, grid


# Offbeats fall on the final third of the beat. Alternate the comping figure
# across the two-bar phrase, retaining an audible attack at each chord change.
COMP = (
    ((0, .72, 52), (1 + 2/3, .40, 45), (3, .64, 48)),
    ((0, 1.08, 49), (2 + 2/3, .58, 52)),
)
SPARSE_COMP = ((0, 1.2, 49),)


def add(notes, bar_start, bar_end, beat, offset, hold, pitch, velocity):
    start = bar_start + offset * beat
    end = min(bar_end, start + hold * beat)
    if frame(end) <= frame(start):
        return
    # The shared MIDI-to-sampler scheduler uses round-to-even. Pin half-frame
    # disagreements to the reviewed chart's half-up grid without shifting other onsets.
    if frame(start) != round(start * 44100):
        start = frame(start) / 44100
    if round(end * 44100) > round(start * 44100):
        notes.append(dict(start=start, end=end, pitch=pitch, velocity=velocity))


def swing_combo(chart, duration, bass_enabled, sparse, soft):
    """A major-sixth or minor-seventh comp and a bar-local acoustic bass line."""
    beat = 60 / chart['bpm']
    chords, bass = [], []
    bounds = grid(duration, chart['bpm'], chart['downbeatMs'])
    for index, ((start, end), label) in enumerate(zip(bounds, chart['chords'], strict=True)):
        if label is None:
            continue
        root, third, fifth = CHORDS[label]
        minor = label.endswith('m')
        color = 10 if minor else 9
        pcs = (root, third, fifth, (root + color) % 12)
        voicing = sorted(55 + (pc - 55) % 12 for pc in pcs)
        rhythm = SPARSE_COMP if sparse else COMP[index % 2]
        for offset, hold, velocity in rhythm:
            for pitch in voicing:
                add(chords, start, end, beat, offset, hold, pitch, velocity - (14 if soft else 0))
        if bass_enabled:
            # Root–third–fifth–sixth/seventh in quarters. A two-feel uses only
            # root and fifth. No look-ahead: a bar edit leaves its neighbors intact.
            low = 31 + (root - 31) % 12
            intervals = (0, (third - root) % 12, 7, color)
            steps = (0, 2) if sparse else range(4)
            for step in steps:
                velocity = (63, 52, 58, 50)[step] - (16 if soft else 0)
                add(bass, start, end, beat, step, 1.52 if sparse else .86, low + intervals[step], velocity)
    return [dict(id=name, bank=0, program=program, notes=events)
            for name, program, events in (('chords', 0, chords), ('bass', 32, bass)) if events]
