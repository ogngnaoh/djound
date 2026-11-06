"""Verify the audio actually served by the two-version Studio jazz walkthrough."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import soundfile as sf


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_stereo(path):
    audio, rate = sf.read(path, always_2d=True)
    assert rate == 44100 and audio.shape[1] == 2 and np.isfinite(audio).all(), path
    return audio


def rms(samples):
    return np.sqrt(np.mean(np.square(samples)))


def verify(result, data):
    evidence = Path(result['evidence'])
    original = evidence / 'original.wav'
    assert sha(original) == result['snapshot']['original']['sha256']
    # Decode separately from the engine. Preserve the level of a mono upload on both channels.
    channels = sf.info(original).channels
    assert channels in (1, 2)
    decoded = subprocess.check_output(
        [
            'ffmpeg',
            '-nostdin',
            '-v',
            'error',
            '-i',
            str(original),
            '-map',
            '0:a:0',
            '-ar',
            '44100',
            '-f',
            'f32le',
            '-',
        ]
    )
    source = np.frombuffer(decoded, dtype='<f4').reshape(-1, channels)
    if channels == 1:
        source = np.repeat(source, 2, axis=1)

    readings = []
    previous = None
    renditions = result['snapshot']['renditions']
    assert len(renditions) == 2
    for index, recording in enumerate(renditions, 1):
        output = data / 'local-jobs' / recording['attemptId'] / 'output'
        manifest = json.loads((output / 'result.json').read_text())['provenance']
        stems = {s['id']: read_stereo(output / Path(s['path']).name) for s in manifest['stems']}
        assert set(stems) == {'lead', 'bass', 'chords', 'drums'}
        np.testing.assert_array_equal(stems['lead'], source)
        assert all(np.any(s) for s in stems.values())
        if previous:
            for name, samples in stems.items():
                np.testing.assert_array_equal(samples, previous[name])
        previous = stems
        mix = read_stereo(output / 'audio.raw.wav')
        others = stems['lead'] + stems['bass'] + stems['chords']
        drum_gain = rms(mix - others) / rms(stems['drums'])
        measured_db = float(20 * np.log10(drum_gain))
        expected_db = 0 if index == 1 else -3
        assert abs(measured_db - expected_db) < 0.001, measured_db
        np.testing.assert_allclose(
            mix, others + stems['drums'] * 10 ** (expected_db / 20), rtol=0, atol=1e-7
        )
        served = evidence / f'version-{index}.wav'
        assert sha(served) == recording['sha256'] == manifest['audioSha256']
        playback = read_stereo(served)
        peak = float(np.max(np.abs(playback)))
        assert 0 < peak < 0.999 and playback.shape == source.shape
        export = json.loads((output / 'audio.export.json').read_text())
        np.testing.assert_allclose(playback, mix * export['gain'], rtol=0, atol=1 / 32768)
        readings.append(
            dict(
                version=index,
                drumGainDb=measured_db,
                samplePeakDbfs=20 * np.log10(peak),
                backingToLeadRmsDb=20 * np.log10(rms(mix - source) / rms(source)),
                matchedLeadFrames=len(source),
                sha256=sha(served),
            )
        )
    assert readings[0]['sha256'] != readings[1]['sha256']
    (evidence / 'audio-validation.json').write_text(json.dumps(readings, indent=2) + '\n')
    print(
        f"PASS: both served mixes preserve {len(source)} original stereo frames. "
        f"Drums measured {readings[1]['drumGainDb']:.3f} dB on the follow-up."
    )


if __name__ == '__main__':
    verify(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
