"""Compose swing backing or drum balance with reviewed controls on the selected basis.

Requests use an affirmative action, a known musical target/style, optional preservation
clauses, and explicit mix/arrangement edits. Every clause must compile. This grammar
does not interpret open-ended genres, references, comparisons, or creative adjectives.
"""
import math
import re
from dataclasses import dataclass

from common import UNITY_STRIP, notes as confirmed_notes
from edit_commands import Assignment, reviewed_plan, unsupported


# A clause must consume a complete musical role, its target, and any relation.
# The grammar has one style destination. Target-only fragments and unknown
# adjectives, artists, instruments, meters, and other qualifiers cannot compile.
DETERMINER = r'(?:(?:a|an|some|the|this|that|my|our|your) )?'
TARGET_NOUN = r'(?:soundtrack|recording|track|song|tune|melody|clip|piece|take|loop|performance|arrangement|music|version|backing|accompaniment)'
TARGET = rf'(?:this|that|it|{DETERMINER}(?:(?:uploaded|recorded|original|current|existing|selected) )?(?:(?:piano|guitar) )?{TARGET_NOUN})'
STYLE = r'(?:jazz(?:[ -](?:like|inspired|style))?|jazzy|jazzier|swing(?:ing)?)'
ENSEMBLE = r'(?:combo|trio|ensemble)'
ROLE = r'(?:backing|accompaniment|arrangement|version|feel|groove|style|sound|treatment)'
DEGREE = r'(?:(?:a (?:little|bit) |slightly )?(?:more (?:of )?)?)'
SOUND = rf'{DEGREE}{DETERMINER}(?:(?:small )?(?:{STYLE} (?:{ENSEMBLE} )?{ROLE}|{STYLE} {ENSEMBLE})|{STYLE})'
LINK = r'(?:(?:to sound|to feel|sound|feel) )?(?:(?:more )?like |in(?:to)? |as |for |to |with |behind )?'
SOURCE_LINK = r'(?:for|under|behind|around|over|to|on)'
STYLE_FRAME = re.compile(
    rf'(?:(?:make|give|turn|arrange|rearrange|back|set|play|try|hear) {TARGET} {LINK}{SOUND}'
    rf'|(?:(?:add|give|use|try|build|create|put|play|layer|apply|get|hear) )?{SOUND}(?: {SOURCE_LINK} {TARGET})?)'
)
REQUEST_FRAME = re.compile(r"^(?:(?:can|could|would|will) (?:you|we) |(?:i(?: would|'d) like|i want) (?:you to )?|let's )?(?:please )?")
DRUMS = r'(?:the |my |that )?(?:drums|drum (?:part|track|level|volume)|percussion(?: (?:part|track|level|volume))?)'
SMALL = r'(?:a (?:little|bit|touch|notch)|slightly)'
QUIETER = re.compile(r'(?:' +
    rf'(?:quieter {DRUMS}|(?:make )?{DRUMS} (?:{SMALL} )?(?:quieter|softer)'
    rf'|(?:turn|bring|pull|dial|take) (?:{DRUMS} (?:down|back)|(?:down|back)(?: on)? {DRUMS})(?: {SMALL})?'
    rf'|(?:lower|reduce) {DRUMS}(?: {SMALL})?)'
    + r')(?: in the mix)?'
)
DB_AMOUNT = re.compile(r'(?<!\S)(?:by )?([0-9]+(?:\.[0-9]{1,3})?) (?:db|decibels?)(?!\S)')
KEEP = re.compile(
    rf'(?:keep(?:ing)?|preserv(?:e|ing)|retain(?:ing)?|leav(?:e|ing)) {DETERMINER}'
    r'(?:(?:original|recorded|existing|current|same|main) )?'
    r'(melody|tune|notes|lead(?: (?:sound|instrument|part))?|performance|recording)'
    r'(?: (?:as[ -]is|intact|unchanged|untouched|alone|the same))?'
)
SEPARATORS = re.compile(r'\s*(?:[;,]\s*(?:(?:and then|and|then|but|while)\s+)?|\s+(?:and then|and|then|but|while)\s+)')
STYLE_LANGUAGE = re.compile(r'\b(?:jazz\w*|swing\w*|genre|styles?|bebop|blues|reggae|metal|rock|techno|'
                            r'classical|orchestral|bossa nova|hip[ -]hop|lo[ -]?fi|funk|country|disco|salsa|edm)\b'
                            rf'|\b(?:turn {TARGET} into|make {TARGET} (?:sound |feel )?(?:more )?like)\b')


@dataclass(frozen=True)
class DrumReduction:
    amount: str
    explicit: bool


def musical_clause(clause, requested=False):
    if (STYLE_FRAME.fullmatch(clause) and not re.fullmatch(STYLE, clause)) or (
            requested and re.fullmatch(rf'{TARGET} {LINK}{SOUND}', clause)):
        return 'swing_combo'
    if measured := DB_AMOUNT.search(clause):
        simple = ' '.join((clause[:measured.start()] + clause[measured.end():]).split())
        if QUIETER.fullmatch(simple):
            return DrumReduction(measured[1], explicit=True)
    if QUIETER.fullmatch(clause):
        return DrumReduction('3', explicit=False)
    if guard := KEEP.fullmatch(clause):
        target = guard[1]
        if target in ('melody', 'tune', 'notes'):
            return 'keep_notes'
        return 'keep_performance' if target in ('recording', 'performance') else 'keep_lead'
    if re.fullmatch(r'(?:keep|leave|keeping|leaving) (?:the rest|everything else) (?:as[ -]is|alone|unchanged|the same)', clause):
        return 'keep everything else unchanged'
    if clause.startswith('only '):
        return None  # Its exclusive scope cannot be combined with another musical edit.
    from reviewed_phrases import compile_clause
    known = compile_clause(clause)
    return known[0] if known else None


def direction(text):
    """Only a complete affirmative style/balance request can use the musical planner."""
    normalized = ' '.join(text.lower().replace('’', "'").split()).rstrip('.!?')
    frame = REQUEST_FRAME.match(normalized)
    normalized = normalized[frame.end():].removesuffix(', please')
    parts = SEPARATORS.split(normalized)
    parsed = []
    for part in parts:
        action = musical_clause(part, requested=bool(frame.end()))
        if action is None and ' with ' in part:
            # In “back this with jazz”, with belongs to the musical frame. Only
            # split it when the whole frame failed and both halves are instructions.
            left, _, right = part.rpartition(' with ')
            pair = [musical_clause(left, requested=bool(frame.end())), musical_clause(right)]
            if all(pair):
                parsed.extend(pair)
                continue
        parsed.append(action)
    reductions = sum(isinstance(kind, DrumReduction) for kind in parsed)
    guarded = any(kind in ('keep_notes', 'keep_lead', 'keep_performance') for kind in parsed)
    has_direction = 'swing_combo' in parsed or reductions or (guarded and len(parsed) > 1)
    if has_direction and all(parsed):
        if parsed.count('swing_combo') > 1 or reductions > 1:
            raise unsupported('Give each musical change once.')
        return parsed
    if has_direction or STYLE_LANGUAGE.search(normalized):
        raise unsupported('Review a swing combo backing or a drum level change. Each additional instruction must describe a supported edit.')
    return None


def onset_tempo(onsets):
    """Estimate a reviewable quarter from recurring eighths or quarters and longer gaps."""
    gaps = [b - a for a, b in zip(onsets, onsets[1:])]
    if len(gaps) < 3:
        return None
    # Integer candidates keep work bounded even for a dense polyphonic guide.
    # A few longer gaps may represent rests or sustained notes. Do not infer a
    # meter or downbeat from them. The listener can review the proposed grid.
    subdivisions = (.5, 1, 1.5, 2, 3, 4)
    candidates = []
    for bpm in range(60, 161):
        beat = 60 / bpm
        matches = []
        error_sum = 0
        for gap in gaps:
            units = gap / beat
            nearest = min(subdivisions, key=lambda size: abs(size - units))
            error = abs(units - nearest) / min(nearest, 1)
            error_sum += min(error, .16)
            if error <= .14:
                matches.append(nearest)
        recurring = max(matches.count(.5), matches.count(1))
        if len(matches) >= math.ceil(.75 * len(gaps)) and recurring >= max(3, math.ceil(.35 * len(gaps))):
            # Exact half/double tempo is ambiguous. Favor a playable swing pulse
            # in the 70–130 BPM range, then the tightest alignment to the attacks.
            candidates.append((-len(matches), not 70 <= bpm <= 130, error_sum, -matches.count(1), bpm))
    return min(candidates)[-1] if candidates else None


def pulse(parent, notes, duration):
    if groove := parent.get('groove'):
        return parent['bpm'], groove['downbeatMs'], 'existing drum grid'
    if chart := parent.get('harmony'):
        return chart['bpm'], chart['downbeatMs'], 'existing chord grid'
    # Simultaneous notes and a slight spread in a played chord count as one attack.
    onsets = []
    for start in sorted(note['start'] for note in notes):
        if not onsets or start - onsets[-1] >= .05:
            onsets.append(start)
    downbeat = max(0, min(round(1000 * onsets[0]), math.floor(1000 * duration) - 10))
    bpm = onset_tempo(onsets)
    if bpm is not None:
        return bpm, downbeat, 'regular confirmed note onsets'
    return 100, downbeat, '100 BPM review default from first confirmed onset'


def swing_actions(parent, notes, duration, basis_kind, evidence):
    if basis_kind not in ('original', 'rendition'):
        raise unsupported('Select the uploaded recording or a local version before reviewing swing backing.')
    if not notes or duration is None:
        raise unsupported('Confirm the melody before asking for swing backing.')
    notes = confirmed_notes(notes, duration)
    if all(parent.get('mix', {}).get(stem, UNITY_STRIP)['muted'] for stem in ('bass', 'drums', 'chords')):
        raise unsupported('The backing tracks are muted. Unmute a part before asking for swing backing.')
    bpm, offset, source = pulse(parent, notes, duration)
    evidence.update(style='swing_combo', pulseSource=source, basisKind=basis_kind)
    actions = [Assignment('instrument', 'recorded')] if basis_kind == 'original' else []
    actions.extend(['add bass', f'use swing ride at {bpm} bpm from {offset} ms'])
    if 'harmony' not in parent:
        actions.append('suggest chords')
    elif not any(parent['harmony']['chords']):
        raise unsupported('The existing chord chart is all rests. Choose a chord or request a new chart before adding swing backing.')
    actions.append(Assignment('accompanimentStyle', 'swing_combo'))
    return actions


def musical_plan(kinds, parent, notes, duration, basis_kind):
    actions = []
    evidence = {}
    guards = set()
    for kind in kinds:
        if kind == 'swing_combo':
            actions.extend(swing_actions(parent, notes, duration, basis_kind, evidence))
        elif isinstance(kind, DrumReduction):
            actions.append(f'lower drums by {kind.amount} db')
            evidence['drumGainDeltaDb'] = -float(kind.amount)
            evidence['drumGainSource'] = 'requested amount' if kind.explicit else 'relative review default'
        elif kind in ('keep_lead', 'keep_notes', 'keep_performance'):
            guards.add(kind)
        else:
            actions.append(kind)
    result = reviewed_plan(actions, parent, notes, duration, 'musical-direction-v1')
    child = result['recipe']
    if 'swing_combo' in kinds:
        if (child.get('accompanimentStyle') != 'swing_combo' or not child['bass'] or not child['drums']
                or child.get('groove', {}).get('pattern') != 'swing_ride'):
            raise unsupported('The additional request conflicts with the swing combo backing.')
        if not any(child['harmony']['chords']):
            raise unsupported('No chord sounds on the selected grid. Review its first beat or select chords directly.')
    if 'drumGainDeltaDb' in evidence:
        if not child['drums'] or child.get('mix', {}).get('drums', UNITY_STRIP)['muted']:
            raise unsupported('The drums must be active and unmuted to make them quieter.')
        result['explanation'] += f' Drums lowered by {-evidence["drumGainDeltaDb"]:g} dB from the selected version.'
    if guards:
        expected = 'recorded' if basis_kind == 'original' else parent['instrument']
        if guards & {'keep_lead', 'keep_performance'} and (
                child['instrument'] != expected or child.get('mix', {}).get('lead') != parent.get('mix', {}).get('lead')):
            raise unsupported('The proposed edit would also change the lead you asked to keep.')
        if 'keep_performance' in guards and expected != 'recorded':
            raise unsupported('Select the uploaded recording or a recorded-lead version to keep its original performance.')
        evidence['preserved'] = sorted(guards)
    if 'pulseSource' in evidence:
        result['explanation'] += f' Pulse source: {evidence["pulseSource"]}.'
    result['provenance'].update(evidence)
    return result
