"""Checks the production routing and model-response boundaries without loading weights."""
from copy import deepcopy
import unittest

from common import DEFAULT_RECIPE, EngineError, UNITY_STRIP
from planner import checked_model_patch, plan
from reviewed_phrases import freeform_requirements
from worker import needs_freeform


class FreeformMixerScope(unittest.TestCase):
    def setUp(self):
        self.parent = dict(DEFAULT_RECIPE, bass=True, drums=True,
                           harmony={'bpm': 100, 'downbeatMs': 0, 'chords': ['C']},
                           mix={'bass': dict(UNITY_STRIP, gainDb=-3.5, tone='warm')})
        self.keep = dict(supported=True, instrument='KEEP', bass='KEEP',
                         drums='KEEP', density='KEEP', backingVolume='KEEP')

    def test_partial_or_unrepresentable_track_requests_stop_before_model_entry(self):
        for request in ('lower the bass by 6 dB and switch to guitar',
                        'switch to guitar and lower the bass by 6 dB',
                        'mute the bass and put the lead on guitar',
                        'pan the drums and add a guitar',
                        'turn up only the bass', 'make the percussion quieter'):
            with self.subTest(request=request):
                before = deepcopy(self.parent)
                with self.assertRaises(EngineError) as failure:
                    needs_freeform({'operation': 'plan', 'prompt': request})
                self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
                with self.assertRaises(EngineError) as failure:
                    plan(request, self.parent, None, duration=1)
                self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
                self.assertEqual(self.parent, before)

    def test_complete_track_commands_target_only_the_selected_strip(self):
        prompt = 'lower the bass by 6 dB and remove drums'
        self.assertFalse(needs_freeform({'operation': 'plan', 'prompt': prompt}))
        result = plan(prompt, self.parent, None, duration=1)
        self.assertEqual(result['recipe'], dict(self.parent, drums=False,
                         mix={'bass': dict(UNITY_STRIP, gainDb=-9.5, tone='warm')}))
        self.assertEqual(result['provenance']['inferenceAttempts'], 0)
        with self.assertRaises(EngineError):
            plan(prompt, dict(DEFAULT_RECIPE, drums=True), None, duration=1)

    def test_model_must_deliver_both_edits_in_the_recorded_demo_direction(self):
        prompt = 'Remove drums and make the backing quieter.'
        required = freeform_requirements(prompt)
        self.assertTrue(needs_freeform({'operation': 'plan', 'prompt': prompt}))
        self.assertEqual(required, ({'drums': False, 'backingVolume': 'soft'}, True))
        for parent in (dict(DEFAULT_RECIPE, drums=True, bass=True), self.parent):
            complete, _ = checked_model_patch(dict(self.keep, drums=False, backingVolume='soft'),
                                              parent, prompt, required)
            self.assertEqual(complete, dict(parent, drums=False, backingVolume='soft'))
            for missing in (self.keep, dict(self.keep, drums=False), dict(self.keep, backingVolume='soft')):
                with self.assertRaises(EngineError) as failure:
                    checked_model_patch(missing, parent, prompt, required)
                self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
            with self.assertRaises(EngineError):
                checked_model_patch(dict(self.keep, drums=False, backingVolume='soft', instrument='guitar'),
                                    parent, prompt, required)

    def test_model_cannot_insert_unrequested_shared_volume(self):
        for text in ('Use guitar.', 'Keep the backing and switch to guitar.',
                     'Keep the backing volume unchanged and switch to guitar.'):
            with self.assertRaises(EngineError) as failure:
                checked_model_patch(dict(self.keep, instrument='guitar', backingVolume='soft'),
                                    self.parent, text, freeform_requirements(text))
            self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')

    def test_repeating_a_known_edit_keeps_the_response_confined_to_requested_fields(self):
        prompt = 'Remove drums and make the backing quieter and make the backing quieter.'
        required = freeform_requirements(prompt)
        trimmed = dict(self.keep, drums=False, backingVolume='soft')
        complete, _ = checked_model_patch(trimmed, self.parent, prompt, required)
        self.assertEqual(complete, dict(self.parent, drums=False, backingVolume='soft'))
        with self.assertRaises(EngineError):
            checked_model_patch(dict(trimmed, instrument='guitar'), self.parent, prompt, required)
