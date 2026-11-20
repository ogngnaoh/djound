import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from rendition_melody import MELODY_TRACE_VERSION, _recorded_line
from rendition_octaves import recover_octave_excursions


class OctaveExcursionTest(unittest.TestCase):
    def test_recorded_period_selects_octave_and_rejects_a_stale_proposal_in_a_rest(self):
        notes = [(0., .5, 65, .7), (.5, .9, 81, .8), (.9, 1.4, 65, .7)]
        events = notes + [(.50, .69, 53, .6), (.70, .90, 53, .7)]
        rate = 44100
        t = np.arange(round(1.4 * rate)) / rate
        tone = lambda pitch: np.sin(2 * np.pi * 440 * 2 ** ((pitch - 69) / 12) * t)
        middle = (t >= .5) & (t <= .9)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "ensemble.wav"
            for low, middle_pitch in ((True, 53), (False, 81)):
                source_wave = np.where(middle, .18 * tone(81) + low * .26 * tone(53), .24 * tone(65))
                sf.write(source, source_wave, rate)
                out = recover_octave_excursions(source, events, notes, 1.4)
                self.assertEqual([n[2] for n in out], [65, middle_pitch, 65])
                self.assertEqual(out[1][:2], (.5, .9))

    def test_a_missing_candidate_or_a_coherent_neighboring_line_requires_no_audio_read(self):
        notes = [(0., .5, 65, .7), (.5, .9, 67, .8), (.9, 1.4, 65, .7)]
        with patch("rendition_octaves.sf.SoundFile", side_effect=AssertionError("No disputed note")):
            for events in (notes, notes + [(.50, .90, 53, .7)]):
                self.assertIs(recover_octave_excursions("unopened.wav", events, notes, 1.4), notes)

    def test_existing_traces_replay_their_original_tracker(self):
        notes = [(0., .3, 67, .8)]
        with (patch("rendition_melody._select_polyphonic", return_value=notes) as historical,
              patch("rendition_foreground.foreground_notes", return_value=notes) as foreground,
              patch("rendition_octaves.recover_octave_excursions", return_value=notes) as excursion):
            for version in (1, 2, 3, 4, MELODY_TRACE_VERSION):
                self.assertEqual(_recorded_line("sample.wav", None, notes, 1., version, "mix", None), (notes, "mix"))
            self.assertEqual(MELODY_TRACE_VERSION, 5)
            self.assertEqual([args.kwargs["trace_version"] for args in historical.call_args_list], [1, 2, 3])
            self.assertEqual(foreground.call_count, 2)
            excursion.assert_called_once_with("sample.wav", notes, notes, 1.)


if __name__ == "__main__":
    unittest.main()
