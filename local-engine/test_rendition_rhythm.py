import copy
import unittest

from rendition_rhythm import write_chart_figure
from rendition_score_plan import _arrange
from test_rendition_score_plan import Musician, SOURCE


class ChartRhythmTest(unittest.TestCase):
    def test_distinct_figures_write_bounded_events_in_both_duple_meters(self):
        for meter in (2, 4):
            for figure, phases in (("marcato", list(range(meter))),
                                   ("habanera", [0, 3 * meter / 8, meter / 2, 3 * meter / 4]),
                                   ("tresillo", [0, 3 * meter / 8, 3 * meter / 4])):
                plan = {"meter": meter, "roles": [{"kind": "bass", "program": 43}]}
                write_chart_figure(plan, figure, set())
                hits = plan["roles"][0]["pattern"]
                self.assertEqual(phases, [h["beat"] for h in hits])
                self.assertGreater(len({h["degree"] for h in hits}), 1)
                self.assertGreater(len({h["velocity"] for h in hits}), 1)
                for h, next_beat in zip(hits, phases[1:] + [meter]):
                    self.assertTrue(0 < h["duration"] <= next_beat - h["beat"])

    def test_chart_pulse_reaches_a_free_player_but_keeps_explicit_strokes(self):
        m = Musician({"Which listed player": 73}, players="Flute, guitar, double bass")
        m.new_chart_figure = "tresillo"
        plan = _arrange(SOURCE, "Flute plays the tune, guitar plays chords between beats, with double bass.", None, m)
        self.assertEqual("offbeat", next(r for r in plan["roles"] if r["program"] == 25)["gesture"])
        bass = next(r for r in plan["roles"] if r["program"] == 43)
        self.assertEqual("tresillo", bass["gesture"])
        saved = copy.deepcopy(plan["roles"])
        m.choices["What is the listener"] = "revoice"
        changed = _arrange(SOURCE, "Give the melody to cello and keep the backing.", plan, m)
        self.assertEqual(saved, changed["roles"])
        self.assertEqual(saved, plan["roles"])

    def test_an_explicit_subdivision_or_free_texture_keeps_the_played_chart(self):
        m = Musician(players="Flute, upright bass")
        m.new_chart_figure = "marcato"
        for direction in ("Flute over a walking upright bass.", "Make this swing with flute and upright bass."):
            plan = _arrange(SOURCE, direction, None, m)
            self.assertNotIn("marcato", [r["gesture"] for r in plan["roles"]])
        plan = {"meter": 3, "roles": [{"kind": "bass", "program": 43}]}
        original = copy.deepcopy(plan)
        write_chart_figure(plan, "marcato", set())
        self.assertEqual(original, plan)
