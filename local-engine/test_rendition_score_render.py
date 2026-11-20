from pathlib import Path
import tempfile
import time
import unittest
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


if __name__ == "__main__":
    unittest.main()
