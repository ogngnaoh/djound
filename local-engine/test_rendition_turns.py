"""A melodic turn should follow the foreground player's timbre and two distinct attacks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from rendition_turns import recover_supported_turns


def performance(path, mode, transpose, step):
    rate = 44100
    audio = np.zeros(round(2.6 * rate))
    lead = [.38, .26, .67, 1., .33, .68, .08, .18, .24, .09, .10, .05]
    other = [1., .32, .12, .05, .02, .01]
    def play(a, b, pitch, color, gain):
        t = np.arange(round(b * rate) - round(a * rate)) / rate
        phase = 2 * np.pi * 440 * 2 ** ((pitch + transpose - 69) / 12) * t
        envelope = np.minimum(1, np.minimum(t, b - a - t) / .015)
        audio[round(a * rate):round(b * rate)] += gain * envelope * sum(
            v * np.sin(i * phase) for i, v in enumerate(color, 1))

    start, split, end = .8, .97, 1.14
    first, second, held = 67, 67 + step, 60
    before = [(0., .38, 65), (.41, .77, 64)]
    after = [(1.17, 1.60, first), (1.65, 2.15, 66)]
    for a, b, pitch in before + after:
        play(a, b, pitch, lead, .18)
    if mode == "lead turns":
        play(start, split, first, lead, .18)
        play(split, end, second, lead, .18)
        play(start, end, held, other, .10)
    elif mode == "accompaniment turns":
        play(start, end, held, lead, .18)
        play(start, split, first, other, .12)
        play(split, end, second, other, .12)
    else:
        play(start, end, first, lead, .14)
        play(start, end, second, lead, .14)
        play(start, end, held, other, .10)
    sf.write(path, audio, rate)
    notes = [(a, b, p + transpose, .8) for a, b, p in before + [(start, end, held)] + after]
    candidates = [(start, split, first + transpose, .7), (split, end, second + transpose, .65)]
    return notes, candidates


class RecordedTurnTest(unittest.TestCase):
    def test_same_detector_proposals_across_distinct_performances_and_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "phrase.wav"
            for step in (-2, 2):
                for transpose in (0, 5):
                    for mode in ("lead turns", "accompaniment turns", "sustained chord"):
                        with self.subTest(mode=mode, transpose=transpose, step=step):
                            notes, candidates = performance(wav, mode, transpose, step)
                            selected = recover_supported_turns(wav, notes + candidates, notes)
                            expected = notes[:2] + candidates + notes[3:] if mode == "lead turns" else notes
                            self.assertEqual(expected, selected)

    def test_missing_attack_and_detached_context_do_not_open_the_source(self):
        notes = [(0., .5, 65, .9), (.5, .85, 60, .9), (.85, 1.25, 67, .9)]
        first, second = (.5, .70, 67, .8), (.70, .85, 69, .8)
        with patch("rendition_turns.sf.SoundFile", side_effect=AssertionError("no supported turn")):
            self.assertIs(notes, recover_supported_turns(None, notes + [second], notes))
            separated = [notes[0], notes[1], (1.1, 1.4, 67, .9)]
            self.assertIs(separated, recover_supported_turns(None, separated + [first, second], separated))

    def test_v9_mixed_trace_replays_without_later_turn_decisions(self):
        from rendition_melody import _recorded_line
        notes = [(0., .35, 65, .9)]
        with (patch("rendition_foreground.foreground_notes", return_value=notes),
              patch("rendition_octaves.recover_octave_excursions", return_value=notes),
              patch("rendition_sustains.keep_supported_sustains", return_value=notes),
              patch("rendition_turns.recover_supported_turns", return_value=notes) as turns):
            self.assertEqual((notes, "mix"), _recorded_line(None, None, notes, .35, 9, "mix", None))
            turns.assert_not_called()
            self.assertEqual((notes, "mix"), _recorded_line(None, None, notes, .35, 10, "mix", None))
            turns.assert_called_once_with(None, notes, notes)
