"""Preview one tone choice by remixing a saved render's verified dry stems."""
from contextlib import contextmanager, ExitStack
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
from tempfile import TemporaryDirectory

from common import EngineError, UNITY_STRIP, active_stems, data_root, digest, recipe, validate_mix


ATTEMPT = re.compile(r'attempt_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
SHA256 = re.compile(r'[0-9a-f]{64}')
FIELDS = {'schemaVersion', 'operation', 'basisAttemptId', 'basisAudioSha256', 'parentRecipe', 'duration', 'stem', 'tone'}


def invalid_basis():
    return EngineError('LOCAL_AUDITION_BASIS_INVALID', 'The saved audio or its tracks have changed or are unavailable. Choose another local version.')


def validate_request(request):
    if (not isinstance(request, dict) or set(request) != FIELDS or type(request['schemaVersion']) is not int
            or request['schemaVersion'] != 1 or request['operation'] != 'audition'
            or not isinstance(request['basisAttemptId'], str) or not ATTEMPT.fullmatch(request['basisAttemptId'])
            or not isinstance(request['basisAudioSha256'], str) or not SHA256.fullmatch(request['basisAudioSha256'])):
        raise EngineError('LOCAL_INVALID_REQUEST', 'Choose a saved local version to audition.')
    duration = request['duration']
    if type(duration) not in (int, float) or not 0 < duration <= 12:
        raise EngineError('LOCAL_AUDIO_LIMIT', 'Choose a finite saved version up to twelve seconds.')
    parent = recipe(request['parentRecipe'], duration)
    target = request['stem']
    if (target not in active_stems(parent) or parent.get('mix', {}).get(target, UNITY_STRIP)['muted']
            or request['tone'] not in ('neutral', 'warm', 'bright')):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose an active, unmuted track and a neutral, warm or bright tone.')
    return parent


@contextmanager
def basis_directory(root, attempt):
    # Pin each directory and reject symlinks even if one is swapped during traversal.
    flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY
    with ExitStack() as close:
        directory = os.open(root, flags)
        close.callback(os.close, directory)
        for component in ('local-jobs', attempt, 'output'):
            directory = os.open(component, flags, dir_fd=directory)
            close.callback(os.close, directory)
        yield directory


def read_artifact(directory, name, limit):
    flags = os.O_NOFOLLOW | os.O_RDONLY | os.O_NONBLOCK
    with os.fdopen(os.open(name, flags, dir_fd=directory), 'rb') as src:
        info = os.fstat(src.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise invalid_basis()
        contents = src.read(limit + 1)
        if len(contents) != info.st_size:
            raise invalid_basis()
        return contents


def wav_samples(contents, frames, subtype, *, read_pcm):
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(BytesIO(contents)) as audio:
        if (audio.format != 'WAV' or audio.samplerate != 44100 or audio.channels != 2 or
                audio.frames != frames or audio.subtype != subtype):
            raise invalid_basis()
        if read_pcm:
            samples = audio.read(frames, dtype='float32', always_2d=True)
            if samples.shape != (frames, 2) or not np.isfinite(samples).all():
                raise invalid_basis()
            return np.ascontiguousarray(samples, dtype='<f4')


def verified_stems(request, parent):
    import soundfile as sf

    attempt = request['basisAttemptId']
    root = data_root()
    expected = root / 'local-jobs' / attempt / 'output'
    frames = round(request['duration'] * 44100)
    names = active_stems(parent)
    try:
        with basis_directory(root, attempt) as directory:
            basis = json.loads(read_artifact(directory, 'result.json', 2 * 1024**2))
            if (not isinstance(basis, dict) or type(basis.get('schemaVersion')) is not int
                    or basis['schemaVersion'] != 1 or basis.get('operation') != 'render'
                    or basis.get('status') != 'succeeded' or type(basis.get('duration')) not in (int, float)
                    or basis['duration'] != request['duration'] or recipe(basis['recipe'], basis['duration']) != parent
                    or basis.get('audioPath') != str(expected / 'audio.wav')):
                raise invalid_basis()
            meta = basis['provenance']
            audio = read_artifact(directory, 'audio.wav', 4 * frames + 65536)
            if meta['audioSha256'] != request['basisAudioSha256'] or hashlib.sha256(audio).hexdigest() != meta['audioSha256']:
                raise invalid_basis()
            wav_samples(audio, frames, 'PCM_16', read_pcm=False)
            if not isinstance(meta['stems'], list) or len(meta['stems']) != len(names):
                raise invalid_basis()
            stems = {}
            for index, entry in enumerate(meta['stems']):
                name = entry['id']
                filename = f'stem-{index}.raw.wav'
                if name not in names or name in stems or entry.get('path') != str(expected / filename):
                    raise invalid_basis()
                data = read_artifact(directory, filename, 8 * frames + 65536)
                samples = wav_samples(data, frames, 'FLOAT', read_pcm=True)
                if hashlib.sha256(samples).hexdigest() != entry.get('pcmSha256'):
                    raise invalid_basis()
                stems[name] = samples
            return stems
    except (OSError, ValueError, KeyError, TypeError, sf.SoundFileError) as exc:
        raise invalid_basis() from exc


def audition(request, output):
    from safe_export import save_safe_wav
    from stem_mixer import render_mix
    from validate_audio import validate

    parent = validate_request(request)
    dry = verified_stems(request, parent)
    name, tone = request['stem'], request['tone']
    controls = dict(parent.get('mix', {}))
    controls[name] = {**controls.get(name, UNITY_STRIP), 'tone': tone}
    controls = validate_mix(controls, dry.keys())
    mixed, _ = render_mix(dry, controls)
    audio = Path(output) / 'audio.wav'
    with TemporaryDirectory(dir=output) as tmp:
        preview = Path(tmp) / 'audio.wav'
        save_safe_wav(preview, mixed.T)
        try:
            integrity = validate(preview, expected_seconds=request['duration'])
        except ValueError as exc:
            raise EngineError('LOCAL_AUDIO_INVALID', 'This preview is silent or invalid. Choose a different track or tone.') from exc
        if integrity['clipped_fraction'] or integrity['rms'] < 1e-6:
            raise EngineError('LOCAL_AUDIO_INVALID', 'The tone preview is clipped or silent.')
        checksum = digest(preview)
        os.link(preview, audio)  # Publish only the checked PCM16 mix; never replace an earlier output.
    return {'audioPath': str(audio), 'duration': request['duration'],
            'provenance': {'basisAttemptId': request['basisAttemptId'], 'basisAudioSha256': request['basisAudioSha256'],
                           'audioSha256': checksum, 'stem': name, 'tone': tone, 'inferenceAttempts': 0}}
