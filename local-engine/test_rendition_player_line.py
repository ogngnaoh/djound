import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from rendition_foreground import RATE
from rendition_player_line import needs_player_analysis, select_player_line


def ensemble(source, flip=False, transpose=0):
    """Eight short bars with a moving inner theme and faster counterpoint."""
    theme = [60, 64, 62, 65, 63, 61, 64, 62]
    duration = 0.8 * len(theme)
    wave = np.zeros(round(RATE * duration))
    players = {11: [], 35: [], 67: []}
    events, scattered, tune, bass, outer = [], [], [], [], []

    def play(start, end, pitch, volume):
        pitch = (132 - pitch if flip else pitch) + transpose
        a, b = round(start * RATE), round(end * RATE)
        t = np.arange(b - a) / RATE
        phase = 2 * np.pi * (440 * 2 ** ((pitch - 69) / 12)) * t
        envelope = np.clip(np.minimum(t, end - start - t) / 0.01, 0, 1)
        wave[a:b] += volume * envelope * (np.sin(phase) + 0.25 * np.sin(2 * phase))
        note = (start, end, pitch, 0.8)
        events.append(note)
        return note

    for bar, pitch in enumerate(theme):
        start, end = 0.8 * bar, 0.8 * (bar + 1)
        main = play(start, end, pitch, 0.35)
        low = play(start, end, (44, 47, 45, 48)[bar % 4], 0.1)
        tune.append(main)
        bass.append(low)
        players[35].append(low)
        # The fast and slow phrases can both be allocated to one model player.
        players[11 if bar % 2 else 67].append(main)
        for count, step in enumerate([0, 3, 1, 4, 2, 5]):
            a, b = start + 0.8 * count / 6, start + 0.8 * (count + 1) / 6
            quick = play(a, b, 80 + step, 0.08)
            players[11].append(quick)
            outer.append(quick)
            scattered.append(quick if count % 2 else (a, b, low[2], 0.8))
    sf.write(source, wave, RATE)
    return duration, sorted(events), scattered, players, tune, bass, outer


class PlayerLineTest(unittest.TestCase):
    def test_recovers_inner_voice_with_rapid_figure_above_or_below(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "mix.wav"
            for flip, transpose in ((False, 0), (True, 0), (False, 5), (True, -3)):
                with self.subTest(flip=flip, transpose=transpose):
                    duration, events, mixed, parts, tune, *_ = ensemble(source, flip, transpose)
                    notes = select_player_line(source, events, mixed, [parts, parts], duration)
                    self.assertIsNotNone(notes)
                    for a, b, p, *_ in tune:
                        self.assertEqual([p], [k for x, y, k, _ in notes if x <= (a + b) / 2 < y])

    def test_keeps_a_fast_lead_bass_and_a_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "mix.wav"
            duration, events, mixed, parts, tune, bass, outer = ensemble(source)
            # The fast part can itself be an angular tune. It may leap enough to
            # trigger the first screen, with the full player track corroborating it.
            fast = [(a, b, p + (12 if i % 2 else 0), c) for i, (a, b, p, c) in enumerate(outer)]
            parts[11] = fast
            events = [*events, *fast]
            bass_jump = [(a, b, p + (12 if i % 2 else 0), c) for i, (a, b, p, c) in enumerate(bass)]
            parts[35] = bass_jump
            handoff = [*fast[: len(fast) // 2], *bass_jump[len(bass_jump) // 2 :]]
            for incumbent in (fast, bass_jump, handoff):
                with self.subTest(incumbent=incumbent[:1]):
                    self.assertTrue(needs_player_analysis(incumbent, events, duration))
                    with patch(
                        "rendition_player_line._harmonics", side_effect=AssertionError("unnecessary FFT")
                    ):
                        self.assertIsNone(select_player_line(source, events, incumbent, [parts], duration))

    def test_sparse_leaps_do_not_schedule_the_instrument_model(self):
        notes = [(i * 0.3, (i + 1) * 0.3, 60 + 24 * (i % 2), 0.8) for i in range(16)]
        self.assertFalse(needs_player_analysis(notes, notes, 4.8))
