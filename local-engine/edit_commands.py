"""Compile Studio's explicit edit: command form into a complete, reviewable recipe."""
import re
from dataclasses import dataclass
from decimal import Decimal

from common import DEFAULT_RECIPE, UNITY_STRIP, EngineError, active_stems, canonical_hash, recipe


PREFIX = re.compile(r'^edit:', re.IGNORECASE | re.ASCII)
STEM = r'(lead|bass|drums)'
NUMBER = r'([+-]?(?:\d+(?:\.\d{1,3})?|\.\d{1,3}))'
GAIN = re.compile(rf'set {STEM} gain to {NUMBER} db', re.ASCII)
RELATIVE_GAIN = re.compile(rf'(lower|raise) {STEM} by {NUMBER} db', re.ASCII)
PAN = re.compile(rf'pan {STEM} {NUMBER}% (left|right)', re.ASCII)
HARD_PAN = re.compile(rf'pan {STEM} hard (left|right)', re.ASCII)


@dataclass(frozen=True)
class Assignment:
    field: str
    value: str | bool | Decimal
    stem: str | None = None
    relative: bool = False


def unsupported(reason):
    return EngineError('LOCAL_UNSUPPORTED_REQUEST', reason)


def parse(clause):
    if match := re.fullmatch(r'use (piano|electric piano|guitar)', clause):
        return Assignment('instrument', match[1].replace(' ', '_'))
    if match := re.fullmatch(r'(add|remove) (bass|drums)', clause):
        return Assignment(match[2], match[1] == 'add')
    if match := re.fullmatch(r'make the backing (soft|normal|sparse|steady)', clause):
        return Assignment('density' if match[1] in ('sparse', 'steady') else 'backingVolume', match[1])
    if match := GAIN.fullmatch(clause):
        return Assignment('gainDb', Decimal(match[2]), match[1])
    if match := RELATIVE_GAIN.fullmatch(clause):
        magnitude = Decimal(match[3])
        if magnitude <= 0:
            raise unsupported('Use a positive dB amount with lower or raise.')
        return Assignment('gainDb', magnitude if match[1] == 'raise' else -magnitude, match[2], relative=True)
    if match := PAN.fullmatch(clause):
        percent = Decimal(match[2])
        if not 0 <= percent <= 100:
            raise unsupported('Pan must be between 0% and 100% left or right.')
        return Assignment('pan', percent / (100 if match[3] == 'right' else -100), match[1])
    if match := HARD_PAN.fullmatch(clause):
        return Assignment('pan', Decimal(-1 if match[2] == 'left' else 1), match[1])
    if match := re.fullmatch(rf'center {STEM}', clause):
        return Assignment('pan', Decimal(0), match[1])
    if match := re.fullmatch(rf'(mute|unmute) {STEM}', clause):
        return Assignment('muted', match[1] == 'mute', match[2])
    raise unsupported('Choose a listed command and separate each change with a semicolon.')


def apply(assignment, state, strips, assigned):
    field, stem, value = assignment.field, assignment.stem, assignment.value
    key = (stem, field)
    if key in assigned:
        raise unsupported(f'Give one instruction for {stem + " " if stem else ""}{field}.')
    assigned.add(key)
    if stem is None:
        if field in ('bass', 'drums') and value is False:
            if any(track == field for track, _ in assigned):
                raise unsupported(f'Choose between adjusting {field} and removing it.')
            strips.pop(field, None)
        state[field] = value
        return
    if stem not in active_stems(state):
        raise unsupported(f'Add {stem} before adjusting its mix.')
    strip = strips.setdefault(stem, dict(UNITY_STRIP))
    if field == 'gainDb':
        if assignment.relative:
            value += Decimal(str(strip[field]))
        if not -60 <= value <= 12:
            raise unsupported(f'The resulting {stem} gain must be between -60 and +12 dB.')
    strip[field] = float(value) if isinstance(value, Decimal) else value


def summarize(before, after):
    parts = []
    if before['instrument'] != after['instrument']:
        parts.append(f"lead on {after['instrument'].replace('_', ' ')}")
    for stem in ('bass', 'drums'):
        if before[stem] != after[stem]:
            parts.append(f"{stem} {'on' if after[stem] else 'off'}")
    for field in ('backingVolume', 'density'):
        if before[field] != after[field]:
            parts.append(f"{after[field]} backing")
    for stem in active_stems(after):
        old = before.get('mix', {}).get(stem, UNITY_STRIP)
        new = after.get('mix', {}).get(stem, UNITY_STRIP)
        if old['gainDb'] != new['gainDb']:
            parts.append(f"{stem} gain {new['gainDb']:+g} dB")
        if old['pan'] != new['pan']:
            direction = 'left' if new['pan'] < 0 else 'right'
            pan = f"{abs(new['pan']) * 100:g}% {direction}" if new['pan'] else 'center'
            parts.append(f'{stem} pan {pan}')
        if old['muted'] != new['muted']:
            parts.append(f"{stem} {'muted' if new['muted'] else 'unmuted'}")
    sentence = ', '.join(parts)
    return sentence[:1].upper() + sentence[1:] + '.'


def guided_plan(prompt, parent):
    """Return None for free text. Invalid explicit commands always raise a typed error."""
    text = prompt.lstrip() if isinstance(prompt, str) else ''
    prefix = PREFIX.match(text)
    if prefix is None:
        return None
    if len(prompt) > 1000:
        raise EngineError('LOCAL_INVALID_REQUEST', 'Keep the guided edit within 1000 characters.')
    before = recipe(DEFAULT_RECIPE if parent is None else parent)
    state = dict(before)
    strips = {stem: dict(strip) for stem, strip in state.pop('mix', {}).items()}
    assigned = set()
    for index, raw in enumerate(text[prefix.end():].split(';'), 1):
        try:
            if not raw.isascii() or not raw.strip():
                raise unsupported('Enter one complete listed command using plain English text.')
            clause = ' '.join(raw.lower().split())
            apply(parse(clause), state, strips, assigned)
        except EngineError as error:
            raise EngineError(error.code, f'Command {index}. {error.message}') from error
    try:
        complete = recipe({**state, 'mix': strips})
    except EngineError as error:
        raise unsupported(error.message) from error
    if complete == before:
        raise EngineError('LOCAL_NO_CHANGE', 'These commands already match the selected version.')
    changes = {key: complete.get(key, {}) for key in (*DEFAULT_RECIPE, 'mix')
               if complete.get(key) != before.get(key)}
    return {
        'recipe': complete,
        'explanation': summarize(before, complete),
        'provenance': {'origin': 'GUIDED_COMMANDS', 'inferenceAttempts': 0,
                       'parentRecipeSha256': canonical_hash(before), 'changes': changes},
    }
