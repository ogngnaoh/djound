import unittest

from rendition_instrument_notes import decode_segments


class InstrumentNotesTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.vocabulary = {"sample_rate": 16000, "segment": 32767, "tps": 100,
                           "channels": 2, "events": self.events}

    def tokens(self, *events):
        result = []
        for event in events:
            if isinstance(event, str):
                event = (event, 0)
            if event not in self.events:
                self.events.append(event)
            result.append(self.events.index(event))
        return result

    def row(self, *events):
        return [self.tokens(*events), self.tokens("tie", "EOS")]

    def decode(self, *rows, duration=8):
        return decode_segments(rows, self.vocabulary, duration)

    def test_ties_span_multiple_segments_and_shifts_are_absolute(self):
        first = self.row("tie", ("program", 41), ("velocity", 1), ("shift", 193), ("pitch", 60))
        second = self.row(("program", 41), ("pitch", 60), "tie", ("shift", 30),
                          ("velocity", 1), ("pitch", 61), ("shift", 55), ("velocity", 0), ("pitch", 61))
        third = self.row(("program", 41), ("pitch", 60), "tie", ("shift", 4),
                         ("velocity", 0), ("pitch", 60), ("drum", 36))
        result = self.decode(first, second, third)
        self.assertEqual(result[41], [(1.93, 4.14, 60, 1.0), (2.35, 2.60, 61, 1.0)])
        self.assertEqual(result[128], [(4.14, 4.14 + .01, 36, 1.0)])

    def test_tie_in_another_channel_or_program_cannot_continue_the_attack(self):
        first = self.row("tie", ("program", 41), ("velocity", 1), ("shift", 193), ("pitch", 60))
        second = [self.tokens(("program", program), ("pitch", 60), "tie", ("shift", 4),
                              ("velocity", 0), ("pitch", 60)) for program in (40, 41)]
        self.assertEqual(self.decode(first, second), {41: [(1.93, 32767 / 16000, 60, 1.0)]})

    def test_reattacks_and_final_unreleased_note(self):
        events = self.row("tie", ("program", 42), ("velocity", 1), ("pitch", 58),
                          ("shift", 20), ("pitch", 58), ("velocity", 0), ("pitch", 58))
        self.assertEqual(self.decode(events), {42: [(0., .2, 58, 1.0), (.2, .2 + .01, 58, 1.0)]})

    def test_missing_delimiter_or_velocity_creates_no_attacks(self):
        first = self.row(("shift", 3), "tie", ("program", 48), ("velocity", 1), ("pitch", 60))
        second = self.row(("program", 48), "tie", ("pitch", 61))
        self.assertEqual(self.decode(first, second), {})

    def test_padding_specials_and_unknown_events_preserve_timing(self):
        events = self.row("tie", "PAD", "UNK", ("program", 41), ("velocity", 1), ("shift", 9),
                          ("shift", 0), ("shift", 1001), "unused", ("pitch", 65), "EOS", ("pitch", 66))
        self.assertEqual(self.decode(events), {41: [(.09, .09 + .01, 65, 1.0)]})

    def test_padded_audio_is_clipped_and_later_notes_discarded(self):
        events = self.row("tie", ("program", 40), ("velocity", 1), ("shift", 20), ("pitch", 66),
                          ("shift", 40), ("velocity", 0), ("pitch", 66), ("drum", 36))
        self.assertEqual(self.decode(events, duration=.205), {40: [(.2, .205, 66, 1.0)]})
        self.assertEqual(self.decode(events, duration=.20), {})

    def test_same_instrument_emitted_in_two_channels(self):
        first = self.tokens("tie", ("program", 0), ("velocity", 1), ("pitch", 65),
                            ("shift", 30), ("velocity", 0), ("pitch", 65))
        second = self.tokens("tie", ("program", 0), ("velocity", 1), ("shift", 10),
                             ("pitch", 65), ("shift", 50), ("velocity", 0), ("pitch", 65))
        self.assertEqual(self.decode([first, second]), {0: [(0., .10, 65, 1.), (.10, .5, 65, 1.)]})

    def test_invalid_tokens_channel_width_and_duration_fail_clearly(self):
        empty = self.tokens("tie")
        for invalid in (-1, len(self.events), 0.5, False):
            with self.subTest(token=invalid), self.assertRaisesRegex(ValueError, "token ID"):
                self.decode([empty + [invalid], empty])
        with self.assertRaisesRegex(ValueError, "2 instrument channels"):
            self.decode([empty])
        with self.assertRaisesRegex(ValueError, "duration"):
            self.decode(duration=float("nan"))
