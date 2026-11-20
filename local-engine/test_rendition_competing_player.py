import unittest

from rendition_competing_player import veto_competing_attacks


def notes(pitches, start=0., step=.4):
    return [(start + i * step, start + (i + 1) * step, p, .85) for i, p in enumerate(pitches)]


class CompetingPlayerTest(unittest.TestCase):
    def test_rest_instead_of_following_a_backing_part_above_or_below_the_theme(self):
        for direction, shift in ((1, 0), (-1, 0), (1, 5), (-1, -3)):
            with self.subTest(direction=direction, shift=shift):
                phrase = notes([60 + shift + direction * delta for delta in (0, 2, 0, 12, 14, 0)])
                lead = phrase[:3] + phrase[5:]
                backing = phrase[3:5]
                # The first view puts both the theme and the accompaniment in one program.
                views = [{40: phrase}, {40: lead, 21: backing}]
                self.assertEqual(
                    veto_competing_attacks(phrase, views, [{40: lead}, {40: lead}], 120),
                    lead,
                )

    def test_keeps_a_doubled_leap_a_solo_leap_and_indeterminate_evidence(self):
        phrase = notes([60, 62, 60, 79])
        theme, leap = phrase[:-1], phrase[-1:]
        for voices, selected in [
            ([{40: phrase, 41: leap}] * 2, [{40: phrase}] * 2),
            ([{40: theme}] * 2, [{40: theme}] * 2),
            ([{40: theme, 41: leap}, {40: theme}], [{40: theme}] * 2),
            ([{40: theme, 128: leap}] * 2, [{40: theme}] * 2),
            ([{41: leap}, {41: leap}], [{40: []}] * 2),
            ([{40: theme, 41: leap}], [{40: theme}]),
        ]:
            with self.subTest(voices=voices):
                self.assertEqual(veto_competing_attacks(phrase, voices, selected, 80), phrase)

    def test_small_interval_requires_a_consistently_identified_player(self):
        phrase = notes([60, 62, 63, 60, 62, 63, 60, 62, 63, 60, 62, 64])
        target, piano = phrase[:-1], phrase[-1:]
        trusted = {41: target, 0: piano}
        self.assertEqual(veto_competing_attacks(phrase, [trusted] * 2, [{41: target}] * 2, 240), target)
        uncertain = {41: target[::2], 0: piano}
        self.assertEqual(
            veto_competing_attacks(phrase, [uncertain] * 2, [{41: target[::2]}] * 2, 240),
            phrase,
        )

    def test_a_short_or_stale_history_cannot_establish_the_previous_register(self):
        phrase = notes([60], step=.14) + notes([84], start=2.4)
        views = [{40: phrase[:1], 0: phrase[1:]}] * 2
        self.assertEqual(veto_competing_attacks(phrase, views, [{40: phrase[:1]}] * 2, 150), phrase)
