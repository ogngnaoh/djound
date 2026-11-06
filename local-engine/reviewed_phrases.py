"""Compile complete instructions whose tone, chart and mix operations are known."""
import json
import re

from chart_phrases import chart_action
from common import ROOT


MIX = [(re.compile(row['pattern']), row['command'])
       for row in json.loads((ROOT / 'contracts/mix-phrases.json').read_text())]
TONES = json.loads((ROOT / 'contracts/tone-phrases.json').read_text())
PHRASES = {
    pattern.format(track=article + name): (stem, tone)
    for name, stem in TONES['tracks'].items()
    for tone, patterns in TONES['tones'].items()
    for pattern in patterns
    for article in ('', 'the ')
}


def reviewed_phrases(text):
    normalized = ' '.join(text.lower().split()).removeprefix('please ').removesuffix('.')
    commands = []
    kinds = set()
    for clause in re.split(r'\s*;\s*|,? (?:and then|and|then) ', normalized):
        if clause in PHRASES:
            stem, tone = PHRASES[clause]
            commands.append(f'set {stem} tone to {tone}')
            kinds.add('tone')
        elif chart_action(clause):
            commands.append(clause)
            kinds.add('chart')
        else:
            for pattern, template in MIX:
                if match := pattern.fullmatch(clause):
                    commands.append(re.sub(r'\$(\d)', lambda group: match[int(group[1])], template))
                    kinds.add('mix')
                    break
            else:
                return None
    if kinds == {'tone'}:
        syntax = 'tone-phrases-v1'
    elif kinds == {'chart'}:
        syntax = 'chart-phrases-v1'
    else:
        syntax = 'reviewed-phrases-v1'
    return '; '.join(commands), syntax
