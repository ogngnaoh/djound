import json
import unittest
from unittest.mock import Mock, patch

from rendition_drum_notes import counted_drum_notes
from rendition_score import analyze, arrange
import rendition_score_plan as planner


class WrittenDrumTest(unittest.TestCase):
    def setUp(self):
        self.old = {"version": 1, "bpm": 88, "meter": 4, "introBars": 1, "endingBars": 2,
                    "subdivision": "straight", "themeAnchors": [0], "phrasing": "flowing",
                    "phraseDynamics": "steady", "harmony": "triads", "leadProgram": 66,
                    "roles": [{"kind": kind, "program": patch_no, "gesture": figure,
                               "pattern": planner._pattern(kind, figure, 4)}
                              for kind, patch_no, figure in (("chords", 25, "offbeat"), ("bass", 32, "walk"))]}
        self.words = ("I love where this is going. Could a drummer tap the rim on beats two and four? "
                      "Keep the sax, guitar and bass playing just what they play now.")

    def test_invited_drummer_keeps_prior_players_and_the_counted_rim_through_the_ending(self):
        notes = {"frames": 8 * 44100, "melody": [(i, i + .5, 65 + i, .8) for i in range(8)], "candidates": []}
        analysis = analyze(notes)
        conductor = Mock(brief="Full drum set with hand drums.")
        conductor.choose.side_effect = AssertionError("All players and the new drum notes are specified.")
        with patch.object(planner, "_changes", side_effect=AssertionError("A new backing player was named.")):
            plan = planner._arrange(analysis, self.words, self.old, conductor)
        saved = planner._prior({"parentPlan": json.loads(json.dumps(plan))})
        tracks, _, _ = arrange(notes, analysis, saved)
        self.assertEqual(arrange(notes, analysis, self.old)[0], tracks[:-1])
        drums = tracks[-1]
        self.assertTrue(drums["percussion"])
        self.assertGreater(len(drums["notes"]), 8)
        self.assertEqual({37}, {n[2] for n in drums["notes"]})
        for index, (onset, *_) in enumerate(drums["notes"]):
            self.assertAlmostEqual(1 + 2 * index, onset * 88 / 60)
        self.assertFalse(planner._phrase_part(conductor, plan["roles"][-1], self.words))

    def test_other_percussion_and_written_counts_use_the_same_route(self):
        self.assertEqual([(0, 56), (1, 37), (2, 56), (3, 37)], counted_drum_notes(
            "Add cowbell on 1 and 3 and side stick on beats two and four.", 4))
        words = "Let a percussionist play closed hi-hat on the offbeats."
        self.assertEqual([planner.KIT], planner._backing_additions(words, self.old))
        self.assertEqual([(.5, 42), (1.5, 42), (2.5, 42)], counted_drum_notes(words, 3))
        self.assertEqual([(0, 39), (1, 39)], counted_drum_notes("Clap on every beat.", 2))
        self.assertEqual([], counted_drum_notes("Tap the rim on two and four.", 3))
        self.assertEqual([], counted_drum_notes("No rim taps on beats two and four.", 4))
        self.assertEqual([], counted_drum_notes("Play the rim on beat two, plus a kick.", 4))
        self.assertEqual([(1, 37)], counted_drum_notes("Rim click on beat two, no hi-hat.", 4))

    def test_a_compound_edit_or_existing_drummer_is_not_a_single_addition(self):
        for text in ("Could a drummer tap the rim on two and four and make it faster?",
                     "Add a drummer but remove the guitar.", "Don't add a drummer tapping the rim on beat two."):
            self.assertEqual([], planner._backing_additions(text, self.old))
        existing = dict(self.old, roles=self.old["roles"] + [{"kind": "percussion", "program": None}])
        self.assertEqual([], planner._backing_additions(self.words, existing))
