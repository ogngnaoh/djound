"""Exercise production mix/export without loading a model or instrument bank."""
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from common import DEFAULT_RECIPE, UNITY_STRIP


@unittest.skipUnless(all(find_spec(name) for name in ('numpy', 'pretty_midi', 'scipy', 'soundfile')),
                     'Requires the local audio environment.')
class MixRenderTests(unittest.TestCase):
    def test_dry_stem_identity_and_unity_restore(self):
        import numpy as np
        import soundfile as sf
        from audio import render

        frames = 44100
        lead = np.full((frames, 2), [.02, .04], dtype='<f4')
        bass = np.full((frames, 2), [.03, .07], dtype='<f4')
        drums = np.full((frames, 2), [-.01, .01], dtype='<f4')
        dry = [lead, bass, drums]

        def native_render(command, **options):
            path = Path(command[-1])
            np.sum(dry, axis=0, dtype=np.float64).astype('<f4').tofile(path)
            for index, stem in enumerate(dry):
                stem.tofile(f'{path}.track-{index}.f32')

        notes = [{'start':0., 'end':.5, 'pitch':65, 'velocity':87}]
        parent = dict(DEFAULT_RECIPE, bass=True, drums=True)
        edits = {'bass':dict(UNITY_STRIP, gainDb=-6, pan=-1), 'drums':dict(UNITY_STRIP, muted=True)}
        versions = {}
        with TemporaryDirectory() as temp, patch('audio.subprocess', run=Mock(side_effect=native_render)):
            for name, spec in [('old', parent), ('mixed', dict(parent, mix=edits)),
                               ('restored', dict(parent, mix={'bass':UNITY_STRIP, 'drums':UNITY_STRIP}))]:
                output = Path(temp)/name
                output.mkdir()
                versions[name] = render(notes, 1., spec, output)
            old, mixed, restored = (versions[key] for key in ('old', 'mixed', 'restored'))
            self.assertEqual(old['recipe'], restored['recipe'])
            for key in ('notesSha256', 'midiSha256'):
                self.assertEqual(old['provenance'][key], mixed['provenance'][key])
            self.assertEqual(old['provenance']['audioSha256'], restored['provenance']['audioSha256'])
            self.assertNotEqual(old['provenance']['audioSha256'], mixed['provenance']['audioSha256'])
            for before, after in zip(old['provenance']['stems'], mixed['provenance']['stems'], strict=True):
                self.assertEqual(before['pcmSha256'], after['pcmSha256'])
            expected = lead.astype(np.float64)
            expected[:, 0] += bass.sum(axis=1, dtype=np.float64) * 10 ** (-6 / 20)
            actual, _ = sf.read(Path(temp)/'mixed/audio.raw.wav', dtype='float32')
            np.testing.assert_array_equal(expected.astype('<f4'), actual)

    def test_short_tail_keeps_the_requested_backing_events(self):
        from audio import arrangement, schedule

        for start, end, duration in [(.95, .99, 1.), (0., .08, .10), (.02, .10, .12)]:
            for bass, drums in [(True, False), (False, True), (True, True)]:
                with self.subTest(duration=duration, bass=bass, drums=drums):
                    notes = [{'start':start, 'end':end, 'pitch':65, 'velocity':87}]
                    tracks = arrangement(notes, duration, dict(DEFAULT_RECIPE, bass=bass, drums=drums))
                    self.assertEqual(tracks[0]['notes'], notes)
                    self.assertEqual([track['id'] for track in tracks],
                                     ['lead'] + (['bass'] if bass else []) + (['drums'] if drums else []))
                    for track in tracks[1:]:
                        self.assertTrue(track['notes'])
                        for note in track['notes']:
                            self.assertEqual(note['start'], start)
                            self.assertLess(note['start'], note['end'])
                            self.assertLessEqual(note['end'], duration)
                    schedule(tracks, duration)

    def test_regular_backing_retains_the_original_tail_margin(self):
        from audio import arrangement

        notes = [{'start':0., 'end':1., 'pitch':65, 'velocity':87}]
        tracks = arrangement(notes, 1.25, dict(DEFAULT_RECIPE, bass=True, drums=True))
        self.assertEqual([(n['start'], n['end'], n['pitch']) for n in tracks[1]['notes']],
                         [(0., .39, 41)])
        self.assertEqual([(n['start'], n['end'], n['pitch']) for n in tracks[2]['notes']],
                         [(0., .1, 36), (0., .08, 42), (.6, .6 + .08, 42)])
