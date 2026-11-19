"""A follow-up should earn its own take by changing the performance a listener hears."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rendition_performance import perform_score
from rendition_score import analyze, arrange
import rendition_score_plan as planner


class ScoreRevisionTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.output = self.root / "revision.wav"
        melody = [(0.1, 0.5, 63, 0.9), (0.7, 1.1, 66, 0.8), (1.3, 1.9, 70, 0.9)]
        self.notes = {"frames": 88200, "candidates": melody, "melody": melody}
        self.parent = {"version": 1, "fromUpload": True, "realization": "score", "bpm": 100,
                       "meter": 4, "subdivision": "straight", "room": 0.44, "harmony": "triads",
                       "phrasing": "flowing", "phraseDynamics": "steady", "leadProgram": 0,
                       "introBars": 0, "endingBars": 0, "themeAnchors": [0], "roles": []}

    def perform(self, candidates):
        def transcribe(source, target, **kwargs):
            target.write_text(json.dumps(self.notes))
            return True

        def write_performance(tracks, seconds, target, **kwargs):
            target.write_bytes(b"rendered performance")
            return 1.0

        with (patch("rendition_performance.guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording", side_effect=transcribe) as transcriber,
              patch("rendition_score_plan.plan_arrangement", side_effect=deepcopy(candidates)) as planner,
              patch("rendition_score_render.render_score", side_effect=write_performance) as player):
            result = perform_score(self.root / "previous.wav", self.root / "upload.wav", self.output, self.root,
                                   direction="Make a soundtrack I can study to.", caption="A study soundtrack.",
                                   parent_caption="A piano performance.", parent_plan=deepcopy(self.parent),
                                   parent_map=None, recover_original=False)
        transcriber.assert_called_once()
        reconsidered = [call.kwargs.get("reconsider", False) for call in planner.call_args_list]
        return result, reconsidered, player

    def test_reconsiders_an_inaudible_change_before_rendering(self):
        # A solo has no backing to perform extra intro bars. Even a changed
        # score field and new rehearsal cue can sound exactly like the parent.
        initial = dict(self.parent, introBars=2, conductorCue="Plenty of space.")
        revision = dict(self.parent, leadTouch={"velocity": [70, 60, 50], "lengthPercent": 88, "endOffsetMs": 10})
        analysis = analyze(self.notes)
        self.assertEqual(arrange(self.notes, analysis, initial), arrange(self.notes, analysis, self.parent))
        expected, _, _ = arrange(self.notes, analysis, revision)
        result, tries, player = self.perform([initial, revision])
        self.assertEqual(tries, [False, True])
        player.assert_called_once()
        self.assertEqual(expected, player.call_args.args[0])
        self.assertEqual(revision["leadTouch"], result["arrangementPlan"]["leadTouch"])
        self.assertTrue(self.output.is_file())

    def test_an_unresolved_retry_leaves_the_parent_as_the_only_performance(self):
        first = dict(self.parent, conductorCue="Focus on the melody.")
        for second in (None, dict(self.parent, conductorCue="Keep a steady pulse.")):
            with self.subTest(second=second):
                result, tries, player = self.perform([first, second])
                self.assertEqual(tries, [False, True])
                self.assertEqual("clarify", result["state"])
                self.assertTrue(result["error"].strip())
                player.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_a_new_player_or_real_acoustic_change_can_proceed_on_the_first_plan(self):
        for change in ({"leadProgram": 42}, {"room": 0.18}):
            with self.subTest(change=change):
                candidate = dict(self.parent, **change)
                result, tries, player = self.perform([candidate])
                self.assertEqual(tries, [False])
                player.assert_called_once()
                self.assertEqual(candidate["room"], player.call_args.kwargs["room"])
                self.assertEqual(candidate["leadProgram"], player.call_args.args[0][0]["program"])
                self.assertTrue(self.output.is_file())
                self.output.unlink()

    def test_director_reconsiders_with_the_freedom_given_in_the_request(self):
        analysis = analyze(self.notes)
        analysis["parentPlan"] = self.parent
        for decision, words in (("interpret", "Something to play while I paint."),
                                ("targeted", "Keep that melody and soften the attack."),
                                ("satisfied", "Keep the tune on piano.")):
            with (self.subTest(decision=decision), patch.object(planner, "_Director") as director,
                  patch.object(planner, "_arrange", return_value={}) as arrange_chart):
                director.return_value.brief = "Studio direction."
                director.return_value.identity = "test"
                director.return_value.context = ""
                director.return_value.choose.side_effect = (
                    ["interpret"] if decision == "interpret" else ["targeted", "satisfied" if decision == "satisfied" else "change"]
                )
                result = planner._run({"analysis": analysis, "parentCaption": "Solo piano", "direction": words,
                                       "reconsider": True})
                self.assertEqual(words, director.call_args.args[0]["listener"])
                self.assertNotIn("currentScore", director.call_args.args[0])
                self.assertNotIn("currentTake", director.call_args.args[0])
                self.assertEqual("Selected take" in director.return_value.context, decision != "interpret")
                if decision == "satisfied":
                    self.assertIsNone(result)
                    arrange_chart.assert_not_called()
                else:
                    source, requested, prior, _ = arrange_chart.call_args.args
                    self.assertIs(source, analysis)
                    self.assertEqual(words, requested)
                    self.assertEqual(self.parent if decision == "targeted" else None, prior)


if __name__ == "__main__":
    unittest.main()
