"""Window boundaries, selective backing restoration and cache ownership in canonical PCM."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np

import rendition_sketch as sketch


def write(path, pcm):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, sketch.RATE, len(pcm), "NONE", "not compressed"))
        wav.writeframes(np.rint(pcm * 32768).astype("<i2").tobytes())


def read(path):
    with wave.open(str(path), "rb") as wav:
        return sketch._read(wav, 0, sketch._frames(wav))


def write_mono(path, raw, width=2, rate=sketch.RATE):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, width, rate, 0, "NONE", "not compressed"))
        wav.writeframes(raw)


class ArrangementSketchTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name)
        self.guide = self.tmp / "guide.wav"
        self.draft = self.tmp / "draft.wav"
        self.refined = self.tmp / "refined.wav"
        self.output = self.tmp / "result.wav"
        self.deadline = time.monotonic() + 80
        samples = np.arange(2 * sketch.RATE + 17) / sketch.RATE
        self.line = np.tile((0.13 * np.sin(2 * np.pi * 691 * samples))[:, None], (1, 2))
        self.bass = np.tile((0.08 * np.sin(2 * np.pi * 91 * samples))[:, None], (1, 2))
        # A deterministic pulse pattern makes an invented or duplicated rhythm detectable.
        beat = (samples % 0.4 < 0.12) * 0.09 * np.sin(2 * np.pi * 3271 * samples)
        self.drums = np.tile(beat[:, None], (1, 2))
        self.zeros = np.zeros_like(self.line)
        write(self.guide, self.line)
        write(self.draft, self.bass + self.drums + self.line * 0.5)
        write(self.refined, self.line)

    def fake_stems(self, draft, refined):
        parts = [draft, refined]

        def separate(wav, stats, deadline, groups):
            self.assertEqual(wav.getnframes(), len(self.line))
            found = parts.pop(0)
            yield 0, np.stack([found[i] for i in groups], axis=1).astype("float32")

        return separate

    def test_mono_vocal_reference_uses_only_its_own_scratch(self):
        def separate(wav, stats, deadline, groups):
            yield 0, sketch._read(wav, 0, wav.getnframes())[:, None, :]

        previous_scratch = self.tmp / "stereo-reference.wav"
        for filename, values in [("stereo-reference.wav", [9, -5, 7]), ("memo.wav", [-20, 1, 85])]:
            with self.subTest(filename=filename):
                source = self.tmp / filename
                samples = np.array(values, dtype="<i2")
                write_mono(source, samples.tobytes())
                existing = {path: path.read_bytes() for path in {source, previous_scratch}}
                output = source.with_suffix(".voice.wav")
                before = set(self.tmp.iterdir())
                with patch.object(sketch, "_separated", side_effect=separate):
                    times, voice, mix = sketch.vocal_reference(source, output, self.deadline)
                for path, data in existing.items():
                    self.assertEqual(path.read_bytes(), data)
                self.assertEqual(set(self.tmp.iterdir()) - before, {output})
                np.testing.assert_array_equal(read(output) * 32768, np.repeat(samples[:, None], 2, axis=1))
                np.testing.assert_array_equal(voice, mix)
                np.testing.assert_allclose(times, [len(samples) / (2 * sketch.RATE)])

    def test_vocal_reference_rejects_unsupported_or_incomplete_mono_before_separation(self):
        source = self.tmp / "memo.wav"
        for reason, width, rate, frames in [
            ("width", 1, sketch.RATE, 4), ("rate", 2, 8000, 4), ("empty", 2, sketch.RATE, 0),
            ("long", 2, sketch.RATE, 9), ("truncated", 2, sketch.RATE, 8),
        ]:
            with self.subTest(reason=reason):
                write_mono(source, bytes(width * frames), width, rate)
                if reason == "truncated":
                    source.write_bytes(source.read_bytes()[:-2])
                original = source.read_bytes()
                before = set(self.tmp.iterdir())
                with patch.object(sketch, "MAX_FRAMES", 8), patch.object(sketch, "_separated") as model:
                    with self.assertRaises(ValueError):
                        sketch.vocal_reference(source, self.output, self.deadline)
                    model.assert_not_called()
                self.assertEqual(set(self.tmp.iterdir()), before)
                self.assertEqual(source.read_bytes(), original)

    def test_vocal_reference_does_not_publish_a_failed_or_short_estimate(self):
        for reason in ("exception", "short", "gap"):
            with self.subTest(reason=reason):
                def separate(wav, stats, deadline, groups):
                    yield int(reason == "gap"), sketch._read(wav, 0, 8)[:, None, :]
                    if reason == "exception":
                        raise RuntimeError("separation interrupted")

                before = set(self.tmp.iterdir())
                with patch.object(sketch, "_separated", side_effect=separate), self.assertRaises(RuntimeError):
                    sketch.vocal_reference(self.draft, self.output, self.deadline)
                self.assertEqual(set(self.tmp.iterdir()), before)

    def test_vocal_reference_preserves_input_alias_and_competing_output(self):
        self.output.symlink_to(self.draft)
        original = self.draft.read_bytes()
        with patch.object(sketch, "_separated") as model, self.assertRaisesRegex(ValueError, "separately"):
            sketch.vocal_reference(self.draft, self.output, self.deadline)
        model.assert_not_called()
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.draft.read_bytes(), original)
        self.output.unlink()

        def competing_writer(wav, stats, deadline, groups):
            self.output.write_bytes(b"another completed reference")
            yield 0, sketch._read(wav, 0, wav.getnframes())[:, None, :]

        before = set(self.tmp.iterdir())
        with patch.object(sketch, "_separated", side_effect=competing_writer), self.assertRaises(FileExistsError):
            sketch.vocal_reference(self.draft, self.output, self.deadline)
        with patch.object(sketch, "_separated") as model, self.assertRaises(FileExistsError):
            sketch.vocal_reference(self.draft, self.output, self.deadline)
        model.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"another completed reference")
        self.assertEqual(set(self.tmp.iterdir()) - before, {self.output})

    def test_cache_avoids_second_draft_inference_and_restores_the_missing_rhythm(self):
        source = [self.drums, self.bass, self.line * 0.5]
        missing = [self.zeros, self.zeros]
        with patch.object(sketch, "_separated", side_effect=self.fake_stems(source, missing)) as model:
            with sketch.build_arrangement_sketch(
                self.guide, self.draft, self.tmp / "conditioning.wav", deadline=self.deadline
            ) as cache:
                self.assertEqual(model.call_count, 1)
                sketch.restore_backing(
                    self.draft, self.refined, self.output, cache=cache, deadline=self.deadline
                )
                self.assertEqual(model.call_count, 2)
            wet = read(self.output)
            self.assertEqual(wet.shape, self.line.shape)
            self.assertLessEqual(np.max(np.abs(wet)), 0.881)
            # Both actual draft parts return. The dry refined take contained neither.
            for part in (self.bass, self.drums):
                dry_projection = np.mean(read(self.refined) * part) / np.mean(part**2)
                wet_projection = np.mean(wet * part) / np.mean(part**2)
                self.assertGreater(wet_projection - dry_projection, 0.06)
            self.assertTrue(cache.file.closed)
            with self.assertRaisesRegex(ValueError, "different generated performance"):
                sketch.restore_backing(
                    self.draft, self.refined, self.output, cache=cache, deadline=self.deadline
                )

    def test_quiet_estimates_do_not_turn_a_solo_take_into_an_ensemble(self):
        draft = [self.drums * 0.0001, self.bass * 0.0001, self.line]
        write(self.draft, self.line)
        with patch.object(sketch, "_separated", side_effect=self.fake_stems(draft, [])) as model:
            sketch.restore_backing(self.draft, self.refined, self.output, deadline=self.deadline)
        self.assertEqual(model.call_count, 1)
        np.testing.assert_array_equal(read(self.refined), read(self.output))

    def test_isolated_performers_mix_at_fixed_gain_including_rests_and_quiet_phrases(self):
        lead = self.line.copy() * 7.3
        backing = self.bass.copy() * 0.25
        lead[:, 1] = backing[:, 0] = 0
        rate = sketch.RATE
        lead[:rate // 2] = 0
        backing[-rate // 2:] = 0
        lead[rate : rate * 3 // 2] *= 0.25
        backing[rate : rate * 3 // 2] *= 0.25
        write(self.guide, lead)
        write(self.draft, backing)
        lead, backing = read(self.guide), read(self.draft)
        sketch.mix_performers(self.guide, self.draft, self.output, deadline=self.deadline)
        mixed = read(self.output)
        self.assertEqual(len(mixed), len(lead))
        self.assertAlmostEqual(abs(mixed).max(), sketch.HEADROOM, places=4)
        for ch, part in enumerate((lead, backing)):
            x, y = part[:, ch], mixed[:, ch]
            gain = np.vdot(x, y) / np.vdot(x, x)
            np.testing.assert_allclose(y, x * gain, atol=2 / 32768)
        overlap = mixed[rate // 2 : rate * 3 // 2]
        balance_db = 20 * np.log10(sketch._rms(overlap[:, 1]) / sketch._rms(overlap[:, 0]))
        self.assertAlmostEqual(balance_db, -5, delta=0.02)
        self.assertGreater(sketch._rms(mixed[: rate // 2, 1]), 0.1)
        self.assertEqual(np.count_nonzero(mixed[: rate // 2, 0]), 0)

    def test_isolated_call_and_response_and_failed_duos(self):
        split = sketch.RATE
        lead, response = self.line.copy(), self.bass.copy()
        lead[split:] = response[:split] = 0
        write(self.guide, lead)
        write(self.draft, response)
        sketch.mix_performers(self.guide, self.draft, self.output, deadline=self.deadline)
        mixed = read(self.output)
        balance_db = 20 * np.log10(sketch._rms(mixed[split:]) / sketch._rms(mixed[:split]))
        self.assertAlmostEqual(balance_db, -5, delta=0.02)
        kept = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "separately"):
            sketch.mix_performers(self.guide, self.draft, self.guide, deadline=self.deadline)
        with self.assertRaisesRegex(RuntimeError, "time limit"):
            sketch.mix_performers(self.guide, self.draft, self.output, deadline=time.monotonic() - 1)
        write(self.draft, self.zeros)
        with self.assertRaisesRegex(ValueError, "audible"):
            sketch.mix_performers(self.guide, self.draft, self.output, deadline=self.deadline)
        write(self.draft, self.bass[:-1])
        with self.assertRaisesRegex(ValueError, "matching timelines"):
            sketch.mix_performers(self.guide, self.draft, self.output, deadline=self.deadline)
        self.assertEqual(self.output.read_bytes(), kept)

    def test_a_drum_fill_in_a_melody_rest_keeps_its_attack(self):
        lead = self.line.copy()
        lead[: sketch.RATE // 2] = 0
        write(self.refined, lead)
        source = [self.drums, self.zeros, self.line]
        with patch.object(
            sketch, "_separated", side_effect=self.fake_stems(source, [self.zeros, self.zeros])
        ):
            sketch.restore_backing(self.draft, self.refined, self.output, deadline=self.deadline)
        # The first hit lands during a silent piano phrase. Compare its actual waveform just after 5 ms.
        span = slice(sketch.RATE // 100, sketch.RATE // 20)
        gain = np.sum(read(self.output)[span] * self.drums[span]) / np.sum(self.drums[span] ** 2)
        self.assertGreater(gain, 0.35)

    def test_surviving_parts_are_not_doubled_or_limited(self):
        loud = 0.915 * self.line / 0.13
        write(self.draft, loud)
        write(self.refined, loud)
        draft = [loud * 0.3, loud * 0.3, loud * 0.4]
        with patch.object(sketch, "_separated", side_effect=self.fake_stems(draft, draft[:2])):
            sketch.restore_backing(self.draft, self.refined, self.output, deadline=self.deadline)
        np.testing.assert_array_equal(read(self.output), read(self.refined))

    def test_mismatch_alias_deadline_or_changed_performance_cannot_replace_previous_take(self):
        self.output.write_bytes(b"previous")
        with self.assertRaisesRegex(ValueError, "separately"):
            sketch.build_arrangement_sketch(self.guide, self.draft, self.draft, deadline=self.deadline)
        with self.assertRaisesRegex(RuntimeError, "time limit"):
            sketch.restore_backing(self.draft, self.refined, self.output, deadline=time.monotonic() - 1)
        with patch.object(
            sketch, "_separated", side_effect=self.fake_stems([self.drums, self.bass, self.line], [])
        ):
            with sketch.build_arrangement_sketch(
                self.guide, self.draft, self.tmp / "sketch.wav", deadline=self.deadline
            ) as cache:
                write(self.draft, self.line)
                with self.assertRaisesRegex(ValueError, "different generated performance"):
                    sketch.restore_backing(
                        self.draft, self.refined, self.output, cache=cache, deadline=self.deadline
                    )
        write(self.guide, self.line[:-1])
        with self.assertRaisesRegex(ValueError, "matching timelines"):
            sketch.build_arrangement_sketch(self.guide, self.draft, self.output, deadline=self.deadline)
        self.assertEqual(self.output.read_bytes(), b"previous")

    def test_scnet_windows_reconstruct_stereo_without_gain_ripples(self):
        import scnet

        rate = sketch.RATE
        t = np.arange(scnet.SEGMENT + rate + 37) / rate
        pcm = np.stack((0.12 * np.sin(2 * np.pi * 290 * t), 0.09 * np.cos(2 * np.pi * 630 * t)), axis=1)
        write(self.draft, pcm)

        class Identity:
            def run(self, outputs, inputs, options):
                return [np.concatenate([inputs["mix_spec"]] * 4, axis=1)]

        with patch.object(scnet, "session", return_value=Identity()), wave.open(str(self.draft), "rb") as wav:
            stats = sketch._statistics(wav, self.deadline)
            blocks = list(sketch._separated(wav, stats, self.deadline, (0,)))
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0][0], 0)
            self.assertEqual(blocks[1][0], len(blocks[0][1]))
            rebuilt = np.concatenate([block[:, 0] for _, block in blocks])
            np.testing.assert_allclose(rebuilt + stats[0], read(self.draft), atol=1e-6)


if __name__ == "__main__":
    unittest.main()
