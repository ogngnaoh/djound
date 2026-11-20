import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import rendition_melody as melody


class VocalForegroundTest(unittest.TestCase):
    def singer(self, share, *, strict=False, starts=(1., 1.6, 2.2), strengths=None):
        centers = (np.arange(len(share)) + .5) / 10
        duration = len(share) / 10
        times = np.arange(0, duration, .02)
        pitch = np.full(len(times), np.nan)
        confidence = np.zeros(len(times))
        notes = [(start, start + .55, 60 + i, .8) for i, start in enumerate(starts)]
        for start, end, key, strength in notes:
            active = (times >= start) & (times < end)
            pitch[active], confidence[active] = key, strength
        mix = np.ones(len(share)) * .1 if strengths is None else strengths
        def notes_above_voicing_floor(stem, frames, pitches, gated_confidence):
            return [note for note in notes if
                    np.median(gated_confidence[(frames >= note[0]) & (frames < note[1])]) >= .15]

        with (
            patch("rendition_sketch.backing_available", return_value=True),
            patch("rendition_sketch.vocal_reference", return_value=(centers, share * mix, mix)),
            patch.object(melody, "_fundamental", return_value=(times, pitch, confidence)),
            patch.object(melody, "_voiced_notes", side_effect=notes_above_voicing_floor),
        ):
            kwargs = {"require_foreground": True} if strict else {}
            return melody._singer_in_mix(Path("mix.wav"), Path("."), notes, duration, 10., **kwargs)

    def test_legacy_route_retains_coherent_but_quiet_stem(self):
        share = np.full(40, .24)
        self.assertEqual(len(self.singer(share)), 3)
        self.assertFalse(self.singer(share, strict=True))

    def test_distributed_leaks_do_not_establish_a_local_phrase(self):
        share = np.full(100, .24)
        share[::3] = .7
        self.assertFalse(self.singer(share, strict=True))

    def test_loud_unpitched_intro_does_not_confirm_later_pitched_leak(self):
        share = np.full(60, .24)
        share[:16] = .8
        self.assertFalse(self.singer(share, starts=(2., 2.6, 3.2), strict=True))

    def test_short_phrase_in_long_track_and_quieter_notes(self):
        share = np.full(1200, .01)
        share[801:819] = .70
        share[805:807] = .20
        share[819:831] = .24
        result = self.singer(share, starts=(80.1, 80.8, 81.4, 82.0), strict=True)
        self.assertEqual([key for _, _, key, _ in result], [60, 61, 62, 63])

    def test_inaudible_windows_do_not_qualify_at_high_share(self):
        share = np.full(40, .24)
        share[:16] = .8
        level = np.full(40, .1)
        level[:16] = .0001
        self.assertFalse(self.singer(share, strict=True, strengths=level))


if __name__ == "__main__":
    unittest.main()
