import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave

import rendition_score_plan as planner
from rendition_score import analyze, arrange
from rendition_score_form import cut_wav, export_window, measure_outro, omit_edge, render_saved, requested_cut, requested_restore, score_window
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
        both = dict(self.plan, omittedForm=["intro", "outro"])
        self.assertEqual("outro", requested_restore("Can we bring the original outro back?", both))
        self.assertEqual("intro", requested_restore("Restore the intro.", both))
        self.assertEqual("outro", requested_restore("Bring the coda back, but leave the intro out.", both))
        self.assertIsNone(requested_restore("Bring the intro back and add a piano.", both))
        self.assertIsNone(requested_restore("Restore the intro.", self.plan))
        director = Mock(brief="A new one-bar closing cadence.")
        director.choose.return_value = 1
        with patch.object(planner, "_changes", return_value={"form"}):
            rewritten = planner._arrange({}, "Write me a new one-bar coda but leave the intro out.", both, director)
        self.assertEqual((1, 1, ["intro"]), (rewritten["introBars"], rewritten["endingBars"], rewritten["omittedForm"]))
        director.choose.assert_called_once()

    @unittest.skipUnless(score_available(), "Run local score setup for the native performer")
    def test_releasing_the_last_lead_excludes_new_band_attacks_and_keeps_historical_cuts(self):
        # This note crosses the start of the coda; two new drum strokes would
        # enter if the entire mix simply played on until the note-off.
        tracks = [{"program": 66, "level_db": -18., "notes": [(.1, 1.55, 69, 82)]},
                  {"program": 0, "percussion": True, "level_db": -19.,
                   "notes": [(.1, .2, 37, 70), (1., 1.1, 37, 70), (1.6, 1.7, 38, 88)]}]
        duration = 2.
        mapping = [[0, 0], [RATE, RATE], [2*RATE, RATE]]
        deadline = time.monotonic() + 30
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            full, lead = home / "band.wav", home / "sax.wav"
            render_score(tracks, duration, full, room=self.plan["room"], receipt=self.plan, deadline=deadline)
            render_saved(tracks, duration, self.plan, lead, deadline, lead_only=True)
            new = omit_edge(self.plan, "outro")
            measure_outro(new, tracks, mapping)
            heard = home / "new.wav"
            export_window(full, heard, new, tracks, duration, mapping, deadline)
            self.assertEqual(2*RATE, len(pcm(heard))//4)
            self.assertEqual(pcm(lead)[RATE*4:], pcm(heard)[RATE*4:])
            self.assertNotEqual(pcm(full)[RATE*4:], pcm(heard)[RATE*4:])
            self.assertEqual(pcm(full)[:(RATE-441)*4], pcm(heard)[:(RATE-441)*4])
            earlier = dict(self.plan, omittedForm=["outro"])
            measure_outro(earlier, tracks, mapping)
            self.assertNotIn("outroReleaseFrames", earlier)
            replay, expected = home / "legacy.wav", home / "reference.wav"
            export_window(full, replay, earlier, tracks, duration, mapping, deadline)
            cut_wav(full, expected, 0, RATE)
            self.assertEqual(expected.read_bytes(), replay.read_bytes())

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
                body = source_map[-2][0]
                self.assertEqual(original[(left+fade)*4:(body-fade)*4], cropped[fade*4:(body-left-fade)*4])
                self.assertEqual([[0, 0], [body-left, notes["frames"]], [right-left, notes["frames"]]], heard_map)
                # Restoring either edge leaves the other omission in place. The
                # saved full score, including its gains, still belongs to the take.
                for edge, remaining in (("intro", "outro"), ("outro", "intro")):
                    restored = work / ("restored-" + edge + ".wav")
                    revived = perform_score(current, upload, restored, work, direction="Bring it back.",
                                            caption="", parent_caption="", parent_plan=plan, parent_map=heard_map,
                                            recover_original=False, restore_score_edge=edge)
                    self.assertEqual([remaining], revived["arrangementPlan"]["omittedForm"])
                    if edge == "intro":
                        self.assertEqual(pcm(work / "outro.wav"), pcm(restored))
                    else:
                        self.assertEqual(original[(left+fade)*4:], pcm(restored)[fade*4:])
                    (work / "full-form.wav").unlink()
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
                kept = work / "heard-replay.wav"
                export_window(replay, kept, saved, parts, seconds, mapping, time.monotonic()+30)
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
