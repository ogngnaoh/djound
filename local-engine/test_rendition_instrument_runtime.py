import math
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

import rendition_instrument_runtime as player


class InstrumentRuntimeTest(unittest.TestCase):
    def test_segment_boundaries_match_whole_resampling_in_stereo_and_shifted_views(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "recording.wav"
            t = np.arange(200123) / 44100
            stereo = np.column_stack((np.sin(2 * np.pi * 220 * t), np.cos(2 * np.pi * 330 * t))) * .2
            sf.write(source, stereo, 44100, subtype="PCM_16")
            samples, _ = sf.read(source, dtype="float32", always_2d=True)
            for lead_in in (0, 16384):
                with self.subTest(lead_in=lead_in):
                    expected = resample_poly(samples.mean(axis=1), 160, 441)
                    expected = np.pad(expected, (lead_in, -(len(expected) + lead_in) % player.SEGMENT))
                    blocks = list(player._segments(source, lead_in))
                    self.assertEqual(len(blocks), math.ceil(len(expected) / player.SEGMENT))
                    np.testing.assert_array_equal(np.concatenate(blocks), expected)

    def test_deadline_expires_before_the_model_consumes_a_segment(self):
        with patch.object(player, "_segments", return_value=[np.zeros(player.SEGMENT)]):
            with self.assertRaises(TimeoutError):
                list(player._tokenize(None, None, None, 0, time.monotonic() - 1))
