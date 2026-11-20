"""Keep a melody from adopting another player's notes when its own player rests."""

from collections import deque

import numpy as np

from rendition_foreground import FPS
from rendition_player_line import _roll


def _part_masks(view, selected, frames):
    lead = []
    background = []
    for program, notes in view.items():
        if int(program) == 128:  # Drum keys label kit pieces, not their sounding pitches.
            continue
        part = selected.get(program, ())
        lead.extend(part)
        if not part:
            background.extend(notes)
        elif len(part) != len(notes):
            # A busy program may contain both a melody and an independent running part.
            retained = {tuple(note[:3]) for note in part}
            background.extend(note for note in notes if tuple(note[:3]) not in retained)
    return _roll(lead, frames).astype(bool), _roll(background, frames).astype(bool)


def _outside_register(history, start, pitch):
    cutoff = start - 1.8
    while history and history[0][1] <= cutoff:
        history.popleft()
    weighted = sorted((p, end - max(onset, cutoff)) for onset, end, p in history)
    total = sum(weight for _, weight in weighted)
    if total < .25:
        return False
    elapsed = 0.
    for center, weight in weighted:
        elapsed += weight
        if elapsed >= total / 2:
            return abs(pitch - center) >= 7
    return False


def veto_competing_attacks(notes, views, source_anchors, frames):
    """Remove whole attacks only when the other player has affirmative pitch evidence in time."""
    if len(views) != 2 or len(source_anchors) != 2 or not notes:
        return notes
    masks = [_part_masks(view, selected, frames) for view, selected in zip(views, source_anchors)]
    if any(not lead.any() for lead, _ in masks) or not any(rival.any() for _, rival in masks):
        return notes

    support = []
    agreed = 0
    played = 0
    for start, end, pitch, *_ in notes:
        lo, hi = max(0, round(start * FPS)), min(frames, round(end * FPS))
        key = int(pitch) - 21
        if hi <= lo or not 0 <= key < masks[0][0].shape[1]:
            support.append((1., 1., 0.))
            continue
        lead = [part[lo:hi, key] for part, _ in masks]
        levels = [float(part.mean()) for part in lead]
        background = [other[lo:hi, key].mean() for _, other in masks]
        rival = np.mean(background) if min(background) >= .15 else 0.
        support.append((float(np.mean(levels)), max(levels), float(rival)))
        agreed += np.count_nonzero(lead[0] & lead[1])
        played += hi - lo

    reliable_part = played > 0 and agreed >= .8 * played
    kept = []
    history = deque()
    for note, (mean_lead, max_lead, rival) in zip(notes, support):
        start, end, pitch = note[:3]
        leap = _outside_register(history, start, pitch)
        # A consonant doubling or a solo leap can both be genuine. An ambiguous
        # register change needs backing support as well as little lead evidence.
        if rival >= .5 and ((leap and mean_lead <= .25) or (reliable_part and max_lead <= .15)):
            continue
        kept.append(note)
        history.append((start, end, pitch))
    return kept
