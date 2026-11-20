import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from rendition_score import _gap_answers, analyze, arrange, Timeline
from rendition_score_plan import ArrangementClarification, _lead_duration, _scope_gap_answers


class GapAnswersTest(unittest.TestCase):
    def test_only_the_named_player_answers_in_the_heard_tunes_gaps(self):
        melody = [(.2,.6,62,.9), (1.,1.35,65,.9), (1.78,2.12,62,.9), (2.8,3.15,60,.9)]
        source = {"frames": 4 * 44100, "melody": melody, "candidates": melody}
        analysis = analyze(source)
        def part(kind, program):
            return dict(kind=kind, program=program, gesture="pulse",
                        pattern=[dict(beat=i, degree=i % 2, duration=.65, velocity=82) for i in range(4)])
        parent = dict(bpm=112, meter=4, subdivision="swing", phrasing="articulated", phraseDynamics="steady",
                      leadProgram=21, themeAnchors=[0], harmony="triads", introBars=1, endingBars=1,
                      backingTiming={"phrasing":"flowing"}, roles=[part("bass", 32), part("arpeggio", 0)])
        initial, seconds, mapping = arrange(source, analysis, parent)
        saved = copy.deepcopy(parent)
        director = Mock()
        director.choose.side_effect = [1, "gaps"]
        self.assertTrue(_scope_gap_answers(saved, "Let the piano answer in gaps in the accordion tune.", director))
        revised, length, markers = arrange(source, analysis, saved)
        self.assertEqual((length, markers, revised[:2]), (seconds, mapping, initial[:2]))
        self.assertEqual(parent["roles"][1] | {"answerTiming":{"phrasing":"articulated"}}, saved["roles"][1])
        self.assertEqual(initial[2]["program"], revised[2]["program"])
        first, last = revised[0]["notes"][0][0], revised[0]["notes"][-1][1]
        answers = [n for n in revised[2]["notes"] if first <= n[0] < last]
        self.assertGreaterEqual(len(answers), 2)
        for start, end, *_ in answers:
            self.assertTrue(all(end <= a or start >= b for a,b,*_ in revised[0]["notes"]))
        timeline = Timeline(analysis, parent)
        def outside(track):
            return [n for n in track["notes"] if n[0] < timeline.intro * timeline.beat
                    or n[0] >= timeline.beats[-1] * timeline.beat]
        self.assertEqual(outside(revised[2]), outside(initial[2]))
        self.assertEqual(arrange(source, analysis, saved), (revised, length, markers))

        # Asking a melody to sing instead of clipping its notes is a duration
        # edit. The selected piano replies still need room to enter.
        smooth = copy.deepcopy(saved)
        director.choose.side_effect = ["extend"]
        self.assertTrue(_lead_duration(smooth, "Let the accordion sing instead of clipping every note.", director))
        held, same_length, same_map = arrange(source, analysis, smooth)
        self.assertEqual((held[1:], same_length, same_map), (revised[1:], length, markers))
        def attacks(track):
            return [(start, pitch, velocity) for start, _, pitch, velocity in track["notes"]]
        self.assertEqual(attacks(held[0]), attacks(revised[0]))
        self.assertGreater(sum(n[1]-n[0] for n in held[0]["notes"]), sum(n[1]-n[0] for n in revised[0]["notes"]))
        for start, end, *_ in answers:
            self.assertTrue(all(end <= a or start >= b for a,b,*_ in held[0]["notes"]))
        director.choose.side_effect = ["shorten"]
        self.assertTrue(_lead_duration(smooth, "Make the melody notes shorter.", director))
        self.assertEqual(arrange(source, analysis, smooth)[0], revised)

        # A later lead revision must not quietly move the already selected piano performance.
        next_lead = dict(saved, phrasing="laid_back", leadTouch={"velocity":[76, 84, 88],
                                                                "lengthPercent":85, "endOffsetMs":20})
        self.assertEqual(revised[1:], arrange(source, analysis, next_lead)[0][1:])
        director.choose.side_effect = [1, "cycle"]
        self.assertTrue(_scope_gap_answers(saved, "Please let the piano play continuously again.", director))
        self.assertEqual(saved, parent)

    def test_no_breath_returns_a_listener_action_and_simultaneous_kit_hits_survive(self):
        role = {"pattern":[{"duration":.1, "pitch":36, "velocity":84}]}
        timeline = SimpleNamespace(beat=.5, intro=0., beats=[0.,4.])
        melody = [(0., .5, 64, 80), (.5, .8, 65, 85)]
        with self.assertRaisesRegex(ArrangementClarification, "shorter melody notes"):
            _gap_answers([], role, melody, timeline)
        written = [(1.,1.1,1.,dict(duration=.1,pitch=k,velocity=84)) for k in (36,42)]
        notes = _gap_answers(written, role, [(0.,.4,64,80),(.75,1.,65,85)], timeline)
        self.assertEqual([36,42], [hit["pitch"] for a,b,raw,hit in notes])

    def test_global_and_lead_changes_do_not_acquire_a_scoped_reply(self):
        plan = {"roles":[{"kind":"arpeggio","program":0}], "phrasing":"flowing"}
        director = Mock()
        self.assertFalse(_scope_gap_answers(plan, "Have the whole band answer in the gaps.", director))
        self.assertFalse(_scope_gap_answers(plan, "Make the piano gentler.", director))
        director.choose.return_value = "whole"
        self.assertFalse(_scope_gap_answers(plan, "Let the whole band and piano trade phrases in the gaps.", director))
        self.assertNotIn("answerTiming", plan["roles"][0])

    def test_explicit_level_backing_and_recording_peak_requests_keep_their_own_scope(self):
        plan = {"leadProgram": 21, "roles": []}
        director = Mock()
        for text in ("Make the accordion louder.", "Play shorter notes on the piano.",
                     "Keep the long notes. Turn the accordion up.", "The recording is clipping.",
                     "Make the whole band staccato."):
            with self.subTest(text=text):
                self.assertFalse(_lead_duration(plan, text, director))
        director.choose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
