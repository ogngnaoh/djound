"""Guide instrument interpretation is independent of the style or music generation backend."""

import json
from subprocess import CompletedProcess, TimeoutExpired
import time
import unittest
from unittest.mock import patch

from rendition_voicing import _catalog, accompaniment_lane, choose_voicing, guide_named, preserve_register


class GuideVoicingTest(unittest.TestCase):
    def test_complete_patch_catalog_and_named_lead_without_model(self):
        without_model = patch(
            "rendition_voicing.subprocess.run", side_effect=AssertionError("unexpected language model")
        )
        with without_model:
            for program, name in enumerate(_catalog()[0]):
                with self.subTest(program=program):
                    self.assertEqual((program, False), choose_voicing(name))
            self.assertEqual(
                (42, False),
                choose_voicing(
                    "Plucked guitar chords and bowed cello.", "Give the theme to the cello over the guitar."
                ),
            )
            self.assertEqual((11, False), choose_voicing("A vibraphone carries the tune over bowed bass."))
            self.assertEqual((70, True), choose_voicing("One-player study.", "Unaccompanied bassoon."))

    def test_named_lead_uses_role_and_keeps_register_requests_separate(self):
        with patch("rendition_voicing.subprocess.run", side_effect=AssertionError("no ML needed")):
            cases = [
                (
                    "Marimba leads a procession with a reed organ and light brushes.",
                    "Reed organ pumping offbeats, wooden bars carrying the hook.",
                    12,
                ),
                (
                    "A bassoon intones the melody above a pulsing vibraphone.",
                    "Make the low woodwind tell the story, with shimmering bars beneath.",
                    70,
                ),
            ]
            for caption, request, expected in cases:
                self.assertEqual((expected, False), choose_voicing(caption, request))
                self.assertTrue(guide_named(expected, caption))
            folk = "A duet with acoustic guitar and bowed cello. Give the cello the main tune."
            self.assertTrue(guide_named(42, folk))
            self.assertFalse(guide_named(25, folk))
            self.assertFalse(guide_named(0, "No piano; the flute carries the tune."))
            self.assertTrue(guide_named(73, "No piano; the flute carries the tune."))
            self.assertFalse(guide_named(68, "Oboe and clarinet duet."))
            self.assertTrue(preserve_register("Please keep my original octave."))
            self.assertTrue(preserve_register("Let it sing in its upper register."))
            self.assertTrue(preserve_register("Put it above middle C."))
            self.assertFalse(preserve_register("A high-energy chorus with very low drums."))
            self.assertFalse(preserve_register("Any octave is fine."))

    def test_duet_support_requires_a_resolved_pair_and_real_backing_role(self):
        with patch("rendition_voicing.subprocess.run", side_effect=AssertionError("no model needed")):
            cases = [
                (
                    42,
                    "A chamber folk duet with fingerpicked acoustic guitar and bowed cello. Give the cello the main tune.",
                    "guitar",
                ),
                (70, "Bassoon leads with a pulsing marimba.", "percussion"),
                (68, "Oboe carries the motif above a quietly played acoustic piano.", "keyboard"),
                (71, "A clarinet and acoustic bass duo. The acoustic bass plays soft chords.", "bass"),
                (40, "A trumpet and violin duet. The violin takes the lead.", "brass"),
                (12, "Marimba carries the tune over an alto saxophone.", "woodwinds"),
                (73, "Flute plays the hook over a bowed cello.", "strings"),
            ]
            for program, direction, family in cases:
                with self.subTest(direction=direction):
                    self.assertEqual(family, accompaniment_lane(program, direction))
            excluded = [
                "A chamber folk duet with cello and guitar.",
                "Cello leads in a trio with guitar.",
                "Cello leads over the guitar; add a theremin.",
                "Cello leads over the guitar and theremin.",
                "An earlier duet had guitar and cello. Let the cello lead.",
                "I want a calmer guitar and cello.",
                "Cello leads over the guitar and piano.",
                "Cello leads above the synth.",
                "A duo for guitar and cello; no guitar this time. Cello carries the tune.",
                "Cello leads over vocals.",
                "Cello leads over the hurdy-gurdy.",
                "A guitar and cello duo. The guitar plays the tune.",
                "No piano. Only cello.",
                "A solo cello recording, guitar was playing on the earlier take.",
            ]
            for direction in excluded:
                with self.subTest(direction=direction):
                    self.assertIsNone(accompaniment_lane(42, direction))

    def test_ambiguity_semantics_and_bounded_fallback(self):
        directions = [
            ("A harmonica player and an organ", "Let the wheezing pocket instrument carry it."),
            ("Cello solo with a backing band", "Keep the cellist in front of the trio."),
            ("A verse that used to be for cello", "No cello; give it to something bright and airy."),
        ]
        for caption, prompt in directions:
            with patch(
                "rendition_voicing.subprocess.run", return_value=CompletedProcess([], 0, "[22,false]")
            ) as run:
                self.assertEqual((22, False), choose_voicing(caption, prompt))
                arguments = run.call_args.kwargs
                self.assertLessEqual(arguments["timeout"], 25)
                self.assertEqual({"listener": prompt, "arrangement": caption}, json.loads(arguments["input"]))
        for invalid in ("no clear answer", '[999,"yes"]', "{}", "null"):
            with patch("rendition_voicing.subprocess.run", return_value=CompletedProcess([], 0, invalid)):
                self.assertEqual((0, False), choose_voicing("Light, barely audible pulses."))

    def test_solo_does_not_mean_ensemble_solo_break(self):
        with patch(
            "rendition_voicing.subprocess.run", return_value=CompletedProcess([], 0, "[42,false]")
        ) as model:
            self.assertEqual((42, False), choose_voicing("A cello solo over a guitar trio."))
            model.assert_called_once()
        with self.assertRaises(TimeoutError):
            choose_voicing("Solo violin", deadline=time.monotonic() - 1)
        with (
            patch("rendition_voicing.subprocess.run", side_effect=TimeoutExpired("qwen", 1)),
            self.assertRaises(TimeoutError),
        ):
            choose_voicing("Invent a sound that suggests a clock shop.")


if __name__ == "__main__":
    unittest.main()
