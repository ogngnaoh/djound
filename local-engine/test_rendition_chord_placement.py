import unittest
from unittest.mock import Mock

from rendition_score import analyze, arrange
from rendition_score_plan import _roles


class ChordPlacementTest(unittest.TestCase):
    def roles(self, words, cue="", *, cast=(("chords", 25),), default="backbeats", meter=4):
        director = Mock(brief=cue)
        director.choose.side_effect = lambda question, options: default if default in options else next(iter(options))
        return _roles(director, cast, meter, words)

    def test_short_chords_between_beats_are_audibly_scheduled_through_the_coda(self):
        words = ("I'd love to hear this as a laid-back reggae band. Let the bass carry the groove "
                 "and have the guitar play those short chords between the beats. Keep the tune clear.")
        roles = self.roles(words, "Guitar hits beats two and four.")
        self.assertEqual("offbeat", roles[0]["gesture"])
        notes = {"frames": 8 * 44100, "melody": [(i, i + .5, 60 + i, .8) for i in range(8)], "candidates": []}
        plan = {"roles": roles, "bpm": 120, "meter": 4, "introBars": 0, "endingBars": 1, "leadProgram": 71,
                "phrasing": "flowing", "harmony": "triads", "phraseDynamics": "steady", "themeAnchors": [],
                "subdivision": "straight"}
        tracks, _, _ = arrange(notes, analyze(notes), plan)
        strokes = tracks[1]["notes"]
        self.assertGreater(len(strokes), 12)
        for start, end, pitch, velocity in strokes:
            # Chord voicing can stagger the upper notes by a few milliseconds.
            self.assertAlmostEqual(.5, (start * 2) % 1, delta=.04)
            self.assertLessEqual(end - start, .18)

    def test_two_and_four_and_every_beat_are_distinct_from_an_offbeat(self):
        for phrase, expected in (("on beats two and four", [1, 3]), ("on every beat", [0, 1, 2, 3]),
                                 ("on the ‘and’ of each beat", [.5, 1.5, 2.5, 3.5])):
            with self.subTest(phrase=phrase):
                role = self.roles("Use guitar chords " + phrase + ".", "Offbeat guitar chords.")[0]
                self.assertEqual(expected, [e["beat"] for e in role["pattern"]])
        role = self.roles("Guitar chords and bass.", "Guitar plays short chords (D, A, G) between beats.",
                          meter=3)[0]
        self.assertEqual([.5, 1.5, 2.5], [e["beat"] for e in role["pattern"]])

    def test_another_players_offbeats_and_negated_offbeats_do_not_bind_the_guitar(self):
        roles = self.roles("Piano between beats and guitar on the beat.", cast=(("chords", 0), ("chords", 25)))
        self.assertEqual(["offbeat", "pulse"], [r["gesture"] for r in roles])
        role = self.roles("Guitar chords, please.", "Guitar should avoid offbeats.", default="offbeat")[0]
        self.assertNotEqual("offbeat", role["gesture"])
        role = self.roles("Guitar should not play between beats.", "Guitar on offbeats.", default="offbeat")[0]
        self.assertNotEqual("offbeat", role["gesture"])
