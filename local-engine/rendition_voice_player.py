"""Recover a played phrase that the voice separator assigned to a singer."""

from bisect import bisect_left, bisect_right
import math

import numpy as np

from rendition_competing_player import veto_competing_attacks
from rendition_foreground import FPS, KEYS, _harmonics
from rendition_player_line import _roll, _trace


def _near(roll):
    near = roll.copy()
    near[:, 1:] = np.maximum(near[:, 1:], roll[:, :-1])
    near[:, :-1] = np.maximum(near[:, :-1], roll[:, 1:])
    return near


def needs_voice_player(separated, mix, duration):
    """An independently audible mixed line should agree before running the player model."""
    if duration <= 0 or min(len(separated), len(mix)) < 3:
        return False
    frames = math.ceil(duration * FPS)
    voice = _roll(separated, frames)
    active = voice.any(axis=1)
    matched = (voice * _near(_roll(mix, frames))).any(axis=1)
    return bool(active.any() and matched.sum() >= 0.75 * active.sum())


def _dominant_part(views, guides, frames):
    heard = guides.any(axis=1).sum()
    ranked = []
    for program in set(views[0]) | set(views[1]):
        if int(program) == 128:
            continue
        matrices = [_roll(view.get(program, []), frames) for view in views]
        occupied = [matrix.sum(axis=1) for matrix in matrices]
        average = (matrices[0] + matrices[1]) / 2
        matches = (_near(average) * guides).max(axis=1)
        coverage = matches.sum() / max(1, heard)
        # YourMT3's training vocabulary reserves 100/101 for sung melody and chorus.
        if int(program) in (100, 101):
            if coverage >= 0.2:
                return None
            continue
        # A chordal backing player often doubles the melody by chance. It cannot
        # independently identify a single line.
        if average.any(axis=1).mean() < 0.2 or any(
            (count[count > 0] > 1).mean() > 0.4 for count in occupied if count.any()
        ):
            continue
        ranked.append((coverage, program, average))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 0.38 or (len(ranked) > 1 and ranked[0][0] < 1.25 * ranked[1][0]):
        return None
    return ranked[0][1:]


def _by_pitch(notes):
    """Index the performed attacks and releases of each pitch on this passage's clock."""
    rows = [[] for _ in range(KEYS)]
    for start, end, pitch, *_ in notes:
        if 21 <= pitch < 21 + KEYS:
            rows[pitch - 21].append((start, end))
    return [sorted(row) for row in rows]


def _attack(index, key, when):
    # MIDI and the mixed acoustic transcriber can disagree slightly on the start
    # of a bow, pluck or sung syllable.
    row = index[key]
    i = bisect_left(row, (when - 0.12,))
    return i < len(row) and row[i][0] <= when + 0.12


def _sustained(index, key, when):
    row = index[key]
    i = bisect_left(row, (when - 0.14,))
    return i > 0 and row[i - 1][1] > when + 0.12


def _keep_rests(scores, separated, primary, mixed, confirmations):
    silent = ~separated.any(axis=1)
    edges = np.r_[0, np.flatnonzero(np.diff(silent)) + 1, len(silent)]
    for lo, hi in zip(edges, edges[1:]):
        if not silent[lo] or hi - lo < 8:
            continue
        # A continuing accompaniment should not fill a breath. Both instrument
        # windows and an acoustic attack must support a new entrance in the gap.
        permitted = np.zeros((hi - lo, KEYS), dtype=bool)
        for a, b, pitch, *_ in primary:
            key = pitch - 21
            if (
                a >= lo / FPS - 0.1
                and a < hi / FPS
                and 0 <= key < KEYS
                and _attack(mixed, key, a)
                and _attack(confirmations, key, a)
            ):
                start, end = max(lo, round(a * FPS)), min(hi, round(b * FPS))
                if end > start:
                    permitted[start - lo : end - lo, key] = True
        scores[lo:hi][~permitted] = -5.0


def _attacks(separated, mix, players, proposals):
    sources = [*players, mix, separated]
    indexes = [_by_pitch(notes) for notes in sources]
    result = [[] for _ in range(KEYS)]
    for j, notes in enumerate(sources):
        for i, (a, b, pitch, *_) in enumerate(notes):
            key = pitch - 21
            if not 0 <= key < KEYS or not _attack(proposals, key, a):
                continue
            votes = [_attack(index, key, a) for index in indexes]
            if j >= 2:
                # A change of pitch or re-entry independently present in the two
                # contours can expose an attack missed by an instrument window.
                previous = notes[i - 1] if i else None
                arrival = previous and (previous[2] != pitch or a - previous[1] > 0.08)
                if (
                    not arrival
                    or not all(votes[2:])
                    or all(_sustained(index, key, a) for index in indexes[:2])
                ):
                    continue
            result[key].append((round(a * FPS), sum(votes)))
    for row in result:
        row.sort()
    return result


def _write_line(path, attacks, confidence, duration):
    boundaries = np.r_[0, np.flatnonzero(np.diff(path)) + 1, len(path)]
    notes = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        key = int(path[lo])
        if key == KEYS or hi - lo < 3:
            continue
        all_attacks = attacks[key]
        nearby = all_attacks[bisect_left(all_attacks, (lo + 4,)) : bisect_left(all_attacks, (hi - 3,))]
        # Collapse different estimates of the same attack; use the center of
        # the most corroborated estimates in each 100-ms neighborhood.
        cuts = [lo]
        first = 0
        while first < len(nearby):
            end = bisect_right(nearby, (nearby[first][0] + 5, math.inf), lo=first)
            group = nearby[first:end]
            best = max(count for _, count in group)
            attack = round(float(np.median([time for time, count in group if count == best])))
            if attack - cuts[-1] >= 5:
                cuts.append(attack)
            first = end
        for a, b in zip(cuts, [*cuts[1:], hi]):
            notes.append(
                (float(a / FPS), min(duration, float(b / FPS)), key + 21, float(confidence[a:b, key].mean()))
            )
    return notes


def select_voice_player(source, events, separated, mix, views, duration):
    """Use two aligned instrument views in one bounded passage. None preserves its saved vocal line."""
    if len(views) != 2 or not needs_voice_player(separated, mix, duration):
        return None
    frames = math.ceil(duration * FPS)
    voice, mixed = _roll(separated, frames), _roll(mix, frames)
    chosen = _dominant_part(views, np.maximum(voice, mixed), frames)
    if chosen is None:
        return None
    program, model = chosen
    players = [view.get(program, []) for view in views]
    acoustic = _roll(events, frames, with_confidence=True)
    energy, _ = _harmonics(source, frames)
    # Give the isolated player the deciding vote at a disputed pitch. Its
    # estimates need audible support, and the guides can fill a model dropout.
    scores = 3 * model + mixed + 0.5 * voice + 0.6 * energy + 0.45 * acoustic
    supported = ((model + mixed + voice) > 0) & ((acoustic > 0) | (energy > 0.2))
    scores[~supported] = -5.0
    proposals = _by_pitch(events)
    _keep_rests(scores, voice, players[0], proposals, _by_pitch(players[1]))
    path = _trace(np.column_stack((scores, np.full(frames, 0.6))))
    attacks = _attacks(separated, mix, players, proposals)
    notes = _write_line(path, attacks, np.maximum(acoustic, model), duration)
    return veto_competing_attacks(notes, views, [{program: part} for part in players], frames) or None
