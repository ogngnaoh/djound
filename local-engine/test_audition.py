"""Saved-track preview contract and PCM parity with the production mixer."""
from copy import deepcopy
from importlib.util import find_spec
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from audition import audition, validate_request
from common import DEFAULT_RECIPE, UNITY_STRIP, EngineError, digest, validate_mix
from worker import needs_freeform, request_values


@unittest.skipUnless(all(find_spec(m) for m in ('numpy', 'scipy', 'soundfile')), 'Requires the local audio environment.')
class AuditionTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import soundfile as sf
        from safe_export import save_safe_wav
        from stem_mixer import render_mix
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict('os.environ', DJOUND_LOCAL_DATA_ROOT=str(self.root))
        env.start()
        self.addCleanup(env.stop)
        attempt = 'attempt_d94cbadc-197a-42c3-be52-1bd2005cc341'
        self.basis = self.root / 'local-jobs' / attempt / 'output'
        self.basis.mkdir(parents=True)
        self.output = self.root / 'preview'
        self.output.mkdir()
        frames = 11025
        t = np.arange(frames) / 44100
        self.dry = {}
        entries = []
        for index, (name, hz) in enumerate((('lead', 271), ('bass', 139))):
            signal = .05 * np.sin(2*np.pi*hz*t) + .03 * np.cos(2*np.pi*3011*t)
            samples = np.stack((signal, np.roll(signal, 7)), axis=1).astype('<f4')
            self.dry[name] = samples
            path = self.basis / f'stem-{index}.raw.wav'
            sf.write(path, samples, 44100, subtype='FLOAT')
            entries.append({'id':name, 'path':str(path), 'pcmSha256':hashlib.sha256(samples).hexdigest()})
        self.parent = dict(DEFAULT_RECIPE, bass=True, mix={
            'lead':dict(UNITY_STRIP, gainDb=-4, pan=.5), 'bass':dict(UNITY_STRIP, tone='warm', pan=-.3)})
        basis_mix, _ = render_mix(self.dry, self.parent['mix'])
        self.audio = self.basis / 'audio.wav'
        save_safe_wav(self.audio, basis_mix.T)
        self.result = dict(schemaVersion=1, operation='render', status='succeeded', duration=.25,
                           audioPath=str(self.audio), recipe=self.parent,
                           provenance={'stems':entries, 'audioSha256':digest(self.audio)})
        self.manifest = self.basis / 'result.json'
        self.manifest.write_text(json.dumps(self.result))
        self.request = dict(schemaVersion=1, operation='audition', basisAttemptId=attempt,
                            basisAudioSha256=digest(self.audio), parentRecipe=self.parent,
                            duration=.25, stem='lead', tone='bright')

    def test_prior_mix_and_same_tone_reproduce_exact_wavs(self):
        from safe_export import save_safe_wav
        from stem_mixer import render_mix
        config = self.root / 'request.json'
        config.write_text(json.dumps(self.request))
        self.assertEqual(request_values(config), self.request)
        self.assertFalse(needs_freeform(self.request))
        basis_hashes = {p:digest(p) for p in self.basis.iterdir()}
        for tone in ('neutral','bright'):
            dest = self.output / tone
            dest.mkdir()
            result = audition(dict(self.request, tone=tone), dest)
            path = Path(result['audioPath'])
            self.assertEqual(list(dest.iterdir()), [path])
            if tone == 'neutral':
                self.assertEqual(path.read_bytes(), self.audio.read_bytes())
            else:
                controls = deepcopy(self.parent['mix'])
                controls['lead']['tone'] = tone
                samples, _ = render_mix(self.dry, validate_mix(controls, self.dry.keys()))
                reference = self.root / 'expected.wav'
                save_safe_wav(reference, samples.T)
                self.assertNotEqual(path.read_bytes(), self.audio.read_bytes())
                self.assertEqual(path.read_bytes(), reference.read_bytes())
            self.assertEqual(result['provenance']['audioSha256'], digest(path))
            self.assertEqual(result['provenance']['inferenceAttempts'], 0)
        self.assertEqual({p:digest(p) for p in self.basis.iterdir()}, basis_hashes)

    def assert_rejected(self, request):
        with self.assertRaises(EngineError):
            audition(request, self.output)
        self.assertFalse(list(self.output.iterdir()))

    def test_offline_stage_returns_the_saved_mix_at_the_normal_entrypoint(self):
        config = self.root / 'input.json'
        config.write_text(json.dumps(dict(self.request, tone='neutral')))
        stage = Path(__file__).with_name('stage.py')
        completed = subprocess.run([sys.executable, str(stage), str(config), str(self.output)],
                                   check=False, capture_output=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads((self.output / 'stage-result.json').read_text())
        self.assertEqual((result['schemaVersion'], result['operation'], result['status']), (1, 'audition', 'succeeded'))
        self.assertEqual(result['provenance']['audioSha256'], digest(self.audio))
        self.assertEqual(set(p.name for p in self.output.iterdir()), {'stage-result.json', 'audio.wav'})

    def test_rejects_corrupt_or_stale_inputs(self):
        for changes in ({'basisAudioSha256':'0'*64}, {'duration':.24}, {'parentRecipe':DEFAULT_RECIPE},
                        {'stem':'chords'}, {'basisAttemptId':'../other'}, {'sourceAudio':'/elsewhere'},
                        {'tone':'wide'}, {'stem':[]}, {'duration':float('nan')}):
            with self.subTest(changes=changes):
                self.assert_rejected({**self.request, **changes})
        muted = deepcopy(self.parent)
        muted['mix']['lead']['muted'] = True
        with self.assertRaises(EngineError):
            validate_request(dict(self.request, parentRecipe=muted))
        contents = self.audio.read_bytes()
        self.audio.write_bytes(contents[:-2] + b'AB')
        self.assert_rejected(self.request)
        self.audio.write_bytes(contents)
        path = self.basis / 'stem-0.raw.wav'
        contents = path.read_bytes()
        path.write_bytes(contents[:-4] + b'\0\0\0\0')
        self.assert_rejected(self.request)

    def test_rejects_bad_manifest_symlinks_and_nonfloat_stems(self):
        import soundfile as sf
        for fields in ({'operation':'plan'}, {'status':'failed'}, {'schemaVersion':True},
                       {'provenance':{'audioSha256':digest(self.audio),'stems':[]}},
                       {'audioPath':str(self.root/'unrelated.wav')}):
            self.manifest.write_text(json.dumps({**self.result, **fields}))
            self.assert_rejected(self.request)
        duplicate = deepcopy(self.result)
        duplicate['provenance']['stems'][1]['id'] = 'lead'
        self.manifest.write_text(json.dumps(duplicate))
        self.assert_rejected(self.request)
        self.manifest.write_text(json.dumps(self.result))
        stem = self.basis / 'stem-0.raw.wav'
        moved = self.root / 'moved.wav'
        stem.rename(moved)
        stem.symlink_to(moved)
        self.assert_rejected(self.request)
        stem.unlink()
        moved.rename(stem)
        destination = self.root / 'other-output'
        self.basis.rename(destination)
        self.basis.symlink_to(destination, target_is_directory=True)
        self.assert_rejected(self.request)
        self.basis.unlink()
        destination.rename(self.basis)
        for subtype, samples, rate in [('PCM_16', self.dry['lead'], 44100), ('FLOAT', self.dry['lead'][:,0], 44100),
                                       ('FLOAT', self.dry['lead'], 48000), ('FLOAT', self.dry['lead'][:23], 44100)]:
            sf.write(stem, samples, rate, subtype=subtype)
            self.assert_rejected(self.request)
