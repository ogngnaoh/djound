import unittest
from unittest.mock import patch

import numpy as np

import rendition_voice_player as player


class VoicePlayerTest(unittest.TestCase):
    def phrase(self, shift=0):
        def note(a, b, pitch):
            return (a, b, pitch + shift, 0.8)

        phrase = [note(0, 0.5, 60), note(0.5, 1, 62), note(1, 1.5, 64), note(1.5, 2, 65), note(2.5, 3, 67)]
        separated = [*phrase[:2], *phrase[3:]]
        sustain = note(1.5, 2.5, 65)
        part = [*phrase[:3], sustain, phrase[-1]]
        bass = [note(i / 2, (i + 1) / 2, 46 + i % 2) for i in range(6)]
        # Both guides share a missed attack. A backing note also continues
        # through the later phrase break in both instrument views.
        mix = [*phrase[:3], sustain, phrase[-1]]
        return separated, mix, sorted([*part, *bass]), part, bass

    def test_recovers_a_supported_entrance_and_keeps_the_old_breath(self):
        for program, transpose in ((11, 0), (79, -9)):
            with self.subTest(program=program, transpose=transpose):
                separated, mix, events, part, bass = self.phrase(transpose)
                views = [{program: part, 6: bass}, {program: part, 6: bass}]
                self.assertTrue(player.needs_voice_player(separated, mix, 3))
                with patch.object(player, "_harmonics", return_value=(np.full((150, 88), 0.4), None)):
                    result = player.select_voice_player(None, events, separated, mix, views, 3)
                self.assertEqual(64 + transpose, next(p for a, b, p, _ in result if a <= 1.25 < b))
                self.assertFalse(any(a <= 2.25 < b for a, b, *_ in result))

    def test_a_corroborated_singer_or_an_ambiguous_ensemble_keeps_the_voice(self):
        separated, mix, events, part, bass = self.phrase()
        for shared in ({100: separated, 11: part}, {101: separated, 11: part}, {11: part, 12: part}):
            with (
                self.subTest(shared=list(shared)),
                patch.object(player, "_harmonics", side_effect=AssertionError),
            ):
                self.assertIsNone(
                    player.select_voice_player(None, events, separated, mix, [shared, shared], 3)
                )

    def test_missing_mix_agreement_does_not_load_the_player_model(self):
        separated, mix, events, part, bass = self.phrase()
        self.assertFalse(player.needs_voice_player(separated, bass, 3))

    def test_an_old_attack_ending_before_the_rest_cannot_sound_again_in_it(self):
        rolls = np.ones((60, 88))
        voice = np.ones((60, 88))
        voice[20:40] = 0.0
        old = [(0.3, 0.34, 62, 0.8)]
        timed = player._by_pitch(old)
        player._keep_rests(rolls, voice, old, timed, timed)
        np.testing.assert_array_equal(rolls[20:40], np.full((20, 88), -5.0))
