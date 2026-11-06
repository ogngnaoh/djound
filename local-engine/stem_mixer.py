"""Offline channel strips for synchronized, dry, stereo instrument stems."""
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class Strip:
    gain_db: float = 0
    pan: float = 0
    mute: bool = False

    def __post_init__(self):
        for value, lower, upper in ((self.gain_db, -60, 12), (self.pan, -1, 1)):
            if type(value) not in (int, float) or not lower <= value <= upper:
                raise ValueError(f'Expected a finite number between {lower} and {upper}')
        if type(self.mute) is not bool:
            raise ValueError('Mute must be a boolean')


def process(samples, strip):
    """Interleaved float64 stereo result, using the Web Audio stereo pan law."""
    output = np.array(samples, dtype=np.float64, copy=True)
    if output.ndim != 2 or output.shape[1] != 2 or not len(output) or not np.isfinite(output).all():
        raise ValueError('Expected a nonempty, finite stereo stem')
    if strip.mute:
        output.fill(0)
        return output
    if strip.pan:
        near, far = (0, 1) if strip.pan < 0 else (1, 0)
        angle = abs(strip.pan) * math.pi / 2
        output[:, near] += output[:, far] * math.sin(angle)
        output[:, far] *= 0 if abs(strip.pan) == 1 else math.cos(angle)
    output *= 10 ** (strip.gain_db / 20)
    return output


def mix(stems, strips):
    """Render a whole snapshot from dry inputs, rounding only the final sum."""
    if not stems or strips.keys() - stems.keys():
        raise ValueError('Every strip must refer to an available stem')
    total = None
    for name, samples in stems.items():
        component = process(samples, strips.get(name, Strip()))
        if total is None:
            total = np.zeros_like(component)
        if total.shape != component.shape:
            raise ValueError('Stems must share a timeline')
        total += component
    return total.astype('<f4')
