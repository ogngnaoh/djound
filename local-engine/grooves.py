"""Fixed 4/4 percussion on a listener-confirmed beat grid."""
import math


def drum_hits(pattern, beat):
    """Offset and gate in quarters, GM percussion pitch, and velocity."""
    if pattern == 'swing_ride':
        hits = [(0, .13, 51, (47, 58, 48, 58)[beat])]
        if beat in (1, 3):
            hits.extend([(0, .09, 44, 46), (2 / 3, .10, 51, 38)])
        elif beat == 0:
            hits.append((0, .10, 36, 33))
        return hits
    if pattern == 'four_on_the_floor':
        hits = [(0, .16, 36, (73, 66, 70, 66)[beat])]
        if beat in (1, 3):
            hits.append((0, .10, 39, 57))
        hits.append((.5, .18, 46, (49, 41, 48, 43)[beat]))
        return hits
    raise ValueError('Unknown drum pattern')


def groove_notes(pattern, bpm, origin, duration, sparse=False, soft=False):
    quarter = 60 / bpm
    events = []
    for index in range(math.ceil((duration - origin) / quarter)):
        for offset, gate, pitch, velocity in drum_hits(pattern, index % 4):
            # Retain triplet rides and quarter-note kicks at both densities.
            if sparse and (pitch == 44 or (pitch == 46 and index % 2 == 0)):
                continue
            start = origin + (index + offset) * quarter
            end = min(duration, start + gate * quarter)
            if round((end - start) * 44100) >= 441:
                events.append({'start': start, 'end': end, 'pitch': pitch,
                               'velocity': round(velocity * .65) if soft else velocity})
    return events
