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
import rendition_score_render as player
from rendition_timeline import relative_map


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

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_nonupload_score_descendants_keep_parts_gain_and_parent_time(self):
        upload, basis = self.root / "upload.wav", self.root / "audio-only-take.wav"
        basis.touch()
        self.notes["melodySource"] = "mix"
        piano = dict(self.parent, fromUpload=False, introBars=1, endingBars=1, roles=[
            {"program": 0, "kind": "arpeggio", "pattern": planner._pattern("arpeggio", "wave", 4)}])
        # The first score transcribes an audio-only take. Two successive revisions
        # revoice its melody; the third intentionally changes its tempo.
        def transcribe(source, path, **kwargs):
            self.assertEqual(basis.resolve(), source.resolve())
            path.write_text(json.dumps(self.notes))
            return True

        source, prior = basis, None
        for index, (program, bpm) in enumerate(((0, 100), (42, 100), (71, 100), (71, 120))):
            work = self.root / str(index)
            work.mkdir()
            output = work / "take.wav"
            candidate = dict(prior or piano, leadProgram=program, bpm=bpm)
            with (patch("rendition_performance.guide_available", return_value=True),
                  patch("rendition_melody.transcribe_recording", side_effect=transcribe) as extractor,
                  patch("rendition_score_plan.plan_arrangement", return_value=candidate)):
                result = perform_score(source, upload, output, work, direction="Revise the arrangement",
                                       caption="Instrumental", parent_caption="Piano", parent_plan=prior,
                                       parent_map=None, recover_original=False,
                                       parent_score_input={"source": str(basis)} if prior else None)
            self.assertEqual(bool(prior), result["inheritedScoreInput"])
            self.assertEqual("mix" if prior else None, extractor.call_args.kwargs["trace_source"])
            saved = result["arrangementPlan"]
            tracks, seconds, mapping = arrange(self.notes, analyze(self.notes), saved)
            self.assertTrue(tracks[1]["notes"])
            self.assertEqual(program, tracks[0]["program"])
            if prior:
                old_tracks, old_seconds, old_map = arrange(self.notes, analyze(self.notes), prior)
                self.assertEqual(relative_map(mapping, old_map), result["parentTimeMap"])
                self.assertNotEqual(source.read_bytes(), output.read_bytes())
                if bpm == prior["bpm"]:
                    self.assertEqual(old_seconds, seconds)
                    self.assertEqual(old_tracks[1:], tracks[1:])
                    self.assertEqual(prior["scoreMasterGain"], saved["scoreMasterGain"])
                else:
                    self.assertLess(seconds, old_seconds)
                    self.assertEqual([round(seconds * player.RATE), round(old_seconds * player.RATE)],
                                     result["parentTimeMap"][-1])
            source, prior = output, saved

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

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_local_volume_edit_round_trips_with_the_heard_gain_and_an_immutable_parent(self):
        import time
        import wave

        source, parent = self.root / "upload.wav", self.root / "parent.wav"
        prior = dict(self.parent, leadProgram=42, roles=[{
            "kind": "arpeggio", "program": 0, "pattern": planner._pattern("arpeggio", "wave", 4)}])
        tracks, seconds, _ = arrange(self.notes, analyze(self.notes), prior)
        player.render_score(tracks, seconds, parent, room=prior["room"], deadline=time.monotonic() + 30,
                            master_gain=.65, part_gains=[.7, .9], protected_parts=[0, 1], receipt=prior)
        original_audio = parent.read_bytes()
        edit = {"edge": "ending", "count": 2, "db": -8}

        def transcribe(_, path, **kwargs):
            path.write_text(json.dumps(self.notes))
            return True

        def revise(heard, plan, out, change):
            with (patch("rendition_performance.guide_available", return_value=True),
                  patch("rendition_melody.transcribe_recording", side_effect=transcribe),
                  patch.object(planner, "plan_arrangement", side_effect=AssertionError("Keep the rest of the band"))):
                result = perform_score(heard, source, out, self.root, direction="Change the last two lead notes",
                                       caption="Lead dynamics", parent_caption="Cello and piano", parent_plan=plan,
                                       parent_map=None, recover_original=False, lead_note_edit=change)
                return json.loads(json.dumps(result["arrangementPlan"]))

        revised = revise(parent, prior, self.output, edit)
        self.assertEqual(original_audio, parent.read_bytes())
        self.assertEqual(prior["scorePartGains"], revised["scorePartGains"])
        self.assertEqual(prior["scoreMasterGain"], revised["scoreMasterGain"])
        self.assertNotIn("leadNoteDynamics", prior)
        self.assertEqual({"ending": {"count": 2, "db": -8}}, revised["leadNoteDynamics"])
        with wave.open(str(parent)) as a, wave.open(str(self.output)) as b:
            early = round(tracks[0]["notes"][-2][0] * player.RATE)
            self.assertEqual(a.readframes(early), b.readframes(early))
            self.assertNotEqual(a.readframes(a.getnframes()), b.readframes(b.getnframes()))
        undone = self.root / "undone.wav"
        restored = revise(self.output, revised, undone, dict(edit, db=8))
        self.assertNotIn("leadNoteDynamics", restored)
        self.assertEqual(original_audio, undone.read_bytes())

        # Another listener may narrow the change: keep the first taper and
        # soften only the last note further, then undo just that refinement.
        narrower = self.root / "narrower.wav"
        last = dict(edit, count=1, db=-4)
        layered = revise(self.output, revised, narrower, last)
        self.assertEqual([{"count": 1, "db": -4}, {"count": 2, "db": -8}],
                         layered["leadNoteDynamics"]["ending"])
        with wave.open(str(self.output)) as a, wave.open(str(narrower)) as b:
            early = round(tracks[0]["notes"][-1][0] * player.RATE)
            self.assertEqual(a.readframes(early), b.readframes(early))
            self.assertNotEqual(a.readframes(a.getnframes()), b.readframes(b.getnframes()))
        previous = self.root / "previous.wav"
        self.assertEqual(revised, revise(narrower, layered, previous, dict(last, db=4)))
        self.assertEqual(self.output.read_bytes(), previous.read_bytes())

    def test_explicit_register_follows_the_heard_lead_or_the_upload_through_revisions(self):
        role = {"program": 0, "kind": "arpeggio", "pattern": planner._pattern("arpeggio", "wave", 4)}
        trumpet = dict(self.parent, leadProgram=56, roles=[role])
        analysis = analyze(self.notes)
        trumpet_tracks, _, _ = arrange(self.notes, analysis, trumpet)
        words = ("Keep the beat and the accompaniment exactly as they are. "
                 "Give the tune to a cello in the same octave as this trumpet.")
        def revise(prior, direction, axis):
            with patch.object(planner, "_changes", return_value={axis}), patch.object(planner, "_Director") as director:
                chart = director.return_value
                chart.brief = "Keep the established pulse."
                chart.identity = "scripted"
                # If offered both players, the director favors the comparator.
                chart.choose.side_effect = lambda _, players: 56 if 56 in players else next(iter(players))
                revised = planner._run({"analysis": dict(analysis, parentPlan=prior), "direction": direction,
                                        "parentCaption": "A saved instrumental."})
            tracks, _, _ = arrange(self.notes, analysis, revised)
            self.assertEqual(trumpet_tracks[1:], tracks[1:])
            return revised, [event[2] for event in tracks[0]["notes"]]

        pitches = [n[2] for n in trumpet_tracks[0]["notes"]]
        for classified_as in ("revoice", "players", "register"):
            saved, heard = revise(trumpet, words, classified_as)
            self.assertEqual((42, 0, pitches), (saved["leadProgram"], saved["leadOctaveShift"], heard))
        saved, heard = revise(saved, "Give the tune to a bassoon.", "revoice")
        self.assertEqual((70, 0, pitches), (saved["leadProgram"], saved["leadOctaveShift"], heard))
        natural, lower = revise(saved, "Use the bassoon's natural register.", "register")
        self.assertNotIn("leadOctaveShift", natural)
        self.assertEqual([p-12 for p in pitches], lower)
        preserved, still_lower = revise(natural, "Give the tune to a clarinet in the same octave.", "revoice")
        self.assertEqual((71, -1, lower), (preserved["leadProgram"], preserved["leadOctaveShift"], still_lower))
        restored, original = revise(preserved, "Use the original recording's octave for the melody.", "register")
        self.assertEqual((0, pitches), (restored["leadOctaveShift"], original))
        with self.assertRaisesRegex(ValueError, "Choose Original"):
            revise(dict(preserved, fromUpload=False), "Use the upload's octave for the tune.", "register")

    def test_backing_register_is_not_a_lead_placement_instruction(self):
        from unittest.mock import Mock

        lead = dict(self.parent, leadProgram=56)
        director = Mock()
        for words in ("Keep the bass in the same octave.", "Keep the same register for the backing.",
                      "Give the melody to a cello and keep the bass in its natural register.",
                      "Make a walking bass and brushed drum accompaniment."):
            self.assertIsNone(planner._lead_register(words, lead, director), words)
        director.choose.assert_not_called()

    def test_adding_players_to_a_full_quartet_keeps_its_actual_parts_across_reloads(self):
        from unittest.mock import Mock

        analysis = analyze(self.notes)
        def role(kind, program):
            return {"kind": kind, "program": program, "gesture": "pulse",
                    "pattern": planner._pattern(kind, "pulse", 4)}

        prior = dict(self.parent, leadProgram=56, roles=[role("bass", 32), role("chords", 0), role("percussion", None)])
        for instrument, program in (("acoustic guitar", 25), ("electric guitar", 27)):
            previous, _, _ = arrange(self.notes, analysis, prior)
            words = f"Keep the trumpet melody exactly. Add {instrument} chords to the accompaniment."
            director = Mock()
            director.choose.side_effect = lambda question, options: "add" if "add" in options else next(iter(options))
            with patch.object(planner, "_changes", side_effect=AssertionError("A new backing player is explicit.")):
                revised = planner._arrange(analysis, words, prior, director)
            tracks, _, _ = arrange(self.notes, analysis, revised)
            self.assertEqual(previous, tracks[:-1])
            self.assertEqual(program, tracks[-1]["program"])
            self.assertTrue(tracks[-1]["notes"])
            prior = planner._prior({"parentPlan": json.loads(json.dumps(revised))})

        # A player can supply both lead and backing; the limit is on sounding parts.
        roles = [role("chords", p) for p in range(player.MAX_TRACKS-2)]
        old = dict(self.parent, leadProgram=0, roles=roles)
        with patch.object(planner, "_changes", return_value={"players"}):
            at_limit = planner._arrange(analysis, "Add flute chords.", old, director)
            self.assertEqual(player.MAX_TRACKS, len(arrange(self.notes, analysis, at_limit)[0]))
            self.assertLess(len(json.dumps(at_limit).encode()), 48 * 1024)
            restored = planner._prior({"parentPlan": json.loads(json.dumps(at_limit))})
            with self.assertRaisesRegex(planner.ArrangementClarification, "Choose a part to replace"):
                planner._arrange(analysis, "Add violin chords.", restored, director)

    def test_a_supporting_addition_does_not_override_a_compound_edit(self):
        old = dict(self.parent, leadProgram=56)
        for words in ("Give the tune to a cello and add guitar to the backing.",
                      "Add strings instead of piano.", "Add a guitar to carry the melody.",
                      "Add guitar chords and make this much faster.", "Add guitar chords in 3/4.",
                      "Add guitar but remove the trumpet."):
            self.assertEqual([], planner._backing_additions(words, old), words)
        words = "Keep the trumpet melody intact. Add softly strummed acoustic guitar and electric piano to the backing."
        self.assertEqual([25, 4], planner._backing_additions(words, old))

        # Existing performers can have two different figures for the same patch.
        # The original arrangement must survive without reasking the director.
        from unittest.mock import Mock
        original = [{"kind": "chords", "program": 0, "gesture": g, "pattern": planner._pattern("chords", g, 4),
                     "followPhrases": follow} for g, follow in (("hold", True), ("pulse", False))]
        old = dict(old, roles=original, phraseInteraction="phrase_answers")
        chart = Mock()
        chart.choose.side_effect = lambda q, opts: next(iter(opts))
        revised = planner._arrange(analyze(self.notes), "Add flute chords to the accompaniment.", old, chart)
        self.assertEqual(original, revised["roles"][:2])
        self.assertFalse(any("Acoustic Grand Piano" in call.args[0] for call in chart.choose.call_args_list))

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_removing_a_backing_player_sounds_like_muting_their_written_parts(self):
        import time
        from test_rendition_score_plan import Musician

        roles = [{"kind": kind, "program": program, "gesture": gesture, "pattern": planner._pattern(kind, gesture, 4)}
                 for kind, program, gesture in (("chords", 25, "hold"), ("bass", 32, "two"),
                                                ("chords", 0, "pulse"), ("chords", 0, "anticipate"))]
        plan = dict(self.parent, leadProgram=42, introBars=1, endingBars=1, roles=roles,
                    backingTiming={"phrasing": "articulated"})
        analysis = analyze(self.notes)
        original, seconds, clock = arrange(self.notes, analysis, plan)
        musician = Musician({"What is the listener": "players", "How is the listener": "remove"})
        child = planner._arrange(analysis, "Remove the guitar. Keep everyone else as they are.", plan, musician)
        tracks, duration, mapping = arrange(self.notes, analysis, json.loads(json.dumps(child)))
        self.assertEqual((original[:1] + original[2:], seconds, clock), (tracks, duration, mapping))
        self.assertEqual(plan["backingTiming"], child["backingTiming"])
        options = dict(room=plan["room"], deadline=time.monotonic() + 30, master_gain=.40)
        muted = self.root / "muted.wav"
        player.render_score(original, seconds, muted, part_gains=[.75, 0., .85, .92, .65], **options)
        player.render_score(tracks, duration, self.output, part_gains=[.75, .85, .92, .65], **options)
        self.assertEqual(muted.read_bytes(), self.output.read_bytes())
        solo = planner._arrange(analysis, "Remove the bass and piano.", child, musician)
        self.assertEqual(([original[0]], seconds, clock), arrange(self.notes, analysis, solo))

    def test_adding_backing_to_a_take_the_listener_likes(self):
        from unittest.mock import Mock

        bass = {"kind": "bass", "program": 32, "gesture": "walk", "pattern": planner._pattern("bass", "walk", 4)}
        parent = dict(self.parent, leadProgram=42, roles=[bass])
        words = "I like the cello just the way it is. Could you add a little piano behind it?"
        self.assertEqual([0], planner._backing_additions(words, parent))
        analysis = analyze(self.notes)
        chart = Mock()
        chart.choose.side_effect = lambda _, options: next(iter(options))
        with patch.object(planner, "_changes", side_effect=AssertionError("The new player is explicit")):
            revised = planner._arrange(analysis, words, parent, chart)
        before = arrange(self.notes, analysis, parent)[0]
        after = arrange(self.notes, analysis, revised)[0]
        self.assertEqual(before, after[:-1])
        self.assertEqual(0, after[-1]["program"])
        self.assertTrue(after[-1]["notes"])
        # An unsolicited tempo in the generated rehearsal notes must not move
        # the saved melody when the listener only asked for another player.
        chart.brief = "140 BPM. Piano answers the existing lead."
        chart.identity = "scripted"
        with (patch.object(planner, "_Director", return_value=chart),
              patch.object(planner, "_interpret_freely", side_effect=AssertionError("Keep the old pace"))):
            saved = planner._run({"analysis": dict(analysis, parentPlan=parent), "direction": words,
                                  "parentCaption": "Cello with bass."})
        self.assertEqual(before, arrange(self.notes, analysis, saved)[0][:-1])
        for prefix in ("I'd like the cello louder.", "I like the flute.", "I love the cello but change its octave."):
            self.assertEqual([], planner._backing_additions(prefix + " Add piano behind it.", parent))

    def test_upload_octave_uses_the_recording_actually_selected_for_transcription(self):
        from unittest.mock import Mock

        upload, parent = self.root / "upload.wav", self.root / "earlier-take.wav"
        old = dict(self.parent, fromUpload=False, leadProgram=70, leadOctaveShift=-1)
        words = "Use the uploaded recording's octave for the melody."
        def transcribe(source, path, **options):
            path.write_text(json.dumps(self.notes))
            return True

        def plan(analysis, *args, **kwargs):
            return planner._arrange(analysis, words, analysis["parentPlan"], Mock())

        def render(tracks, seconds, path, **kwargs):
            path.write_bytes(b"score")
            return 1.

        with (patch("rendition_performance.guide_available", return_value=True),
              patch("rendition_score_render.score_available", return_value=True),
              patch("rendition_melody.transcribe_recording", side_effect=transcribe) as extractor,
              patch("rendition_score_plan.plan_arrangement", side_effect=plan),
              patch.object(planner, "_changes", return_value={"register"}),
              patch("rendition_score_render.render_score", side_effect=render)):
            args = dict(direction=words, caption="Melody", parent_caption="Bassoon", parent_plan=old, parent_map=None)
            with self.assertRaisesRegex(ValueError, "Choose Original"):
                perform_score(parent, upload, self.output, self.root, recover_original=False, **args)
            self.assertEqual(parent, extractor.call_args.args[0])
            reply = perform_score(parent, upload, self.output, self.root, recover_original=True, **args)
            self.assertEqual(upload, extractor.call_args.args[0])
            self.assertEqual(0, reply["arrangementPlan"]["leadOctaveShift"])
            self.assertTrue(reply["arrangementPlan"]["fromUpload"])

    def test_revision_uses_chart_tempo_only_when_the_listener_allows_a_new_pace(self):
        parent = dict(self.parent, bpm=72)
        analysis = analyze(self.notes)
        analysis["parentPlan"] = parent
        original_tracks, original_length, _ = arrange(self.notes, analysis, parent)
        cases = (
            ("Music for my evening run.", "**BPM:** `120`. A steady 120 BPM.", "interpret", 120),
            ("Keep the beat and play the melody more gently.", "Aim for 120 BPM.", "targeted", 72),
            ("Music for my evening run.", "Start at 90 BPM, accelerate to 120 BPM.", None, 72),
            ("Music for my evening run.", "A brisk 280 BPM.", None, 72),
            ("Play this at 108 BPM for my evening run.", "Aim for 120 BPM.", None, 108),
            ("Play this at 108 BPM.", "Keep the existing pace.", None, 108),
        )
        for words, chart, tempo_decision, expected in cases:
            with self.subTest(words=words, chart=chart), patch.object(planner, "_Director") as model:
                director = model.return_value
                director.identity = "scripted"
                director.brief = chart
                director.write.return_value = "[60,64,48,88,0]"
                pace_questions = []

                def choose(question, options, *, context=None):
                    if "interpret" in options:
                        pace_questions.append(json.loads(context))
                        return tempo_decision
                    if "revoice" in options:
                        return "phrasing"
                    if "articulated" in options:
                        return "articulated"
                    self.fail(f"Unexpected choice: {question}")

                director.choose.side_effect = choose
                revision = planner._run({"analysis": analysis, "parentCaption": "Unhurried piano.",
                                         "direction": words})
                self.assertEqual(72, parent["bpm"])
                self.assertEqual(expected, revision["bpm"])
                self.assertEqual([{"listener": words}] if tempo_decision else [], pace_questions)
                tracks, seconds, _ = arrange(self.notes, analysis, revision)
                self.assertNotEqual(original_tracks[0]["notes"], tracks[0]["notes"])
                if expected == 72:
                    self.assertEqual(original_length, seconds)
                else:
                    self.assertLess(seconds, original_length)

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

    def test_numbers_from_rejected_and_earlier_tempos_do_not_override_the_listener(self):
        parent = dict(self.parent, bpm=72)
        analysis = dict(analyze(self.notes), parentPlan=parent)
        examples = (
            ("Keep this beat and soften the melody. I don't want 120 BPM.", "current", 72),
            ("The 120 BPM version was too fast. I like the pace here.", "current", 72),
            ("Keep this below 120 BPM and find a slower pulse.", "different", 62),
            ("Instead of 120 BPM please play this at 92 BPM.", 92, 92),
        )
        for direction, decision, expected in examples:
            for axis in ("pulse", "phrasing"):
                with self.subTest(direction=direction, axis=axis), patch.object(planner, "_Director") as factory:
                    director = factory.return_value
                    director.brief = "120 BPM. Connected phrases."
                    director.write.return_value = "[60,64,48,88,0]"

                    def choose(question, options, *, context=None):
                        if "different" in options:
                            self.assertEqual({"listener": direction}, json.loads(context))
                            self.assertIn(decision, options)
                            return decision
                        if "revoice" in options:
                            return axis
                        if "articulated" in options:
                            return "articulated"
                        if all(isinstance(option, int) for option in options):
                            self.assertNotIn(120, options)
                            return min(options, key=lambda bpm: abs(bpm - expected))
                        self.fail(f"Unexpected choice: {question}")

                    director.choose.side_effect = choose
                    child = planner._run({"analysis": analysis, "parentCaption": "Piano at 72 BPM.",
                                          "direction": direction})
                    if decision == "different" and axis == "phrasing":
                        # A chart that simply repeats the upper bound supplies no trustworthy destination.
                        self.assertEqual(72, child["bpm"])
                    elif decision == "different":
                        self.assertLess(child["bpm"], parent["bpm"])
                    else:
                        self.assertEqual(expected, child["bpm"])
                    self.assertEqual(72, parent["bpm"])


if __name__ == "__main__":
    unittest.main()
