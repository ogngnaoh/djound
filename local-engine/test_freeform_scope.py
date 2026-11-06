"""Checks the production routing and model-response boundaries without loading weights."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from common import DEFAULT_RECIPE, EngineError, HERE, UNITY_STRIP
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
        for request in ('lower the bass by 6 dB and switch to flute',
                        'switch to flute and lower the bass by 6 dB',
                        'mute the bass and put the lead on accordion',
                        'use guitar and add bass and reverse the tape',
                        'pan the drums and add a guitar',
                        'turn up only the bass', 'make the percussion quieter during the chorus'):
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

    def test_compound_operations_match_guided_edits_and_inherit_the_selected_version(self):
        before = deepcopy(self.parent)
        for text in ('switch to guitar and lower the bass by 6 dB', 'lower the bass by 6 dB and switch to guitar'):
            self.assertFalse(needs_freeform({'operation': 'plan', 'prompt': text}))
            first = plan(text, self.parent, None, duration=1)
            self.assertEqual(first['recipe'], dict(self.parent, instrument='guitar',
                             mix={'bass': dict(UNITY_STRIP, gainDb=-9.5, tone='warm')}))
            self.assertEqual(first['provenance']['syntax'], 'reviewed-phrases-v1')
            self.assertEqual(first['provenance']['inferenceAttempts'], 0)
            guided = plan('edit: use guitar; lower bass by 6 dB', self.parent, None, duration=1)
            self.assertEqual(first['recipe'], guided['recipe'])
        second = plan('set backing density to sparse and set bar 1 to D minor and brighten the chords',
                      first['recipe'], None, duration=1)
        self.assertEqual(second['recipe'], dict(first['recipe'], density='sparse',
                         harmony=dict(first['recipe']['harmony'], chords=['Dm']),
                         mix={**first['recipe']['mix'], 'chords':dict(UNITY_STRIP, tone='bright')}))
        self.assertEqual(self.parent, before)

    def test_late_failure_or_conflict_does_not_publish_earlier_actions(self):
        original = deepcopy(self.parent)
        for text in ('use guitar and warm the lead and set bar 2 to Am',
                     'set backing density to sparse and set bass gain to 13 dB',
                     'use guitar and set bass tone to bright and remove bass',
                     'put the lead on guitar and switch to piano'):
            self.assertFalse(needs_freeform({'operation': 'plan', 'prompt': text}))
            with self.assertRaises(EngineError):
                plan(text, self.parent, None, duration=1)
            self.assertEqual(self.parent, original)

    def test_isolated_worker_stage_handles_compounds_without_model_packages_or_assets(self):
        with TemporaryDirectory() as folder:
            work = Path(folder)
            engine_home = work / 'empty-engine'
            engine_home.mkdir()
            env = {**os.environ, 'DJOUND_LOCAL_ENGINE_HOME': str(engine_home), 'PYTHONPATH': ''}
            for text, expected_status in [
                ('use electric piano and suggest chords and warm the chords', 'succeeded'),
                ('use electric piano and suggest chords and set bar 2 to Am', 'failed'),
            ]:
                output = work / expected_status
                output.mkdir()
                request = {'schemaVersion': 1, 'operation': 'plan', 'prompt': text, 'duration': 1,
                           'parentRecipe': DEFAULT_RECIPE,
                           'notes': [{'pitch': 60, 'velocity': 85, 'start': .1, 'end': .7}]}
                path = output / 'request.json'
                path.write_text(json.dumps(request))
                self.assertFalse(needs_freeform(request))
                stage = subprocess.run([sys.executable, '-S', str(HERE / 'stage.py'), str(path), str(output)],
                                       env=env, capture_output=True, timeout=10)
                self.assertEqual(stage.returncode, 0 if expected_status == 'succeeded' else 1)
                result = json.loads((output / 'stage-result.json').read_text())
                self.assertEqual(result['status'], expected_status)
                if expected_status == 'succeeded':
                    self.assertEqual(result['recipe']['instrument'], 'electric_piano')
                    self.assertEqual(result['recipe']['mix']['chords']['tone'], 'warm')
                    self.assertEqual(len(result['recipe']['harmony']['chords']), 1)
                    self.assertEqual(result['provenance']['inferenceAttempts'], 0)
                else:
                    self.assertNotIn('recipe', result)
                self.assertEqual(json.loads(path.read_text()), request)
                self.assertFalse((output / 'planner-output.json').exists())

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

    def test_reviewed_model_fields_and_native_commands_apply_the_same_inheritance_rules(self):
        prompt = 'use guitar and remove bass'
        natural = plan(prompt, self.parent, None, duration=1)
        guided = plan('edit: use guitar; remove bass', self.parent, None, duration=1)
        model, _ = checked_model_patch(dict(self.keep, instrument='guitar', bass=False),
                                       self.parent, prompt, freeform_requirements(prompt))
        self.assertEqual(model, guided['recipe'])
        self.assertEqual(model, natural['recipe'])
        self.assertNotIn('mix', model)
        self.assertEqual(self.parent['harmony'], model['harmony'])
        self.assertEqual(self.parent['mix']['bass']['tone'], 'warm')

    def test_preservation_checks_the_selected_parent_and_the_final_settings(self):
        before = dict(self.parent, instrument='guitar')
        expected = dict(before, density='sparse')
        for text in ('keep that guitar and set backing density to sparse',
                     'set backing density to sparse and retain the current lead instrument',
                     'make the backing sparse and keep everything else unchanged',
                     'edit: keep the same lead instrument; make the backing sparse'):
            self.assertFalse(needs_freeform({'operation': 'plan', 'prompt': text}))
            self.assertEqual(plan(text, before, None, duration=1)['recipe'], expected)
        for text in ('keep that piano and use piano', 'use piano and keep that piano',
                     'use electric piano and keep that instrument',
                     'keep the bass on and remove the bass', 'keep the drums off'):
            with self.subTest(text=text), self.assertRaises(EngineError) as failure:
                plan(text, before, None, duration=1)
            self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
        for text in ('retain the instrument', 'keep everything else the same', 'keep that guitar'):
            with self.assertRaises(EngineError) as failure:
                plan(text, before, None, duration=1)
            self.assertEqual(failure.exception.code, 'LOCAL_NO_CHANGE')

    def test_coordinated_membership_carries_only_the_verb_and_keeps_other_settings(self):
        expected = dict(self.parent, bass=False, drums=False)
        expected.pop('mix')
        for text in ('remove the drums and bass', 'remove bass and the drums',
                     'edit: remove the bass and the drums'):
            self.assertFalse(needs_freeform({'operation': 'plan', 'prompt': text}))
            self.assertEqual(plan(text, self.parent, None, duration=1)['recipe'], expected)
        combined = plan('use guitar; only add the bass and the drums', DEFAULT_RECIPE, None)
        self.assertEqual(combined['recipe'], dict(DEFAULT_RECIPE, instrument='guitar', bass=True, drums=True))
        for text in ('only add the bass if the lead rests', 'only add the drums except at the end',
                     'only add the bass and use guitar', 'do not remove bass and the drums',
                     'remove drums and everything except bass', 'add bass; the drums',
                     'keep the lead instrument and reverse the melody'):
            with self.subTest(text=text), self.assertRaises(EngineError) as failure:
                plan(text, self.parent, None, duration=1)
            self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')

    def test_keep_the_beat_locks_drum_events_and_their_mix_while_other_parts_can_change(self):
        groove = {'pattern': 'swing_ride', 'downbeatMs': 50}
        parent = dict(self.parent, bpm=130, groove=groove, bass=False,
                      mix={'drums': dict(UNITY_STRIP, gainDb=-4, tone='bright')})
        active = plan('preserve the drum pattern; I want the bass added', parent, None, duration=1)
        self.assertEqual(active['recipe'], dict(parent, bass=True))
        for text in ('use swing ride at 150 bpm from 50 ms and keep the beat',
                     'retain the beat and warm the drums',
                     'keep the drum beat and make the backing sparse',
                     'make the backing soft and preserve the drum pattern',
                     'keep the beat and I only want the drums out',
                     'I only want the drums off in the chorus'):
            with self.subTest(text=text), self.assertRaises(EngineError) as failure:
                plan(text, parent, None, duration=1)
            self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
        for no_beat in (dict(DEFAULT_RECIPE),
                        dict(parent, mix={'drums': dict(UNITY_STRIP, muted=True)})):
            with self.assertRaises(EngineError) as failure:
                plan('keep the beat and add bass', no_beat, None, duration=1)
            self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')

    def test_repeating_a_known_edit_keeps_the_response_confined_to_requested_fields(self):
        prompt = 'Remove drums and make the backing quieter and make the backing quieter.'
        required = freeform_requirements(prompt)
        trimmed = dict(self.keep, drums=False, backingVolume='soft')
        complete, _ = checked_model_patch(trimmed, self.parent, prompt, required)
        self.assertEqual(complete, dict(self.parent, drums=False, backingVolume='soft'))
        with self.assertRaises(EngineError):
            checked_model_patch(dict(trimmed, instrument='guitar'), self.parent, prompt, required)
