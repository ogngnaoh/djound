"""Protect a solo's sounding register when its loud overtones look like another player."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import rendition_melody as melody
from rendition_harmonic_solo import harmonic_solo, RATE


class HarmonicSoloTest(unittest.TestCase):
    def test_actual_fundamental_across_leaps_rearticulation_and_transposition(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "reed.wav"
            for transpose in (0, 7):
                phrase = [47, 62, 62, 50, 61, 53, 65, 60]
                audio = np.zeros(round(2.2 * RATE))
                events = []
                for i, key in enumerate(phrase):
                    key += transpose
                    start = .08 + i * .25
                    frames = round(.19 * RATE)
                    t = np.arange(frames) / RATE
                    phase = 2 * np.pi * 440 * 2**((key - 69) / 12) * t
                    note = .12 * (np.sin(phase) + 3 * np.sin(2 * phase) + .8 * np.sin(3 * phase))
                    envelope = np.minimum(1., np.minimum(t, .19 - t) / .015)
                    begin = round(start * RATE)
                    audio[begin:begin + frames] += note * envelope
                    # The note model writes a louder overtone over every fundamental.
                    events.extend([(start, start + .19, key, .65), (start, start + .19, key + 12, .8)])
                sf.write(source, audio, RATE)
                line = harmonic_solo(source, events, 2.2)
                self.assertEqual([key + transpose for key in phrase], [n[2] for n in line])
                np.testing.assert_allclose([n[0] for n in line], [.08 + .25*i for i in range(8)], atol=.04)
                self.assertTrue(all(left[1] <= right[0] for left, right in zip(line, line[1:])))

                # The same model proposals could be octave doubling on paper. If the actual
                # waveform instead plays unrelated pitches, preserve the existing selector.
                wrong = [(a, b, k + 3, q) for a, b, k, q in events]
                self.assertIsNone(harmonic_solo(source, wrong, 2.2))

    def test_polyphonic_chords_and_a_simple_unambiguous_line_need_no_extra_audio_read(self):
        with patch("rendition_harmonic_solo.sf.SoundFile", side_effect=AssertionError("unnecessary FFT")):
            for keys in ((60,), (60, 64, 67)):
                notes = [(0., 1., key, .8) for key in keys]
                self.assertIsNone(harmonic_solo("absent.wav", notes, 1.))

    def test_octave_part_with_independent_attacks_and_a_late_second_voice_abstain(self):
        frames = 1200
        events = [(0., 12., 52, .8)]
        events.extend((a, a + .6, 64, .7) for a in (1., 3., 5., 7., 9., 11.))
        keys = np.full(frames, 52)
        with patch("rendition_harmonic_solo._sounding_keys",
                   return_value=(keys, np.ones(frames), np.full(frames, .8))):
            self.assertIsNone(harmonic_solo("octave-duet.wav", events, 12.))
            # Across the full recording the source and model agree over 90%.
            # A brief incompatible entry still requires the ensemble selector.
            keys[-80:] = 55
            self.assertIsNone(harmonic_solo("late-entry.wav", [(0., 12., 52, .8), (0., 12., 64, .8)], 12.))

    def test_versioned_auto_selection_and_replay_leave_older_singer_takes_intact(self):
        notes = [(0., .3, 62, .9)]
        with (patch.object(melody, "_fundamental", return_value=([], [], [])) as pitch,
              patch.object(melody, "_single_voice", return_value=False),
              patch.object(melody, "_singer_in_mix", return_value=notes) as singer,
              patch.object(melody, "_with_instrumental_phrases", return_value=notes),
              patch("rendition_harmonic_solo.harmonic_solo", return_value=notes) as solo):
            self.assertEqual((notes, "separated_voice"),
                             melody._recorded_line(None, None, notes, 1., 7, "separated_voice", None))
            solo.assert_not_called()
            self.assertEqual((notes, "harmonic_solo"), melody._recorded_line(None, None, notes, 1., 8, None, None))
            self.assertEqual((notes, "harmonic_solo"),
                             melody._recorded_line(None, None, notes, 1., 8, "harmonic_solo", None))
            self.assertEqual(pitch.call_count, 1)
            self.assertEqual(singer.call_count, 1)
            solo.return_value = None
            with self.assertRaisesRegex(ValueError, "saved source"):
                melody._recorded_line(None, None, notes, 1., 8, "harmonic_solo", None)


if __name__ == "__main__":
    unittest.main()
