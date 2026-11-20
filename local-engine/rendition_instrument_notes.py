"""Decode YourMT3+ segment tokens into instrument notes on the recording's clock.

The protocol follows the authors' event2note and multi-channel decoder:
https://huggingface.co/spaces/mimbres/YourMT3/blob/main/amt/src/utils/event2note.py
https://huggingface.co/spaces/mimbres/YourMT3/blob/main/amt/src/model/ymt3.py
"""
from collections import defaultdict
from math import isfinite
from numbers import Integral


MIN_NOTE = .01
DRUMS = 128


def _events(tokens, table):
    for token in tokens:
        if isinstance(token, bool) or not isinstance(token, Integral) or not 0 <= token < len(table):
            raise ValueError(f"Invalid instrument token ID {token!r}.")
        yield table[token]


def _segment(tokens, table, start, ticks_per_second):
    events = _events(tokens, table)
    program = None
    ties = []
    # The prefix inventories keys already held in this decoder channel.
    for kind, value in events:
        if kind == "tie":
            break
        if kind == "shift":
            return [], [], []
        if kind == "program":
            program = value
        elif kind == "pitch" and program is not None:
            ties.append((program, value))
    else:
        return [], [], []

    sounding = ties.copy()
    changes = []
    origin = round(start * ticks_per_second)
    tick = origin
    velocity = None
    for kind, value in events:
        if kind == "EOS":
            break
        if kind == "shift" and 0 < value <= 1000:
            tick = origin + value  # Each shift is absolute within its segment.
        elif kind == "program":
            program = value
        elif kind == "velocity":
            velocity = value
        elif kind == "drum":
            changes.append((tick / ticks_per_second, DRUMS, value, 1))
        elif kind == "pitch" and program is not None and velocity is not None:
            key = (program, value)
            if velocity == 1:
                sounding.append(key)
            elif velocity == 0 and key in sounding:
                sounding.remove(key)
            else:
                continue
            changes.append((tick / ticks_per_second, program, value, velocity))
    return ties, sounding, changes


def _trim_overlaps(notes):
    by_key = defaultdict(list)
    for start, end, program, pitch in notes:
        by_key[program, pitch].append((start, end))
    for (program, pitch), spans in by_key.items():
        spans.sort()
        for i, (start, end) in enumerate(spans):
            if i + 1 < len(spans):
                end = min(end, spans[i + 1][0])
            if start < end:
                yield start, end, program, pitch


def _channel_notes(changes):
    active = {}
    notes = []
    # A release and a reattack of the same key can share a timestamp.
    changes.sort(key=lambda event: (event[0], event[1] == DRUMS, event[2], event[3], event[1]))
    for time, program, pitch, velocity in changes:
        if program == DRUMS:
            notes.append((time, time + MIN_NOTE, program, pitch))
            continue
        onset = active.pop((program, pitch), None)
        if onset is not None:
            end = time if MIN_NOTE <= time - onset <= 10 else onset + MIN_NOTE
            notes.append((onset, end, program, pitch))
        if velocity:
            active[program, pitch] = time
    for (program, pitch), onset in active.items():
        notes.append((onset, onset + MIN_NOTE, program, pitch))
    return _trim_overlaps(notes)


def decode_segments(rows, vocabulary, duration):
    """Return raw GM -> [(start seconds, end seconds, MIDI pitch, confidence)].

    The export emits binary attacks without a calibrated confidence. Use 1.0
    for each decoded note. Program 128 denotes percussion.
    """
    channels = vocabulary["channels"]
    segment_samples = vocabulary["segment"]
    sample_rate = vocabulary["sample_rate"]
    ticks_per_second = vocabulary["tps"]
    if min(channels, segment_samples, sample_rate, ticks_per_second) <= 0:
        raise ValueError("Invalid instrument vocabulary timing or channel count.")
    if not isfinite(duration) or duration < 0:
        raise ValueError("Instrument audio duration must be finite and nonnegative.")
    changes = [[] for _ in range(channels)]
    previous = [[] for _ in range(channels)]
    for i, row in enumerate(rows):
        if len(row) != channels:
            raise ValueError(f"Expected {channels} instrument channels in segment {i}.")
        start = i * segment_samples / sample_rate
        for channel, tokens in enumerate(row):
            ties, sounding, local = _segment(tokens, vocabulary["events"], start, ticks_per_second)
            # A missing tie closes a key at the exact sample boundary.
            for program, pitch in previous[channel]:
                if (program, pitch) not in ties:
                    changes[channel].append((start, program, pitch, 0))
            changes[channel].extend(local)
            previous[channel] = sounding

    merged = (note for channel in changes for note in _channel_notes(channel))
    programs = defaultdict(list)
    for start, end, program, pitch in _trim_overlaps(merged):
        if program != DRUMS and end - start < MIN_NOTE:
            end = start + MIN_NOTE
        end = min(end, duration)
        if start < end:
            programs[program].append((start, end, pitch, 1.0))
    for notes in programs.values():
        notes.sort(key=lambda note: (note[0], note[2]))
    return dict(sorted(programs.items()))
