import unittest

from common import DEFAULT_RECIPE, EngineError, recipe
from edit_commands import guided_plan
from grooves import groove_notes
from planner import slot_patch


class DrumGridTests(unittest.TestCase):
    def test_guided_switch_preserves_other_settings_and_restores_the_pulse(self):
        parent = {**DEFAULT_RECIPE, 'drums': True, 'mix': {'lead': {'gainDb': -3, 'pan': .5, 'muted': False}}}
        swing = guided_plan('edit: use swing ride at 120 bpm from 710 ms', parent)['recipe']
        self.assertEqual(swing, {**parent, 'bpm': 120, 'groove': {'pattern': 'swing_ride', 'downbeatMs': 710}})
        straight = guided_plan('edit: use four on the floor at 120 bpm from 710 ms', swing)['recipe']
        self.assertEqual(straight['groove']['pattern'], 'four_on_the_floor')
        self.assertEqual(guided_plan('edit: restore the original pulse', straight)['recipe'], parent)
        slots = {'supported': True, 'instrument': 'guitar', 'bass': 'KEEP', 'drums': 'KEEP',
                 'density': 'KEEP', 'backingVolume': 'KEEP'}
        self.assertEqual(slot_patch(slots, swing)[0], {**swing, 'instrument': 'guitar'})

    def test_invalid_grid_and_conflicting_commands_cannot_be_approved(self):
        valid = {**DEFAULT_RECIPE, 'bpm': 120, 'groove': {'pattern': 'swing_ride', 'downbeatMs': 990}}
        self.assertEqual(recipe(valid, 1), valid)
        for clip_length, patch in [(0.999, {}), (12, {'bpm': True}), (12, {'bpm': 201}),
                                   (12, {'groove': {'downbeatMs': 10.5, 'pattern': 'swing_ride'}}),
                                   (12, {'groove': {'downbeatMs': 0, 'pattern': 'waltz'}})]:
            with self.subTest(patch=patch), self.assertRaises(EngineError):
                recipe({**valid, **patch}, clip_length)
        command = 'use swing ride at 120 bpm from 710 ms'
        for clauses in ([command, 'remove drums'], ['remove drums', command], [command, 'restore the original pulse']):
            with self.subTest(clauses=clauses), self.assertRaises(EngineError):
                guided_plan('edit: ' + '; '.join(clauses), DEFAULT_RECIPE)

    def test_swing_and_four_on_floor_have_distinct_subdivisions_and_respect_the_pickup(self):
        for pattern in ['swing_ride', 'four_on_the_floor']:
            notes = groove_notes(pattern, 120, .71, 2.71)
            self.assertEqual(min(n['start'] for n in notes), .71)
            self.assertLessEqual(max(n['end'] for n in notes), 2.71)
            sparse = groove_notes(pattern, 120, .71, 2.71, sparse=True)
            self.assertLess(len(sparse), len(notes))
            soft = groove_notes(pattern, 120, .71, 2.71, soft=True)
            self.assertEqual(len(soft), len(notes))
            self.assertTrue(all(a['velocity'] < b['velocity'] for a, b in zip(soft, notes)))
            if pattern == 'swing_ride':
                ride = [round((n['start'] - .71) / .5, 4) for n in notes if n['pitch'] == 51]
                self.assertEqual(ride, [0, 1, 1.6667, 2, 3, 3.6667])
            else:
                kicks = [round((n['start'] - .71) / .5, 4) for n in notes if n['pitch'] == 36]
                self.assertEqual(kicks, [0, 1, 2, 3])
