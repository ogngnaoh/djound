"""Separate actual octave changes from a switch to another harmonic of a sustained player."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from rendition_sustains import keep_supported_sustains


def phrase(path, mode, transpose):
    rate = 44100
    audio = np.zeros(round(3.9 * rate))
    theme = [.32, .19, .39, .95, .24, .18, .05, .21, .13, .09, .04, .02]
    other = [1., .36, .14, .06, .04, .02]
    def tone(start, end, key, timbre, gain):
        a, b = round(start * rate), round(end * rate)
        t = np.arange(b - a) / rate
        phase = 2 * np.pi * 440 * 2 ** ((key + transpose - 69) / 12) * t
        envelope = np.minimum(1, np.minimum(t, end - start - t) / .025)
        audio[a:b] += gain * envelope * sum(v * np.sin(h * phase) for h, v in enumerate(timbre, 1))
    before = [(0., .35, 64), (.38, .76, 66)]
    after = [(2.6, 3., 65), (3.05, 3.55, 64)]
    for a, b, k in before + after:
        tone(a, b, k, theme, .18)
    lo, hi = 60, 72
    if mode == "low holds":
        tone(.8, 2.55, lo, theme, .18)
        tone(.8, 2.55, hi, other, .035)
    elif mode == "high holds":
        tone(.8, 2.55, hi, theme, .18)
        tone(.8, 2.55, lo, other, .035)
    else:
        tone(.8, 1.45, lo, theme, .18)
        tone(.8, 2.55, lo, other, .045)
        tone(.8, 2.55, hi, other, .035)
        tone(1.45, 2.55, hi, theme, .18)
    sf.write(path, audio, rate)
    notes = [(a, b, k + transpose, .8) for a, b, k in before + [(.8, 1.45, lo), (1.45, 2.55, hi)] + after]
    # Keep the same detector proposals for all three performances, including an
    # independently sounding lower octave under the real leap.
    events = notes + [(.8, 2.55, lo + transpose, .7), (.8, 2.55, hi + transpose, .7)]
    return events, notes


class SustainedPitchTest(unittest.TestCase):
    def test_both_registers_and_a_real_octave_leap_with_the_same_proposals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "phrase.wav"
            for transpose in (0, 4):
                for mode, pitch in (("low holds", 60), ("high holds", 72), ("real leap", None)):
                    with self.subTest(transpose=transpose, mode=mode):
                        events, notes = phrase(path, mode, transpose)
                        parts = keep_supported_sustains(path, events, notes)
                        if pitch is None:
                            self.assertEqual(notes, parts)
                        else:
                            self.assertEqual(notes[:2] + [(.8, 2.55, pitch + transpose, .8)] + notes[4:], parts)

    def test_an_octave_without_a_continuing_candidate_costs_no_audio_reads(self):
        notes = [(0., .4, 65, .9), (.5, 1.2, 60, .9), (1.2, 2., 72, .9), (2., 2.5, 62, .9)]
        with patch("rendition_sustains.sf.SoundFile", side_effect=AssertionError("no contested sustain")):
            self.assertIs(keep_supported_sustains("unopened.wav", notes, notes), notes)

    def test_older_mix_traces_keep_the_original_octave_choices(self):
        from rendition_melody import _recorded_line
        notes = [(0., .4, 65, .9)]
        revised = [(0., .4, 77, .9)]
        with (patch("rendition_foreground.foreground_notes", return_value=notes),
              patch("rendition_octaves.recover_octave_excursions", return_value=notes),
              patch("rendition_sustains.keep_supported_sustains", return_value=revised) as check):
            self.assertEqual((notes, "mix"), _recorded_line(None, None, notes, .4, 8, "mix", None))
            check.assert_not_called()
            self.assertEqual((revised, "mix"), _recorded_line(None, None, notes, .4, 9, "mix", None))
