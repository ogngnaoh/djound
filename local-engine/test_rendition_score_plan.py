"""The director selects musical material; the score contract owns time, notes and revision semantics."""
import copy
import json
import subprocess
import time
import unittest
from unittest.mock import patch

import rendition_score_plan as score


SOURCE = {"bpm": 94, "durationSeconds": 25, "phrases": [
    {"openingPitches": [62, 64, 67, 65]}, {"openingPitches": [64, 69, 69, 67]},
    {"openingPitches": [67, 69, 72, 70]}]}
DUET = "Bassoon plays the theme over marimba."
SYNTH = "Make this synth-pop with a bright synth melody, pulsing synth bass and a drum machine."


class Musician:
    """Supply decisions by meaning so these tests never depend on menu order or language-model weights."""
    def __init__(self, choices=None, brief="108 BPM, 4/4, straight eighths.", players="Flute, harp"):
        self.brief = brief
        self.players = players
        self.choices = choices or {}
        self.questions = []

    def write(self, question, limit):
        return self.players

    def choose(self, question, options):
        self.questions.append(question)
        for prefix, value in self.choices.items():
            if question.startswith(prefix):
                assert value in options, (value, options)
                return value
        return next(iter(options))


class ScorePlanTest(unittest.TestCase):
    def plan(self, direction, musician=None, prior=None):
        return score._arrange(SOURCE, direction, prior, musician or Musician())

    def test_concrete_musical_decisions_from_cue_and_source(self):
        m = Musician({"How should the lead": "laid_back", "How should the accompanists": "open_fifths",
                      "What supporting part": "arpeggio", "Choose the repeating": "answer"},
                     "At 82 BPM, 3/4 with swung eighths; bassoon sings and marimba answers.")
        plan = self.plan(DUET, m)
        self.assertEqual((82, 3, "swing", "laid_back", "open_fifths", 70),
                         tuple(plan[k] for k in ("bpm", "meter", "subdivision", "phrasing", "harmony", "leadProgram")))
        self.assertEqual([0, 2], plan["themeAnchors"])
        self.assertEqual(("arpeggio", 12), (plan["roles"][0]["kind"], plan["roles"][0]["program"]))
        self.assertGreater(plan["roles"][0]["pattern"][0]["beat"], 0)

    def test_electronic_voices_are_musical_instrument_names(self):
        self.assertEqual([81, 38, score.KIT], score._members(SYNTH))
        self.assertEqual([39, 114, 118], score._members("Synth Bass 2, steel drums, Synth Drum."))
        self.assertTrue(score._excluded_player(38, "No synth bass. Bring in the harp."))
        m = Musician({"What supporting part": "bass", "Choose the repeating bass": "anticipate",
                      "Choose the repeating percussion": "backbeat", "Which listed player": 81})
        plan = self.plan(SYNTH, m)
        self.assertEqual(81, plan["leadProgram"])
        self.assertEqual([("bass", 38, None), ("percussion", None, 24)],
                         [(r["kind"], r["program"], r.get("kit")) for r in plan["roles"]])
        self.assertIn(38, {e["pitch"] for e in plan["roles"][-1]["pattern"]})
        self.assertEqual(25, score._roles(m, [("percussion", score.KIT)], 4, "A TR-808")[0]["kit"])
        self.assertEqual(40, score._roles(m, [("percussion", score.KIT)], 4, "brushed snare")[0]["kit"])

    def test_solo_can_play_its_own_accompaniment(self):
        m = Musician({"How does the melody player": "arpeggio"})
        piano = self.plan("Solo piano", m)
        self.assertEqual(0, piano["leadProgram"])
        self.assertEqual([("arpeggio", 0)], [(r["kind"], r["program"]) for r in piano["roles"]])
        oboe = self.plan("Unaccompanied oboe", m)
        self.assertEqual((68, []), (oboe["leadProgram"], oboe["roles"]))

    def test_explicit_timing_overrides_language_cue(self):
        m = Musician(brief="82 BPM in 3/4 with swung eighths.")
        plan = self.plan("Play this at 126 BPM, 5/4, straight eighths.", m)
        self.assertEqual((126, 5, "straight"), (plan["bpm"], plan["meter"], plan["subdivision"]))
        with self.assertRaisesRegex(ValueError, "40 and 240"):
            self.plan("At 350 BPM", m)

    def test_gesture_events_stay_inside_all_supported_meters_and_rests_survive(self):
        for meter in score.METERS:
            for kind, gestures in score.GESTURES.items():
                for gesture in gestures:
                    events = score._pattern(kind, gesture, meter)
                    with self.subTest(kind=kind, gesture=gesture, meter=meter):
                        self.assertTrue(events)
                        self.assertEqual(sorted(e["beat"] for e in events), [e["beat"] for e in events])
                        for e in events:
                            self.assertGreater(e["duration"], 0)
                            self.assertLessEqual(e["beat"] + e["duration"], meter + 1e-7)
                            self.assertTrue(1 <= e["velocity"] <= 127)
        self.assertEqual([1., 3.], [e["beat"] for e in score._pattern("chords", "backbeats", 4)])

    def test_one_lead_replacement_preserves_every_accompaniment_event(self):
        old = self.plan(SYNTH, Musician({"What supporting part": "bass", "Which listed player": 81}))
        before = copy.deepcopy(old)
        for axis in ("players", "revoice"):
            m = Musician({"What is the listener": axis, "Which performer": 42})
            result = self.plan("Give the melody to a cello.", m, old)
            self.assertEqual({**old, "leadProgram": 42}, result)
        self.assertEqual(before, old)

    def test_tempo_or_production_revision_retains_cast_and_note_patterns(self):
        old = self.plan(DUET)
        faster = self.plan("Keep everything and make it 119 BPM.", Musician({"What is the listener": "pulse"}), old)
        self.assertEqual({**old, "bpm": 119}, faster)
        wet = self.plan("A smeared, time-reversed tape echo in the distance.",
                        Musician({"What is the listener": "production", "How must": "produced"}), old)
        self.assertEqual({**old, "realization": "produced"}, wet)

    def test_language_process_receives_bounded_source_excerpt_and_deadline(self):
        phrases = [{"openingPitches": [60, 63, 65, 66], "notes": 5, "privateWave": [157] * 250}] * 160
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"version":1}')) as child:
            score.plan_arrangement({"bpm": 100, "phrases": phrases}, "Play it lightly")
            body = json.loads(child.call_args.kwargs["input"])
            self.assertEqual(160, body["sourceSnapshot"]["phraseCount"])
            self.assertLessEqual(len(body["sourceSnapshot"]["phrases"]), 6)
            self.assertNotIn("privateWave", child.call_args.kwargs["input"])
            with self.assertRaises(TimeoutError):
                score.plan_arrangement(SOURCE, "Play it lightly", deadline=time.monotonic() - 1)
            self.assertEqual(1, child.call_count)


if __name__ == "__main__":
    unittest.main()
