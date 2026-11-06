"""Promoted CPU transcription and independent-track synthesis, without research imports."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pretty_midi
import soundfile as sf

from common import HOME, DEFAULT_RECIPE, EngineError, canonical_hash, digest, notes as validate_notes, recipe as validate_recipe, validate_mix, write_json
from safe_export import save_safe_wav
from stem_mixer import render_mix
from grooves import groove_notes
from validate_audio import validate


def transcribe(source, output):
    normalized = output / 'source.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                    '-i', str(source), '-map', '0:a:0', '-map_metadata', '-1',
                    '-t', '13', '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le', str(normalized)], check=True, timeout=20)
    data, rate = sf.read(normalized, always_2d=True)
    duration = len(data) / rate
    if not 0 < duration <= 12.00001 or not np.isfinite(data).all():
        raise EngineError('LOCAL_AUDIO_LIMIT', 'Choose an audio clip up to 12 seconds.')
    if float(np.sqrt(np.mean(data ** 2))) < 0.0001:
        raise EngineError('LOCAL_NO_MELODY', 'This clip is too quiet to propose notes. Try a clear isolated melody.')
    duration = min(duration, 12.0)
    sys.path.insert(0, str(HOME / 'basic-pitch'))
    import onnxruntime as ort
    from basic_pitch.inference import Model, run_inference
    from basic_pitch.note_creation import model_output_to_notes
    model_path = HOME / 'basic-pitch/basic_pitch/saved_models/icassp_2022/nmp.onnx'
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    model = Model.__new__(Model)
    model.model_type = Model.MODEL_TYPES.ONNX
    model.model = ort.InferenceSession(str(model_path), sess_options=options, providers=['CPUExecutionProvider'])
    activation = run_inference(normalized, model)
    settings = dict(onset_thresh=.5, frame_thresh=.3, min_note_len=11,
                    include_pitch_bends=False, melodia_trick=True)
    _, events = model_output_to_notes(activation, **settings)
    proposal = sorted([{'start': max(0., float(start)), 'end': min(duration, float(end)),
                        'pitch': int(pitch), 'velocity': max(1, min(127, round(float(amplitude) * 127)))}
                       for start, end, pitch, amplitude, _ in events if min(duration, end) > max(0., start)],
                      key=lambda note: (note['start'], note['pitch'], note['end']))
    if not proposal:
        raise EngineError('LOCAL_NO_MELODY', 'No melody notes were found. Try a clear isolated instrument or hum.')
    validate_notes(proposal, duration)
    np.savez_compressed(output / 'activations.npz', **activation)
    result = render(proposal, duration, DEFAULT_RECIPE, output, status='unreviewed_proposal')
    result['provenance'].update({'transcriber': 'Spotify Basic Pitch 0.4.0', 'backend': 'ONNX Runtime CPU',
                                 'modelSha256': digest(model_path), 'sourceSha256': digest(source),
                                 'decoder': settings, 'threads': 2,
                                 'limitations': 'Unreviewed transcription; no key correction, note selection or pitch-bend preservation.'})
    return result


def arrangement(notes, duration, recipe):
    # For a recorded lead this program is only the MIDI transcription guide.
    programs = {'piano': 0, 'electric_piano': 4, 'guitar': 24, 'recorded': 0}
    tracks = [{'id': 'lead', 'program': programs[recipe['instrument']], 'bank': 0, 'notes': deepcopy(notes)}]
    beat = 60 / recipe['bpm']
    grid = recipe.get('groove')
    origin = grid['downbeatMs'] / 1000 if grid else min(note['start'] for note in notes)
    sparse, soft = recipe['density'] == 'sparse', recipe['backingVolume'] == 'soft'
    use_melody_bass = 'harmony' not in recipe and recipe['bass']
    bass, drums = [], []
    step = 0
    # Always schedule the opening backing beat. Keep the existing tail margin on
    # subsequent beats so ordinary arrangements retain their qualified timing.
    while step == 0 or origin + step * beat < duration - .12:
        start = origin + step * beat
        if use_melody_bass and step % (4 if sparse else 2) == 0:
            active = [note for note in notes if note['start'] <= start < note['end']]
            if grid and not active:
                active = [min(notes, key=lambda n: min(abs(n['start'] - start), abs(n['end'] - start)))]
            if active:
                chosen = max(active, key=lambda note: note['velocity'])
                bass.append({'start': start, 'end': min(duration, start + beat * .65),
                             'pitch': 36 + chosen['pitch'] % 12, 'velocity': 38 if soft else 58})
        if step % (4 if sparse else 2) == 0:
            drums.append({'start': start, 'end': min(duration, start + .10), 'pitch': 36, 'velocity': 36 if soft else 54})
        if not sparse or step % 2 == 0:
            drums.append({'start': start, 'end': min(duration, start + .08), 'pitch': 42, 'velocity': 28 if soft else 43})
        step += 1
    if 'harmony' in recipe:
        from chart import backing
        from jazz_arrangement import swing_combo
        score = swing_combo if recipe.get('accompanimentStyle') == 'swing_combo' else backing
        tracks.extend(score(recipe['harmony'], duration, recipe['bass'], sparse, soft))
    elif recipe['bass'] and bass:
        tracks.append({'id': 'bass', 'program': 32, 'bank': 0, 'notes': bass})
    if grid and recipe['drums']:
        drums = groove_notes(grid['pattern'], recipe['bpm'], origin, duration, sparse, soft)
    if recipe['drums'] and drums:
        tracks.append({'id': 'drums', 'program': 0, 'bank': 128, 'notes': drums})
    return tracks


def schedule(tracks, duration):
    events = []
    for channel, track in enumerate(tracks):
        validate_notes(track['notes'], duration)
        for note in track['notes']:
            events.extend([(round(note['start'] * 44100), channel, 1, note['pitch'], note['velocity']),
                           (round(note['end'] * 44100), channel, 0, note['pitch'], note['velocity'])])
    if len(events) > 16384:
        raise EngineError('LOCAL_INVALID_NOTES', 'This arrangement contains too many notes.')
    events.sort(key=lambda event: (event[0], event[2], event[1], event[3]))
    frames = round(duration * 44100)
    lines = [f'{frames} {len(tracks)} {len(events)}', *[f'{track["bank"]} {track["program"]}' for track in tracks]]
    lines.extend(' '.join(map(str, event)) for event in events)
    return '\n'.join(lines) + '\n', frames


def synthesized_stems(tracks, duration, output):
    event_text, frames = schedule(tracks, duration)
    events = output / 'events.txt'
    events.write_text(event_text)
    raw = output / 'render.f32'
    with (output / 'render.log').open('w') as log:
        subprocess.run([str(HOME / 'renderer'), str(HOME / 'FluidR3_GM.sf2'), str(events), str(raw)],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=20)
    samples = np.fromfile(raw, dtype='<f4')
    if samples.size != frames * 2 or not np.isfinite(samples).all():
        raise EngineError('LOCAL_AUDIO_INVALID', 'The rendered audio is incomplete or invalid.')
    samples = samples.reshape(-1, 2)
    dry_audio = {}
    accumulated = np.zeros_like(samples, dtype=np.float64)
    for index, track in enumerate(tracks):
        path = Path(str(raw) + f'.track-{index}.f32')
        stem = np.fromfile(path, dtype='<f4').reshape(-1, 2)
        if stem.shape != samples.shape or not np.isfinite(stem).all():
            raise EngineError('LOCAL_AUDIO_INVALID', 'A rendered instrument stem is invalid.')
        if track['id'] == 'chords' and not np.any(stem):
            raise EngineError('LOCAL_INVALID_RECIPE', 'The chord bars are too short to sound. Choose a chord in an earlier bar or leave the chart at rest.')
        if track['id'] != 'lead' and not np.any(stem):
            raise EngineError('LOCAL_INVALID_RECIPE', 'The backing produced no audio. Choose an earlier melody onset or turn off that backing track.')
        accumulated += stem
        dry_audio[track['id']] = stem
        path.unlink()
    raw.unlink()
    if not np.array_equal(accumulated.astype('<f4'), samples):
        raise EngineError('LOCAL_AUDIO_INVALID', 'Instrument stems do not reconstruct the rendered mix.')
    return dry_audio


def render(notes, duration, recipe, output, status='provided_notes', source=None, source_sha=None):
    notes = validate_notes(notes, duration)
    recipe = validate_recipe(recipe, duration)
    anchor = canonical_hash(notes)
    tracks = arrangement(notes, duration, recipe)
    if canonical_hash(tracks[0]['notes']) != anchor:
        raise EngineError('LOCAL_INTERNAL', 'The locked melody changed before rendering.')
    controls = validate_mix(recipe.get('mix', {}), [track['id'] for track in tracks])
    started = time.monotonic()
    recorded = recipe['instrument'] == 'recorded'
    if recorded:
        from recorded_lead import decode_recording
        dry_audio = {'lead': decode_recording(source, source_sha, round(duration * 44100), output)}
        if tracks[1:]:
            dry_audio.update(synthesized_stems(tracks[1:], duration, output))
    else:
        dry_audio = synthesized_stems(tracks, duration, output)
    stems = []
    for index, (name, samples) in enumerate(dry_audio.items()):
        stem_path = output / f'stem-{index}.raw.wav'
        sf.write(stem_path, samples, 44100, subtype='FLOAT')
        stems.append({'id': name, 'path': str(stem_path),
                      'pcmSha256': hashlib.sha256(np.ascontiguousarray(samples, dtype='<f4').tobytes()).hexdigest()})
    samples, tone_results = render_mix(dry_audio, controls)
    audio = output / 'audio.wav'
    export = save_safe_wav(audio, samples.T, 44100)
    try:
        integrity = validate(audio, expected_seconds=duration)
    except ValueError as error:
        if controls and str(error) == 'Silent output':
            raise EngineError('LOCAL_INVALID_RECIPE', 'This mix is too quiet to publish. Raise the gain or unmute a track.') from error
        raise
    if integrity['clipped_fraction'] or integrity['rms'] < 1e-6:
        raise EngineError('LOCAL_AUDIO_INVALID', 'The rendered audio is clipped or silent.')
    # Chart clips may end one sample into their final bar. MIDI note-off must
    # follow note-on by at least one tick for other programs to read the note.
    has_chart = 'harmony' in recipe
    midi = pretty_midi.PrettyMIDI(resolution=7680 if has_chart else 960, initial_tempo=recipe['bpm'])
    expanded = 0
    for track in tracks:
        name = 'lead transcription guide' if recorded and track['id'] == 'lead' else track['id']
        part = pretty_midi.Instrument(program=track['program'], is_drum=track['bank'] == 128, name=name)
        part.notes = [pretty_midi.Note(**note) for note in track['notes']]
        if has_chart and track['id'] in ('bass', 'chords'):
            for note in part.notes:
                on = midi.time_to_tick(note.start)
                if midi.time_to_tick(note.end) == on:
                    note.end = midi.tick_to_time(int(on) + 1)
                    expanded += 1
        midi.instruments.append(part)
    midi_path = output / 'arrangement.mid'
    midi.write(str(midi_path))
    imported = pretty_midi.PrettyMIDI(str(midi_path))
    for original, actual in zip(tracks, imported.instruments, strict=True):
        expected = sorted(original['notes'], key=lambda note: (note['pitch'], note['start'], note['end']))
        received = sorted(actual.notes, key=lambda note: (note.pitch, note.start, note.end))
        if len(expected) != len(received) or not all(a['pitch'] == b.pitch and a['velocity'] == b.velocity and
                abs(a['start'] - b.start) < .001 and abs(a['end'] - b.end) < .001 for a, b in zip(expected, received)):
            raise EngineError('LOCAL_MIDI_INVALID', 'MIDI export changed the arrangement notes.')
    note_path = output / 'notes.json'
    write_json(note_path, {'schemaVersion': 1, 'status': status, 'notes': notes, 'duration': duration, 'notesSha256': anchor})
    write_json(output / 'tracks.json', tracks)
    renderer = 'TinySoundFont isolated-track v2 / FluidR3'
    if recorded:
        renderer = 'Uploaded performance + TinySoundFont backing / FluidR3' if tracks[1:] else 'Uploaded performance'
    return {'notes': notes, 'duration': duration, 'notesPath': str(note_path), 'audioPath': str(audio),
            'midiPath': str(midi_path), 'recipe': recipe,
            'provenance': {'renderer': renderer, 'notesSha256': anchor,
                           'audioSha256': digest(audio), 'midiSha256': digest(midi_path), 'stems': stems,
                           **({'harmony': {'method': 'reviewed major/minor chart v1',
                                           **({'performance': 'swing combo v1'}
                                              if recipe.get('accompanimentStyle') == 'swing_combo' else {}),
                                           'chartSha256': canonical_hash(recipe['harmony']),
                                           'minimumTickExtensions': expanded}} if has_chart else {}),
                           **({'mix': {'method': 'isolated stereo stem mixer v1', 'strips': controls,
                                       **({'toneEqV1': tone_results} if tone_results else {}),
                                       'stemFiles': 'dry pre-fader audio', 'masterGainDb': export['gain_db']}} if controls else {}),
                           **({'leadSource': {'type': 'original_recording', 'sourceSha256': source_sha,
                                               'notesRole': 'MIDI guide and backing arrangement'}} if recorded else {}),
                           'export': export, 'audioValidation': integrity, 'seconds': time.monotonic() - started,
                           'arrangement': 'Explicit backing recipes; unchanged lead timing.',
                           'tailPolicy': 'Final 20ms fade on synthesized stems; recorded performances keep their ending.'}}
