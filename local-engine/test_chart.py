"""Contract parity, chart audio preservation and exact restoration."""
from copy import deepcopy
from importlib import util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from chart import backing, edit_bar, frame, grid, propose, validate_chart
from common import DEFAULT_RECIPE, HOME, ROOT, EngineError, UNITY_STRIP, active_stems, digest, recipe
from planner import plan, slot_patch


CHART = {'bpm': 120, 'downbeatMs': 0, 'chords': ['C', 'Gm']}
NOTES = [dict(start=.125, end=2.5, pitch=64, velocity=90)]


class ChartContractTests(unittest.TestCase):
    def test_duration_and_half_frame_boundaries(self):
        self.assertEqual(len(grid(12, 200, 0)), 10)
        self.assertEqual(len(grid(2, 120, 0)), 1)
        self.assertEqual(len(grid(2 + .49/44100, 120, 0)), 1)
        self.assertEqual(len(grid(1.2 + .5/44100, 200, 0)), 2)
        self.assertEqual(frame(.005), 221)
        self.assertEqual(validate_chart(CHART), CHART)
        self.assertEqual(validate_chart(CHART, 3), CHART)
        for bad in [dict(CHART, bpm=True), dict(CHART, downbeatMs=3000), dict(CHART, chords=['C']),
                    dict(CHART, chords=['C', 'C#7']), dict(CHART, bars=[])]:
            with self.subTest(bad=bad), self.assertRaises(EngineError):
                validate_chart(bad, 3)
        with self.assertRaises(EngineError):
            edit_bar(CHART, 1, 'G', None)

    def test_qualified_defaults_match_frozen_proposal_and_events(self):
        spec = util.spec_from_file_location('frozen_chart', ROOT / 'benchmarks/harmony/harmony.py')
        frozen = util.module_from_spec(spec)
        spec.loader.exec_module(frozen)
        study = json.loads((ROOT / 'experiments/rhythm-arrangement/study.json').read_text())
        chart, bars = propose(study['notes'], 12, 110, 500)
        original = frozen.fit(study['notes'], 12, 110, .5)
        self.assertEqual(bars, original)
        self.assertEqual(backing(chart, 12, True, False, False), frozen.backing(original))
        edited = edit_bar(chart, 1, 'Ab', 12)
        self.assertEqual(chart['chords'][1], 'Cm')
        self.assertEqual(edited['chords'], ['Eb', 'Ab', 'Fm', 'Cm', 'Cm', None])

    def test_rest_strips_and_closed_model_scope(self):
        parent = dict(DEFAULT_RECIPE, bass=True, harmony=deepcopy(CHART), mix={'chords': dict(UNITY_STRIP, gainDb=-2)})
        slots = dict(supported=True, instrument='guitar', bass='KEEP', drums='KEEP', density='KEEP', backingVolume='KEEP')
        self.assertEqual(slot_patch(slots, parent)[0], dict(parent, instrument='guitar'))
        with self.assertRaises(EngineError):
            plan('edit: use guitar', parent, None)
        for prompt in ('add a seventh chord and switch to guitar', 'change the progression and switch to guitar',
                       'Remove all backing', 'Solo the melody', 'Just leave the melody'):
            with self.subTest(prompt=prompt), patch.dict('sys.modules', {'mlx': None}):
                with self.assertRaises(EngineError) as failure:
                    plan(prompt, parent, None, notes=NOTES, duration=3)
                self.assertEqual(failure.exception.code, 'LOCAL_UNSUPPORTED_REQUEST')
        rest = dict(DEFAULT_RECIPE, bass=True, harmony=dict(CHART, chords=[None, None]))
        self.assertEqual(active_stems(rest), ('lead',))
        for bad in [{'lead':dict(UNITY_STRIP, muted=True)}, {'bass':UNITY_STRIP}, {'chords':UNITY_STRIP}]:
            with self.assertRaises(EngineError):
                recipe(dict(rest, mix=bad), 3)
        self.assertEqual(active_stems(dict(rest, drums=True)), ('lead', 'drums'))


@unittest.skipUnless(all(util.find_spec(m) for m in ('numpy', 'soundfile', 'pretty_midi', 'scipy')) and
                     (HOME / 'renderer').is_file() and (HOME / 'FluidR3_GM.sf2').is_file(),
                     'Requires the local audio environment and installed SoundFont.')
class ChartAudioTests(unittest.TestCase):
    def test_actual_child_chain_restores_original_and_preserves_dry_stems(self):
        from audio import render
        from recorded_lead import decode_recording
        import hashlib
        import numpy as np
        source = ROOT / 'fixtures/audio/local-demo-source.wav'
        spec = json.loads((ROOT / 'experiments/rhythm-arrangement/study.json').read_text())
        notes = spec['notes']
        chart, _ = propose(notes, 12, 110, 500)
        old = dict(DEFAULT_RECIPE, instrument='recorded', bass=True, drums=True)
        parent = dict(old, harmony=chart)
        edited = dict(parent, harmony=edit_bar(chart, 1, 'Ab', 12))
        mixed = dict(edited, mix={'chords': dict(UNITY_STRIP, gainDb=-5, pan=.3, tone='warm')})
        chain = [('old',old), ('parent',parent), ('edited',edited), ('mixed',mixed),
                 ('soft',dict(parent,backingVolume='soft')), ('sparse',dict(parent,density='sparse')),
                 ('restore',parent), ('remove',old)]
        with TemporaryDirectory() as temporary:
            out = Path(temporary)
            source_sha = digest(source)
            original = decode_recording(source, source_sha, 12 * 44100, out)
            lead_pcm = hashlib.sha256(np.ascontiguousarray(original, dtype='<f4').tobytes()).hexdigest()
            ids, stems, scores = {}, {}, {}
            for name, configuration in chain:
                version = out / name
                version.mkdir()
                result = render(notes, 12, configuration, version, source=source, source_sha=source_sha)
                ids[name] = result['provenance']['audioSha256']
                stems[name] = {s['id']: s['pcmSha256'] for s in result['provenance']['stems']}
                scores[name] = json.loads((version/'tracks.json').read_text())
                self.assertEqual(result['notes'], notes)
                self.assertEqual(scores[name][0]['notes'], notes)
                self.assertEqual(stems[name]['lead'], lead_pcm)
            self.assertEqual(digest(source), source_sha)
            self.assertEqual(ids['parent'], ids['restore'])
            self.assertEqual(ids['old'], ids['remove'])
            self.assertEqual(len({ids[n] for n in ('old', 'parent', 'edited', 'mixed', 'sparse', 'soft')}), 6)
            self.assertEqual({stems[k]['drums'] for k in ('old','parent','edited','mixed','restore','remove')},
                             {stems['old']['drums']})
            self.assertEqual(stems['edited'], stems['mixed'])
            for part in ('chords','bass'):
                self.assertEqual(len({stems[k][part] for k in ('parent', 'edited','sparse','soft')}), 4)
            self.assertEqual(scores['parent'], scores['restore'])
            self.assertEqual(scores['old'], scores['remove'])

    def test_all_rest_and_one_frame_bar(self):
        from audio import render
        with TemporaryDirectory() as temporary:
            out = Path(temporary)
            duration = 2 + 1 / 44100
            source_notes = [dict(start=0., end=1., pitch=60, velocity=89)]
            ids = {}
            for name, labels in [('off',None), ('rest',[None,None]), ('tail',['C','Dm'])]:
                dst = out/name
                dst.mkdir()
                parent = dict(DEFAULT_RECIPE)
                if labels is not None:
                    parent.update(bass=True, harmony=dict(CHART, chords=labels))
                result = render(source_notes, duration, parent, dst)
                ids[name] = result['provenance']['audioSha256']
                if name == 'tail':
                    self.assertGreater(result['provenance']['harmony']['minimumTickExtensions'], 0)
            self.assertEqual(ids['off'], ids['rest'])
            self.assertNotEqual(ids['off'], ids['tail'])
            dst = out/'silent-attack'
            dst.mkdir()
            with self.assertRaises(EngineError) as error:
                render(source_notes, duration, dict(DEFAULT_RECIPE, harmony=dict(CHART, chords=[None,'C'])), dst)
            self.assertEqual(error.exception.code, 'LOCAL_INVALID_RECIPE')
