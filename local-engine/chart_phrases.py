"""Recognize complete requests to review or edit a local chord chart."""
import json
import re

from common import ROOT


SPEC = json.loads((ROOT / 'contracts/chart-phrases.json').read_text())
BAR = re.compile(SPEC['barPattern'])
GRID = re.compile(SPEC['gridPattern'])
ROOTS = {'c': 0, 'd': 2, 'e': 4, 'f': 5, 'g': 7, 'a': 9, 'b': 11}
NAMES = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B')


def chart_action(text):
    clause = ' '.join(text.lower().replace('♯', '#').replace('♭', 'b').split())
    clause = clause.removeprefix('please ').removesuffix('.')
    if clause in SPEC['suggest']:
        return 'suggest', None, None
    if clause in SPEC['remove']:
        return 'remove', None, None
    if match := GRID.fullmatch(clause):
        return 'grid', int(match[1]), int(match[2])
    match = BAR.fullmatch(clause)
    if not match:
        return None
    number, rest, root, accidental, quality = match.groups()
    label = None
    if not rest:
        shift = {None: 0, '#': 1, ' sharp': 1, 'b': -1, ' flat': -1}[accidental]
        label = NAMES[(ROOTS[root] + shift) % 12] + ('m' if quality in ('m', 'minor') else '')
    return 'bar', int(number) - 1, label
