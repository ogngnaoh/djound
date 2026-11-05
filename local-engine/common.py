"""Local engine paths, strict value contracts, and artifact identities."""
import hashlib
import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOME = Path(os.environ.get('DJOUND_LOCAL_ENGINE_HOME', ROOT / '.runtime/local-engine')).resolve()
DEFAULT_RECIPE = {'instrument': 'piano', 'bass': False, 'drums': False,
                  'density': 'steady', 'backingVolume': 'normal', 'bpm': 100}


class EngineError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def data_root():
    value = os.environ.get('DJOUND_LOCAL_DATA_ROOT')
    if not value:
        raise EngineError('LOCAL_CONFIGURATION', 'The local workspace data directory is not configured.')
    return Path(value).resolve()


def inside(path, root, must_exist=False):
    candidate = Path(path)
    if not candidate.is_absolute():
        raise EngineError('LOCAL_INVALID_REQUEST', 'Worker artifact paths must be absolute.')
    candidate = candidate.resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise EngineError('LOCAL_INVALID_REQUEST', 'The requested artifact is outside this workspace.')
    if must_exist and not candidate.is_file():
        raise EngineError('LOCAL_INVALID_REQUEST', 'A required local artifact is missing.')
    return candidate


def recipe(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_RECIPE):
        raise EngineError('LOCAL_INVALID_RECIPE', 'The proposed arrangement is incomplete or has unsupported fields.')
    if value['instrument'] not in ('piano', 'electric_piano', 'guitar'):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose piano, electric piano, or guitar.')
    if type(value['bass']) is not bool or type(value['drums']) is not bool:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Bass and drums must be explicit on/off choices.')
    if value['density'] not in ('sparse', 'steady') or value['backingVolume'] not in ('soft', 'normal'):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Use sparse or steady backing and soft or normal backing volume.')
    if type(value['bpm']) is not int or value['bpm'] != 100:
        raise EngineError('LOCAL_INVALID_RECIPE', 'This version uses a fixed 100 BPM backing; melody timing stays unchanged.')
    # Equivalent Java/JSON maps may arrive in different key orders. The model
    # consumes tokens, not an abstract map: use one stable prompt representation.
    return {key: value[key] for key in DEFAULT_RECIPE}


def notes(value, duration):
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 12:
        raise EngineError('LOCAL_AUDIO_LIMIT', 'Choose a finite audio clip up to 12 seconds.')
    if not isinstance(value, list) or not 1 <= len(value) <= 4096:
        raise EngineError('LOCAL_INVALID_NOTES', 'Provide between one and 4096 melody notes.')
    copied = []
    for note in value:
        if not isinstance(note, dict) or set(note) != {'start', 'end', 'pitch', 'velocity'}:
            raise EngineError('LOCAL_INVALID_NOTES', 'Each melody note needs start, end, pitch and velocity.')
        if any(type(note[key]) not in (int, float) or not math.isfinite(note[key]) for key in ('start', 'end')):
            raise EngineError('LOCAL_INVALID_NOTES', 'Note times must be finite numbers.')
        if not 0 <= note['start'] < note['end'] <= duration:
            raise EngineError('LOCAL_INVALID_NOTES', 'A note falls outside the audio clip.')
        if type(note['pitch']) is not int or not 0 <= note['pitch'] <= 127 or type(note['velocity']) is not int or not 1 <= note['velocity'] <= 127:
            raise EngineError('LOCAL_INVALID_NOTES', 'Note pitch or velocity is outside MIDI range.')
        copied.append(dict(note))
    occupied = {}
    for note in sorted(copied, key=lambda item: (item['start'], item['pitch'])):
        start, end = round(note['start'] * 44100), round(note['end'] * 44100)
        if end <= start or start < occupied.get(note['pitch'], -1):
            raise EngineError('LOCAL_INVALID_NOTES', 'Review overlapping notes at the same pitch or notes shorter than one audio sample.')
        occupied[note['pitch']] = end
    return copied
