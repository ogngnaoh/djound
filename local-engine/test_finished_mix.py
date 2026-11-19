"""Sample-domain and contract tests for editing an imported stereo mix."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest('The finished mix audio environment is required.')

from common import EngineError, digest
import finished_mix as mix
import scnet


class ImportedMixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        setting = patch.dict(os.environ, {'DJOUND_LOCAL_DATA_ROOT': str(self.root)})
        setting.start()
        self.addCleanup(setting.stop)
        self.raw = np.tile(np.array([1500, -2100], dtype='<i2'), (2000, 1))
        self.raw[825:835] = [31700, -31200]
        self.source = self.root / 'source.wav'
        with wave.open(str(self.source), 'wb') as out:
            out.setparams((2, 2, mix.RATE, 0, 'NONE', ''))
            out.writeframes(self.raw.tobytes())
        self.source_info = {'sourceAudio': str(self.source), 'sourceSha256': digest(self.source)}
        self.selection = {**self.source_info, 'startFrame': 200, 'endFrame': 1800}
        self.vocals = np.tile(np.array([.55, -.49], dtype='<f4'), (1600, 1))
        self.drums = np.tile(np.array([.11, -.14], dtype='<f4'), (1600, 1))
        prepared = self.root / 'prepared.npz'
        metadata = mix.preparation_metadata(self.selection)
        np.savez(prepared, metadata=np.array(json.dumps(metadata)), vocals=self.vocals, drums=self.drums)
        self.request = {**self.selection, 'schemaVersion': 1, 'operation': 'mix-preview',
                        'preparationPath': str(prepared), 'preparationSha256': digest(prepared)}

    def render(self, name, vocal, drum, **extra):
        out = self.root / name
        out.mkdir()
        return mix.execute({**self.request, 'changedControl': 'vocals',
                            'gains': {'vocals': vocal, 'drums': drum}, **extra}, out)

    def test_neutral_is_byte_identical(self):
        rendered = self.render('neutral', 0, 0)
        self.assertEqual(rendered['audioSha256'], digest(self.source))
        self.assertEqual(rendered['frames'], len(self.raw))

    def test_combined_gain_and_explicit_trim_are_sample_exact(self):
        result = self.render('trimmed', 2, -3, passageTrimDb=-3)
        with wave.open(result['audioPath'], 'rb') as handle:
            data = np.frombuffer(handle.readframes(handle.getnframes()), '<i2').reshape(-1, 2)
        np.testing.assert_array_equal(data[:201], self.raw[:201])
        np.testing.assert_array_equal(data[1799:], self.raw[1799:])
        wet = self.raw[200:1800].astype(float)
        wet += self.vocals.astype(float) * (32768 * (10**.1 - 1))
        wet += self.drums.astype(float) * (32768 * (10**(-3/20) - 1))
        wet *= 10**(-3/20)
        for frame, weight in ((201, 1/440), (420, .5), (1100, 1), (1579, .5), (1798, 1/440)):
            expected = np.rint(self.raw[frame] + weight * (wet[frame-200] - self.raw[frame]))
            np.testing.assert_array_equal(data[frame], expected)

    def test_clip_recommends_two_verified_choices(self):
        with self.assertRaises(EngineError) as failure:
            self.render('too-loud', 4, 0)
        self.assertEqual(failure.exception.code, 'MIX_WOULD_CLIP')
        choices = failure.exception.details
        self.assertGreater(choices['suggestedGainDb'], 0)
        self.assertLess(choices['suggestedGainDb'], 4)
        self.assertLess(choices['suggestedPassageTrimDb'], 0)
        self.render('less', choices['suggestedGainDb'], 0)
        self.render('headroom', 4, 0, passageTrimDb=choices['suggestedPassageTrimDb'])
        self.assertFalse((self.root / 'too-loud/audio.wav').exists())

    def test_invalid_or_mismatched_preparation_is_rejected(self):
        for index, extra in enumerate(({'startFrame': 201}, {'endFrame': 1801}, {'passageTrimDb': True},
                                       {'preparationSha256': '0'*64}, {'sourceSha256': '0'*64})):
            with self.subTest(extra=extra), self.assertRaises(EngineError):
                self.render(str(index), 0, -2, **extra)

    def test_estimate_cache_is_bound_to_exact_source_and_range(self):
        request = {**self.selection, 'schemaVersion': 1, 'operation': 'mix-prepare'}
        def estimated(signal, provider):
            return {'vocals': signal.copy(), 'drums': -signal}
        with patch.object(scnet, 'estimate', side_effect=estimated) as call:
            results = []
            for index, end in enumerate((1800, 1800, 1799)):
                output = self.root / str(index)
                output.mkdir()
                results.append(mix.execute({**request, 'endFrame': end}, output))
        self.assertEqual(call.call_count, 2)
        self.assertEqual([r['cacheHit'] for r in results], [False, True, False])
        self.assertEqual(results[0]['preparationSha256'], results[1]['preparationSha256'])
        self.assertNotEqual(results[0]['cacheKey'], results[2]['cacheKey'])
        with np.load(results[0]['preparationPath']) as prepared:
            np.testing.assert_array_equal(prepared['vocals'], self.raw[200:1800].astype(np.float32)/32768)

    def test_corrupt_preparation_is_distinct_from_a_changed_source(self):
        p = Path(self.request['preparationPath'])
        for index, content in enumerate((None, b'incomplete', b'PK\x03\x04invalid')):
            if content is None:
                p.unlink()
            else:
                p.write_bytes(content)
                self.request['preparationSha256'] = digest(p)
            with self.assertRaises(EngineError) as problem:
                self.render(f'bad-{index}', 1, 0)
            self.assertEqual(problem.exception.code, 'MIX_PREPARATION_INVALID')
        np.savez(p, metadata=np.array(json.dumps({**mix.preparation_metadata(self.selection), 'revision': 'retired'})),
                 vocals=self.vocals, drums=self.drums)
        self.request['preparationSha256'] = digest(p)
        with self.assertRaises(EngineError) as problem:
            self.render('stale', 1, 0)
        self.assertEqual(problem.exception.code, 'MIX_PREPARATION_INVALID')
        self.source.unlink()
        with self.assertRaises(EngineError) as problem:
            self.render('gone-base', 1, 0)
        self.assertEqual(problem.exception.code, 'MIX_SOURCE_CHANGED')

    def test_corrupt_cache_and_receipt_are_replaced_together(self):
        request = {**self.selection, 'schemaVersion': 1, 'operation': 'mix-prepare'}
        with patch.object(scnet, 'estimate', side_effect=lambda signal, _: {'vocals': signal, 'drums': signal}) as call:
            for index in range(3):
                output = self.root / f'cache-{index}'
                output.mkdir()
                data = mix.execute(request, output)
                path = Path(data['preparationPath'])
                if index == 0:
                    good = path.read_bytes()
                    path.write_bytes(b'corrupt cache and old link')
                elif index == 1:
                    self.assertEqual(path.read_bytes(), good)
                    self.assertFalse(data['cacheHit'])
                    receipt = self.root / 'finished-mix-cache' / (data['cacheKey'] + '.sha256')
                    receipt.write_bytes(b'\xff')
                else:
                    self.assertFalse(data['cacheHit'])
                    self.assertEqual(path.read_bytes(), good)
        self.assertEqual(call.call_count, 3)

    def test_scnet_stereo_graph_preprocessing_keeps_exact_alignment(self):
        rng = np.random.default_rng(934)
        signal = rng.uniform(-1, 1, size=(scnet.SEGMENT, 2)).astype('f4')
        spec = scnet.spectrum(signal)[0]
        self.assertEqual(spec.shape, (4, 2049, 476))
        # Independent first-frame FFT with the exact left reflect context.
        frame = np.concatenate((signal[1:2049][::-1], signal[:2048]))
        expected = np.fft.rfft(frame, axis=0)/64
        np.testing.assert_allclose(spec[0::2, :, 0].T, expected.real, atol=1e-6)
        np.testing.assert_allclose(spec[1::2, :, 0].T, expected.imag, atol=1e-6)
        np.testing.assert_allclose(scnet.waveform(spec), signal, atol=2e-7)


if __name__ == '__main__':
    unittest.main()
