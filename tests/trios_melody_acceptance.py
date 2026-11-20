"""Rebuild the registered TRIOS saxophone performance and score its original trace."""

import argparse
import csv
from functools import partial
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pretty_midi
from scipy.signal import correlate, find_peaks
import soundfile as sf

from layered_melody_acceptance import release_members
from melody_acceptance import evaluate, prepare_fixture


NAME = "trios-take-five"
COMMITMENT = "cb2ec6a070a704a84ae590ecd559ce2d35233cebfeccfde52752a9b0fcf4aeb3"


def source_files(dataset, hashes):
    files = release_members(dataset, "take_five", hashes)
    for name, content in files.items():
        if hashlib.sha256(content).hexdigest() != hashes[name]:
            raise ValueError(f"The TRIOS release member {name} differs from the original.")
    return files


def pitch_check(stem, rate, notes):
    checked = []
    for start, end, pitch in notes:
        center = round((start + end) / 2 * rate)
        half = min(2048, round((end - start) * rate * .39))
        segment = stem[center - half:center + half] * np.hanning(2 * half)
        ac = correlate(segment, segment, method="fft")[len(segment) - 1:]
        ac /= ac[0]
        peaks = [lag for lag in find_peaks(ac)[0] if 30 <= lag <= 270]
        strongest = max(ac[lag] for lag in peaks)
        lag = next(lag for lag in peaks if ac[lag] >= .88 * strongest)
        checked.append([pitch, round(69 + 12 * np.log2(rate / lag / 440))])
    return checked


def build(files, destination, manifest):
    first, last = manifest["clip_frames_half_open"]
    rate = manifest["sample_rate"]
    lo, hi = first / rate, last / rate
    audio, sr = sf.read(io.BytesIO(files["mix.wav"]), start=first, stop=last, dtype="int16")
    if sr != rate or audio.ndim != 1:
        raise ValueError("Expected the original mono performance.")
    sf.write(destination / "recording.wav", audio, rate, subtype="PCM_16")

    def lane(name):
        score = pretty_midi.PrettyMIDI(io.BytesIO(files[name + ".mid"]))
        return sorted((n.start, n.end, n.pitch) for part in score.instruments for n in part.notes
                      if n.end > lo and n.start < hi)

    def row(start, end, pitch):
        return [f"{max(0, start - lo):.8f}", f"{min(hi, end) - lo:.8f}", pitch]

    theme = lane("saxophone")
    reference = [row(*note) for note in theme]
    columns = ["start_s", "end_s", "midi"]
    with (destination / "reference.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(reference)
    with (destination / "parts.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(columns + ["instrument"])
        writer.writerows(r + [manifest["reference_instrument"]] for r in reference)
        writer.writerows(row(*n) + [1] for n in lane("piano"))
    stem, sr = sf.read(io.BytesIO(files["saxophone.wav"]))
    if sr != rate or stem.ndim != 1:
        raise ValueError("Expected the isolated saxophone microphone.")
    checked = pitch_check(stem, rate, theme)
    (destination / "origin" / "independent-stem-pitches.json").write_text(json.dumps(checked, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Original TRIOS Dataset.zip or its extracted folder")
    parser.add_argument("output", type=Path, help="Local directory for the reconstructed fixture")
    parser.add_argument("--trace", type=Path, help="Selected source-notes.json in the uploaded recording's time")
    args = parser.parse_args()
    folder = prepare_fixture(NAME, COMMITMENT, args.output, partial(source_files, args.dataset), build)
    return evaluate(folder, args.trace)


if __name__ == "__main__":
    raise SystemExit(main())
