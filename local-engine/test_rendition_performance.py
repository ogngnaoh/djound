"""Check the provenance of the music heard on first takes and revisions."""
from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

import rendition_performance as music


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.parent = self.root / "parent.wav"
        self.destination = self.root / "take.wav"
        self.write(self.parent, 17, 2)

    def write(self, path, amplitude, seconds):
        frame = amplitude.to_bytes(2, "little", signed=True) * 2
        with wave.open(str(path), "wb") as wav:
            wav.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            wav.writeframes(frame * int(seconds * 44100))

    def perform(self, **changes):
        fields = dict(description="A cello duet", prompt="Have the cello play the tune",
                      duration=2)
        music.perform_take(self.parent, self.destination, "cello duet", **{**fields, **changes})

    def test_melody_source_choice_is_pinned_only_for_the_same_recording(self):
        original = self.root / "upload.wav"
        self.write(original, 8, 2)
        with (patch.object(music, "guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording", return_value=False) as transcribe):
            for from_upload, recover, basis, pin in ((True, False, original, "separated_voice"),
                                                      (False, False, self.parent, None),
                                                      (False, True, original, None)):
                old = {"fromUpload": from_upload, "melodyTraceVersion": 3, "melodyTraceSource": "separated_voice"}
                music.perform_score(self.parent, original, self.destination, self.root, direction="Piano",
                                    caption="Piano", parent_caption="A song", parent_plan=old,
                                    parent_map=None, recover_original=recover)
                self.assertEqual(basis, transcribe.call_args.args[0])
                self.assertEqual(3, transcribe.call_args.kwargs["trace_version"])
                self.assertEqual(pin, transcribe.call_args.kwargs["trace_source"])

    def test_upload_based_score_requires_its_saved_melody_on_a_direct_revision(self):
        import rendition_worker

        original = self.root / "upload.wav"
        self.write(original, 12, 2)
        plan = {"version": 1, "fromUpload": True, "realization": "score",
                "melodyTraceVersion": 6, "melodyTraceSource": "instrument_tracks"}
        request = {"source": str(self.parent), "original": str(original), "output": str(self.destination),
                   "supportsArrangedScore": True, "parentCaption": "Saved trio", "parentPlan": plan,
                   "prompt": "Solo piano"}
        with (patch.object(music, "perform_take") as fallback,
              patch("rendition_score_render.score_available", return_value=True),
              patch.object(music, "guide_available", return_value=False)):
            with self.assertRaisesRegex(RuntimeError, "rendition setup"):
                rendition_worker.run(request)
            fallback.assert_not_called()
        with (patch.object(music, "perform_take") as fallback,
              patch.object(music, "guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording", return_value=False) as transcribe):
            result = rendition_worker.run(request)
            self.assertEqual("clarify", result["state"])
            self.assertIn("fresh interpretation", result["error"])
            self.assertEqual(original.resolve(), transcribe.call_args.args[0])
            self.assertEqual("instrument_tracks", transcribe.call_args.kwargs["trace_source"])
            fallback.assert_not_called()
        with patch.object(music, "perform_score", return_value={"state": "clarify"}) as perform:
            rendition_worker.run({**request, "prompt": "Start fresh with solo piano"})
            self.assertIsNone(perform.call_args.kwargs["parent_plan"])
            self.assertEqual("fresh", perform.call_args.kwargs["passage_policy"])
        self.assertFalse(self.destination.exists())

    def test_retained_score_uses_its_verified_source_and_original_trace_version(self):
        upload = self.root / "upload.wav"
        earlier = self.root / "earlier.wav"
        self.write(upload, 21, 2)
        self.write(earlier, 31, 2)
        for basis, restore, plan, version, analyzer in (
            (upload, True, {"melodyTraceVersion": 3, "melodyTraceSource": "separated_voice"}, 3, "separated_voice"),
            (earlier, False, {"melodyTraceVersion": 3, "melodyTraceSource": "mix"}, 3, "mix"),
            (upload, True, {}, 1, None),
            (earlier, False, {"melodyTraceVersion": 2}, 2, None),
        ):
            ancestor = {"plan": {**plan, "fromUpload": basis == upload}, "scoreSource": str(basis)}
            with (self.subTest(basis=basis, version=version),
                  patch.object(music, "guide_available", return_value=True),
                  patch("rendition_score_render.score_available", return_value=True),
                  patch("rendition_melody.transcribe_recording", return_value=False) as transcribe):
                music.perform_score(self.parent, upload, self.destination, self.root, direction="Piano",
                                    caption="Piano", parent_caption="A song", parent_plan=None,
                                    parent_map=None, recover_original=restore, scored_ancestor=ancestor,
                                    passage_policy="develop")
                self.assertEqual(basis.resolve(), transcribe.call_args.args[0])
                self.assertEqual(version, transcribe.call_args.kwargs["trace_version"])
                self.assertEqual(analyzer, transcribe.call_args.kwargs["trace_source"])

    def test_direct_score_can_start_fresh_when_its_old_recording_is_missing(self):
        import rendition_worker

        upload = self.root / "upload.wav"
        self.write(upload, 21, 2)
        request = {"source": str(self.parent), "original": str(upload), "output": str(self.destination),
                   "supportsArrangedScore": True, "parentCaption": "Piano",
                   "parentPlan": {"fromUpload": False, "melodyTraceSource": "mix"},
                   "parentScoreInput": {"id": "earlier", "source": None}}
        with (patch.object(music, "guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording") as transcribe):
            result = rendition_worker.run({**request, "prompt": "Solo piano"})
            self.assertEqual("clarify", result["state"])
            self.assertIn("fresh interpretation", result["error"])
            transcribe.assert_not_called()
        with patch.object(music, "perform_score", return_value={"state": "clarify"}) as perform:
            for words, original in (("Start fresh with solo piano", False), ("Solo piano and start fresh", False),
                                    ("Bring back the original melody and make a fresh interpretation of the whole take.", True)):
                rendition_worker.run({**request, "prompt": words})
                self.assertIsNone(perform.call_args.kwargs["parent_plan"])
                self.assertIsNone(perform.call_args.kwargs["parent_score_input"])
                self.assertEqual(original, perform.call_args.kwargs["recover_original"])

    def test_original_melody_conflict_and_fresh_retry_go_to_the_requested_recording(self):
        import rendition_worker
        from rendition_melody import MELODY_TRACE_VERSION

        upload, earlier = self.root / "upload.wav", self.root / "earlier.wav"
        self.write(upload, 21, 2)
        self.write(earlier, 31, 2)
        request = {"source": str(self.parent), "original": str(upload), "supportsArrangedScore": True,
                   "parentCaption": "An instrumental take.", "scoredAncestor": {
                       "scoreSource": str(earlier), "plan": {"fromUpload": False, "melodyTraceVersion": 2},
                       "frames": 88200, "editedFrames": [[66150, 88200]]}}
        with (patch.object(music, "guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording") as transcribe):
            for suffix in ("", " and keep my edited passage exactly"):
                result = rendition_worker.run({**request, "output": str(self.destination),
                                               "prompt": "Bring back the original melody" + suffix})
                self.assertEqual("clarify", result["state"])
                self.assertIn("fresh interpretation", result["error"])
                self.assertFalse(self.destination.exists())
            transcribe.assert_not_called()

        def transcribe(source, path, **options):
            self.assertEqual(MELODY_TRACE_VERSION, options["trace_version"])
            self.assertIsNone(options["trace_source"])
            path.write_text(json.dumps({"frames": 88200, "melodySource": "single", "melody": [(0, .3, 60, .8)]}))
            return True

        def render(tracks, seconds, output, **options):
            self.write(output, 41, seconds)

        tracks = [{"program": 0, "notes": [(0, .3, 60, 80)]}]
        plans = [{"realization": "score", "bpm": 120, "phrasing": "flowing"} for _ in range(2)]
        with (patch.object(music, "guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording", side_effect=transcribe) as extractor,
              patch("rendition_score_plan.plan_arrangement", side_effect=plans),
              patch("rendition_score.arrange", return_value=(tracks, 2, [[0, 0], [88200, 88200]])),
              patch("rendition_score_render.render_score", side_effect=render)):
            for direction, basis in (("Bring back the original melody", upload), ("Solo piano", self.parent)):
                output = self.root / (basis.stem + "-fresh.wav")
                result = rendition_worker.run({**request, "output": str(output),
                                               "prompt": direction + " and make a fresh interpretation of the whole take."})
                self.assertEqual("ready", result["state"])
                self.assertEqual(basis.resolve(), extractor.call_args.args[0])
                self.assertEqual(basis == upload, result["arrangementPlan"]["fromUpload"])
                self.assertNotIn("retainedPassageFrames", result["arrangementPlan"])
                self.assertTrue(output.is_file())

    def test_finished_score_caption_names_only_sounding_players_and_their_performed_direction(self):
        import rendition_worker

        request = {"source": str(self.parent), "original": str(self.parent), "output": str(self.destination),
                   "prompt": "I like that beat. Bring back the wistful feeling.", "supportsArrangedScore": True}
        proposal = {"scope": "whole", "caption": "Add pads and an echoing piano.", "label": "Wistful pulse"}
        plan = {"realization": "score", "bpm": 128, "leadProgram": 81, "phrasing": "flowing",
                "leadTouch": {"velocity": [80, 88, 72], "lengthPercent": 105, "endOffsetMs": 15}}
        sounding = [(0., .3, 60, 80)]
        tracks = [{"program": 81, "notes": sounding}, {"program": 38, "notes": sounding},
                  {"program": 24, "percussion": True, "notes": sounding}, {"program": 0, "notes": []}]
        def transcribe(source, path, **kwargs):
            path.write_text(json.dumps({"frames": 88200, "melody": sounding}))
            return True
        def render(parts, duration, output, **kwargs):
            self.assertEqual(tracks, parts)
            self.write(output, 42, duration)
        with (patch.object(rendition_worker, "infer_plan", return_value=(proposal, "local model")),
              patch.object(music, "guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording", side_effect=transcribe),
              patch("rendition_score_plan.plan_arrangement", return_value=plan),
              patch("rendition_score.arrange", return_value=(tracks, 2, [[0, 0], [88200, 88200]])),
              patch("rendition_score_render.render_score", side_effect=render)):
            result = rendition_worker.run(request)
        self.assertEqual("ready", result["state"])
        self.assertEqual("Sawtooth synth carries the tune with synth bass and electronic drums at 128 BPM. "
                         "The lead part calls for connected notes and a softer touch at phrase endings.", result["caption"])
        produced = music._score_caption(dict(plan, realization="produced"), tracks)
        self.assertIn("arrangement used for the generated audio", produced)
        self.assertNotIn("piano", produced)

    def test_saved_passage_details_append_to_the_confirmed_score_description(self):
        import rendition_worker

        for choice, description in (("keep", "Kept the edited ending exactly."),
                                    ("develop", "Applied the new direction to the edited ending as well.")):
            output = self.root / (choice + ".wav")
            request = {"source": str(self.parent), "original": str(self.parent), "output": str(output),
                       "prompt": "Give the tune to a cello.", "supportsArrangedScore": True, "passagePolicy": choice,
                       "scoredAncestor": {"frames": 88200, "editedFrames": [[44100, 88200]]}}
            def complete(source, original, destination, work, **kwargs):
                self.write(destination, 21, 2)
                return {"caption": "Cello carries the tune at 128 BPM."}
            with patch.object(music, "perform_score", side_effect=complete):
                result = rendition_worker.run(request)
            self.assertEqual("Cello carries the tune at 128 BPM. " + description, result["caption"])

    def test_ensemble_refines_the_guide_with_accompaniment_from_the_draft(self):
        def guide(source, output, *, program, deadline, preserve_register):
            self.assertEqual(self.parent, source)
            self.assertEqual(42, program)
            self.write(output, 23, 2)
            return True

        calls = []
        def perform(source, output, caption, **options):
            calls.append((source, options))
            self.write(output, 38 if len(calls) == 1 else 42, 2)

        def sketch(line, backing, output, **options):
            self.assertEqual("melody.wav", line.name)
            self.assertEqual("performance.wav", backing.name)
            shutil.copy(line, output)
            return nullcontext("separated backing")

        def restore(backing, refined, output, *, cache, deadline):
            self.assertEqual(calls[1][1]["timbre_reference"], backing)
            self.assertEqual(cache, "separated backing")
            shutil.copy(refined, output)

        with (patch.object(music, "guide_available", return_value=True),
              patch.object(music, "build_melody_guide", side_effect=guide),
              patch("rendition_voicing.choose_voicing", return_value=(42, False)),
              patch.object(music.audio, "part_available", side_effect=AssertionError("Unnamed accompaniment")),
              patch("rendition_sketch.backing_available", return_value=True),
              patch("rendition_sketch.build_arrangement_sketch", side_effect=sketch),
              patch("rendition_sketch.restore_backing", side_effect=restore),
              patch.object(music.audio, "render_take", side_effect=perform)):
            self.perform()
        self.assertEqual(self.parent, calls[0][0])
        self.assertEqual("sketch.wav", calls[1][0].name)
        self.assertEqual(0.2, calls[1][1]["cover_noise_strength"])
        self.assertEqual(calls[0][1]["deadline"], calls[1][1]["deadline"])
        self.assertTrue(self.destination.is_file())

    def test_explicit_duet_conditions_one_backing_part_on_the_performed_lead(self):
        direction = "A chamber folk duet with fingerpicked guitar and bowed cello. Give the cello the main tune."
        def guide(source, output, **kwargs):
            self.write(output, 21, 2)
            return True

        def render(line, output, caption, **kwargs):
            self.assertEqual("melody.wav", line.name)
            self.assertEqual(line, kwargs["timbre_reference"])
            self.assertIn("Cello", caption)
            self.write(output, 25, 2)

        def retain(line, refined, output, **kwargs):
            shutil.copy(refined, output)

        def accompaniment(lead, output, *, lane, caption, deadline):
            self.assertEqual("lead.wav", lead.name)
            self.assertEqual("guitar", lane)
            self.assertIn(direction, caption)
            self.write(output, 18, 2)

        def mix(lead, part, output, **kwargs):
            self.assertEqual(("lead.wav", "accompaniment.wav"), (lead.name, part.name))
            self.write(output, 37, 2)

        with (patch.object(music, "guide_available", return_value=True),
              patch.object(music, "build_melody_guide", side_effect=guide),
              patch("rendition_voicing.choose_voicing", return_value=(42, False)),
              patch.object(music.audio, "part_available", return_value=True),
              patch("rendition_sketch.backing_available", side_effect=AssertionError("Duet requested")),
              patch.object(music.audio, "render_take", side_effect=render) as lead,
              patch("rendition_lead.retain_requested_lead", side_effect=retain),
              patch.object(music.audio, "generate_part", side_effect=accompaniment) as part,
              patch("rendition_sketch.mix_performers", side_effect=mix) as mixed):
            self.perform(prompt=direction)
        lead.assert_called_once()
        part.assert_called_once()
        mixed.assert_called_once()
        self.assertEqual(lead.call_args.kwargs["deadline"], part.call_args.kwargs["deadline"])
        self.assertEqual(part.call_args.kwargs["deadline"], mixed.call_args.kwargs["deadline"])
        with wave.open(str(self.destination)) as wav:
            self.assertEqual(2 * 44100, wav.getnframes())
            self.assertEqual(37, int.from_bytes(wav.readframes(1)[:2], "little"))

    def test_missing_base_and_parent_ensemble_retention_do_not_call_part_generation(self):
        direction = "Bowed cello plays the tune over acoustic guitar."
        for keep in (False, True):
            with (self.subTest(keep=keep),
                  patch.object(music, "guide_available", return_value=True),
                  patch("rendition_voicing.choose_voicing", return_value=(42, False)),
                  patch.object(music.audio, "part_available", return_value=False) as available,
                  patch("rendition_sketch.backing_available", return_value=False) as backing,
                  patch.object(music.audio, "generate_part") as part,
                  patch.object(music.audio, "render_take") as cover):
                self.perform(prompt=direction, keep_ensemble=keep)
                self.assertEqual(0 if keep else 1, available.call_count)
                backing.assert_called_once()
                cover.assert_called_once()
                part.assert_not_called()

    def test_no_reliable_line_uses_the_requested_performance_once(self):
        with (patch.object(music, "guide_available", return_value=True),
              patch.object(music, "build_melody_guide", return_value=False),
              patch("rendition_voicing.choose_voicing", return_value=(0, True)),
              patch.object(music.audio, "render_take") as perform):
            self.perform()
        self.assertEqual(1, perform.call_count)
        self.assertEqual((self.parent, self.destination, "cello duet"), perform.call_args.args)
        self.assertNotIn("cover_noise_strength", perform.call_args.kwargs)

    def test_instrument_change_reuses_the_edited_passage_for_its_tune_and_rhythm(self):
        from unittest.mock import Mock
        import time

        def revoice(melody, guide, *, program, deadline, preserve_register):
            self.assertEqual((self.parent, 42), (melody, program))
            self.write(guide, 50, 2)
            return True

        backing = Mock()
        def accompany(guide, performance, output, *, deadline):
            self.assertEqual(("melody.wav", self.parent), (guide.name, performance))
            self.write(output, 62, 2)
            return backing

        with (patch.object(music, "build_melody_guide", side_effect=revoice),
              patch("rendition_sketch.backing_available", return_value=True),
              patch("rendition_sketch.build_arrangement_sketch", side_effect=accompany),
              patch.object(music.audio, "render_take", side_effect=AssertionError("Regenerated the rhythm section"))):
            made = music._perform_with_guide(self.parent, self.parent, self.destination, self.root,
                                             "Cello lead", "Cello lead", "Give it to the cello",
                                             "Give it to the cello", True, 1., 2., time.monotonic() + 5,
                                             voicing=(42, False), revoice_only=True)
        self.assertTrue(made)
        backing.close.assert_called_once()
        # The constant-valued fixture is room tone; a new attack here would be audible.
        with wave.open(str(self.destination)) as result, wave.open(str(self.parent)) as source:
            self.assertEqual(source.readframes(88200), result.readframes(88200))

    def test_local_melody_recovery_uses_an_upload_excerpt_and_keeps_parent_around_it(self):
        original = self.root / "original.wav"
        self.write(self.parent, 10, 70)
        self.write(original, 90, 70)
        def guide(source, output, **options):
            with wave.open(str(source)) as wav:
                self.assertLess(wav.getnframes(), 70 * 44100)
                self.assertEqual(90, int.from_bytes(wav.readframes(1)[:2], "little"))
            shutil.copy(source, output)
            return True

        def cover(line, output, caption, **options):
            with wave.open(str(options["timbre_reference"])) as timbre:
                self.assertEqual(10, int.from_bytes(timbre.readframes(1)[:2], "little"))
            shutil.copy(line, output)

        with (patch.object(music, "guide_available", return_value=True),
              patch.object(music, "build_melody_guide", side_effect=guide),
              patch("rendition_voicing.choose_voicing", return_value=(0, True)),
              patch.object(music.audio, "render_take", side_effect=cover) as perform):
            self.perform(duration=70, start=35., end=37., original_reference=original, keep_ensemble=True)
        self.assertEqual(1, perform.call_count)
        for path in (self.parent, self.destination):
            with wave.open(str(path)) as wav:
                pcm = wav.readframes(wav.getnframes())
            if path == self.parent:
                parent = pcm
        left, right = 35 * 44100 * 4, 37 * 44100 * 4
        self.assertEqual(pcm[:left], parent[:left])
        self.assertNotEqual(pcm[left:right], parent[left:right])
        self.assertEqual(pcm[right:], parent[right:])
