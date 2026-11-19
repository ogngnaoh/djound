"""The director selects musical material; the score contract owns time, notes and revision semantics."""
import copy
import json
import subprocess
import time
import unittest
from unittest.mock import patch

import rendition_score_plan as score
from rendition_score import arrange


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

    def test_multiline_director_cue_round_trips_as_one_server_plan_field(self):
        chart = Musician(brief="128 BPM, 4/4, straight eighths.  \nBright synth melody.\r\nPulsing bass.\tDrum machine.")
        chart.identity = "test-director@1"
        with patch.object(score, "_Director", return_value=chart):
            plan = score._run({"analysis": SOURCE, "direction": SYNTH, "parentCaption": ""})
        self.assertEqual("128 BPM, 4/4, straight eighths. Bright synth melody. Pulsing bass. Drum machine.",
                         plan["conductorCue"])
        self.assertEqual(1, plan["version"])

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


class PhraseBackingTest(unittest.TestCase):
    def test_one_reply_fits_a_short_breath_without_touching_the_lead(self):
        from types import SimpleNamespace
        from rendition_score import _phrase_backing

        source = {"phrases": [{"anchorPitches": [65, 67, 69, 70]}]}
        timeline = SimpleNamespace(beats=[0, 4])
        attacks, releases = [0., 1.45, 2.7], [1., 2.55, 4.]
        plan = {"phraseInteraction": "phrase_answers", "subdivision": "straight"}
        self.assertFalse(_phrase_backing(source, timeline, plan, attacks, releases)[0].answers)
        section = _phrase_backing(source, timeline, dict(plan, phraseInteractionVersion=2), attacks, releases)[0]
        self.assertEqual(1, len(section.answers))
        self.assertLess(releases[0], section.answers[0])
        self.assertLess(section.answers[0], section.rest_end)
        self.assertLess(section.rest_end, attacks[1])

    def setUp(self):
        phrases, melody = [], []
        # A second phrase intervenes before the opening returns four semitones higher.
        for i, pitches in enumerate(((60, 62, 65, 64), (66, 65, 62, 59), (64, 66, 69, 68))):
            phrases.append({"start": 2 * i, "end": 2 * i + 2, "anchorPitches": pitches})
            for j, pitch in enumerate(pitches):
                at = 2 * i + .3 * j
                melody.append([at, at + .25, pitch, .8])
        source = {"bpm": 120, "phrases": phrases}
        notes = {"melody": melody, "candidates": melody, "frames": 6 * 44100}
        written = Musician({"Which listed player": 81, "Choose the repeating bass": "walk",
                            "Choose the repeating percussion": "backbeat", "How should the backing": "build_returns"},
                           brief="120 BPM, 4/4, straight eighths.")
        direction = "Synth lead plays the melody with synth bass and a drum machine."
        plan = score._arrange(source, direction, None, written)
        self.source, self.notes, self.plan = source, notes, plan

    def test_first_statement_leaves_room_and_transposed_return_brings_back_the_pulse(self):
        self.assertEqual("build_returns", self.plan["phraseInteraction"])
        self.assertEqual(["bass", "percussion"], [r["kind"] for r in self.plan["roles"]])
        tracks, _, _ = arrange(self.notes, self.source, self.plan)
        for track in tracks[1:]:
            attacks = [a for a, *_ in track["notes"]]
            self.assertNotIn(.5, attacks)  # Space beneath the first statement.
            self.assertIn(2.5, attacks)    # The intervening phrase keeps its figure.
            self.assertIn(4.5, attacks)    # Full pulse when the motif returns.

    def test_changing_ensemble_interaction_places_the_answer_after_the_lead_release(self):
        change = Musician({"What is the listener": "interaction", "How should the backing": "phrase_answers"})
        replying = score._arrange(self.source, "Leave room for the theme and answer in its breaths.", self.plan, change)
        self.assertEqual(dict(self.plan, phraseInteraction="phrase_answers"), replying)
        answering, _, _ = arrange(self.notes, self.source, replying)
        last_lead_end = answering[0]["notes"][3][1]
        answer = next(note for note in answering[1]["notes"] if last_lead_end <= note[0] < 2)
        self.assertEqual(1.25, answer[0])
        self.assertLess(answer[1], 2.)
        stretched = copy.deepcopy(self.notes)
        stretched["melody"][3][1] += .25
        later, _, _ = arrange(stretched, self.source, replying)
        self.assertGreater(next(a for a, *_ in later[1]["notes"] if 1.15 < a < 2), answer[0])

    def test_v1_legacy_figure_and_exact_backing_after_a_lead_swap(self):
        legacy = dict(self.plan)
        del legacy["phraseInteraction"]
        plain, duration, mapping = arrange(self.notes, self.source, legacy)
        self.assertEqual([i / 2 for i in range(12)], [at for at, *_ in plain[1]["notes"]])
        steady = dict(legacy, phraseInteraction="steady")
        self.assertEqual((plain, duration, mapping), arrange(self.notes, self.source, steady))
        for parent in (legacy, self.plan, dict(self.plan, phraseInteraction="phrase_answers")):
            child = score._arrange(self.source, "Give the melody to a cello.", parent,
                                   Musician({"What is the listener": "revoice", "Which performer": 42}))
            self.assertEqual(dict(parent, leadProgram=42), child)
            parent_tracks = arrange(self.notes, self.source, parent)[0]
            child_tracks, child_duration, child_map = arrange(self.notes, self.source, child)
            self.assertNotEqual(parent_tracks[0]["program"], child_tracks[0]["program"])
            self.assertEqual((parent_tracks[1:], duration, mapping), (child_tracks[1:], child_duration, child_map))

    def test_piano_answers_keep_the_requested_walk_and_ride_intact(self):
        direction = ("Let the piano rephrase the theme and answer it with chords, "
                     "with walking upright bass and brushed drums on the ride cymbal.")
        chart = Musician({"How should the backing": "phrase_answers", "How does the melody player": "chords",
                          "Choose the repeating chords": "anticipate", "Choose the repeating percussion": "ride"},
                         brief="120 BPM, 4/4, straight eighths.")
        plan = score._arrange(self.source, direction, None, chart)
        self.assertEqual([("chords", True), ("bass", False), ("percussion", False)],
                         [(r["kind"], r["followPhrases"]) for r in plan["roles"]])
        parts, duration, mapping = arrange(self.notes, self.source, plan)
        looping, _, _ = arrange(self.notes, self.source, dict(plan, phraseInteraction="steady"))
        self.assertNotEqual(looping[1]["notes"], parts[1]["notes"])
        self.assertEqual(looping[2:], parts[2:])
        child = score._arrange(self.source, "Give the melody to a cello.", plan,
                               Musician({"What is the listener": "revoice", "Which performer": 42}))
        revised, length, landmarks = arrange(self.notes, self.source, child)
        self.assertEqual((parts[1:], duration, mapping), (revised[1:], length, landmarks))
        # Earlier responsive scores applied the interaction to every player.
        old = copy.deepcopy(plan)
        del old["phraseInteractionVersion"]
        for role in old["roles"]:
            del role["followPhrases"]
        old_tracks, _, _ = arrange(self.notes, self.source, old)
        self.assertNotEqual(old_tracks[2]["notes"], looping[2]["notes"])
        self.assertNotEqual(old_tracks[3]["notes"], looping[3]["notes"])


if __name__ == "__main__":
    unittest.main()
