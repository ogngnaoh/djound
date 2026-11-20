from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np

import rendition_score_render as player


class ScoreRendererTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.deadline = time.monotonic() + 30

    def render(self, name, tracks, *, seconds=1.84, room=0):
        path = self.work / name
        player.render_score(tracks, seconds, path, deadline=self.deadline, room=room)
        with wave.open(str(path), "rb") as wav:
            self.assertEqual(wav.getparams()[:4], (2, 2, 44100, round(seconds * 44100)))
            return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").reshape(-1, 2) / 32768.

    def test_schedule_uses_separate_channels_for_overlapping_unisons(self):
        path = self.work / "schedule"
        track = {"program": 8, "notes": [(0., 1.5, 60, 90), (.1, .3, 60, 41), (.3, .7, 60, 54)]}
        self.assertEqual(player._schedule(track, 2 * player.RATE, path, 3), 3)
        header, *events = [[int(x) for x in line.split()] for line in path.read_text().splitlines()]
        self.assertEqual(header[3:], [2, 6])
        self.assertEqual(events[2][1:], [1, 60, 0])
        self.assertEqual(events[3][1:], [1, 60, 54])
        self.assertEqual(events[-1][1:], [0, 60, 0])
        self.assertEqual(events[2][0], events[3][0])

    def test_note_edges_follow_distinct_attacks_including_a_short_or_muted_lead(self):
        dynamics = {"ending": {"count": 8, "db": -9}, "opening": {"count": 1, "db": 6}}
        notes = [(.2, .3, 62, 90), (.2, .5, 66, 80), (.7, .8, 64, 75)]
        ending, opening = player._edge_envelopes({"note_dynamics": dynamics, "notes": notes})
        a, b = (round(s * player.RATE) for s in (.2, .7))
        self.assertEqual(ending, ([a, b], [0, -9]))
        self.assertEqual(opening, ([round(.5 * player.RATE) - round(.008 * player.RATE),
                                    round(.5 * player.RATE)], [6, 0]))
        self.assertEqual(player._edge_envelopes({"note_dynamics": dynamics, "notes": []}), [])
        for invalid in ({"count": 0, "db": -6}, {"count": True, "db": -6}, {"count": 4, "db": float("nan")}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                player._edge_envelopes({"note_dynamics": {"ending": invalid}, "notes": notes})
        for entries in ([{"count": 1, "db": 0}, {"count": 1, "db": -2}], [None],
                        [{"count": 1, "db": -2}] * 33):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                player._edge_envelopes({"note_dynamics": {"opening": entries}, "notes": []})

    def test_arrangement_attaches_note_dynamics_only_to_the_existing_lead(self):
        from rendition_score import analyze, arrange

        melody = [(.1, .4, 60, .8), (.6, 1., 62, .9), (1.2, 1.8, 64, .8)]
        source = {"frames": 88200, "melody": melody, "candidates": melody}
        plan = dict(bpm=90, meter=4, introBars=1, endingBars=1, leadProgram=42, subdivision="straight", themeAnchors=[0],
                    phrasing="flowing", phraseDynamics="steady", harmony="triads",
                    roles=[{"kind": "bass", "program": 32,
                            "pattern": [{"beat": 0, "duration": 1, "degree": 0, "velocity": 70}]}])
        analysis = analyze(source)
        tracks, duration, mapping = arrange(source, analysis, plan)
        settings = {"ending": {"count": 2, "db": -8.}}
        revised, length, clock = arrange(source, analysis, dict(plan, leadNoteDynamics=settings))
        self.assertEqual((revised, length, clock), ([dict(tracks[0], note_dynamics=settings), *tracks[1:]],
                                                    duration, mapping))

    @unittest.skipUnless(player.score_available(), "Install the local score performer")
    def test_taper_leaves_the_kept_intro_cello_and_backing_unchanged_through_the_outro(self):
        # With the two players panned apart, the actual saved recording exposes
        # both the edited cello and the backing that must stay unchanged.
        lead = dict(program=42, pan=-1., level_db=-21.,
                    notes=[(.2 + .2*i, .35 + .2*i, 50 + i, 88) for i in range(7)])
        bass = dict(program=32, pan=1., level_db=-25., notes=[(0., 1.78, 43, 70)])
        parent = self.work / "parent.wav"
        receipt = {}
        player.render_score([lead, bass], 1.84, parent, deadline=self.deadline, room=0., receipt=receipt)

        def replay(name, edited):
            path = self.work / name
            player.render_score([edited, bass], 1.84, path, deadline=self.deadline, room=0.,
                                master_gain=receipt["scoreMasterGain"], part_gains=receipt["scorePartGains"],
                                protected_parts=[0, 1])
            return read(path)

        def read(path):
            with wave.open(str(path)) as wav:
                return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").reshape(-1, 2)

        before = read(parent)
        closing = dict(lead, note_dynamics={"ending": {"count": 3, "db": -8}})
        after = replay("ending.wav", closing)
        np.testing.assert_array_equal(after[:, 1], before[:, 1])
        np.testing.assert_array_equal(after[:player.RATE], before[:player.RATE])
        self.assertGreater(np.max(abs(before[-4000:, 1])), 10)
        for start, stop, low, high in [(1.07, 1.11, -2.3, -1.), (1.24, 1.28, -5.7, -4.4),
                                       (1.43, 1.5, -8.03, -7.97)]:
            window = slice(round(start * player.RATE), round(stop * player.RATE))
            change_db = 20 * np.log10(np.linalg.norm(after[window, 0].astype(float))
                                      / np.linalg.norm(before[window, 0].astype(float)))
            self.assertTrue(low < change_db < high, (start, change_db))
        neutral = dict(lead, note_dynamics={"opening": {"count": 1, "db": 0}})
        np.testing.assert_array_equal(replay("neutral.wav", neutral), before)
        opening = dict(lead, note_dynamics={"opening": {"count": 2, "db": -6}})
        soft_start = replay("opening.wav", opening)
        np.testing.assert_array_equal(soft_start[round(.4 * player.RATE):], before[round(.4 * player.RATE):])
        np.testing.assert_array_equal(soft_start[:, 1], before[:, 1])
        window = slice(round(.21 * player.RATE), round(.25 * player.RATE))
        self.assertLess(np.linalg.norm(soft_start[window, 0].astype(float)),
                        .65 * np.linalg.norm(before[window, 0].astype(float)))

    @unittest.skipUnless(player.score_available(), "Install the local score performer")
    def test_successive_note_tapers_preserve_the_earlier_edit_and_cap_their_combined_gain(self):
        lead = dict(program=42, level_db=-32., notes=[(.15+i*.27, .33+i*.27, 52+i, 90) for i in range(6)])
        previous = {"count": 4, "db": -8}
        narrower = {"count": 2, "db": -4}
        def ending(name, settings):
            return self.render(name, [dict(lead, note_dynamics={"ending": settings})])

        unchanged = self.render("untouched.wav", [lead])
        first = ending("first.wav", previous)
        np.testing.assert_array_equal(ending("single-item-replay.wav", [previous]), first)
        stacked = ending("second.wav", [previous, narrower])
        penultimate = round(lead["notes"][-2][0] * player.RATE)
        np.testing.assert_array_equal(stacked[:penultimate], first[:penultimate])
        self.assertFalse(np.array_equal(stacked[:penultimate], unchanged[:penultimate]))
        final = round(lead["notes"][-1][0] * player.RATE)
        def last_note_change(pcm):
            return 20 * np.log10(np.linalg.norm(pcm[final:]) / np.linalg.norm(unchanged[final:]))
        self.assertAlmostEqual(last_note_change(stacked), -12., delta=.03)
        for broad, narrow, limit in [(-16, -12, -24), (10, 6, 12)]:
            with self.subTest(limit=limit):
                limited = ending(f"bounded-{limit}.wav", [{"count": 4, "db": broad}, {"count": 2, "db": narrow}])
                self.assertAlmostEqual(last_note_change(limited), limit, delta=.03)

    @unittest.skipUnless(player.score_available(), "Install the local score performer")
    def test_overloaded_note_boost_cannot_lower_a_protected_solo(self):
        lead = dict(program=42, level_db=-6, notes=[(.1, .6, 55, 90), (.85, 1.4, 55, 90)])
        receipt = {}
        parent = self.work / "solo.wav"
        player.render_score([lead], 1.84, parent, deadline=self.deadline, room=0, receipt=receipt)
        old = parent.read_bytes()
        boosted = dict(lead, note_dynamics={"ending": {"count": 1, "db": 12}})
        path = self.work / "louder.wav"
        with self.assertRaises(player.ScoreBalanceConflict):
            player.render_score([boosted], 1.84, path, deadline=self.deadline, room=0, protected_parts=[0],
                                master_gain=receipt["scoreMasterGain"], part_gains=receipt["scorePartGains"])
        self.assertEqual(parent.read_bytes(), old)
        self.assertFalse(path.exists())

    def test_invalid_duration_deadline_or_existing_output(self):
        path = self.work / "previous.wav"
        path.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "new WAV"):
            player.render_score([{}], 1., path, deadline=self.deadline)
        with self.assertRaisesRegex(ValueError, "duration"):
            player.render_score([{}], 600.1, path, deadline=self.deadline)
        with self.assertRaises(TimeoutError):
            player.render_score([{}], 1., path, deadline=time.monotonic() - 1)
        self.assertEqual(path.read_bytes(), b"keep")
        for note in [(1, 0, 64, 127), (0, 1.1, 64, 30), (0, 1, 200, 30), (0, float("nan"), 64, 30)]:
            with self.subTest(note=note), self.assertRaises(ValueError):
                player._schedule({"program": 0, "notes": [note]}, player.RATE, self.work/"events", 4)

    def test_historical_gain_needs_matching_audible_score_outside_edited_passages(self):
        from rendition_performance import _same_recorded_score

        frames = player.RATE
        edit = [frames // 4, frames // 2]
        seam = frames // 100
        clean = np.zeros((frames, 2), dtype="<i2")
        clean[2000:2100] = 100
        original, edited = self.work / "score.wav", self.work / "hybrid.wav"
        def write(path, samples):
            with wave.open(str(path), "wb") as wav:
                wav.setparams((2, 2, player.RATE, frames, "NONE", "not compressed"))
                wav.writeframes(samples.tobytes())
        write(original, clean)
        changed = clean.copy()
        changed[edit[0]-seam:edit[1]+seam] = 35
        write(edited, changed)
        self.assertTrue(_same_recorded_score(original, edited, [edit]))
        changed[2100] = -70
        write(edited, changed)
        self.assertFalse(_same_recorded_score(original, edited, [edit]))
        self.assertFalse(_same_recorded_score(original, original, [(0, frames)]))

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_stereo_mix_pans_and_preserves_velocity_and_room_across_blocks(self):
        lead = dict(program=0, pan=-1, level_db=-35, notes=[(.64,.75,69,45), (1.18,1.44,69,105)])
        bass = dict(program=32, pan=1, gain=.7, notes=[(.17,.48,38,91), (.90,1.25,43,82)])
        piano = self.render("piano.wav", [lead])
        upright = self.render("bass.wav", [bass])
        both = self.render("duo.wav", [lead, bass])
        np.testing.assert_allclose(both, piano + upright, atol=1/32768)
        self.assertFalse(piano[:, 1].any() or upright[:, 0].any())
        self.assertGreater(abs(piano[54000:59000, 0]).max(), 2 * abs(piano[28500:31500, 0]).max())
        wet = self.render("room.wav", [lead], room=1)
        # The left-only performance reflects to the right, including across native blocks.
        expected = np.zeros(len(piano))
        for lag, gain in player.REFLECTIONS:
            expected[lag:] += piano[:-lag, 0] * gain
        np.testing.assert_allclose(wet[:-441, 1], expected[:-441], atol=1/32768)
        room_starts = np.flatnonzero(abs(wet[:, 1]) > .0001)[0]
        self.assertGreater(room_starts, round(.65 * player.RATE))
        self.assertLess(abs(wet[-1]).max(), 1/32768)

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_drum_bank_has_distinct_kits_and_track_wide_peak_trim(self):
        kit = dict(program=40, percussion=True, notes=[(.09,.2,38,101), (.4,.7,51,72)])
        brush = self.render("brush.wav", [kit])
        standard = self.render("standard.wav", [dict(kit, program=0)])
        self.assertGreater(abs(brush-standard).max(), .025)
        hot = self.render("hot.wav", [dict(kit, gain=12, level_db=-3)])
        self.assertAlmostEqual(abs(hot).max(), player.PEAK, delta=1/32768)
        sample = np.unravel_index(np.argmax(abs(brush)), brush.shape)
        ratio = hot[sample] / brush[sample]
        np.testing.assert_allclose(hot, ratio * brush, atol=(ratio+1)/32768)
        dest = self.work / "missing.wav"
        with self.assertRaisesRegex(RuntimeError, "absent"):
            player.render_score([dict(kit,program=127)], 1., dest, deadline=self.deadline)
        self.assertFalse(dest.exists())
        self.assertFalse(any(self.work.glob(".score-*")))

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_unchanged_backing_keeps_its_pcm_when_new_lead_is_quieter_or_hotter(self):
        from rendition_performance import _inherited_score_mix

        lead = dict(program=0, pan=-1., level_db=-9, notes=[(.45,.85,60,105), (1.1,1.45,65,90)])
        backing = dict(program=32, pan=1., level_db=-30, notes=[(.12,.54,41,82), (.88,1.65,43,86)])
        parent = self.work / "selected.wav"
        def samples(path):
            with wave.open(str(path), "rb") as wav:
                return np.frombuffer(wav.readframes(wav.getnframes()), "<i2").reshape(-1, 2)
        def perform(tracks, output, **kwargs):
            return player.render_score(tracks, 1.84, output, deadline=self.deadline, room=0., **kwargs)
        old_mix = perform([lead, backing], parent)
        self.assertLess(old_mix, 1.)
        baseline = samples(parent)
        previous = ([lead, backing], 1.84, [])
        plan = {"realization": "score", "room": 0.}
        for amount in (.2, 4.):
            parts = [dict(lead, gain=amount), backing]
            new = self.work / f"new-{amount}.wav"
            recovered = _inherited_score_mix(previous, parts, 1.84, plan, plan, parent, self.work, self.deadline)
            self.assertEqual(recovered["master_gain"], old_mix)
            self.assertEqual(recovered["protected_parts"], [1])
            receipt = {}
            chosen = perform(parts, new, receipt=receipt, **recovered)
            self.assertAlmostEqual(chosen, old_mix, places=6)
            expected = samples(new)
            np.testing.assert_array_equal(expected[:,1], baseline[:,1])
            self.assertGreater(np.max(np.abs(expected[:,0])), 1000)
            self.assertLessEqual(np.max(np.abs(expected)) / 32768, player.PEAK + 1/32768)
            replay = self.work / f"replay-{amount}.wav"
            perform(parts, replay, master_gain=chosen, part_gains=receipt["scorePartGains"])
            np.testing.assert_array_equal(samples(replay), expected)
        wrong_parent = self.work / "other.wav"
        perform([dict(lead, gain=.2), backing], wrong_parent)
        self.assertFalse(_inherited_score_mix(previous, [lead, backing], 1.84, plan, plan, wrong_parent,
                                              self.work, self.deadline))
        self.assertEqual(old_mix, _inherited_score_mix(previous, [lead, backing], 1.84, plan,
                                                       dict(plan, scoreMasterGain=old_mix), parent,
                                                       self.work, self.deadline)["master_gain"])

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_edit_backing_keeps_a_previously_attenuated_lead_and_replays_exactly(self):
        from rendition_performance import _inherited_score_mix

        # Each musician has a separate channel so the published mix exposes the
        # actual preserved lead, even when both are playing simultaneously.
        lead = dict(program=71, pan=-1., level_db=-13, notes=[(.4,.9,67,81), (1.1,1.4,64,78)])
        old = dict(program=48, pan=1., level_db=-27, notes=[(.12,.32,57,72), (1.1,1.4,62,91)])
        parts = [dict(lead, gain=4.), old]
        edited = [parts[0], dict(old, program=11, gain=8.)]
        plan = dict(realization="score", room=0.)
        def render(tracks, name, **options):
            path = self.work / (name + ".wav")
            receipt = {}
            player.render_score(tracks, 1.84, path, deadline=self.deadline, room=0., receipt=receipt, **options)
            with wave.open(str(path)) as stream:
                pcm = np.frombuffer(stream.readframes(stream.getnframes()), "<i2").reshape(-1, 2)
            return path, receipt, pcm

        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                # Master-only ancestors used a different, post-sum rounding order.
                options = {} if legacy else dict(protected_parts=[1], part_gains=[1.,1.])
                path, received, heard = render(parts, f"louder-{legacy}", master_gain=.8, **options)
                self.assertLess(received["scorePartGains"][0], .9)
                parent = dict(plan, **received)
                if legacy:
                    parent.pop("scorePartGains")
                keep = _inherited_score_mix((parts,1.84,[]), edited, 1.84, plan, parent, path,
                                            self.work, self.deadline)
                self.assertEqual(keep["protected_parts"], [0])
                self.assertAlmostEqual(keep["part_gains"][0], received["scorePartGains"][0])
                _, result, changed = render(edited, f"backing-{legacy}", **keep)
                self.assertLess(result["scorePartGains"][1], 1.)
                # Historical post-sum and current in-part multiplication can round
                # one sample differently when migrating a master-only score.
                np.testing.assert_allclose(changed[:,0], heard[:,0], atol=int(legacy))
                self.assertGreater(np.sum(abs(changed[:,1].astype(int)-heard[:,1])>100), 1000)
                self.assertLessEqual(abs(changed.astype(int)).max()/32768, player.PEAK+1/32768)
                _, _, replay = render(edited, f"replay-cascade-{legacy}", master_gain=result["scoreMasterGain"],
                                      part_gains=result["scorePartGains"])
                np.testing.assert_array_equal(changed, replay)

    @unittest.skipUnless(player.score_available(), "Run the local guide setup for soundfont tests")
    def test_unworkable_balance_asks_for_an_audible_alternative_and_keeps_the_parent(self):
        from rendition_performance import perform_score

        piano = dict(program=0, level_db=-7., notes=[(.15,.85,69,96)])
        answer = dict(piano, notes=[(.15,.85,69,96), (1.2,1.5,76,96)])
        clarinet = dict(program=71, gain=1.59, level_db=-13., notes=[(.15,1.1,69,96)])
        delay = 50 / player.RATE
        doubling = dict(clarinet, notes=[(.15+delay,1.1+delay,69,96)])
        later = dict(clarinet, gain=.1, notes=[(1.35,1.64,71,96)])
        for original, edited, reason in [
            ([clarinet,doubling], [clarinet,later], "overload"),
            ([piano,answer], [piano,answer,dict(piano, notes=[(.15,.85,69,96), (1.6,1.8,79,96)])], "too quiet"),
        ]:
            with self.subTest(reason=reason):
                selected = self.work / f"parent-{reason}.wav"
                plan = dict(realization="score", fromUpload=True, room=0.)
                if reason == "too quiet":
                    # The first addition starts in unison, then plays its own answer.
                    # It uses the space above the nominal peak without touching the
                    # piano. A second coincident attack now hits the physical limit.
                    master = player.render_score([piano], 1.85, self.work/"solo.wav",
                                                 deadline=self.deadline, room=0.)
                    player.render_score(original, 1.85, selected, deadline=self.deadline, room=0.,
                                        master_gain=master, part_gains=[1.,1.], protected_parts=[0], receipt=plan)
                    self.assertAlmostEqual(plan["scoreMasterGain"], master, places=6)
                    self.assertGreater(plan["scorePartGains"][1], .05)
                    replay = self.work / "with-first-answer.wav"
                    player.render_score(original, 1.85, replay, deadline=self.deadline, room=0.,
                                        master_gain=plan["scoreMasterGain"], part_gains=plan["scorePartGains"])
                    self.assertEqual(replay.read_bytes(), selected.read_bytes())
                    with wave.open(str(replay)) as wav:
                        pcm = np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(int)
                    self.assertIn(abs(pcm).max(), range(32765, 32768))
                else:
                    player.render_score(original, 1.85, selected, deadline=self.deadline, room=0., receipt=plan)
                heard = selected.read_bytes()
                output = self.work / f"child-{reason}.wav"
                def transcribe(source, dest, **kwargs):
                    dest.write_text("{}")
                    return True
                with (patch("rendition_performance.guide_available", return_value=True),
                      patch("rendition_melody.transcribe_recording", side_effect=transcribe),
                      patch("rendition_score.analyze", return_value={}),
                      patch("rendition_score_plan.plan_arrangement", return_value=dict(plan)),
                      patch("rendition_score.arrange", side_effect=[(original,1.85,[]), (edited,1.85,[])])):
                    result = perform_score(selected, self.work/"upload.wav", output, self.work,
                                           direction="Change the accompaniment and keep the lead.",
                                           caption="", parent_caption="", parent_plan=plan,
                                           parent_map=None, recover_original=False)
                self.assertEqual(result["state"], "clarify")
                self.assertIn(reason, result["error"])
                self.assertIn("choose Original", result["error"])
                self.assertEqual(selected.read_bytes(), heard)
                self.assertFalse(output.exists() or (self.work/"arrangement.wav").exists())

                # Old master-only scores still use their historical fallback and
                # rounding when replaying these exact same musical circumstances.
                legacy = self.work / f"master-only-{reason}.wav"
                receipt = {}
                legacy_tracks = [answer,piano] if reason == "too quiet" else edited[::-1]
                player.render_score(legacy_tracks, 1.85, legacy, deadline=self.deadline, room=0.,
                                    master_gain=plan["scoreMasterGain"], receipt=receipt)
                self.assertTrue(legacy.is_file())
                if reason == "overload":
                    ratio = receipt["scoreMasterGain"] / plan["scoreMasterGain"]
                    self.assertLess(20*np.log10(ratio), -3.)
                else:
                    self.assertLess(receipt["scorePartGains"][0], .000001)


if __name__ == "__main__":
    unittest.main()
