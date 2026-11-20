import unittest

import numpy as np

from rendition_motifs import follow_supported_repeat


class SupportedRepeatTest(unittest.TestCase):
    def setUp(self):
        # Rounded timings from the two TRIOS Schubert statements. The second ends
        # with two high competing notes in the initial foreground estimate.
        self.lead = [(a, b, key, .72) for a, b, key in (
            (.54, .84, 65), (.86, 1.18, 67), (1.18, 2.36, 68), (2.36, 3.28, 67),
            (3.28, 3.72, 65), (3.72, 4.82, 63),
            (5.56, 5.86, 65), (5.86, 6.16, 67), (6.16, 7.42, 68), (7.42, 8.30, 67),
            (8.32, 8.58, 83), (8.68, 9.48, 84),
        )]
        self.ending = [(8.328, 8.431, 65, .68), (8.431, 8.963, 65, .59), (8.720, 9.251, 63, .40)]
        self.strength = np.zeros((500, 88))
        # Median harmonic salience from the original mix during each detected note.
        self.strength[416:449, 65 - 21] = .88
        self.strength[436:463, 63 - 21] = .49

    def test_two_new_attacks_support_the_repeat_without_extending_its_release(self):
        selected = follow_supported_repeat(self.lead, self.lead + self.ending, self.strength, 50)
        self.assertEqual(selected[:-2], self.lead[:-2])
        self.assertEqual(selected[-2:], [(8.328, 8.720, 65, .68), (8.720, 9.251, 63, .40)])

    def test_one_matching_note_under_an_alternate_ending_cannot_complete_the_earlier_phrase(self):
        for ending in (self.ending[:-1], self.ending[-1:], [*self.ending[:-1], (8.720, 9.251, 63, .15)]):
            with self.subTest(ending=ending):
                self.assertIs(follow_supported_repeat(self.lead, self.lead + ending, self.strength, 50), self.lead)

    def test_a_stale_pitch_candidate_cannot_supply_a_quiet_or_missing_player(self):
        self.strength[:, 63 - 21] = .08
        self.assertIs(follow_supported_repeat(self.lead, self.lead + self.ending, self.strength, 50), self.lead)

    def test_a_real_alternate_ending_in_the_same_register_is_preserved(self):
        alternate = self.lead[:-2] + [(8.32, 8.62, 65, .8), (8.64, 8.96, 67, .8), (8.96, 9.88, 68, .8)]
        self.assertIs(follow_supported_repeat(alternate, alternate + self.ending, self.strength, 50), alternate)

    def test_an_unrelated_distant_phrase_cannot_become_a_template(self):
        def later(note):
            a, b, key, confidence = note
            return a + 50., b + 50., key, confidence

        lead = self.lead[:6] + [later(note) for note in self.lead[6:]]
        support = np.pad(self.strength, ((2500, 0), (0, 0)))
        self.assertIs(follow_supported_repeat(lead, lead + [later(n) for n in self.ending], support, 50), lead)


if __name__ == "__main__":
    unittest.main()
