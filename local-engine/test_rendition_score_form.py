import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import rendition_score_plan as planner
from rendition_score import analyze, arrange
from rendition_score_form import cut_wav, omit_edge, requested_cut, score_window
from rendition_score_render import RATE, render_score, score_available
from rendition_performance import perform_score
from rendition_worker import plan_take


def pcm(path):
    with wave.open(str(path)) as wav:
        return wav.readframes(wav.getnframes())


class ScoreFormTest(unittest.TestCase):
    def setUp(self):
        self.plan = {"version": 1, "fromUpload": True, "realization": "score", "bpm": 105,
                     "meter": 4, "subdivision": "straight", "room": .44, "harmony": "triads",
                     "phrasing": "flowing", "phraseDynamics": "arch", "leadProgram": 66,
                     "introBars": 1, "endingBars": 2, "themeAnchors": [0], "roles": [
                         {"kind": "chords", "program": 25, "gesture": "offbeat",
                          "pattern": planner._pattern("chords", "offbeat", 4)}]}
        self.prompt = ("I want this to end with the sax. Can we lose that extra bit where the band carries on "
                       "at the end? I love the beginning and everything leading up to it.")

    def test_removing_an_added_section_has_scope_distinct_from_changing_its_sound(self):
        req = {"supportsArrangedScore": True, "prompt": self.prompt, "parentPlan": self.plan}
        self.assertEqual("outro", plan_take(req, 26)["omitScoreEdge"])
        self.assertEqual("intro", requested_cut("Please skip the intro. Keep everything as it is.", self.plan))
        self.assertEqual("outro", requested_cut("Drop the coda and keep everything else unchanged.", self.plan))
        for words in ("Make the ending gentler.", "Remove the guitar from the outro.", "Trim the last five seconds.",
                      "Drop the coda and warm up the horn.", "End with a cello. Remove the outro.",
                      "Keep the ending unchanged. Cut the outro.", "Don’t remove the outro."):
            self.assertIsNone(requested_cut(words, self.plan), words)
        with self.assertRaises(planner.ArrangementClarification):
            omit_edge(omit_edge(self.plan, "outro"), "outro")
        with self.assertRaises(planner.ArrangementClarification):
            omit_edge(dict(self.plan, introBars=0), "intro")

    @unittest.skipUnless(score_available(), "Run local score setup for the native performer")
    def test_trimming_both_ends_keeps_pcm_and_later_edits_replay_on_the_shortened_clock(self):
        melody = [(0., .4, 65, .8), (.8, 1.4, 71, .7), (2., 2.8, 69, .9)]
        notes = {"frames": 3 * RATE, "melody": melody, "candidates": melody}
        analysis = analyze(notes)
        tracks, secs, source_map = arrange(notes, analysis, self.plan)
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            base, upload = work / "band.wav", work / "upload.wav"
            upload.touch()
            render_score(tracks, secs, base, deadline=time.monotonic() + 30, room=self.plan["room"], receipt=self.plan)
            current, plan = base, self.plan

            def transcribe(_, out, **kwargs):
                out.write_text(json.dumps(notes))
                return True

            with (patch("rendition_performance.guide_available", return_value=True),
                  patch("rendition_melody.transcribe_recording", side_effect=transcribe)):
                for edge in ("outro", "intro"):
                    child = work / (edge + ".wav")
                    result = perform_score(current, upload, child, work, direction="Remove an authored section.",
                                           caption="", parent_caption="", parent_plan=plan, parent_map=source_map,
                                           recover_original=False, omit_score_edge=edge)
                    parent_frames, child_frames = len(pcm(current)) // 4, len(pcm(child)) // 4
                    self.assertLess(child_frames, parent_frames)
                    self.assertEqual([child_frames, parent_frames], result["parentTimeMap"][-1])
                    plan, current = result["arrangementPlan"], child
                left, right, heard_map = score_window(plan, source_map)
                original, cropped = pcm(base), pcm(current)
                fade = RATE // 100
                self.assertEqual(original[(left + fade)*4:(right - fade)*4], cropped[fade*4:-fade*4])
                self.assertEqual([[0, 0], [right-left, notes["frames"]]], heard_map)
                with patch("rendition_score_plan.plan_arrangement", return_value=dict(plan, leadProgram=40)):
                    final = work / "violin.wav"
                    result = perform_score(current, upload, final, work, direction="Give the tune to violin.",
                                           caption="", parent_caption="", parent_plan=plan, parent_map=heard_map,
                                           recover_original=False)
                self.assertEqual([[0, 0], [right-left, right-left]], result["parentTimeMap"])
                saved = result["arrangementPlan"]
                replay = work / "full-replay.wav"
                parts, seconds, mapping = arrange(notes, analysis, saved)
                self.assertEqual(tracks[1:], parts[1:])
                render_score(parts, seconds, replay, deadline=time.monotonic()+30, room=saved["room"],
                             master_gain=saved["scoreMasterGain"], part_gains=saved["scorePartGains"])
                lo, hi, _ = score_window(saved, mapping)
                kept = work / "heard-replay.wav"
                cut_wav(replay, kept, lo, hi)
                self.assertEqual(kept.read_bytes(), final.read_bytes())
                self.assertEqual(len(pcm(current)), len(pcm(final)))
                self.assertNotEqual(pcm(current), pcm(final))

                # A later localized edit lives on the shortened clock too. Keep
                # the actual saved passage while another player takes the tune.
                changed = work / "edited-phrase.wav"
                recorded = bytearray(pcm(current))
                begin, end = RATE, 2 * RATE
                recorded[begin*4:end*4] = bytes((end-begin)*4)
                with wave.open(str(current), "rb") as old, wave.open(str(changed), "wb") as out:
                    out.setparams(old.getparams())
                    out.writeframes(recorded)
                ancestor = {"source": str(current), "scoreSource": str(upload), "plan": plan,
                            "editedFrames": [[begin, end]], "frames": right - left}
                with patch("rendition_score_plan.plan_arrangement", return_value=dict(plan, leadProgram=41)):
                    retained = work / "kept-phrase.wav"
                    result = perform_score(changed, upload, retained, work, direction="Keep the phrase and use viola.",
                                           caption="", parent_caption="", parent_plan=None, parent_map=heard_map,
                                           recover_original=False, scored_ancestor=ancestor, passage_policy="keep")
                self.assertEqual([[0, 0], [right-left, right-left]], result["parentTimeMap"])
                self.assertEqual(len(recorded), len(pcm(retained)))
                self.assertEqual(bytes((end-begin-2*fade)*4), pcm(retained)[(begin+fade)*4:(end-fade)*4])
