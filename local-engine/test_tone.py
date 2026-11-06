from importlib.util import find_spec
from pathlib import Path
import unittest

from common import DEFAULT_RECIPE, UNITY_STRIP, EngineError, recipe
from planner import plan
from tone_phrases import tone_commands
from worker import needs_freeform


class ToneContracts(unittest.TestCase):
    def test_two_tracks_are_reviewed_together_and_restored(self):
        before = dict(DEFAULT_RECIPE, bass=True, mix={'lead': dict(UNITY_STRIP, pan=-.3)})
        result = plan('Please brighten the melody and make the bass warmer.', before, None)
        self.assertEqual(result['provenance']['syntax'], 'tone-phrases-v1')
        self.assertEqual(result['provenance']['inferenceAttempts'], 0)
        self.assertEqual(result['recipe']['mix']['lead'], dict(UNITY_STRIP, pan=-.3, tone='bright'))
        self.assertEqual(result['recipe']['mix']['bass'], dict(UNITY_STRIP, tone='warm'))
        reset = plan('reset the lead tone; reset the bass tone', result['recipe'], None)
        self.assertEqual(reset['recipe'], before)
        self.assertFalse(needs_freeform({'operation':'plan','prompt':'warm the lead'}))

    def test_invalid_or_partial_edits_are_withheld(self):
        for text in ('warm the bass', 'warm the lead and add strings', 'do not brighten the lead',
                     'brighten the drums and warm the lead', 'warm the lead and brighten the lead'):
            with self.subTest(text=text), self.assertRaises(EngineError):
                plan(text, DEFAULT_RECIPE, None)
        for value in (None, [], True, 'deep'):
            with self.subTest(value=value), self.assertRaises(EngineError):
                recipe(dict(DEFAULT_RECIPE, mix={'lead':dict(UNITY_STRIP, tone=value)}))
        self.assertIsNone(tone_commands('warm the lead except the quiet notes'))
        self.assertEqual(recipe(dict(DEFAULT_RECIPE, mix={'lead':dict(UNITY_STRIP, tone='neutral')})), DEFAULT_RECIPE)


@unittest.skipUnless(find_spec('scipy') and find_spec('numpy'), 'Requires the local audio environment.')
class ToneAudio(unittest.TestCase):
    def test_stereo_direction_rms_and_polarity(self):
        import numpy as np
        from track_tone import RATE, shape
        t = np.arange(RATE) / RATE
        mono = .12 * np.sin(2 * np.pi * 350 * t) + .09 * np.sin(2 * np.pi * 5200 * t)
        stereo = np.column_stack((mono, -mono)).astype('<f4')
        dry = stereo.copy()
        for tone, sign in [('warm', -1), ('bright', 1)]:
            out, evidence = shape(stereo, tone)
            self.assertGreater(sign * evidence['bandChangeDb'], .5)
            self.assertEqual(out.shape, stereo.shape)
            np.testing.assert_allclose(np.mean(out**2), np.mean(dry**2), rtol=1e-6)
            np.testing.assert_allclose(out[:, 0], -out[:, 1], atol=1e-7)
            np.testing.assert_array_equal(dry, stereo)

    def test_silent_stem_remains_finite(self):
        import numpy as np
        from track_tone import RATE, shape
        dry = np.zeros((RATE, 2), dtype='<f4')
        result, evidence = shape(dry, 'bright')
        np.testing.assert_array_equal(result, dry)
        self.assertIsNone(evidence['bandChangeDb'])
