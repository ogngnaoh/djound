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
    def test_saved_single_line_with_no_longer_recoverable_voicing_returns_no_notes(self):
        import numpy as np

        with patch.object(melody, "_fundamental", return_value=(
                np.array([0., .02]), np.array([np.nan, np.nan]), np.array([0., 0.]))):
            self.assertEqual(([], "single"), melody._recorded_line(None, None, [], .04, 6, "single", None))

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

    def test_saved_mix_and_historical_traces_do_not_acquire_a_new_singer(self):
        events = [(0, .4, 67, .7), (.5, .9, 69, .8), (1, 1.4, 71, .7)]
        with (patch.object(melody, "_singer_in_mix", side_effect=AssertionError("Wrong analysis source")),
              patch.object(melody, "_fundamental", return_value=([], [], [])) as f0,
              patch.object(melody, "_single_voice", return_value=False)):
            for version, source in ((1, None), (2, None), (3, "mix")):
                found, kind = melody._recorded_line(self.source, self.root, events, 1.5, version, source, 25)
                self.assertEqual(("mix", [67, 69, 71]), (kind, [n[2] for n in found]))
            self.assertEqual(2, f0.call_count)

    def test_a_missing_or_damaged_optional_separator_cannot_change_a_pinned_vocal_take(self):
        from rendition_sketch import SeparatorUnavailable

        events = [(0, .4, 67, .7), (.5, .9, 69, .8), (1, 1.4, 71, .7)]
        with (patch.object(melody, "_fundamental", return_value=([], [], [])),
              patch.object(melody, "_single_voice", return_value=False),
              patch("rendition_sketch.vocal_reference", side_effect=SeparatorUnavailable("Unverified weights"))):
            for installed in (False, True):
                with self.subTest(installed=installed), patch("rendition_sketch.backing_available", return_value=installed):
                    _, kind = melody._recorded_line(self.source, self.root, events, 1.5, 3, None, 25)
                    self.assertEqual("mix", kind)
                    with self.assertRaisesRegex(ValueError, "saved vocal melody"):
                        melody._recorded_line(self.source, self.root, events, 1.5, 3, "separated_voice", 25)

    def test_stem_must_be_audible_pitched_and_supported_in_the_recording(self):
        import numpy as np

        times = np.arange(100) / 50
        pitches = np.repeat([60., 63., 65., 67.], 25)
        events = [(a/2, (a+1)/2, int(pitches[a*25]), .8) for a in range(4)]
        with (patch("rendition_sketch.backing_available", return_value=True),
              patch("rendition_sketch.vocal_reference") as separate,
              patch.object(melody, "_fundamental", return_value=(times, pitches, np.ones(100))) as f0,
              patch.object(melody, "_voiced_notes", return_value=events) as notes):
            for share, keys, accepted in ((.01, events, False), (.6, [(0, 2, 80, .7)], False), (.6, events, True)):
                separate.return_value = (times[::5], np.full(20, .1 * share), np.full(20, .1))
                line = melody._singer_in_mix(self.source, self.root, keys, 2, 25)
                self.assertEqual(events if accepted else [], line)
            self.assertEqual(2, f0.call_count)
            self.assertEqual(1, notes.call_count)

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

    def test_polyphonic_trace_keeps_a_rest_over_a_moving_bass(self):
        notes = [(0, .48, 77, .7), (0, .32, 48, .73), (.5, 1.1, 79, .72),
                 (.64, 1.35, 52, .81), (1.22, 1.52, 53, .80), (1.52, 1.98, 77, .7),
                 (2, 2.48, 79, .73), (2.5, 3, 81, .70)]
        for octave in (0, -12):
            take = [(a, b, pitch + octave, c) for a, b, pitch, c in notes]
            line = melody._select_polyphonic(take, 3)
            self.assertEqual([77, 79, 77, 79, 81], [p - octave for _, _, p, _ in line])
            self.assertEqual([0, .5, 1.52, 2, 2.5], [a for a, _, _, _ in line])
            previous = melody._select_polyphonic(take, 3, trace_version=1)
            self.assertEqual([77, 79, 52, 53, 77, 79, 81], [p - octave for _, _, p, _ in previous])

    @unittest.skipUnless(melody.guide_available(), "local melody assets are optional")
    def test_guide_placement_uses_the_selected_patch_and_preserves_absolute_requests(self):
        def render(program, pitches, keep, tag):
            folder = self.root / tag
            folder.mkdir()
            events = [(i * 0.4, i * 0.4 + 0.32, p, 0.68) for i, p in enumerate(pitches)]
            out = folder / "take.wav"
            melody._synthesize(events, out, program, round(0.4 * len(events) * melody.RATE), keep)
            return out.read_bytes()

        for program, pitches, shift in [
            (42, [83, 86, 88, 90], -12),
            (73, [43, 47, 50, 52], 12),
            (42, [57, 61, 64, 67], 0),
            (0, [83, 86, 88, 90], 0),
            (80, [43, 47, 50, 52], 0),
        ]:
            with self.subTest(program=program, pitches=pitches):
                name = f"{program}-{pitches[0]}"
                actual = render(program, pitches, False, name + "auto")
                expected = render(program, [n + shift for n in pitches], True, name + "manual")
                self.assertEqual(actual, expected)
                if shift:
                    self.assertNotEqual(actual, render(program, pitches, True, name + "unchanged"))

    def test_instrument_can_answer_below_the_singer_and_overlap_a_phrase_boundary(self):
        voice = [(0, .45, 70, .92), (.5, 1., 72, .89), (5., 5.4, 70, .91), (6.6, 7., 72, .9)]
        # The pickup starts just before the voice releases; the cadence rings past its return.
        answer = [(.92, 1.57, 54, .84), (1.60, 2.26, 57, .83), (2.32, 2.95, 59, .82),
                  (3.03, 3.65, 55, .88), (3.72, 4.29, 57, .86), (4.36, 5.24, 54, .85)]
        breath = [(5.5 + i * .27, 5.73 + i * .27, p, .9) for i, p in enumerate((67, 69, 72, 70))]
        result = melody._with_instrumental_phrases(voice, answer + breath, 7.)
        added = [note for note in result if note not in voice]
        self.assertEqual([54, 57, 59, 55, 57, 54], [note[2] for note in added])
        self.assertEqual((1., 5.), (added[0][0], added[-1][1]))
        self.assertTrue(all(note in result for note in voice))

    def test_repeating_backing_does_not_become_the_lead_during_a_vocal_rest(self):
        voice = [(0, .45, 70, .9), (.53, .95, 72, .9), (.99, 1.42, 74, .9),
                 (8., 8.41, 70, .9), (8.45, 8.9, 72, .9), (9., 9.51, 74, .9)]
        for pitches in ((36, 43), (50, 54, 57)):
            backing = [(1.8 + .5 * i, 2.22 + .5 * i, pitches[i % len(pitches)], .85) for i in range(12)]
            with self.subTest(pitches=pitches):
                self.assertEqual(voice, melody._with_instrumental_phrases(voice, backing, 10.))

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
