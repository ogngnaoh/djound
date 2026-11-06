from copy import deepcopy
from itertools import permutations, product
import unittest
from unittest.mock import patch

from common import DEFAULT_RECIPE, UNITY_STRIP, EngineError, canonical_hash, recipe
from edit_commands import guided_plan
from planner import plan


class GuidedCommands(unittest.TestCase):
    def reject(self, prompt, parent, code='LOCAL_UNSUPPORTED_REQUEST'):
        before = deepcopy(parent)
        with self.assertRaises(EngineError) as failure:
            guided_plan(prompt, parent)
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(parent, before)
        return failure.exception.message

    def test_actual_planner_routes_only_prefixed_commands_without_loading_a_model(self):
        with patch.dict('sys.modules', {'mlx.core': None, 'mlx_lm': None}):
            result = plan('  EDIT:  Use Electric PIANO; add bass; ADD drums', None, None)
            self.assertEqual(result['recipe'], dict(DEFAULT_RECIPE, instrument='electric_piano', bass=True, drums=True))
            self.assertEqual(result['provenance']['origin'], 'GUIDED_COMMANDS')
            self.assertEqual(result['provenance']['inferenceAttempts'], 0)
            self.assertNotIn('model', result['provenance'])
            self.assertEqual(result['provenance']['parentRecipeSha256'], canonical_hash(DEFAULT_RECIPE))
            self.assertNotIn('bpm', result['provenance']['changes'])
            with self.assertRaises(EngineError):
                plan('edit: add bass; add harmony', None, None)
        for free in ('add drums', 'Please edit: add drums', 'editor: mute bass', 'guitar'):
            self.assertIsNone(guided_plan(free, None))
        self.reject('\N{NO-BREAK SPACE}edit: add drums; mélange', None)

    def test_arrangement_controls_across_all_48_parents(self):
        instruments = ('piano', 'electric piano', 'guitar')
        toggle = (False, True)
        fields = product(instruments, toggle, toggle, ('sparse', 'steady'), ('soft', 'normal'))
        for instrument, bass, drums, density, volume in fields:
            parent = dict(DEFAULT_RECIPE, instrument=instrument.replace(' ', '_'), bass=bass,
                          drums=drums, density=density, backingVolume=volume)
            if bass:
                parent['mix'] = {'bass': dict(UNITY_STRIP, gainDb=-3, pan=.3)}
            operations = [(f'use {name}', 'instrument', name.replace(' ', '_')) for name in instruments]
            operations += [(f'{verb} {part}', part, verb == 'add') for part in ('bass', 'drums')
                           for verb in ('add', 'remove')]
            operations += [(f'make the backing {value}', key, value) for key, values in
                           [('density', ('sparse', 'steady')), ('backingVolume', ('soft', 'normal'))]
                           for value in values]
            for command, key, expected in operations:
                with self.subTest(parent=parent, command=command):
                    if parent[key] == expected:
                        self.reject('edit: ' + command, parent, 'LOCAL_NO_CHANGE')
                    else:
                        before = deepcopy(parent)
                        result = guided_plan('edit: ' + command, parent)
                        target = dict(before, **{key: expected})
                        if key == 'bass' and not expected:
                            target.pop('mix', None)
                        self.assertEqual(result['recipe'], target)
                        self.assertEqual(before, parent)

    def test_complete_multistep_review_and_return_to_unity(self):
        initial = dict(DEFAULT_RECIPE, bass=True, drums=True)
        colored = guided_plan('edit: pan lead hard left; set lead gain to +1.125 dB; mute drums', initial)
        parent = colored['recipe']
        proposal = guided_plan('edit: lower bass by 6.5 dB; pan bass 50% left; make the backing sparse', parent)
        self.assertEqual(proposal['recipe'], dict(parent, density='sparse', mix={
            **parent['mix'], 'bass': dict(UNITY_STRIP, gainDb=-6.5, pan=-.5)}))
        self.assertEqual(proposal['provenance']['parentRecipeSha256'], canonical_hash(parent))
        restored = guided_plan('edit: set bass gain to 0 dB; center bass; unmute drums; '
                               'center lead; lower lead by 1.125 dB', proposal['recipe'])
        self.assertEqual(restored['recipe'], dict(initial, density='sparse'))
        self.assertEqual(restored['provenance']['changes']['mix'], {})
        self.assertNotIn('bass', parent['mix'])

    def test_mix_uses_parent_gain_and_rejects_out_of_range_instead_of_clamping(self):
        for stem, level, step, verb in product(('lead', 'bass', 'drums'), (-60, -6, 0, 11.5, 12),
                                               (.001, .5, 6, 72), ('raise', 'lower')):
            with self.subTest(stem=stem, level=level, step=step, verb=verb):
                strip = dict(UNITY_STRIP, gainDb=level, pan=.125)
                parent = dict(DEFAULT_RECIPE, bass=True, drums=True, mix={stem: strip})
                prompt = f'edit: {verb} {stem} by {step} dB'
                target = round(level + (step if verb == 'raise' else -step), 3)
                if -60 <= target <= 12:
                    result = guided_plan(prompt, parent)
                    self.assertEqual(result['recipe']['mix'][stem], dict(strip, gainDb=target))
                    self.assertEqual(parent['mix'][stem]['gainDb'], level)
                else:
                    self.reject(prompt, parent)

    def test_pan_absolute_and_hard_endpoints_and_semicolon_order(self):
        initial = dict(DEFAULT_RECIPE, bass=True)
        for clauses in permutations(('set bass gain to -2.5 dB', 'pan bass 27.125% right', 'mute bass')):
            result = guided_plan('edit: ' + '; '.join(clauses), initial)
            self.assertEqual(result['recipe']['mix'], {'bass': dict(UNITY_STRIP, gainDb=-2.5, pan=.27125, muted=True)})
        for stem, side in product(('lead', 'bass', 'drums'), ('left', 'right')):
            parent = dict(DEFAULT_RECIPE, bass=True, drums=True)
            target = dict(UNITY_STRIP, pan=1 if side == 'right' else -1)
            for amount in ('hard', '100%'):
                result = guided_plan(f'edit: pan {stem} {amount} {side}', parent)
                self.assertEqual(result['recipe']['mix'], {stem: target})
                self.assertEqual(guided_plan(f'edit: pan {stem} 0% left', result['recipe'])['recipe'], parent)

    def test_removing_then_adding_a_stem_in_a_new_version_starts_at_unity(self):
        lead = dict(UNITY_STRIP, gainDb=-2, pan=.0625)
        parent = dict(DEFAULT_RECIPE, bass=True, drums=True, mix={
            'lead': lead, 'bass': dict(UNITY_STRIP, pan=-.8, muted=True)})
        removed = guided_plan('edit: remove bass; make the backing soft', parent)['recipe']
        self.assertEqual(removed, dict(parent, bass=False, backingVolume='soft', mix={'lead': lead}))
        added = guided_plan('edit: add bass; raise bass by 1 dB', removed)['recipe']
        self.assertEqual(added['mix'], {'lead': lead, 'bass': dict(UNITY_STRIP, gainDb=1)})
        self.assertIn('bass', parent['mix'])

    def test_activity_checked_before_a_mix_edit_and_after_arranging_backing(self):
        for stem, action in product(('bass', 'drums'), ('mute {}', 'unmute {}', 'center {}',
                                                       'raise {} by 2 dB', 'pan {} hard right')):
            self.reject('edit: ' + action.format(stem) + '; add ' + stem, None)
        with_bass = guided_plan('edit: make the backing soft; add bass; pan bass hard right', None)['recipe']
        self.assertEqual(with_bass, dict(DEFAULT_RECIPE, bass=True, backingVolume='soft',
                                         mix={'bass': dict(UNITY_STRIP, pan=1)}))
        solo = guided_plan('edit: make the backing sparse; remove bass', with_bass)['recipe']
        self.assertEqual(solo, dict(DEFAULT_RECIPE, density='sparse', backingVolume='soft'))
        self.assertEqual(guided_plan('edit: add drums', solo)['recipe'], dict(solo, drums=True))

    def test_mute_and_unmute_use_the_complete_final_selection(self):
        parent = dict(DEFAULT_RECIPE, bass=True, mix={'bass': dict(UNITY_STRIP, muted=True)})
        self.reject('edit: mute lead; use guitar', parent)
        for clauses in permutations(('mute lead', 'unmute bass')):
            result = guided_plan('edit: ' + '; '.join(clauses), parent)
            self.assertEqual(result['recipe'], dict(parent, mix={'lead': dict(UNITY_STRIP, muted=True)}))
        self.reject('edit: remove bass', result['recipe'])

    def test_conflict_redundancy_unknown_clause_and_malformed_amounts_never_partially_apply(self):
        parent = dict(DEFAULT_RECIPE, bass=True, drums=True, mix={'bass': dict(UNITY_STRIP, pan=.2)})
        for text in ('', ';', 'use piano;', '; mute bass', 'unmute drums;; use guitar', 'mute lead; unmute lead',
                     'mute bass; mute bass', 'use guitar; use piano', 'raise bass by 1 dB; set bass gain to 1 dB',
                     'center bass; remove bass', 'remove bass; add bass', 'remove bass; center bass', 'add drum',
                     'use guitar and add reverb', 'if needed mute drums', 'set bass gain to 13 dB',
                     'lower bass by -1 dB', 'raise bass by 0 dB', 'pan bass 100.001% right', 'pan bass -20% left',
                     'set bass gain to 1e1 dB', 'set bass gain to NaN dB', 'raise bass by .1234 dB',
                     'use guitar; ouvrir encore', 'pan drums ５０% left', 'use piano; mute bass, please'):
            with self.subTest(text=text):
                self.reject('edit: ' + text, parent)
                self.reject('edit: make the backing soft; ' + text, parent)
        message = self.reject('edit: add bass; add harmony', None)
        self.assertIn('Command 2', message)
        self.reject('edit: ' + 'a' * 1000, parent, 'LOCAL_INVALID_REQUEST')

    def test_no_change_and_invalid_parent_have_distinct_codes(self):
        parent = dict(DEFAULT_RECIPE, bass=True, mix={'lead': dict(UNITY_STRIP, gainDb=-6)})
        self.reject('edit: add bass; set lead gain to -6 dB; center bass', parent, 'LOCAL_NO_CHANGE')
        self.reject('edit: use guitar', dict(parent, bpm=120), 'LOCAL_INVALID_RECIPE')
        self.assertEqual(recipe(guided_plan('edit: add bass; use guitar', parent)['recipe']),
                         dict(parent, instrument='guitar'))


if __name__ == '__main__':
    unittest.main()
