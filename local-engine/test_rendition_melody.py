"""Keep the optional lead extractor honest about silence, timing, and failed jobs."""

import math
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import rendition_melody as melody


class MelodyGuideTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "recording.wav"
        self.output = self.root / "guide.wav"

    def recording(self, notes):
        with wave.open(str(self.source), "wb") as wav:
            wav.setparams((1, 2, melody.RATE, 0, "NONE", "not compressed"))
            for pitch in notes:
                tone = 440 * 2 ** ((pitch - 69) / 12) if pitch else 0
                count = melody.RATE // 3
                wav.writeframes(
                    b"".join(
                        struct.pack(
                            "<h",
                            round(
                                8000
                                * math.sin(2 * math.pi * tone * i / melody.RATE)
                                * min(1, i / 100, (count - i) / 150)
                            ),
                        )
                        for i in range(count)
                    )
                )
                wav.writeframes(bytes(2 * (melody.RATE // 8)))

    def test_no_evidence_keeps_existing_output(self):
        self.recording([None] * 4)
        self.output.write_bytes(b"previous guide")
        with patch.object(melody, "guide_available", return_value=True):
            self.assertFalse(
                melody.build_melody_guide(self.source, self.output, program=0, deadline=time.monotonic() + 20)
            )
        self.assertEqual(b"previous guide", self.output.read_bytes())
        self.assertEqual({self.source, self.output}, set(self.root.iterdir()))

    def test_unitone_and_low_confidence_are_insufficient(self):
        stable = [(0, 0.4, 60, 0.7), (0.5, 0.9, 60, 0.8), (1, 1.4, 60, 0.8)]
        self.assertFalse(melody._reliable(stable, 1.8))
        self.assertFalse(melody._reliable([(a, b, p + i, 0.16) for i, (a, b, p, c) in enumerate(stable)], 1.8))
        self.assertTrue(melody._reliable([(a, b, p + i, c) for i, (a, b, p, c) in enumerate(stable)], 1.8))

    def test_timeout_and_invalid_destination_leave_files_untouched(self):
        self.recording([64, 67, 69])
        self.output.write_bytes(b"prior")
        self.source.with_suffix(".link").symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "separate"):
            melody.build_melody_guide(
                self.source, self.source.with_suffix(".link"), program=0, deadline=time.monotonic() + 30
            )
        with patch.object(melody, "guide_available", return_value=True), self.assertRaises(TimeoutError):
            melody.build_melody_guide(self.source, self.output, program=0, deadline=time.monotonic() + 0.03)
        self.assertEqual(self.output.read_bytes(), b"prior")
        self.assertFalse(any(p.name.startswith(".melody-") for p in self.root.iterdir()))

    @unittest.skipUnless(melody.guide_available(), "local melody assets are optional")
    def test_real_recording_becomes_a_timed_instrumental_line(self):
        import numpy as np

        pitches = [63, 68, 71, 67, 62]
        self.recording(pitches)
        self.assertTrue(
            melody.build_melody_guide(self.source, self.output, program=0, deadline=time.monotonic() + 90)
        )
        with wave.open(str(self.output), "rb") as wav:
            frames = len(pitches) * (melody.RATE // 3 + melody.RATE // 8)
            self.assertEqual(wav.getparams()[:4], (2, 2, melody.RATE, frames))
            audio = np.frombuffer(wav.readframes(frames), "<i2").reshape(-1, 2).mean(axis=1)
        # Check attacks and pitches using the generated recording alone. The estimator is
        # independent of Basic Pitch and the pYIN path used to create the guide.
        step = melody.RATE // 3 + melody.RATE // 8
        seen = []
        for i in range(len(pitches)):
            block = audio[i * step + 3300 : i * step + 10000]
            fft = abs(np.fft.rfft(np.hanning(len(block)) * block, 32768))
            low, high = 160, 700
            a, b = round(low * 32768 / melody.RATE), round(high * 32768 / melody.RATE)
            hz = (a + fft[a:b].argmax()) * melody.RATE / 32768
            seen.append(round(69 + 12 * np.log2(hz / 440)))
        self.assertEqual(pitches, seen)


if __name__ == "__main__":
    unittest.main()
