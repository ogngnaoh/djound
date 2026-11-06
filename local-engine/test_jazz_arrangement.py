"""Swing-combo score, independent strips, and source-preservation checks."""
from importlib.util import find_spec
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from chart import edit_bar, propose
from common import DEFAULT_RECIPE, HOME, ROOT, UNITY_STRIP, EngineError, active_stems, recipe
from jazz_arrangement import swing_combo


CHART = {'bpm': 120, 'downbeatMs': 250, 'chords': ['C', 'Dm', None, 'Ab']}


class JazzScoreTests(unittest.TestCase):
    def test_comping_and_walk_follow_chart_and_leave_rest_bars_empty(self):
        chords, bass = swing_combo(CHART, 6.8, True, False, False)
        self.assertEqual((chords['id'], chords['program'], bass['id'], bass['program']), ('chords', 0, 'bass', 32))
        self.assertEqual([n['pitch'] for n in bass['notes']], [36,40,43,45,38,41,45,48,32,36])
        attacks = sorted({n['start'] for n in chords['notes'] if n['start'] < 2.25})
        self.assertEqual(attacks, [.25, .25 + (1 + 2/3)*.5, 1.75])
        for time, pitch_classes in [(.25, {0,4,7,9}), (2.25, {2,5,9,0}), (6.25, {8,0,3,5})]:
            self.assertEqual({n['pitch'] % 12 for n in chords['notes'] if n['start'] == time}, pitch_classes)
        for part in (chords, bass):
            for n in part['notes']:
                self.assertGreaterEqual(n['start'], .25)
                self.assertLessEqual(n['end'], 6.8)
                self.assertTrue(n['end'] <= 4.25 or n['start'] >= 6.25)

    def test_sparse_and_soft_have_distinct_controls_and_edits_are_bar_local(self):
        steady = swing_combo(CHART, 6.8, True, False, False)
        sparse = swing_combo(CHART, 6.8, True, True, False)
        quiet = swing_combo(CHART, 6.8, True, False, True)
        edited = swing_combo(edit_bar(CHART, 1, 'Bb', 6.8), 6.8, True, False, False)
        for normal, fewer, soft, revised in zip(steady, sparse, quiet, edited, strict=True):
            self.assertLess(len(fewer['notes']), len(normal['notes']))
            self.assertTrue(all(a['velocity'] < b['velocity'] for a,b in zip(soft['notes'], normal['notes'], strict=True)))
            for note in soft['notes']:
                self.assertGreater(note['velocity'], 0)
            def outside(part):
                return [n for n in part['notes'] if not 2.25 <= n['start'] < 4.25]
            self.assertEqual(outside(normal), outside(revised))
            self.assertNotEqual(normal['notes'], revised['notes'])
        self.assertEqual([steady[0]], swing_combo(CHART, 6.8, False, False, False))

    def test_style_requires_chart_only_and_leaves_drums_independent(self):
        for invalid in [dict(DEFAULT_RECIPE, accompanimentStyle='swing_combo'),
                        dict(DEFAULT_RECIPE, harmony=CHART, accompanimentStyle='latin')]:
            with self.assertRaises(EngineError):
                recipe(invalid, 6.8)
        styled = dict(DEFAULT_RECIPE, harmony=CHART, accompanimentStyle='swing_combo', bass=True)
        self.assertEqual(recipe(styled, 6.8), styled)
        straight = dict(styled, drums=True, bpm=90, groove=dict(pattern='four_on_the_floor',downbeatMs=150))
        self.assertEqual(recipe(straight, 6.8), straight)
        rests = dict(CHART, chords=[None]*4)
        self.assertEqual(swing_combo(rests, 6.8, True, False, False), [])
        self.assertEqual(active_stems(recipe(dict(styled, harmony=rests), 6.8)), ('lead',))


@unittest.skipUnless(all(find_spec(m) for m in ('numpy','pretty_midi','scipy','soundfile'))
                     and (HOME/'renderer').is_file() and (HOME/'FluidR3_GM.sf2').is_file(),
                     'Requires the installed local sound bank and audio environment.')
class JazzRenderTests(unittest.TestCase):
    def test_original_drum_and_lead_identity_through_style_mix_and_undo(self):
        from audio import render
        from recorded_lead import decode_recording
        import numpy as np
        import soundfile as sf

        spec = json.loads((ROOT / 'tests/fixtures/chart.json').read_text())
        notes, duration = spec['notes'], spec['duration']
        harmony, _ = propose(notes, duration, 110, 500)
        old = dict(DEFAULT_RECIPE, instrument='recorded', bass=True, drums=True, bpm=110, harmony=harmony,
                   groove={'pattern':'swing_ride','downbeatMs':500})
        combo = dict(old, accompanimentStyle='swing_combo')
        versions = [('old',old), ('combo',combo), ('mixed',dict(combo, mix={'bass':dict(UNITY_STRIP, muted=True)})),
                    ('soft',dict(combo,backingVolume='soft')), ('sparse',dict(combo,density='sparse')),
                    ('restore',old)]
        stems, hashes, mids = {}, {}, {}
        with TemporaryDirectory() as temp:
            out = Path(temp)
            source = ROOT/spec['source']
            original = decode_recording(source, spec['sourceSha256'], round(duration*44100), out)
            for name, config in versions:
                dst = out/name
                dst.mkdir()
                r = render(notes,duration,config,dst,source=source,source_sha=spec['sourceSha256'])
                self.assertEqual(r['notes'], notes)
                self.assertEqual(r['recipe'], config)
                raw = {s['id']:s for s in r['provenance']['stems']}
                data, _ = sf.read(raw['lead']['path'], dtype='float32')
                np.testing.assert_array_equal(data, original)
                stems[name] = {k:v['pcmSha256'] for k,v in raw.items()}
                hashes[name] = r['provenance']['audioSha256']
                mids[name] = r['provenance']['midiSha256']
                self.assertEqual(r['provenance']['audioValidation']['clipped_fraction'], 0)
        for key in ('bass','chords'):
            self.assertEqual(len({stems[name][key] for name in ('old','combo','sparse','soft')}), 4)
        self.assertEqual(stems['combo'], stems['mixed'])
        self.assertEqual(stems['old']['drums'], stems['combo']['drums'])
        self.assertEqual(hashes['old'], hashes['restore'])
        self.assertEqual(mids['old'], mids['restore'])
        self.assertEqual(len(set(hashes.values())), 5)
