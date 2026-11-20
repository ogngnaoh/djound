from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import rendition_ensemble as ensemble
import rendition_melody as melody


class EnsemblePassageTest(unittest.TestCase):
    def test_saved_player_line_requires_its_model_and_saved_mix_never_loads_it(self):
        notes = [(0., .5, 60, .8)]
        with (patch("rendition_foreground.foreground_notes", return_value=notes),
              patch("rendition_octaves.recover_octave_excursions", return_value=notes),
              patch("rendition_player_line.needs_player_analysis", return_value=True),
              patch.object(ensemble, "installed_model", return_value=None) as lookup):
            self.assertEqual(melody._recorded_line(None, None, notes, 1., 6, "mix", None), (notes, "mix"))
            lookup.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Restore its verified model"):
                melody._recorded_line(None, None, notes, 1., 6, "instrument_tracks", None)
            self.assertIsNone(ensemble.resolve_players(None, None, notes, notes, 1., None))

    def test_an_unchanged_note_can_span_the_boundary_and_an_actual_reattack_stays_distinct(self):
        original = [(0., 1., 62, .8), (1., 3., 65, .7), (3., 4., 60, .8), (4., 5., 60, .8)]
        new = [(1.5, 3., 65, .95), (3., 4., 64, .8)]
        self.assertEqual(ensemble._replace(original, new, 2., 4.), [
            (0., 1., 62, .8), (1., 3., 65, .7), (3., 4., 64, .8), (4., 5., 60, .8)])
        attacked = [(2., 3., 65, .9)]
        self.assertEqual(ensemble._replace(original, attacked, 2., 3.)[1:3],
                         [(1., 2., 65, .7), (2., 3., 65, .9)])

    def test_only_the_disputed_part_of_a_long_recording_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "song.wav"
            sf.write(source, np.zeros(60 * 44100, dtype="int16"), 44100)
            notes = [(i + .1, i + .9, 81 if 29 <= i < 43 else 60, .7) for i in range(60)]
            def disputed(mix, events, duration):
                return any(k == 81 for _, _, k, _ in mix)
            def revised(path, events, mix, views, duration):
                self.assertLess(duration, 32)
                self.assertAlmostEqual(sf.info(path).duration, duration)
                return [(a, b, 62 if k == 81 else k, c) for a, b, k, c in mix]
            with (patch("rendition_player_line.needs_player_analysis", side_effect=disputed),
                  patch("rendition_player_line.select_player_line", side_effect=revised),
                  patch.object(ensemble, "installed_model", return_value=Path(directory)),
                  patch.object(ensemble, "instrument_tracks", return_value=[{}, {}]) as model):
                result = ensemble.resolve_players(source, directory, notes, notes, 60., None)
            self.assertEqual(1, model.call_count)
            self.assertEqual([n[2] for n in result], [62 if n[2] == 81 else 60 for n in notes])
            self.assertEqual(result[:20], notes[:20])
            self.assertEqual(result[-7:], notes[-7:])
