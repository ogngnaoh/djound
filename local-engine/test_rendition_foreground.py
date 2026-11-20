import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import rendition_melody as melody
from rendition_foreground import RATE, foreground_notes


class InstrumentalForegroundTest(unittest.TestCase):
    def test_quiet_high_player_and_split_proposals_do_not_replace_the_tune(self):
        # The same candidate pitches can describe either performance. Only the
        # recording establishes which player has the foreground.
        seconds = 2.4
        t = np.arange(round(seconds * RATE)) / RATE
        lower = np.zeros_like(t)
        upper = np.zeros_like(t)
        events = []
        low_line, high_line = (57, 60, 62, 59), (76, 79, 77, 81)
        for i, (low, high) in enumerate(zip(low_line, high_line)):
            start, end = i * .6 + .04, (i + 1) * .6 - .04
            envelope = np.clip(np.minimum(t - start, end - t) / .025, 0., 1.)
            for audio, pitch in ((lower, low), (upper, high)):
                phase = 2 * np.pi * 440 * 2 ** ((pitch - 69) / 12) * (t - start)
                audio += envelope * (np.sin(phase) + .33 * np.sin(2 * phase))
                # Several adjacent detections of a continuous held note.
                middle = (start + end) / 2
                events.extend(((start, middle, pitch, .7), (middle, end, pitch, .7)))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "ensemble.wav"
            for first, second, expected in ((lower, upper, low_line), (upper, lower, high_line)):
                sf.write(source, np.column_stack([first * .36 + second * .05] * 2), RATE)
                found = foreground_notes(source, sorted(events), seconds)
                self.assertEqual(expected, tuple(note[2] for note in found))
                self.assertTrue(all(end - start >= .45 for start, end, _, _ in found))

    def test_saved_register_trace_and_new_acoustic_trace_use_their_own_versions(self):
        events = [(0., .4, 66, .7)]
        with (patch("rendition_foreground.foreground_notes", return_value=events) as new,
              patch.object(melody, "_select_polyphonic", return_value=events) as old):
            for version in (3, 4):
                self.assertEqual((events, "mix"), melody._recorded_line("audio", None, events, .4,
                                                                        version, "mix", None))
            old.assert_called_once_with(events, .4, trace_version=3)
            new.assert_called_once_with("audio", events, .4)
