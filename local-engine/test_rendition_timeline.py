import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

import rendition_timeline as timeline
from rendition_score_render import score_available


class RelativeMapTest(unittest.TestCase):
    def test_variable_tempos_from_both_performances(self):
        take = [[0, 0], [50, 25], [80, 100]]
        parent = [[0, 0], [20, 50], [80, 100]]
        self.assertEqual(timeline.relative_map(take, parent), [[0, 0], [50, 10], [60, 20], [80, 80]])
        self.assertEqual(timeline.relative_map(take, None), take)
        self.assertEqual(timeline.relative_map(None, parent), [[0, 0], [50, 20], [100, 80]])
        self.assertIsNone(timeline.relative_map(None, None))

    def test_shared_holds_and_same_map_resume_at_the_corresponding_position(self):
        take = [[0, 0], [4, 0], [10, 6], [12, 6]]
        parent = [[0, 0], [2, 0], [8, 6], [12, 6]]
        self.assertEqual(timeline.relative_map(take, parent), [[0, 0], [4, 2], [10, 8], [12, 12]])
        self.assertEqual(timeline.relative_map(take, [p.copy() for p in take]), [[0, 0], [12, 12]])
        # The same piecewise function may arrive with extra knots inside its held interval.
        redundant = [[0, 0], [2, 0], [4, 0], [6, 2], [10, 6], [11, 6], [12, 6]]
        self.assertEqual(timeline.relative_map(take, redundant), [[0, 0], [12, 12]])

    def test_parent_only_holds_preserve_surrounding_frames_and_endpoints(self):
        parent = [[0, 0], [20, 0], [60, 40], [100, 40], [130, 70], [140, 70]]
        child = timeline._Timeline(timeline.relative_map(None, parent))
        self.assertEqual([child.original(i) for i in (0, 1, 39, 40, 41, 69, 70)],
                         [0, 21, 59, 60, 101, 129, 140])
        # Parent hold begins between two child samples. Neither neighboring phrase is smeared.
        parent = [[0, 0], [2, 2], [15, 2], [21, 8]]
        child = timeline._Timeline(timeline.relative_map([[0, 0], [10, 8]], parent))
        self.assertEqual([child.original(i) for i in (0, 2, 3, 10)], [0, 2, 15, 21])

    def test_invalid_maps_and_different_upload_lengths(self):
        identity = [[0, 0], [10, 10]]
        for invalid in ([[1, 0], [5, 5]], [[0, 0], [5, 6], [4, 10]], [[0, 0], [2, 2], [3, 1]],
                        [[0, 0], [10, 0]], [[0, 0], [1, True]], [[0, 0], [10, 11]]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                timeline.relative_map(identity, invalid)


class AlignGuideTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.source = self.path / "source.wav"
        self.output = self.path / "guide.wav"
        self.deadline = time.monotonic() + 60
        rate = timeline.RATE
        with wave.open(str(self.source), "wb") as wav:
            wav.setparams((2, 2, rate, rate * 2, "NONE", ""))
            wav.writeframes(b"\0" * rate * 8)
        self.map = [[0, 0], [rate, 0], [2*rate, rate], [3*rate, rate], [5*rate, 2*rate], [6*rate, 2*rate]]

    def supply_line(self, original, output, *, deadline):
        self.assertEqual(original, self.source)
        self.assertEqual(deadline, self.deadline)
        output.write_text(json.dumps({"frames": 2 * timeline.RATE,
                                     "melody": [[0, .5, 64, .8], [.75, 1.25, 67, .6], [1.5, 2, 69, 1.0]]}))
        return True

    def test_retimes_pitches_and_articulation_and_omits_inserted_measures(self):
        with patch.object(timeline, "transcribe_recording", self.supply_line), patch.object(timeline, "render_score") as render:
            self.assertTrue(timeline.align_original_guide(self.source, self.output, self.map, deadline=self.deadline))
        parts, seconds, path = render.call_args.args
        self.assertEqual((seconds, path), (6., self.output))
        self.assertEqual(parts[0]["notes"], [(1., 1.5, 64, 85), (1.75, 2., 67, 80), (3., 3.5, 67, 80), (4., 5., 69, 90)])
        self.assertEqual(render.call_args.kwargs, {"deadline": self.deadline, "room": 0.})

    def test_no_melody_mismatched_upload_or_expired_deadline_publishes_nothing(self):
        with patch.object(timeline, "transcribe_recording", return_value=False) as model:
            self.assertFalse(timeline.align_original_guide(self.source, self.output, self.map, deadline=self.deadline))
            with self.assertRaisesRegex(ValueError, "does not match"):
                timeline.align_original_guide(self.source, self.output, [[0,0],[20,18]], deadline=self.deadline)
            with self.assertRaises(TimeoutError):
                timeline.align_original_guide(self.source, self.output, self.map, deadline=time.monotonic() - 1)
            self.assertEqual(model.call_count, 1)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.path.glob(".align-*")))

    @unittest.skipUnless(score_available(), "Requires the installed piano bank and score performer")
    def test_real_pcm_starts_after_intro_and_has_six_seconds(self):
        with patch.object(timeline, "transcribe_recording", self.supply_line):
            self.assertTrue(timeline.align_original_guide(self.source, self.output, self.map, deadline=self.deadline))
        with wave.open(str(self.output), "rb") as wav:
            self.assertEqual(wav.getparams()[:4], (2, 2, timeline.RATE, 6 * timeline.RATE))
            self.assertFalse(any(wav.readframes(timeline.RATE)))
            self.assertTrue(any(wav.readframes(timeline.RATE // 2)))


if __name__ == "__main__":
    unittest.main()
