"""Compile complete instructions whose tone, chart and mix operations are known."""
import json
import re

from chart_phrases import chart_action
from common import ROOT, EngineError
from edit_commands import parse


COMMAND_TEMPLATES = [(re.compile(row['pattern']), row['command'], kind)
                     for kind in ('mix', 'arrangement')
                     for row in json.loads((ROOT / f'contracts/{kind}-phrases.json').read_text())]
TONES = json.loads((ROOT / 'contracts/tone-phrases.json').read_text())
PHRASES = {
    pattern.format(track=article + name): (stem, tone)
    for name, stem in TONES['tracks'].items()
    for tone, patterns in TONES['tones'].items()
    for pattern in patterns
    for article in ('', 'the ')
}


def clauses(text):
    normalized = ' '.join(text.lower().split()).removeprefix('please ').removesuffix('.')
    return re.split(r'\s*;\s*|,? (?:and then|and|then) ', normalized)


def compile_clause(clause):
    if clause in PHRASES:
        stem, tone = PHRASES[clause]
        return f'set {stem} tone to {tone}', 'tone'
    if chart_action(clause):
        return clause, 'chart'
    for pattern, template, kind in COMMAND_TEMPLATES:
        if match := pattern.fullmatch(clause):
            return re.sub(r'\$(\d)', lambda group: match[int(group[1])], template), kind
    return None


def reviewed_phrases(text):
    commands = []
    kinds = set()
    for clause in clauses(text):
        matched = compile_clause(clause)
        if matched is None:
            return None
        command, kind = matched
        commands.append(command)
        kinds.add(kind)
    if kinds == {'tone'}:
        syntax = 'tone-phrases-v1'
    elif kinds == {'chart'}:
        syntax = 'chart-phrases-v1'
    else:
        syntax = 'reviewed-phrases-v1'
    return commands, syntax


# The model has one shared backing-volume control and no track mixer fields.
# Check each clause separately so a track removal next to a global backing
# request stays representable. Exact mixer phrases above keep their own strips.
_TRACK = re.compile(r'\b(?:bass(?:line)?|drums?|percussion|lead|melody|piano|keys|guitar|chords?|vocals?|instrument)\b')
_LEVEL = re.compile(r'\b(?:loud(?:er|ness)?|quiet(?:er)?|soft(?:er|en)?|lower|raise|boost|attenuate|volume|level|gain|down|up)\b')
_MIX_UNITS = re.compile(r'\b(?:gain|decibels?|mute|unmute|muting|unmuting|pan|panning|unpan|cent(?:er|re))\b|(?<![a-z])db\b')
_GLOBAL_BACKING = re.compile(r'\b(?:backing|accompaniment|rhythm section)\b')
_PRESERVE = re.compile(r"\b(?:keep|preserve|retain|maintain|leave|unchanged|never|not)\b|n['’]t\b")
_VOLUME = re.compile(r'(?:make (?:the )?(?:backing|accompaniment) (quieter|softer|soft|normal)|'
                     r'set (?:the )?backing volume to (soft|normal)|soften (?:the )?(backing|accompaniment))')


def freeform_requirements(text):
    """Reject track-only instructions and retain explicit obligations for a model proposal."""
    required = {}
    all_clauses_known = True
    for clause in clauses(text):
        matched = compile_clause(clause)
        if matched and (matched[1] == 'arrangement' or matched[0] in ('add bass', 'remove bass', 'add drums', 'remove drums')):
            # Only ordinary slot assignments can become obligations on the six-slot model.
            # A groove or the recorded lead can only go through the complete command path.
            action = parse(matched[0])
            if action.value == 'recorded':
                raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'Review the complete uploaded-lead request in Guided edits.')
            field, value = action.field, action.value
        elif volume := _VOLUME.fullmatch(clause):
            field, value = 'backingVolume', 'normal' if 'normal' in volume.groups() else 'soft'
        else:
            if matched or _MIX_UNITS.search(clause) or (_TRACK.search(clause) and _LEVEL.search(clause)):
                raise EngineError('LOCAL_UNSUPPORTED_REQUEST',
                                  'Review the complete track edit in Guided edits or use the track mix controls.')
            all_clauses_known = False
            continue
        if field in required and required[field] != value:
            raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'Choose one change for each arrangement setting.')
        required[field] = value
    if required and not all_clauses_known:
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST',
                          'Only part of this request matched an arrangement control. Review the complete edit in Guided edits.')
    return required, all_clauses_known


def verify_freeform_scope(text, requirements, parent, complete):
    required, all_clauses_known = requirements
    if any(complete[field] != value for field, value in required.items()):
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'The planner could not apply every requested change.')
    arrangement_fields = ('instrument', 'bass', 'drums', 'density', 'backingVolume')
    if all_clauses_known and any(complete[key] != parent[key] for key in arrangement_fields if key not in required):
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'The planner added an arrangement change outside the request.')
    if complete['backingVolume'] != parent['backingVolume']:
        global_level = any(_GLOBAL_BACKING.search(clause) and not _PRESERVE.search(clause) and
                           (_LEVEL.search(clause) or re.search(r'\b(?:gentle|gentler|normal)\b', clause))
                           for clause in clauses(text))
        if not global_level:
            raise EngineError('LOCAL_UNSUPPORTED_REQUEST',
                              'The model can adjust the whole backing. Use track mix controls to change a single track.')
