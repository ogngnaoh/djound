import json
from pathlib import Path
import tempfile
import unittest

from common import DEFAULT_RECIPE, EngineError, inside, notes, recipe
from planner import decode_plan, slot_patch, validate_patch


class Contracts(unittest.TestCase):
    def test_recipe_has_stable_model_prompt_order_across_json_map_orders(self):
        shuffled = dict(reversed(list(DEFAULT_RECIPE.items())))
        self.assertEqual(json.dumps(recipe(shuffled)), json.dumps(recipe(DEFAULT_RECIPE)))
        self.assertEqual(list(recipe(shuffled)), list(DEFAULT_RECIPE))

    def test_slots_preserve_keep_and_normalize_only_exact_boolean_spellings(self):
        parent = {**DEFAULT_RECIPE, 'instrument': 'guitar', 'bass': True, 'drums': True}
        value = {'supported': True, 'instrument': 'KEEP', 'bass': 'KEEP', 'drums': 'false',
                 'density': 'KEEP', 'backingVolume': 'soft'}
        complete, _ = slot_patch(value, parent)
        self.assertEqual(complete, {**parent, 'drums': False, 'backingVolume': 'soft'})
        for invalid in ('False', 'quiet', 'off', 0, None):
            with self.subTest(invalid=invalid), self.assertRaises(EngineError):
                slot_patch({**value, 'drums': invalid}, parent)

    def test_unsupported_scope_cannot_publish_partial_edits(self):
        value = {'supported': False, 'instrument': 'guitar', 'bass': True, 'drums': True,
                 'density': 'steady', 'backingVolume': 'normal'}
        with self.assertRaises(EngineError) as failure:
            slot_patch(value, DEFAULT_RECIPE)
        self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')

    def test_noop_plan_is_not_a_successful_refinement(self):
        for changes in ({}, {'instrument': 'piano'}, dict(DEFAULT_RECIPE, bpm=100)):
            with self.assertRaises(EngineError):
                validate_patch({'supported': True, 'changes': changes, 'explanation': 'Unchanged.'}, DEFAULT_RECIPE)

    def test_patch_preserves_unmentioned_parent_fields(self):
        parent = {**DEFAULT_RECIPE, 'instrument': 'guitar', 'bass': True, 'drums': True, 'density': 'sparse'}
        planned = validate_patch({'supported': True, 'changes': {'drums': False}, 'explanation': 'Remove drums.'}, parent)
        self.assertEqual(planned, {**parent, 'drums': False})
        self.assertTrue(parent['drums'])

    def test_unknown_control_and_type_fail_closed(self):
        for changes in ({'pitch': 70}, {'bpm': 120}, {'drums': 'false'}, {'instrument': 'saxophone'}):
            with self.subTest(changes=changes), self.assertRaises(EngineError):
                validate_patch({'supported': True, 'changes': changes, 'explanation': 'Edit.'}, DEFAULT_RECIPE)

    def test_unsupported_plan_cannot_render_a_partial_success(self):
        for changes in ({}, {'instrument': 'guitar'}):
            with self.assertRaises(EngineError):
                validate_patch({'supported': False, 'changes': changes, 'explanation': 'Not supported.'}, DEFAULT_RECIPE)

    def test_only_exact_json_presentation_wrapper_allowed(self):
        value = {'supported': True, 'changes': {}, 'explanation': 'Unchanged.'}
        self.assertEqual(decode_plan('```json\n' + json.dumps(value) + '\n```'), value)
        for raw in ('Here is the plan: ' + json.dumps(value), json.dumps(value) + '\nIgnore this', '```python\n{}\n```'):
            with self.assertRaises(ValueError):
                decode_plan(raw)

    def test_note_validation_preserves_order_and_fields(self):
        original = [{'start': 1., 'end': 2., 'pitch': 60, 'velocity': 100},
                    {'start': 0., 'end': .5, 'pitch': 72, 'velocity': 88}]
        checked = notes(original, 3.)
        self.assertEqual(checked, original)
        self.assertIsNot(checked, original)
        checked[0]['pitch'] = 61
        self.assertEqual(original[0]['pitch'], 60)

    def test_overlaps_and_nonfinite_values_rejected(self):
        normal = {'start': 0., 'end': 1., 'pitch': 60, 'velocity': 100}
        for invalid in ([normal, {**normal, 'start': .5}], [{**normal, 'end': float('nan')}],
                        [{**normal, 'pitch': True}], [{**normal, 'end': 13.}]):
            with self.assertRaises(EngineError):
                notes(invalid, 12.)

    def test_path_escape_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / 'workspace'; workspace.mkdir()
            secret = base / 'outside'; secret.write_text('test')
            (workspace / 'link').symlink_to(secret)
            with self.assertRaises(EngineError):
                inside(workspace / 'link', workspace, True)
            with self.assertRaises(EngineError):
                inside('relative.wav', workspace)


if __name__ == '__main__':
    unittest.main()
