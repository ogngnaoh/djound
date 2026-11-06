"""Small audio file checks for the source-performance decoder and MIDI guide."""
from importlib.util import find_spec
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import DEFAULT_RECIPE, EngineError, digest


@unittest.skipUnless(shutil.which('ffmpeg') and all(find_spec(name) for name in
                     ('numpy', 'soundfile', 'scipy', 'pretty_midi')), 'Requires the local audio environment.')
class RecordedLeadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.decoded = self.directory / 'stage'
        self.decoded.mkdir()

    def signal(self, rate, stereo=True):
        import numpy as np

        time = np.arange(rate) / rate
        left = .22 * np.sin(2 * np.pi * 430 * time)
        if not stereo:
            return left
        return np.column_stack([left, .11 * np.cos(2 * np.pi * 710 * time)])

    def test_native_stereo_and_mono_match_input_pcm_at_every_sample(self):
        import numpy as np
        import soundfile as sf
        from recorded_lead import decode_recording

        for stereo in (True, False):
            with self.subTest(stereo=stereo):
                source = self.directory / 'input.wav'
                sf.write(source, self.signal(44100, stereo), 44100, subtype='PCM_16')
                reference, _ = sf.read(source, dtype='float32', always_2d=True)
                if not stereo:
                    reference = np.repeat(reference, 2, axis=1)
                identity = digest(source)
                samples = decode_recording(source, identity, len(reference), self.decoded)
                np.testing.assert_array_equal(samples, reference)
                self.assertEqual(digest(source), identity)

    def test_48khz_wav_and_mp3_retain_timing_and_distinct_channels(self):
        import numpy as np
        import soundfile as sf
        from recorded_lead import decode_recording

        wav = self.directory / '48k.wav'
        mp3 = self.directory / '48k.mp3'
        sf.write(wav, self.signal(48000), 48000, subtype='PCM_16')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(wav), '-c:a', 'libmp3lame',
                        '-b:a', '192k', str(mp3)], check=True, timeout=10)
        reference = self.signal(44100)
        for source, tolerance in ((wav, .0002), (mp3, .012)):
            with self.subTest(format=source.suffix):
                identity = digest(source)
                samples = decode_recording(source, identity, len(reference), self.decoded)
                self.assertEqual(samples.shape, reference.shape)
                # The lossy codec and resampling filter alter samples. Check both
                # channels against their independent time-domain references.
                error_rms = np.sqrt(np.mean((samples[300:-300] - reference[300:-300]) ** 2, axis=0))
                self.assertLess(float(error_rms.max()), tolerance)
                self.assertEqual(digest(source), identity)

    def test_mismatch_fails_without_changing_source(self):
        import soundfile as sf
        from recorded_lead import decode_recording

        source = self.directory / 'source.wav'
        sf.write(source, self.signal(44100), 44100, subtype='PCM_16')
        identity = digest(source)
        for expected, frames, code in [('0' * 64, 44100, 'LOCAL_SOURCE_CHANGED'),
                                       (identity, 44101, 'LOCAL_SOURCE_INVALID')]:
            with self.subTest(code=code), self.assertRaises(EngineError) as failure:
                decode_recording(source, expected, frames, self.decoded)
            self.assertEqual(code, failure.exception.code)
            self.assertEqual(digest(source), identity)
            self.assertEqual(list(self.decoded.iterdir()), [])

    def test_decoder_only_reads_the_verified_snapshot(self):
        import numpy as np
        import soundfile as sf
        from recorded_lead import decode_recording

        source = self.directory / 'take.wav'
        sf.write(source, self.signal(44100), 44100, subtype='PCM_16')
        expected, _ = sf.read(source, dtype='float32', always_2d=True)
        identity = digest(source)
        invoke = subprocess.run

        def replace_at_decode(command, **options):
            replacement = self.directory / 'replacement.wav'
            sf.write(replacement, -expected, 44100, subtype='PCM_16')
            replacement.replace(source)
            return invoke(command, **options)

        with patch('recorded_lead.subprocess.run', side_effect=replace_at_decode):
            actual = decode_recording(source, identity, len(expected), self.decoded)
        np.testing.assert_array_equal(expected, actual)
        self.assertNotEqual(digest(source), identity)
        self.assertEqual(list(self.decoded.iterdir()), [])
        source.unlink()
        with self.assertRaises(EngineError) as missing:
            decode_recording(source, identity, len(expected), self.decoded)
        self.assertEqual(missing.exception.code, 'LOCAL_SOURCE_CHANGED')

    def test_recorded_solo_uses_source_and_treats_reviewed_notes_as_midi_guide(self):
        import numpy as np
        import pretty_midi
        import soundfile as sf
        from audio import render

        source = self.directory / 'source.wav'
        sf.write(source, self.signal(44100), 44100, subtype='PCM_16')
        identity = digest(source)
        audio_hashes = set()
        midi_hashes = set()
        for pitch in (60, 72):
            directory = self.directory / f'pitch-{pitch}'
            directory.mkdir()
            note = dict(start=.02, end=.16, pitch=pitch, velocity=90)
            with patch('audio.synthesized_stems', side_effect=AssertionError('Unexpected synthesizer call')):
                result = render([note], 1., dict(DEFAULT_RECIPE, instrument='recorded'), directory,
                                source=source, source_sha=identity)
            audio_hashes.add(result['provenance']['audioSha256'])
            midi_hashes.add(result['provenance']['midiSha256'])
            original, _ = sf.read(source, dtype='int16')
            output, _ = sf.read(result['audioPath'], dtype='int16')
            np.testing.assert_array_equal(original, output)
            lead, = pretty_midi.PrettyMIDI(result['midiPath']).instruments
            self.assertEqual(lead.name, 'lead transcription guide')
            self.assertEqual([n.pitch for n in lead.notes], [pitch])
        self.assertEqual(len(audio_hashes), 1)
        self.assertEqual(len(midi_hashes), 2)
