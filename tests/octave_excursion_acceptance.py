"""Check a controlled Schubert octave excursion and two different endings with real TRIOS stems."""
import argparse
from fractions import Fraction
import io
from pathlib import Path
import sys
import tempfile

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

from layered_melody_acceptance import CASES, RATE, source_files

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
from rendition_octaves import recover_octave_excursions


def gesture(track, start, end):
    samples, _ = sf.read(io.BytesIO(track), start=round(start * RATE), stop=round(end * RATE))
    samples -= samples.mean()
    distance = np.minimum(np.arange(len(samples)), np.arange(len(samples))[::-1])
    fade = np.clip(distance / (.012 * RATE), 0, 1)
    return .07 * fade * samples / np.sqrt(np.mean(samples ** 2))


def check(dataset):
    files = source_files(dataset, "schubert", CASES["schubert"])
    cello, violin = files["cello.wav"], files["violin.wav"]
    opening = [gesture(cello, 33.30, 33.70), gesture(cello, 36.24, 36.82)]
    closing = [gesture(cello, 38.70, 39.27), gesture(cello, 39.37, 39.90)]
    low_c = gesture(cello, 4.37, 4.81)
    high_c = gesture(cello, 13.20, 13.64)
    upper = gesture(violin, 46.98, 47.42) * 10 ** (3 / 20)
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "performance.wav"
        for ending, voice, initial, wanted in (
            ("octave", low_c, 75, 48), ("rest", np.zeros_like(low_c), 75, 75), ("held", high_c, 60, 60),
        ):
            recording = np.concatenate([*opening, voice + upper, *closing])
            for step in (-2, 0, 3):
                factor = Fraction(2 ** (-step / 12)).limit_denominator(512)
                shifted = resample_poly(recording, factor.numerator, factor.denominator)
                sf.write(source, shifted, RATE, subtype="PCM_16")
                scale = float(factor)
                def event(a, b, k):
                    return a * scale, b * scale, k + step, .65

                # Supply both disputed candidates, even during the rest. This isolates the
                # waveform decision from the note detector and deliberately admits a false proposal.
                line = [event(0., .4, 59), event(.4, .98, 60), event(.98, 1.42, initial),
                        event(1.42, 1.99, 60), event(1.99, 2.52, 59)]
                proposals = line[:2] + [event(.98, 1.42, k) for k in (48, 60, 75)] + line[3:]
                result = recover_octave_excursions(source, proposals, line, len(shifted) / RATE)
                actual = [k for a, b, k, _ in result if a <= 1.20 * scale < b]
                if actual != [wanted + step]:
                    raise AssertionError(f"{ending} at {step:+} semitones: expected {wanted + step}, found {actual}")
                print(f"{ending}, {step:+} semitones: MIDI {actual[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Published TRIOS archive or extracted directory")
    check(parser.parse_args().dataset)
