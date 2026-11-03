#!/usr/bin/env python3
"""Reproduce Djound's original sample recordings; never transforms user audio."""
import hashlib
import json
import math
from pathlib import Path
import struct
import wave

ROOT = Path(__file__).resolve().parent
RATE = 44100
DURATION = 12

# Original short note sequences written for this repository. No source recording,
# model, external service, soundfont, or third-party composition is used.
SCORES = [
    ('source-study', 'Evening study', 88, [60, 64, 67, 71, 69, 67, 64, 62], [48, 45, 53, 55], 0),
    ('sample-warm', 'Warm sample', 88, [60, 67, 71, 74, 72, 69, 67, 64], [48, 45, 53, 55], 1),
    ('sample-pulse', 'Pulse sample', 112, [60, 64, 67, 76, 74, 71, 67, 64], [48, 48, 53, 55], 2),
]


def add_note(samples, start, duration, midi, amplitude, voice):
    frequency = 440 * 2 ** ((midi - 69) / 12)
    begin = int(start * RATE)
    length = min(int(duration * RATE), len(samples) - begin)
    for offset in range(max(0, length)):
        t = offset / RATE
        attack = min(1, t / .012)
        release = min(1, max(0, duration - t) / .09)
        envelope = attack * release * math.exp(-t * (2.2 if voice != 'bass' else 1.3))
        phase = 2 * math.pi * frequency * t
        tone = math.sin(phase)
        if voice == 'keys':
            tone += .28 * math.sin(phase * 2) + .12 * math.sin(phase * 3)
        elif voice == 'bell':
            tone += .18 * math.sin(phase * 3)
        samples[begin + offset] += amplitude * envelope * tone


def build(name, title, bpm, melody, roots, variation):
    samples = [0.] * (RATE * DURATION)
    beat = 60 / bpm
    for i in range(int((DURATION - .7) / beat)):
        start = i * beat
        note = melody[i % len(melody)]
        add_note(samples, start, beat * 1.5, note, .15, 'bell' if variation == 1 else 'keys')
        if i % 2 == 0:
            root = roots[(i // 4) % len(roots)]
            add_note(samples, start, beat * 2, root, .18, 'bass')
            for chord_note in [root + 12, root + 19, root + 22]:
                add_note(samples, start + .03, beat * 2.5, chord_note, .036, 'keys')
        if variation == 2:
            add_note(samples, start, .16, 30, .14, 'bass')
            add_note(samples, start + beat / 2, .06, 104, .025, 'bell')
    peak = max(abs(v) for v in samples)
    data = bytearray()
    for index, sample in enumerate(samples):
        fade = min(1, (len(samples) - index) / RATE)
        data.extend(struct.pack('<h', round(sample / max(1, peak / .78) * fade * 32767)))
    path = ROOT / 'audio' / f'{name}.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(data)
    return dict(file=f'audio/{name}.wav', title=title, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                durationMs=DURATION * 1000, sampleRate=RATE, channels=1, codec='pcm_s16le',
                byteSize=path.stat().st_size, provenance='ORIGINAL_SYNTHETIC_FIXTURE',
                license='CC0-1.0', attribution='Original composition and synthesis authored for Djound.',
                role='upload-example' if variation == 0 else 'predefined-output')


if __name__ == '__main__':
    (ROOT / 'audio').mkdir(exist_ok=True)
    manifest = dict(schemaVersion=1,
                    notice='Fixture demo — sample audio, no music generation. Outputs are predefined and unrelated to uploaded files or submitted directions.',
                    recordings=[build(*score) for score in SCORES])
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
