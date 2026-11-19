"""Verify revoiced lead retention without reading a song or starting any models."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np

import rendition_lead as lead


def write(path, pcm):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((2, 2, lead.RATE, len(pcm), "NONE", "not compressed"))
        audio.writeframes(pcm.astype("<i2").tobytes())


def read(path):
    with wave.open(str(path), "rb") as audio:
        assert audio.getparams()[:3] == (2, 2, lead.RATE)
        return np.frombuffer(audio.readframes(audio.getnframes()), "<i2").reshape(-1, 2)


class RequestedLeadTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.guide, self.refined, self.output = (root / name for name in ("guide.wav", "take.wav", "result.wav"))
        self.deadline = time.monotonic() + 10
        size = lead.WINDOW
        phase = 2 * np.pi * np.arange(size) / size
        self.note = np.rint(2000 * np.sin(31 * phase)).astype("int16")
        self.accompaniment = np.rint(2000 * np.sin(53 * phase)).astype("int16")

    def render(self):
        lead.retain_requested_lead(self.guide, self.refined, self.output, deadline=self.deadline)
        return read(self.output)

    def test_missing_timbre_follows_the_leads_attack_decay_and_rest(self):
        count = lead.WINDOW
        envelope = np.exp(-2 * np.arange(count) / count)
        envelope[:64] *= np.linspace(0, 1, 64)
        expressive = np.rint(self.note * envelope)
        melody = np.concatenate((expressive, np.zeros(count), .5 * expressive, np.zeros(17))).astype("int16")
        ensemble = np.resize(self.accompaniment, len(melody))
        guide = np.column_stack((melody, np.zeros_like(melody)))
        dry = np.column_stack((np.zeros_like(melody), ensemble))
        write(self.guide, guide)
        write(self.refined, dry)
        out = self.render()
        self.assertEqual(dry.shape, out.shape)
        np.testing.assert_array_equal(out[:, 1], dry[:, 1])
        np.testing.assert_array_equal(out[melody == 0], dry[melody == 0])
        active = np.arange(len(melody)) // count
        active = (active == 0) | (active == 2)
        mel = melody[active].astype("float64")
        bed = ensemble[active].astype("float64")
        gained = out[active, 0].astype("float64")
        expected = lead.LEAD_RATIO * np.linalg.norm(bed) / np.linalg.norm(mel)
        self.assertAlmostEqual(lead.LEAD_RATIO, np.linalg.norm(gained) / np.linalg.norm(bed), places=4)
        np.testing.assert_allclose(gained, expected * mel, atol=.51)

    def test_a_surviving_lead_is_not_doubled_including_with_other_instruments(self):
        g = np.column_stack((self.note, self.note))
        b = np.column_stack((self.accompaniment, self.accompaniment))
        write(self.guide, g)
        for performance in (g, -g, np.roll(g, 33, axis=0), g + b, -g + b, g // 2):
            with self.subTest(peak=int(np.max(performance))):
                write(self.refined, performance)
                np.testing.assert_array_equal(performance, self.render())

    def test_matching_pitch_with_missing_partials_still_needs_the_requested_timbre(self):
        phase = 2 * np.pi * 31 * np.arange(lead.WINDOW) / lead.WINDOW
        harmonics = .4 * np.sin(3 * phase) + .25 * np.sin(5 * phase) + .15 * np.sin(7 * phase)
        bowed = np.rint(1800 * (np.sin(phase) + harmonics)).astype("int16")
        simple = np.rint(1800 * np.sin(phase)).astype("int16")
        write(self.guide, np.column_stack((bowed, bowed)))
        write(self.refined, np.column_stack((simple, simple)))
        addition = self.render()[:, 0].astype("float64") - simple
        self.assertGreater(np.dot(addition, harmonics) / np.dot(bowed, harmonics), .9)

    def test_clipping_uses_one_gain_including_through_rests_and_quiet_guides_are_bounded(self):
        zeros = np.zeros_like(self.note)
        line = np.tile(np.concatenate((self.note * 10, zeros))[:, None], (1, 2))
        dry = np.tile(np.tile(self.accompaniment * 10, 2)[:, None], (1, 2))
        write(self.guide, line)
        write(self.refined, dry)
        out = self.render().astype("float64")
        self.assertAlmostEqual(lead.HEADROOM * 32768, np.max(np.abs(out)), delta=1.)
        rest = slice(lead.WINDOW, None)
        old = dry[rest].astype("float64")
        level = np.sum(out[rest] * old) / np.sum(old * old)
        self.assertLess(level, 1)
        np.testing.assert_allclose(out[rest], level * old, atol=1.)

        tiny = np.column_stack((self.note // 24, zeros))
        loud = np.column_stack((zeros, self.accompaniment * 5))
        write(self.guide, tiny)
        write(self.refined, loud)
        result = self.render()
        np.testing.assert_array_equal(result[:, 0], tiny[:, 0] * lead.MAX_GUIDE_GAIN)

    def test_invalid_timeline_alias_and_timeout_keep_prior_output(self):
        signal = np.column_stack((self.note, self.accompaniment))
        write(self.guide, signal)
        write(self.refined, signal[:-1])
        self.output.write_bytes(b"previous take")
        with self.assertRaisesRegex(ValueError, "matching timelines"):
            self.render()
        write(self.refined, signal)
        alias = self.guide.with_name("alias.wav")
        alias.hardlink_to(self.guide)
        with self.assertRaisesRegex(ValueError, "separately"):
            lead.retain_requested_lead(self.guide, self.refined, alias, deadline=self.deadline)
        # Expire after the first two passes, before the staged WAV can be published.
        with patch.object(lead, "_remaining", side_effect=[1., 1., RuntimeError("time limit")]):
            with self.assertRaisesRegex(RuntimeError, "time limit"):
                self.render()
        self.assertEqual(b"previous take", self.output.read_bytes())
        np.testing.assert_array_equal(signal, read(self.guide))


if __name__ == "__main__":
    unittest.main()
