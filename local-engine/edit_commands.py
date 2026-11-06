"""Compile Studio's explicit edit: command form into a complete, reviewable recipe."""
import re
from dataclasses import dataclass
from decimal import Decimal
from chart_phrases import chart_action

from common import DEFAULT_RECIPE, UNITY_STRIP, EngineError, active_stems, canonical_hash, recipe


PREFIX = re.compile(r'^edit:', re.IGNORECASE | re.ASCII)
STEM = r'(lead|bass|drums|chords)'
NUMBER = r'([+-]?(?:\d+(?:\.\d{1,3})?|\.\d{1,3}))'
GAIN = re.compile(rf'set {STEM} gain to {NUMBER} db', re.ASCII)
RELATIVE_GAIN = re.compile(rf'(lower|raise) {STEM} by {NUMBER} db', re.ASCII)
PAN = re.compile(rf'pan {STEM} {NUMBER}% (left|right)', re.ASCII)
HARD_PAN = re.compile(rf'pan {STEM} hard (left|right)', re.ASCII)
TRACK_MEMBERSHIP = re.compile(r'(?:(only) )?(add|remove) (?:the )?(bass|drums)')
TRACK_OBJECT = re.compile(r'(?:the )?(bass|drums)')
GROOVE = re.compile(r'use (swing ride|four on the floor) at (\d{1,3}) bpm from (\d{1,5}) ms', re.ASCII)


@dataclass(frozen=True)
class Assignment:
    field: str
    value: str | bool | Decimal
    stem: str | None = None
    relative: bool = False


@dataclass(frozen=True)
class Preservation:
    field: str | None
    value: str | bool | None = None

    def snapshot(self, recipe):
        if self.field == 'beat':
            drum_mix = recipe.get('mix', {}).get('drums', {})
            if not recipe['drums'] or drum_mix.get('muted'):
                return None
            # These fields determine drum events and their isolated mix on the frozen notes.
            return (recipe['bpm'], recipe.get('groove'), recipe['density'], recipe['backingVolume'], drum_mix)
        return recipe[self.field]


def preservation(clause):
    match = re.fullmatch(r'(?:keep|retain|preserve) (.+)', clause)
    if not match:
        return None
    target = re.sub(r'^(?:the |that |this |my )?(?:same |current |existing )?', '', match[1])
    if target in ('everything else', 'everything else unchanged', 'everything else the same'):
        return Preservation(None)
    if target in ('instrument', 'instrument unchanged', 'lead instrument', 'lead instrument unchanged'):
        return Preservation('instrument')
    if target in ('beat', 'drum beat', 'drum pattern'):
        return Preservation('beat')
    if target in ('piano', 'electric piano', 'guitar', 'uploaded recording'):
        return Preservation('instrument', 'recorded' if target == 'uploaded recording' else target.replace(' ', '_'))
    if match := re.fullmatch(r'(bass|drums)(?: (on|off|unchanged))?', target):
        value = None if match[2] == 'unchanged' else match[2] != 'off'
        return Preservation(match[1], value)
    return None


def unsupported(reason):
    return EngineError('LOCAL_UNSUPPORTED_REQUEST', reason)


def parse(clause):
    if clause == 'use the uploaded recording':
        return Assignment('instrument', 'recorded')
    if match := re.fullmatch(r'use (piano|electric piano|guitar)', clause):
        return Assignment('instrument', match[1].replace(' ', '_'))
    if match := TRACK_MEMBERSHIP.fullmatch(clause):
        return Assignment(match[3], match[2] == 'add')
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
    if match := re.fullmatch(rf'set {STEM} tone to (neutral|warm|bright)', clause):
        return Assignment('tone', match[2], match[1])
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
        name = "uploaded recording" if after['instrument'] == "recorded" else after['instrument'].replace('_', ' ')
        parts.append(f"lead on {name}")
    for stem in ('bass', 'drums'):
        if before[stem] != after[stem]:
            parts.append(f"{stem} {'on' if after[stem] else 'off'}")
    for field in ('backingVolume', 'density'):
        if before[field] != after[field]:
            parts.append(f"{after[field]} backing")
    if (before.get('groove'), before['bpm']) != (after.get('groove'), after['bpm']):
        grid = after.get('groove')
        parts.append(f"{grid['pattern'].replace('_', ' ')} at {after['bpm']} BPM from {grid['downbeatMs']} ms"
                     if grid else 'original pulse at 100 BPM')
    if before.get('harmony') != after.get('harmony'):
        chart = after.get('harmony')
        parts.append('chords ' + ' / '.join(label or 'rest' for label in chart['chords'])
                     if chart else 'chord chart removed')
        if chart is None and after['bass']:
            parts.append('bass follows the melody pulse')
    if before.get('accompanimentStyle') != after.get('accompanimentStyle'):
        parts.append('swing combo backing' if after.get('accompanimentStyle') else 'standard backing')
    if (after['bass'] and after.get('harmony') and not any(after['harmony']['chords'])
            and (before.get('harmony') != after['harmony'] or not before['bass'])):
        parts.append('bass rests until a chord is selected')
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
        if old.get('tone', 'neutral') != new.get('tone', 'neutral'):
            parts.append(f"{stem} tone {new.get('tone', 'neutral')}")
    sentence = ', '.join(parts)
    return sentence[:1].upper() + sentence[1:] + '.'


def apply_chart(action, state, assigned, annotations, notes, duration):
    from chart import edit_bar, propose
    verb, index, label = action
    key = ('chart', index if verb == 'bar' else verb)
    if key in assigned or (verb != 'bar' and any(track == 'chart' for track, _ in assigned)):
        raise unsupported('Suggest one chart, then give at most one change per bar.')
    if duration is None:
        raise unsupported('Review the confirmed melody before editing its chord chart.')
    if verb in ('suggest', 'grid'):
        if not notes:
            raise unsupported('Confirm the melody notes before asking for suggested chords.')
        current = state.get('harmony')
        bpm = current['bpm'] if current else state['bpm']
        downbeat = (current or state.get('groove') or {}).get('downbeatMs', 0)
        if verb == 'grid':
            bpm, downbeat = index, label
        state['harmony'], annotations['chordSuggestions'] = propose(notes, duration, bpm, downbeat)
    elif verb == 'remove':
        state.pop('harmony', None)
        state.pop('accompanimentStyle', None)
    else:
        if 'harmony' not in state:
            raise unsupported('Suggest a chord chart before changing a bar.')
        state['harmony'] = edit_bar(state['harmony'], index, label, duration)
    assigned.add(key)


def guided_plan(prompt, parent, notes=None, duration=None):
    """Return None for free text. Invalid explicit commands always raise a typed error."""
    text = prompt.lstrip() if isinstance(prompt, str) else ''
    prefix = PREFIX.match(text)
    if prefix is None:
        return None
    if len(prompt) > 1000:
        raise EngineError('LOCAL_INVALID_REQUEST', 'Keep the guided edit within 1000 characters.')
    commands = []
    for raw in text[prefix.end():].split(';'):
        first, conjunction, second = ' '.join(raw.lower().split()).partition(' and ')
        action = TRACK_MEMBERSHIP.fullmatch(first)
        track = TRACK_OBJECT.fullmatch(second)
        if conjunction and action and track:
            commands.extend((first, f'{action[2]} {track[1]}'))
        else:
            commands.append(raw)
    return reviewed_plan(commands, parent, notes, duration)


def execute_actions(before, actions, notes=None, duration=None):
    """Apply the whole edit to a copy. Only publishable recipes leave this boundary."""
    state = dict(before)
    strips = {stem: dict(strip) for stem, strip in state.pop('mix', {}).items()}
    assigned = set()
    preserved = {}
    annotations = {}
    for index, raw in enumerate(actions, 1):
        try:
            if isinstance(raw, Assignment):
                apply(raw, state, strips, assigned)
                continue
            if not raw.strip():
                raise unsupported('Enter one complete listed command using plain English text.')
            clause = ' '.join(raw.lower().split())
            guard = preservation(clause)
            if guard is not None:
                # The executor starts from the selected recipe and applies only explicit actions.
                # "Everything else" therefore needs no additional field-level guard.
                if guard.field is not None:
                    value = guard.snapshot(before)
                    if value is None or (guard.value is not None and value != guard.value):
                        raise unsupported('The selected version does not have the setting you asked to keep.')
                    preserved[guard] = value
                continue
            action = chart_action(clause)
            if action:
                apply_chart(action, state, assigned, annotations, notes, duration)
                continue
            if not raw.isascii():
                raise unsupported('Enter one complete listed command using plain English text.')
            grid = GROOVE.fullmatch(clause)
            if grid or clause == 'restore the original pulse':
                if any(field == 'groove' for _, field in assigned):
                    raise unsupported('Choose one drum pattern per edit.')
                if grid:
                    if (None, 'drums') in assigned and not state['drums']:
                        raise unsupported('Choose between selecting a drum pattern and removing drums.')
                    # A pattern activates its track. Only an explicit add/remove owns the membership field.
                    assigned.add(('drums', 'groove'))
                    state.update(drums=True, bpm=int(grid[2]), groove={'pattern': grid[1].replace(' ', '_'), 'downbeatMs': int(grid[3])})
                else:
                    assigned.add((None, 'groove'))
                    state.pop('groove', None)
                    state['bpm'] = 100
            else:
                apply(parse(clause), state, strips, assigned)
        except EngineError as error:
            raise EngineError(error.code, f'Command {index}. {error.message}') from error
    # Remove inherited strips for parts that stopped playing, preserving any explicit request
    # for an inaudible stem as an error instead of silently skipping it.
    for stem in strips.keys() - set(active_stems(state)):
        if any(track == stem for track, _ in assigned):
            raise unsupported(f'The requested mix needs an audible {stem} track.')
        del strips[stem]
    complete = recipe({**state, 'mix': strips}, duration)
    if any(guard.snapshot(complete) != value for guard, value in preserved.items()):
        raise unsupported('The request changes a setting you also asked to keep.')
    if complete == before:
        raise EngineError('LOCAL_NO_CHANGE', 'These settings already match the selected version.')
    return complete, annotations


def reviewed_plan(commands, parent, notes=None, duration=None, syntax=None):
    before = recipe(DEFAULT_RECIPE if parent is None else parent, duration)
    try:
        complete, annotations = execute_actions(before, commands, notes, duration)
    except EngineError as error:
        if error.code == 'LOCAL_INVALID_RECIPE':
            raise unsupported(error.message) from error
        raise
    if syntax:
        annotations['syntax'] = syntax
    changes = {key: complete.get(key, {} if key == 'mix' else None) for key in (*DEFAULT_RECIPE, 'groove', 'harmony', 'accompanimentStyle', 'mix')
               if complete.get(key) != before.get(key)}
    return {
        'recipe': complete,
        'explanation': summarize(before, complete),
        'provenance': {'origin': 'GUIDED_COMMANDS', 'inferenceAttempts': 0, **annotations,
                       'parentRecipeSha256': canonical_hash(before), 'changes': changes},
    }
