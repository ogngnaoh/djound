"""Independent checks of actual app renders; run after local-model-smoke.mjs."""
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import sys

import numpy as np
import soundfile as sf


def grid_onsets(pattern, sample_rate, frame_count):
    """Independent bar score: (GM drum pitch, offset measured in quarter notes)."""
    if pattern == 'swing_ride':
        bar = [(36, 0), (51, 0), (51, 1), (44, 1), (51, Fraction(5, 3)),
               (51, 2), (51, 3), (44, 3), (51, Fraction(11, 3))]
    else:
        assert pattern == 'four_on_the_floor'
        bar = [(36, beat) for beat in range(4)]
        bar += [(39, 1), (39, 3)]
        bar += [(46, Fraction(2 * beat + 1, 2)) for beat in range(4)]
    downbeat = Fraction(710, 1000)
    quarter = Fraction(60, 120)
    expected = []
    index = 0
    while (downbeat + 4 * index * quarter) * sample_rate < frame_count:
        for pitch, offset in bar:
            start = downbeat + (4 * index + offset) * quarter
            frame = round(start * sample_rate)
            if frame_count - frame >= round(.01 * sample_rate):
                expected.append((pitch, frame))
        index += 1
    return sorted(expected)


def spectral_balance_db(samples, sample_rate):
    """Treble/body energy across channels, using an independent block spectrum."""
    size = 8192
    window = np.hanning(size)[:, None]
    frequencies = np.fft.rfftfreq(size, 1 / sample_rate)
    power = np.zeros(len(frequencies))
    for start in range(0, len(samples) - size + 1, size // 2):
        spectrum = np.fft.rfft(samples[start:start + size] * window, axis=0)
        power += np.sum(np.abs(spectrum) ** 2, axis=1)
    body = power[(frequencies >= 150) & (frequencies <= 800)].sum()
    treble = power[(frequencies >= 2000) & (frequencies <= 8000)].sum()
    assert body > 0 and treble > 0, 'The fixture must contain measurable body and treble'
    return 10 * np.log10(treble / body)


def check_chart(track, harmony, rate, frames):
    names = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
    origin = Fraction(harmony['downbeatMs'], 1000)
    bar = Fraction(240, harmony['bpm'])
    labels = harmony['chords']
    assert origin + (len(labels) - 1) * bar < Fraction(frames, rate) <= origin + len(labels) * bar
    total = 0
    per_bar = []
    for index, label in enumerate(labels):
        start = round((origin + index * bar) * rate)
        stop = min(frames, round((origin + (index + 1) * bar) * rate))
        sounding = [note for note in track['notes'] if start <= round(note['start'] * rate) < stop]
        expected = []
        if label is not None:
            root = names.index(label.removesuffix('m'))
            third = 3 if label.endswith('m') else 4
            expected = sorted([root, (root + third) % 12, (root + 7) % 12])
        assert sorted(note['pitch'] % 12 for note in sounding) == expected, (index, label, sounding)
        assert all(abs(round(note['start'] * rate) - start) <= 1
                   and note['start'] < note['end'] and round(note['end'] * rate) <= stop for note in sounding)
        total += len(sounding)
        per_bar.append(sounding)
    assert total == len(track['notes']), 'Unexpected chord note outside the reviewed grid'
    return per_bar


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
assert len(reports) in (3, 5, 7, 10, 13, 17, 18), "Unexpected walkthrough length"
if len(reports) >= 5:
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
if len(reports) >= 7:
    uploaded = Path(__file__).resolve().parents[1] / 'fixtures/audio/local-demo-source.wav'
    source, _ = sf.read(uploaded, dtype='float32', always_2d=True)
    source_hash = hashlib.sha256(source.astype('<f4').tobytes()).hexdigest()
    assert lead_hashes[5] == lead_hashes[6] == source_hash
    assert source_hash != lead_hashes[0], 'The test must distinguish the uploaded take from a synthesized lead'
    first_backing = data / 'local-jobs' / result['snapshot']['renditions'][0]['attemptId'] / 'output'
    expected = source.astype(np.float64)
    for stem in (1, 2):
        backing, _ = sf.read(first_backing / f'stem-{stem}.raw.wav', always_2d=True)
        expected += backing
    for i, reference in ((5, expected), (6, source)):
        folder = data / 'local-jobs' / result['snapshot']['renditions'][i]['attemptId'] / 'output'
        saved, _ = sf.read(folder / 'audio.raw.wav', dtype='float32', always_2d=True)
        np.testing.assert_array_equal(saved, reference.astype('float32'))
        proof = json.loads((folder/'result.json').read_text())['provenance']['leadSource']
        assert proof['sourceSha256'] == result['snapshot']['original']['sha256']
    mix_check['uploadedLeadPcmSha256'] = source_hash
    print('PASS: recorded-lead mix equals the original samples plus backing; isolating the lead restores its source PCM exactly.')
if len(reports) >= 10:
    groove_checks = []
    downbeat_frame = round(.710 * rate)
    assert np.any(source[:downbeat_frame]), 'The fixture must have audible lead before the first drum beat'
    for i, pattern in ((7, 'swing_ride'), (8, 'four_on_the_floor'), (9, 'swing_ride')):
        recording = result['snapshot']['renditions'][i]
        assert recording['localRecipe']['groove'] == {'pattern': pattern, 'downbeatMs': 710}
        assert recording['localRecipe']['bpm'] == 120
        folder = data / 'local-jobs' / recording['attemptId'] / 'output'
        assert lead_hashes[i] == source_hash
        assert [track['id'] for track in tracks[i]] == ['lead', 'drums']
        events = tracks[i][1]['notes']
        expected = grid_onsets(pattern, rate, len(source))
        actual = sorted((n['pitch'], round(n['start'] * rate)) for n in events)
        assert len(actual) == len(expected)
        assert all(a_pitch == e_pitch and abs(a_frame - e_frame) <= 1
                   for (a_pitch, a_frame), (e_pitch, e_frame) in zip(actual, expected)), pattern
        drums, drum_rate = sf.read(folder / 'stem-1.raw.wav', dtype='float32', always_2d=True)
        assert drum_rate == rate and drums.shape == source.shape
        assert not np.any(drums[:downbeat_frame]), 'Drums played before the reviewed downbeat'
        assert np.any(drums[downbeat_frame:downbeat_frame + round(.02 * rate)]), 'Missing first beat'
        mix, mix_rate = sf.read(folder / 'audio.raw.wav', dtype='float32', always_2d=True)
        assert mix_rate == rate
        np.testing.assert_array_equal(mix, (source.astype(np.float64) + drums).astype('float32'))
        groove_checks.append({'pattern': pattern, 'events': len(events), 'pickupFrames': downbeat_frame,
                             'leadPcmSha256': lead_hashes[i], 'playbackSha256': recording['sha256'],
                             'drumPcmSha256': hashlib.sha256(drums.astype('<f4').tobytes()).hexdigest()})
    assert groove_checks[0] == groove_checks[2], 'The restored swing mix must match exactly'
    for field in ('playbackSha256', 'drumPcmSha256'):
        assert groove_checks[0][field] != groove_checks[1][field]
    mix_check['drumGrooves'] = groove_checks
    print('PASS: both drum grids match the reviewed pickup, preserve the source PCM, and restore swing exactly.')
if len(reports) >= 13:
    tone_checks = []
    target = source.astype(np.float64)
    reference_rms = np.sqrt(np.mean(target ** 2))
    reference_balance = spectral_balance_db(target, rate)
    expected_drum_sha = groove_checks[-1]['drumPcmSha256']
    for i, tone in ((10, 'bright'), (11, 'warm'), (12, 'neutral')):
        recording = result['snapshot']['renditions'][i]
        folder = data / 'local-jobs' / recording['attemptId'] / 'output'
        assert tracks[i] == tracks[9] and lead_hashes[i] == source_hash
        drums, drum_rate = sf.read(folder / 'stem-1.raw.wav', dtype='float32', always_2d=True)
        assert drum_rate == rate and hashlib.sha256(drums.astype('<f4').tobytes()).hexdigest() == expected_drum_sha
        mix, mix_rate = sf.read(folder / 'audio.raw.wav', dtype='float64', always_2d=True)
        assert mix_rate == rate and mix.shape == target.shape and np.isfinite(mix).all()
        # No other stems or fader changes. Subtraction measures the signal that was
        # actually mixed, independently of the worker's effect settings or metrics.
        isolated = mix - drums
        rms_delta = 20 * np.log10(np.sqrt(np.mean(isolated ** 2)) / reference_rms)
        ratio_delta = spectral_balance_db(isolated, rate) - reference_balance
        assert abs(rms_delta) < .01, 'Tone also moved the lead level'
        if tone == 'neutral':
            assert abs(ratio_delta) < .001
            assert (evidence / 'version-13.wav').read_bytes() == (evidence / 'version-10.wav').read_bytes()
        else:
            assert ratio_delta * (1 if tone == 'bright' else -1) > .5, f'{tone} failed the physical tone direction'
        manifest = json.loads((folder / 'result.json').read_text())
        master_gain = manifest['provenance']['export']['gain']
        served, served_rate = sf.read(evidence / f'version-{i + 1}.wav', always_2d=True)
        assert served_rate == rate and served.shape == mix.shape
        assert np.max(np.abs(served - mix * master_gain)) <= 1 / 32768 + 1e-7
        tone_checks.append({'version': i + 1, 'tone': tone, 'trebleToBodyChangeDb': float(ratio_delta),
                            'leadRmsChangeDb': float(rms_delta), 'sourcePcmSha256': source_hash,
                            'drumPcmSha256': expected_drum_sha, 'playbackSha256': recording['sha256']})
    mix_check['trackTone'] = tone_checks
    print('PASS: bright and warm change isolated lead spectra at matched RMS; served PCM matches the master and neutral restores the original WAV.')
if len(reports) >= 17:
    chart_checks, scores = [], []
    baseline_tracks = {track['id']: track for track in tracks[12]}
    for i in range(13, 17):
        recording = result['snapshot']['renditions'][i]
        folder = data / 'local-jobs' / recording['attemptId'] / 'output'
        by_name = {track['id']: track for track in tracks[i]}
        assert by_name['lead'] == baseline_tracks['lead'] and by_name['drums'] == baseline_tracks['drums']
        assert lead_hashes[i] == source_hash
        expected_names = {'lead', 'drums', 'chords'} if i < 16 else {'lead', 'drums'}
        assert by_name.keys() == expected_names
        raw, raw_rate = sf.read(folder / 'audio.raw.wav', dtype='float32', always_2d=True)
        assert raw_rate == rate and raw.shape == source.shape
        wanted = np.zeros(source.shape, dtype=np.float64)
        hashes = {}
        chord_rms = None
        for j, track in enumerate(tracks[i]):
            stem, stem_rate = sf.read(folder / f'stem-{j}.raw.wav', dtype='float32', always_2d=True)
            assert stem_rate == rate and stem.shape == raw.shape
            hashes[track['id']] = hashlib.sha256(stem.astype('<f4').tobytes()).hexdigest()
            wanted += stem
            if track['id'] == 'chords':
                assert not np.any(stem[:downbeat_frame]), 'Chord sound entered before the reviewed grid'
                chord_rms = float(np.sqrt(np.mean(stem.astype(np.float64) ** 2)))
                assert chord_rms > 1e-5, 'Scheduled chords did not produce an audible stem'
        assert hashes['drums'] == expected_drum_sha
        np.testing.assert_array_equal(raw, wanted.astype('float32'))
        chart = recording['localRecipe'].get('harmony')
        if i < 16:
            assert chart['bpm'] == 120 and chart['downbeatMs'] == 710
            scores.append(check_chart(by_name['chords'], chart, rate, len(source)))
        else:
            assert chart is None, 'Removed chord chart remained in the recipe'
        chart_checks.append({'version': i + 1, 'chart': chart, 'chordRms': chord_rms, 'stemPcmSha256': hashes,
                             'playbackSha256': recording['sha256']})
    assert scores[0] == scores[2] and scores[0][1] != scores[1][1]
    assert all(a == b for idx, (a, b) in enumerate(zip(scores[0], scores[1], strict=True)) if idx != 1)
    assert chart_checks[0]['stemPcmSha256'] == chart_checks[2]['stemPcmSha256']
    assert chart_checks[0]['stemPcmSha256']['chords'] != chart_checks[1]['stemPcmSha256']['chords']
    assert (evidence / 'version-14.wav').read_bytes() == (evidence / 'version-16.wav').read_bytes()
    assert (evidence / 'version-13.wav').read_bytes() == (evidence / 'version-17.wav').read_bytes()
    mix_check['chordCharts'] = chart_checks
    print('PASS: reviewed triad events, bar-local correction, dry lead and drums, exact chart undo and removal.')
if len(reports) == 18:
    parent, changed = result['snapshot']['renditions'][16:18]
    prior = data / 'local-jobs' / parent['attemptId'] / 'output'
    current = data / 'local-jobs' / changed['attemptId'] / 'output'
    assert [track['id'] for track in tracks[17]] == ['lead', 'drums']
    assert tracks[17][0]['program'] == 24 and lead_hashes[17] != lead_hashes[16]
    guitar, _ = sf.read(current / 'stem-0.raw.wav', always_2d=True)
    drums, _ = sf.read(current / 'stem-1.raw.wav', always_2d=True)
    before_drums, _ = sf.read(prior / 'stem-1.raw.wav', always_2d=True)
    np.testing.assert_array_equal(before_drums, drums)
    raw_mix, _ = sf.read(current / 'audio.raw.wav', dtype='float32', always_2d=True)
    np.testing.assert_array_equal(raw_mix, (guitar + drums * 10 ** (-6 / 20)).astype('float32'))
    drum_delta = float(20 * np.log10(np.linalg.norm(raw_mix - guitar) / np.linalg.norm(drums)))
    assert abs(drum_delta + 6) < .001
    mix_check['compoundEdit'] = {'version': 18, 'leadProgram': 24, 'drumChangeDb': drum_delta,
                                 'matchedEntireRawMix': True, 'playbackSha256': changed['sha256']}
    print('PASS: the same sentence selected an acoustic guitar and changed only the drum gain by -6 dB.')
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
