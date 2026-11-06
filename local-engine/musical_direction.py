"""Plan a bounded swing accompaniment and relative drum balance for human review."""
import math
import re

from common import UNITY_STRIP, notes as confirmed_notes
from edit_commands import Assignment, reviewed_plan, unsupported


TARGET = r'(?:this|it|(?:(?:this|the|my|that) )?(?:soundtrack|recording|track|song|tune|melody|clip|arrangement|music|version))'
JAZZ = r'(?:(?:more |a little more )?(?:jazzy|jazz[ -]like)|jazzier|jazz)'
JAZZ_REQUESTS = (
    rf'make {TARGET} (?:sound |feel )?{JAZZ}',
    rf'make {TARGET} (?:sound |feel )?(?:more )?like jazz',
    rf'(?:give {TARGET}|add) (?:a |some )?(?:jazzy|jazz|swing) (?:feel|backing|accompaniment)',
    rf'(?:turn {TARGET} into|arrange {TARGET} (?:as|for)) (?:a )?(?:jazz|swing) (?:combo|arrangement|trio)',
    rf'(?:add|use) (?:a )?(?:jazz|swing) (?:combo|backing|accompaniment)(?: (?:for|behind) {TARGET})?',
    r'(?:jazz[ -]like|jazzy|swing combo) (?:arrangement|backing|accompaniment)',
)
DRUMS = r'(?:drums|percussion)'
QUIETER = rf'(?:quieter {DRUMS}|(?:make )?(?:the )?{DRUMS} (?:a little |a bit )?quieter|turn (?:the )?{DRUMS} down(?: a little| a bit)?)'
# Genre transformations with extra requirements must never reach the six-field model.
STYLE_LANGUAGE = re.compile(r'\b(?:jazz\w*|swing|genre|styles?|bebop|blues|reggae|metal|rock|techno|'
                            r'classical|orchestral|bossa nova|hip[ -]hop|lo[ -]?fi|funk|country|disco|salsa|edm)\b'
                            rf'|\b(?:turn {TARGET} into|make {TARGET} (?:sound |feel )?(?:more )?like)\b')


def direction(text):
    """Return the whole bounded request or None. An unconsumed style qualifier is an error."""
    normalized = ' '.join(text.lower().split()).removeprefix('can you ').removeprefix('please ').rstrip('.!?')
    parts = re.split(r'\s*;\s*|,? (?:and then|and|then|with) ', normalized)
    kinds = []
    for part in parts:
        if any(re.fullmatch(pattern, part) for pattern in JAZZ_REQUESTS):
            kinds.append('swing_combo')
        elif re.fullmatch(QUIETER, part):
            kinds.append('quieter_drums')
        else:
            break
    else:
        if len(set(kinds)) != len(kinds):
            raise unsupported('Give each musical change once.')
        return kinds
    if STYLE_LANGUAGE.search(normalized):
        raise unsupported('The available style is swing combo backing. Review that arrangement on its own, or ask for it with quieter drums.')
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


def musical_plan(kinds, parent, notes, duration, basis_kind):
    actions = []
    evidence = {}
    if 'swing_combo' in kinds:
        if basis_kind not in ('original', 'rendition'):
            raise unsupported('Select the uploaded recording or a local version before reviewing swing backing.')
        if not notes or duration is None:
            raise unsupported('Confirm the melody before asking for swing backing.')
        notes = confirmed_notes(notes, duration)
        if all(parent.get('mix', {}).get(stem, UNITY_STRIP)['muted'] for stem in ('bass', 'drums', 'chords')):
            raise unsupported('The backing tracks are muted. Unmute a part before asking for swing backing.')
        bpm, offset, source = pulse(parent, notes, duration)
        evidence.update(style='swing_combo', pulseSource=source, basisKind=basis_kind)
        if basis_kind == 'original':
            actions.append(Assignment('instrument', 'recorded'))
        actions.extend(['add bass', f'use swing ride at {bpm} bpm from {offset} ms'])
        if 'harmony' not in parent:
            actions.append('suggest chords')
        elif not any(parent['harmony']['chords']):
            raise unsupported('The existing chord chart is all rests. Choose a chord or request a new chart before adding swing backing.')
        actions.append(Assignment('accompanimentStyle', 'swing_combo'))
    if 'quieter_drums' in kinds:
        adds_drums_first = 'swing_combo' in kinds[:kinds.index('quieter_drums')]
        if not (parent['drums'] or adds_drums_first) or parent.get('mix', {}).get('drums', UNITY_STRIP)['muted']:
            raise unsupported('The drums must be active and unmuted to make them quieter.')
        actions.append('lower drums by 3 db')
        evidence['drumGainDeltaDb'] = -3
    result = reviewed_plan(actions, parent, notes, duration, 'musical-direction-v1')
    if 'swing_combo' in kinds and not any(result['recipe']['harmony']['chords']):
        raise unsupported('No confirmed notes sound on the selected grid. Review its first beat or select chords directly.')
    if 'quieter_drums' in kinds:
        result['explanation'] += ' Drums lowered by 3 dB from the selected version.'
    if 'pulseSource' in evidence:
        result['explanation'] += f' Pulse source: {evidence["pulseSource"]}.'
    result['provenance'].update(evidence)
    return result
