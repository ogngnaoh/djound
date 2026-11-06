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


STEM_NAMES = ('lead', 'bass', 'drums', 'chords')
UNITY_STRIP = {'gainDb': 0, 'pan': 0, 'muted': False}


def arrangement_settings(value):
    """Return the original six model-visible fields in their qualified token order."""
    return {key: value[key] for key in DEFAULT_RECIPE}


def active_stems(arrangement):
    chart = arrangement.get('harmony')
    pitched = chart is not None and any(chord is not None for chord in chart['chords'])
    enabled = {'lead': True, 'bass': arrangement['bass'] and (chart is None or pitched),
               'drums': arrangement['drums'], 'chords': pitched}
    return tuple(name for name in STEM_NAMES if enabled[name])


def validate_mix(value, available):
    if not isinstance(value, dict) or set(value) - set(available):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Adjust the mix only for active tracks in this arrangement.')
    result = {}
    for name in available:
        if name not in value:
            continue
        strip = value[name]
        if (not isinstance(strip, dict) or strip.keys() - {'tone'} != UNITY_STRIP.keys()
                or type(strip['muted']) is not bool):
            raise EngineError('LOCAL_INVALID_RECIPE', 'Each track needs gain in dB, stereo pan and a mute setting.')
        normalized = {}
        for field, low, high in (('gainDb', -60, 12), ('pan', -1, 1)):
            number = strip[field]
            if type(number) not in (int, float) or not low <= number <= high:
                raise EngineError('LOCAL_INVALID_RECIPE', f'{field} must be a finite number from {low} to {high}.')
            normalized[field] = int(number) if number == int(number) else number
        normalized['muted'] = strip['muted']
        tone = strip.get('tone', 'neutral')
        if tone not in ('neutral', 'warm', 'bright'):
            raise EngineError('LOCAL_INVALID_RECIPE', 'Choose neutral, warm or bright track tone.')
        if tone != 'neutral':
            normalized['tone'] = tone
        if normalized != UNITY_STRIP:
            result[name] = normalized
    if all(result.get(name, UNITY_STRIP)['muted'] for name in available):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Unmute at least one active track before creating a version.')
    return result


def recipe(value, duration=None):
    if not isinstance(value, dict) or (set(value) - {'mix', 'groove', 'harmony'}) != set(DEFAULT_RECIPE):
        raise EngineError('LOCAL_INVALID_RECIPE', 'The proposed arrangement is incomplete or has unsupported fields.')
    if value['instrument'] not in ('piano', 'electric_piano', 'guitar', 'recorded'):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose the uploaded recording, piano, electric piano, or guitar.')
    if type(value['bass']) is not bool or type(value['drums']) is not bool:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Bass and drums must be explicit on/off choices.')
    if value['density'] not in ('sparse', 'steady') or value['backingVolume'] not in ('soft', 'normal'):
        raise EngineError('LOCAL_INVALID_RECIPE', 'Use sparse or steady backing and soft or normal backing volume.')
    # Equivalent Java/JSON maps may arrive in different key orders. The model
    # consumes tokens, not an abstract map: use one stable prompt representation.
    result = arrangement_settings(value)
    if type(value['bpm']) is not int or not 40 <= value['bpm'] <= 200:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose a backing tempo from 40 to 200 BPM.')
    if 'groove' in value:
        grid = value['groove']
        if not isinstance(grid, dict) or set(grid) != {'pattern', 'downbeatMs'} or grid['pattern'] not in ('swing_ride', 'four_on_the_floor'):
            raise EngineError('LOCAL_INVALID_RECIPE', 'Choose swing ride or four on the floor with an explicit first beat.')
        if type(grid['downbeatMs']) is not int or not 0 <= grid['downbeatMs'] <= (12 if duration is None else duration) * 1000 - 10 + 1e-7:
            raise EngineError('LOCAL_INVALID_RECIPE', 'Place the first beat inside the recording with at least 10 ms left to play.')
        result['groove'] = dict(grid)
    elif value['bpm'] != 100:
        raise EngineError('LOCAL_INVALID_RECIPE', 'Choose a drum pattern to set a backing tempo. The original pulse uses 100 BPM.')
    if 'harmony' in value:
        from chart import validate_chart
        result['harmony'] = validate_chart(value['harmony'], duration)
    if 'mix' in value:
        strips = validate_mix(value['mix'], active_stems(result))
        if strips:
            result['mix'] = strips
    return result


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
