"""Run with .runtime/local-engine/venv/bin/python tests/sung_melody_acceptance.py."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import wave

import soundfile as sf

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "local-engine"))
from rendition_melody import transcribe_recording

# Vocal boundaries measured on the isolated recording; pitches from the familiar tune in D.
# The four lyric lines are independently timed, including repeated syllables on one pitch.
VOCAL_NOTES = (
    (0.48, 0.98, 50), (1.06, 1.46, 50), (1.58, 2.04, 57), (2.12, 2.66, 57),
    (2.66, 3.02, 59), (3.16, 3.50, 59), (3.74, 4.52, 57),
    (5.06, 5.48, 55), (5.48, 6.04, 55), (6.04, 6.60, 54), (6.60, 7.12, 54),
    (7.12, 7.52, 52), (7.76, 8.22, 52), (8.32, 9.10, 50),
    (9.48, 9.78, 57), (10.02, 10.46, 57), (10.52, 11.00, 55), (11.00, 11.58, 55),
    (11.58, 12.10, 54), (12.28, 12.70, 54), (12.78, 13.58, 52),
    (13.98, 14.44, 57), (14.58, 15.06, 57), (15.12, 15.62, 55), (15.62, 16.16, 55),
    (16.16, 16.54, 54), (16.54, 17.04, 54), (17.36, 18.12, 52),
)


class SungMelodyTest(unittest.TestCase):
    def test_singer_under_upper_guitar(self):
        audio, rate = sf.read(TESTS / "fixtures/controlled-vocal-and-band.flac", dtype="int16", always_2d=True)
        self.assertEqual((rate, audio.shape), (44100, (24 * 44100, 2)))
        pcm = audio.astype("<i2", copy=False).tobytes()
        self.assertEqual(
            hashlib.sha256(pcm).hexdigest(),
            "96c6fa3259eaa4129ad98b35302402c7f79050dab6cfd0a5777ad1cb89bd3ee1",
        )
        with tempfile.TemporaryDirectory() as directory:
            source, trace = Path(directory) / "upload.wav", Path(directory) / "trace.json"
            with wave.open(str(source), "wb") as wav:
                wav.setparams((2, 2, rate, len(audio), "NONE", "not compressed"))
                wav.writeframes(pcm)
            self.assertTrue(transcribe_recording(source, trace, deadline=time.monotonic() + 240))
            result = json.loads(trace.read_text())
        lead = result["melody"]
        # Require a distinct event per syllable. One long held note cannot earn two matches.
        remaining = iter(lead)
        for start, end, key in VOCAL_NOTES:
            found = any(
                abs(pitch - key) <= 1 and min(end, release) - max(start, attack) >= (end - start) / 2
                for attack, release, pitch, _ in remaining
            )
            self.assertTrue(found, f"Missing sung MIDI note {key} at {start:.2f}s ({result['melodySource']})")
        # The backing keeps playing through the singer's breaths.
        rest_time = sounding = 0.
        for (_, end, _), (start, _, _) in zip(VOCAL_NOTES, VOCAL_NOTES[1:]):
            rest_time += start - end
            sounding += sum(max(0., min(start, release) - max(end, attack)) for attack, release, _, _ in lead)
        self.assertLessEqual(sounding / rest_time, .1, "Accompaniment filled the singer's rests")


if __name__ == "__main__":
    unittest.main()
