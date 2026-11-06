"""Independent checks of actual app renders; run after local-model-smoke.mjs."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf


result = json.loads(Path(sys.argv[1]).read_text())
data = Path(sys.argv[2])
evidence = Path(result['evidence'])
reports = []
lead_hashes = []
note_hashes = []
tracks = []
mix_check = None
for index, recording in enumerate(result['snapshot']['renditions'], 1):
    output = data / 'local-jobs' / recording['attemptId'] / 'output'
    manifest = json.loads((output / 'result.json').read_text())
    assert manifest['status'] == 'succeeded' and manifest['operation'] == 'render'
    audio_path = evidence / f'version-{index}.wav'
    assert hashlib.sha256(audio_path.read_bytes()).hexdigest() == recording['sha256']
    samples, rate = sf.read(audio_path, always_2d=True)
    assert rate == 44100 and samples.shape == (529200, 2)
    assert np.isfinite(samples).all() and np.abs(samples).max() < .999
    assert np.sqrt(np.mean(samples ** 2)) > 1e-5
    lead, lead_rate = sf.read(output / 'stem-0.raw.wav', dtype='float32', always_2d=True)
    assert lead_rate == rate and lead.shape == samples.shape
    lead_hashes.append(hashlib.sha256(np.ascontiguousarray(lead, dtype='<f4').tobytes()).hexdigest())
    note_hashes.append(manifest['provenance']['notesSha256'])
    tracks.append(json.loads((output / 'tracks.json').read_text()))
    reports.append({'recordingId': recording['id'], 'samplePeak': float(np.abs(samples).max()),
                    'rms': float(np.sqrt(np.mean(samples ** 2))), 'clippedSamples': 0,
                    'leadPcmSha256': lead_hashes[-1], 'notesSha256': note_hashes[-1],
                    'worker': manifest['provenance']['supervisor']})
if len(reports) == 5:
    # The fourth rendition mixes the first version's dry stems on the same timeline.
    # Compare serialized samples to an independent stereo matrix and gain equation.
    parent = data / 'local-jobs' / result['snapshot']['renditions'][0]['attemptId'] / 'output'
    fourth = data / 'local-jobs' / result['snapshot']['renditions'][3]['attemptId'] / 'output'
    lead, _ = sf.read(parent / 'stem-0.raw.wav', always_2d=True)
    bass, _ = sf.read(parent / 'stem-1.raw.wav', always_2d=True)
    gain = 10 ** (-6 / 20)
    wanted = lead.copy()
    wanted[:, 0] += gain * (bass[:, 0] + np.sin(np.pi / 4) * bass[:, 1])
    wanted[:, 1] += gain * np.cos(np.pi / 4) * bass[:, 1]
    actual, _ = sf.read(fourth / 'audio.raw.wav', always_2d=True)
    max_sample_error = float(np.max(np.abs(actual - wanted)))
    assert max_sample_error < 1e-7, 'Guided mix did not produce the requested channel samples'
    assert lead_hashes[0] == lead_hashes[3] == lead_hashes[4]
    assert (evidence / 'version-1.wav').read_bytes() == (evidence / 'version-5.wav').read_bytes()
    mix_check = {'maxSampleError': max_sample_error, 'sampleFrames': len(actual),
                 'restoredWavSha256': result['snapshot']['renditions'][0]['sha256']}
    print('PASS: actual text-to-mix samples match the stereo matrix; direct controls restore the original WAV byte for byte.')
else:
    assert len(reports) == 3
assert lead_hashes[0] == lead_hashes[1], 'Backing-only refinement changed the locked lead'
assert lead_hashes[2] != lead_hashes[1], 'Explicit guitar correction did not change the lead timbre'
assert len(set(note_hashes)) == 1, 'Refinement or instrument correction changed confirmed notes'
assert [track['id'] for track in tracks[0]] == ['lead', 'bass', 'drums']
assert [track['id'] for track in tracks[1]] == ['lead', 'bass']
assert [track['id'] for track in tracks[2]] == ['lead', 'bass']
assert tracks[2][0]['program'] == 24 and tracks[1][0]['program'] == 4
assert len(tracks[0][1]['notes']) == len(tracks[1][1]['notes']) > 0
for first, second in zip(tracks[0][1]['notes'], tracks[1][1]['notes']):
    assert first['start'] == second['start'] and first['end'] == second['end'] and first['pitch'] == second['pitch']
    assert second['velocity'] < first['velocity'], 'Quieter backing did not reduce bass velocity'
truth = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/audio/local-demo-source.json').read_text())['composition']['events']
confirmed = result['localSnapshot']['notes']['notes']
assert len(confirmed) == len(truth)
onset_errors = []
for note, expected in zip(confirmed, truth):
    assert note['pitch'] == expected['midi_note'], 'Confirmed demo melody differs in pitch'
    onset_errors.append(abs(note['start'] - expected['onset_seconds']))
assert max(onset_errors) < .08
report = {'result': 'PASS', 'scope': 'Authored synthetic demo melody; objective checks do not replace listening judgment.',
          'confirmedNotes': len(confirmed), 'maxOnsetErrorSeconds': max(onset_errors),
          'leadUnchangedAcrossBackingRefinement': True, 'renders': reports}
if mix_check is not None:
    report['guidedMix'] = mix_check
(evidence / 'audio-validation.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS: real audio is finite and unclipped; lead PCM/notes are identical, drums removed, bass softer, demo pitches/onsets retained.')
