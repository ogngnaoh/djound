"""Regressions for choosing which audio a conversational edit may change."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import rendition_worker as worker


class RenditionPlannerTest(unittest.TestCase):
    def test_whole_revision_of_an_edited_performance_has_a_passage_choice(self):
        base = {"prompt": "Give the tune to cello.", "scoredAncestor": {"frames": 44100}}
        plan = {"scope": "whole", "caption": "Cello melody", "label": "Cello"}
        with patch.object(worker, "infer_plan", return_value=(plan, "local model")) as interpret:
            self.assertEqual("clarify", worker.plan_take(base, 1)["state"])
            self.assertNotIn("previously_edited_passages", interpret.call_args.args[0])
            for suffix, policy in worker.PASSAGE_CHOICES.items():
                result = worker.plan_take({**base, "prompt": base["prompt"] + " " + suffix}, 1)
                self.assertEqual(policy, result["passagePolicy"])
                self.assertIsNone(result["appliedStartSeconds"])
            selected = worker.plan_take({**base, "startSeconds": .1, "endSeconds": .5}, 1)
            self.assertEqual("planned", selected["state"])
            self.assertNotIn("passagePolicy", selected)
        natural = "Give the tune to cello throughout, including the ending."
        with patch.object(worker, "infer_plan", return_value=({**plan, "previousPassages": "develop"}, "model")) as interpret:
            self.assertEqual("develop", worker.plan_take({**base, "prompt": natural}, 1)["passagePolicy"])
            self.assertTrue(interpret.call_args.args[0]["previously_edited_passages"])

    def test_only_complete_musical_plans_skip_the_scope_fallback(self):
        valid = {"scope": "whole", "caption": "A cello carries the tune.", "label": "Cello"}
        self.assertTrue(worker.usable_plan(valid))
        for field, value in (("scope", "cello"), ("caption", ""), ("caption", 44), ("label", "")):
            self.assertFalse(worker.usable_plan({**valid, field: value}))

    def test_broad_requests_leave_the_ensemble_open(self):
        for prompt in ("make it jazz", "make this a piano version", "give it a rainy evening feel"):
            with self.subTest(prompt=prompt):
                plan = {"scope": "whole", "caption": prompt, "label": "Another interpretation"}
                with patch.object(worker, "infer_plan", return_value=(plan, "language model")) as infer:
                    result = worker.plan_take({"prompt": prompt}, 24)
                self.assertEqual(infer.call_args.args[0]["listener_request"], prompt)
                self.assertEqual(result["planner"], "language model")

    def test_an_unlisted_arrangement_uses_the_listeners_words_and_selected_take(self):
        request = "Fingerpicked guitar and cello; let the cello sing the tune."
        result = {"scope": "whole", "caption": "Chamber folk duet for guitar and cello.", "label": "Chamber folk", "reference": "parent"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")) as interpret:
            decision = worker.plan_take({"prompt": request, "parentCaption": "A string quartet"}, 40)
        context = interpret.call_args.args[0]
        self.assertEqual(request, context["listener_request"])
        self.assertEqual("A string quartet", context["current_take_description"])
        self.assertEqual("Chamber folk", decision["label"])
        self.assertEqual("local model", decision["planner"])

    def test_combined_original_melody_and_new_style_are_planned_together(self):
        request = "Closer to the original melody, but play it as an oud and cello duet"
        result = {"scope": "whole", "caption": "An oud and cello duet.", "label": "Oud and cello", "reference": "original"}
        with patch.object(worker, "infer_plan", return_value=(result, "local model")) as interpret:
            decision = worker.plan_take({"prompt": request, "parentCaption": "Old arrangement"}, 40)
        self.assertEqual(request, interpret.call_args.args[0]["listener_request"])
        self.assertEqual("An oud and cello duet.", decision["caption"])
        self.assertEqual("original", decision["melodyReference"])
        self.assertFalse(decision["keepEnsemble"])

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

    def test_selected_original_reference_keeps_the_selected_time_bounds(self):
        with patch.object(worker, "infer_plan", side_effect=AssertionError):
            decision = worker.plan_take({"prompt": "Closer to the original melody",
                                         "startSeconds": 2, "endSeconds": 7}, 10)
        self.assertEqual("planned", decision["state"])
        self.assertEqual((2, 7, "original"),
                         (decision["appliedStartSeconds"], decision["appliedEndSeconds"], decision["melodyReference"]))

    def test_a_request_for_an_original_chorus_needs_a_range_before_render(self):
        with patch.object(worker, "infer_plan", side_effect=AssertionError):
            result = worker.plan_take({"prompt": "I miss the way the original chorus begins."}, 31)
        self.assertEqual("clarify", result["state"])

    def test_a_paraphrased_reference_uses_both_the_parent_and_the_selected_range(self):
        request = "That bit I hummed in my voice memo belongs here. Keep the duo."
        reply = {"scope":"selection", "caption":"A cello leads the duo.", "label":"The old tune",
                 "reference":"original", "keep_ensemble":True}
        with patch.object(worker, "infer_plan", return_value=(reply, "local model")):
            decision = worker.plan_take({"prompt":request, "parentCaption":"A cello duo.",
                                         "startSeconds":3, "endSeconds":8}, 31)
        self.assertEqual("original", decision["melodyReference"])
        self.assertTrue(decision["keepEnsemble"])
        self.assertEqual((3, 8), (decision["appliedStartSeconds"], decision["appliedEndSeconds"]))

    def test_mentioning_the_original_can_explicitly_leave_it_out(self):
        reply = {"scope":"ending", "caption":"A softer take.", "label":"Soft ending", "reference":"parent"}
        with patch.object(worker, "infer_plan", return_value=(reply, "local model")):
            decision = worker.plan_take({"prompt":"Don't use the original. Soften the ending.",
                                         "parentCaption":"A cello duo."}, 31)
        self.assertEqual("parent", decision["melodyReference"])

    def test_reanchoring_supplies_the_original_recording_and_keeps_the_style(self):
        with tempfile.TemporaryDirectory() as temp:
            original, take, output = (Path(temp)/name for name in ("original.wav", "take.wav", "output.wav"))
            for path in (original, take):
                with wave.open(str(path), "wb") as out:
                    out.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                    out.writeframes(bytes(4 * 44100))
            def render(source, destination, caption, **options):
                self.assertEqual(source, take.resolve())
                self.assertEqual(original.resolve(), options["reference"])
                self.assertIn("Warm piano trio", caption)
                self.assertEqual(0.8, options["cover_strength"])
                destination.write_bytes(source.read_bytes())
            with (patch("rendition_performance.guide_available", return_value=False),
                  patch("rendition_audio.render_take", side_effect=render)):
                result = worker.run({"prompt": "Closer to the original melody", "original": str(original),
                                     "parentCaption": "Warm piano trio.", "source": str(take), "output": str(output)})
            self.assertEqual(result["state"], "ready")
