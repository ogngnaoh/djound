"""Exercise complete musical requests through the same planning path as Studio."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from common import DEFAULT_RECIPE, EngineError, HERE, UNITY_STRIP, canonical_hash
from planner import plan
from musical_direction import onset_tempo
from worker import needs_freeform, request_values


def notes_at(onsets):
    pitches = (60, 64, 67, 72)
    return [dict(start=t, end=t + .18, pitch=pitches[i % 4], velocity=70) for i, t in enumerate(onsets)]


class MusicalDirectionTests(unittest.TestCase):
    notes = notes_at([.2, .7, 1.2, 1.7, 2.2])
    duration = 4

    def review(self, prompt, parent=DEFAULT_RECIPE, kind='original', notes=None):
        self.assertFalse(needs_freeform({'operation': 'plan', 'prompt': prompt}))
        return plan(prompt, parent, None, self.notes if notes is None else notes, self.duration, kind)

    def test_exact_request_and_paraphrases_preserve_recorded_lead_and_ground_chart_and_pulse(self):
        for prompt in ('make this soundtrack jazz like', 'Make my track jazz-like.', 'Give it a jazzy feel!',
                       'Can you make this sound more like jazz?', 'arrange this song for a swing combo'):
            with self.subTest(prompt=prompt):
                result = self.review(prompt)
                recipe = result['recipe']
                self.assertEqual(recipe['instrument'], 'recorded')
                self.assertEqual(recipe['accompanimentStyle'], 'swing_combo')
                self.assertTrue(recipe['bass'] and recipe['drums'])
                self.assertEqual(recipe['bpm'], 120)
                self.assertEqual(recipe['groove'], dict(pattern='swing_ride', downbeatMs=200))
                self.assertEqual(recipe['harmony']['chords'], ['C', 'C'])
                provenance = result['provenance']
                self.assertEqual(provenance['syntax'], 'musical-direction-v1')
                self.assertEqual(provenance['inferenceAttempts'], 0)
                self.assertEqual(provenance['pulseSource'], 'regular confirmed note onsets')
                self.assertEqual(provenance['parentRecipeSha256'], canonical_hash(DEFAULT_RECIPE))
                self.assertEqual(len(provenance['chordSuggestions']), 2)
                self.assertEqual(provenance['changes']['accompanimentStyle'], 'swing_combo')
                self.assertIn('swing combo', result['explanation'])

    def test_manual_chart_timbre_mix_and_independent_drum_grid_survive_style_and_followups(self):
        bass = dict(UNITY_STRIP, pan=-.3, tone='warm')
        drums = dict(UNITY_STRIP, gainDb=-2.5, pan=.2, tone='bright')
        chart = dict(bpm=73, downbeatMs=180, chords=['Eb', None])
        parent = dict(DEFAULT_RECIPE, instrument='guitar', bass=True, drums=True, bpm=101,
                      groove=dict(pattern='four_on_the_floor', downbeatMs=60),
                      harmony=chart, mix=dict(bass=bass, drums=drums))
        before = deepcopy(parent)
        result = self.review('turn this into a jazz trio', parent, 'rendition')
        expected = dict(before, accompanimentStyle='swing_combo', groove=dict(pattern='swing_ride', downbeatMs=60))
        self.assertEqual(result['recipe'], expected)
        self.assertNotIn('chordSuggestions', result['provenance'])
        second = self.review('quieter drums', expected, 'rendition')
        expected_mix = dict(parent['mix'], drums=dict(drums, gainDb=-5.5))
        self.assertEqual(second['recipe'], dict(expected, mix=expected_mix))
        self.assertEqual(second['provenance']['drumGainDeltaDb'], -3)
        self.assertEqual(second['provenance']['parentRecipeSha256'], canonical_hash(expected))
        third = self.review('make the percussion quieter', second['recipe'], 'rendition')
        self.assertEqual(third['recipe']['mix']['drums']['gainDb'], -8.5)
        reset = self.review('remove the chords', third['recipe'], 'rendition')
        self.assertNotIn('accompanimentStyle', reset['recipe'])
        self.assertEqual(parent, before)

    def test_chart_grid_and_conservative_irregular_timing_fallback(self):
        charted = dict(DEFAULT_RECIPE, harmony=dict(bpm=90, downbeatMs=310, chords=['C', 'F']))
        result = self.review('make it jazzy', charted, 'rendition')
        self.assertEqual(result['provenance']['pulseSource'], 'existing chord grid')
        self.assertEqual(result['recipe']['groove']['downbeatMs'], 310)
        self.assertEqual(result['recipe']['bpm'], 90)
        sparse = self.review('make it jazzy with quieter drums', notes=notes_at([.38, .6, 1.43]))
        self.assertEqual(sparse['recipe']['bpm'], 100)
        self.assertEqual(sparse['recipe']['groove']['downbeatMs'], 380)
        self.assertIn('default', sparse['provenance']['pulseSource'])
        self.assertEqual(sparse['recipe']['mix']['drums']['gainDb'], -3)

    def test_tempo_prefers_a_playable_quarter_when_the_melody_is_in_eighth_notes(self):
        for tempo, subdivisions in ((90, [.5, .5, 1, .5, 1.5, .5, .5]),
                                    (114, [1, 1, 2, 1, .5, .5, 1]),
                                    (75, [.5, .5, .5, .5, 1, 2, .5])):
            onsets = [.12]
            jitter = (.025, -.04, .035, -.025, .015, -.01, .01)
            for step, variation in zip(subdivisions, jitter):
                onsets.append(onsets[-1] + (1 + variation) * step * 60 / tempo)
            with self.subTest(tempo=tempo):
                estimate = onset_tempo(onsets)
                self.assertIsNotNone(estimate)
                self.assertLessEqual(abs(estimate - tempo), 4)
        self.assertIsNone(onset_tempo([0, .13, .36, .45, .69, 2.38, 2.56, 4.87]))

    def test_request_frames_and_targets_compose_with_reviewed_controls_and_preservation(self):
        for frame in ('Could you please ', "I'd like you to ", "Let's "):
            for action in ('build a jazz combo backing behind my recorded tune',
                           'arrange this clip in a swinging trio style',
                           'give my guitar melody more of a jazz feel'):
                text = frame + action + ', keeping the original melody intact and pan the bass 40% right.'
                with self.subTest(text=text):
                    result = self.review(text)
                    self.assertEqual(result['recipe']['instrument'], 'recorded')
                    self.assertEqual(result['recipe']['mix']['bass']['pan'], .4)
                    self.assertEqual(result['provenance']['preserved'], ['keep_notes'])
        selected = self.review("I want my take with a small jazz combo, but leave my lead as-is.")
        self.assertEqual(selected['provenance']['preserved'], ['keep_lead'])

    def test_a_later_balance_request_honors_its_measured_or_default_gain_and_other_clauses(self):
        parent = self.review('make it jazzy')['recipe']
        for clause, amount in [('dial the drum part back a touch', 3), ('take the drums down by 4.5 decibels', 4.5),
                               ('make the percussion 2 db quieter', 2)]:
            result = self.review(f'{clause} in the mix, keeping the lead intact and center the bass', parent, 'rendition')
            self.assertEqual(result['recipe']['mix']['drums']['gainDb'], -amount)
            self.assertEqual(result['provenance']['drumGainDeltaDb'], -amount)
        for prompt in ("explain 'make it jazzy'", 'if it were jazz, turn the drums down', 'a jazz trio or reggae',
                       'not a swing arrangement, but warm the lead', 'add jazz backing and lower the vocals',
                       'add a jazz backing and only remove bass', 'give it jazz backing while removing chords',
                       'give it jazz backing then remove the chords', 'add a jazz backing and make the drums quieter only at the end'):
            with self.subTest(prompt=prompt), self.assertRaises(EngineError):
                plan(prompt, DEFAULT_RECIPE, None, self.notes, self.duration, 'original')

    def test_unknown_compounds_negations_and_unsatisfiable_requests_cannot_publish_a_partial_recipe(self):
        for text in ('do not make the soundtrack jazz like', 'make it jazzy and add saxophone',
                     'make it jazzy without drums', 'make it sound like bebop', 'make it sound like vaporwave',
                     'reverse the melody and make it jazzy', 'use reggae drums and add bass'):
            with self.subTest(text=text), self.assertRaises(EngineError) as fail:
                needs_freeform(dict(operation='plan', prompt=text))
            self.assertEqual(fail.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
        for text, parent in [('quieter drums', DEFAULT_RECIPE),
                             ('quieter drums', dict(DEFAULT_RECIPE, drums=True, mix={'drums': dict(UNITY_STRIP, gainDb=-59)})),
                             ('quieter drums', dict(DEFAULT_RECIPE, drums=True, mix={'drums': dict(UNITY_STRIP, muted=True)})),
                             ('make it jazzy', dict(DEFAULT_RECIPE, harmony=dict(bpm=80, downbeatMs=0, chords=[None, None])))]:
            with self.subTest(text=text, parent=parent), self.assertRaises(EngineError):
                self.review(text, parent)
        with self.assertRaises(EngineError):
            self.review('make it jazzy', notes=[])
        done = self.review('make it jazzy')['recipe']
        with self.assertRaises(EngineError) as same:
            self.review('make it jazzy', done, 'rendition')
        self.assertEqual(same.exception.code, 'LOCAL_NO_CHANGE')

    def test_worker_contract_passes_basis_kind_to_isolated_stage(self):
        request = dict(schemaVersion=1, operation='plan', duration=self.duration, notes=self.notes,
                       prompt='make this soundtrack jazz like', parentRecipe=DEFAULT_RECIPE, basisKind='original')
        with TemporaryDirectory() as directory:
            output = Path(directory)
            source = output / 'request.json'
            source.write_text(json.dumps(request))
            self.assertEqual(request_values(source), request)
            result = subprocess.run([sys.executable, '-S', str(HERE / 'stage.py'), str(source), str(output)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((output / 'stage-result.json').read_text())['recipe']['instrument'], 'recorded')
            source.write_text(json.dumps(dict(request, basisKind='unknown')))
            with self.assertRaises(EngineError):
                request_values(source)
