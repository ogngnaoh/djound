"""Regressions for choosing which audio a conversational edit may change."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import rendition_worker as worker


class RenditionPlannerTest(unittest.TestCase):
    def test_an_unlisted_arrangement_uses_the_listeners_words_and_selected_take(self):
        request = "Fingerpicked guitar and cello; let the cello sing the tune."
        result = {"scope": "whole", "caption": "Chamber folk duet for guitar and cello.", "label": "Chamber folk"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")) as interpret:
            decision = worker.plan_take({"prompt": request, "parentCaption": "A string quartet"}, 40)
        context = interpret.call_args.args[0]
        self.assertEqual(request, context["listener_request"])
        self.assertEqual("A string quartet", context["current_take_description"])
        self.assertEqual("Chamber folk", decision["label"])
        self.assertEqual("local model", decision["planner"])

    def test_combined_original_melody_and_new_style_are_planned_together(self):
        request = "Closer to the original melody, but play it as an oud and cello duet"
        result = {"scope": "whole", "caption": "An oud and cello duet.", "label": "Oud and cello"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")) as interpret:
            decision = worker.plan_take({"prompt": request, "parentCaption": "Old arrangement"}, 40)
        self.assertEqual(request, interpret.call_args.args[0]["listener_request"])
        self.assertEqual("An oud and cello duet.", decision["caption"])

    def test_parent_placeholder_cannot_become_the_new_arrangement_description(self):
        request = "Let a bowed cello play the tune with fingerpicked guitar."
        result = {"scope": "whole", "caption": "The imported recording", "label": "Acoustic duet"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")):
            decision = worker.plan_take({"prompt": request}, 24)
        self.assertEqual(request, decision["caption"])

    def test_unknown_chorus_location_requires_a_selection_before_planning(self):
        with patch.object(worker, "infer_plan", side_effect=AssertionError):
            result = worker.plan_take({"prompt": "Could the strings enter later in the second chorus?"}, 35)
        self.assertEqual(result["state"], "clarify")

    def test_chorus_as_a_guitar_effect_is_a_musical_direction(self):
        prompt = "Use the chorus pedal on the guitars for a dreamy surf ballad."
        result = {"scope": "whole", "caption": "Dreamy electric guitars with chorus.", "label": "Surf ballad"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")):
            self.assertEqual("planned", worker.plan_take({"prompt": prompt}, 35)["state"])

    def test_known_selection_wins_over_whole_song_guess(self):
        result = {"scope": "whole", "caption": "Strings enter later.", "label": "Late strings"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")):
            decision = worker.plan_take({"prompt": "Change the chorus", "startSeconds": 14, "endSeconds": 19}, 35)
        self.assertEqual((14, 19), (decision["appliedStartSeconds"], decision["appliedEndSeconds"]))

    def test_number_of_seconds_in_request_controls_the_edit(self):
        result = {"scope": "ending", "caption": "A slower cadence.", "label": "Quiet ending"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")):
            decision = worker.plan_take({"prompt": "Make the last 5.5 seconds gentler"}, 60)
        self.assertEqual((54.5, 60), (decision["appliedStartSeconds"], decision["appliedEndSeconds"]))

    def test_protected_seconds_are_not_used_as_the_target(self):
        decision = worker.plan_take({"prompt": "Keep the first 12 seconds but change the ending"}, 60)
        self.assertEqual("clarify", decision["state"])

    def test_planner_cannot_edit_the_ending_it_was_asked_to_keep(self):
        result = {"scope": "ending", "caption": "A quiet ending.", "label": "Quiet ending"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")):
            decision = worker.plan_take({"prompt": "Keep the ending but soften the opening"}, 35)
        self.assertEqual("clarify", decision["state"])

    def test_original_reference_does_not_discard_a_selected_performance(self):
        with patch.object(worker, "infer_plan", side_effect=AssertionError):
            decision = worker.plan_take({"prompt": "Closer to the original melody",
                                         "startSeconds": 2, "endSeconds": 7}, 10)
        self.assertEqual("clarify", decision["state"])

    def test_reanchoring_supplies_the_original_recording_and_keeps_the_style(self):
        with tempfile.TemporaryDirectory() as temp:
            original, take, output = (Path(temp)/name for name in ("original.wav", "take.wav", "output.wav"))
            for path in (original, take):
                with wave.open(str(path), "wb") as out:
                    out.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                    out.writeframes(bytes(4 * 44100))
            def render(source, destination, caption, **options):
                self.assertEqual(source, original.resolve())
                self.assertIn("Warm piano trio", caption)
                self.assertEqual(0.8, options["cover_strength"])
                destination.write_bytes(source.read_bytes())
            with patch("rendition_audio.render_take", side_effect=render):
                result = worker.run({"prompt": "Closer to the original melody", "original": str(original),
                                     "parentCaption": "Warm piano trio.", "source": str(take), "output": str(output)})
            self.assertEqual(result["state"], "ready")
