"""Reconstruct and score a new live violin/piano performance. See fixtures/kraisler-dichterliebe/README.md."""
import argparse
import csv
import hashlib
import io
import json
from functools import partial
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pretty_midi
from scipy.signal import correlate, find_peaks
import soundfile as sf

from melody_acceptance import evaluate, prepare_fixture


NAME = "kraisler-dichterliebe"
COMMITMENT = "5782e269dd48d2ba22abc4def4bbc7d539ecf2a9f3706337cfbdc09e1b6b3f0f"
FILES = {
    "11_mix_dry.wav": "performance_wav",
    "11_VN_dry.wav": "performance_wav",
    "11_PF.mid": "performance_midi",
    "11_VN.musicxml": "score_musicxml",
    "11_notes_VN.csv": "annotation_csv",
    "11_beats.csv": "annotation_csv",
}


def read_sources(dataset, hashes):
    if dataset.is_file():
        with ZipFile(dataset) as archive:
            files = {name: archive.read(f"KRAISLER/{folder}/{name}") for name, folder in FILES.items()}
    else:
        base = dataset / "KRAISLER" if (dataset / "KRAISLER").is_dir() else dataset
        files = {name: (base / folder / name).read_bytes() for name, folder in FILES.items()}
    for name, content in files.items():
        if hashlib.sha256(content).hexdigest() != hashes[name]:
            raise ValueError(f"Unexpected recording or annotation contents in {name}.")
    return files


def verify_stem(pcm, notes, rate):
    """Independently estimate the pitch at each violin note's center by autocorrelation."""
    checked = []
    for onset, offset, pitch in notes:
        center = int((onset + offset) / 2 * rate)
        window = pcm[center - 2205:center + 2205] * np.hanning(4410)
        ac = correlate(window, window, method="fft")[len(window) - 1:]
        ac /= ac[0]
        peaks = [lag for lag in find_peaks(ac)[0] if 28 <= lag <= 180]
        strongest = max(ac[lag] for lag in peaks)
        lag = next(lag for lag in peaks if ac[lag] >= .88 * strongest)
        checked.append([pitch, round(69 + 12 * np.log2(rate / lag / 440))])
    return checked


def write_fixture(files, destination, manifest):
    rate = manifest["sample_rate"]
    first, last = manifest["clip_frames_half_open"]
    start, stop = first / rate, last / rate
    with sf.SoundFile(io.BytesIO(files["11_mix_dry.wav"])) as audio:
        if (audio.samplerate, audio.channels, audio.subtype) != (rate, 1, "PCM_16"):
            raise ValueError("Expected the original mono PCM16 mixture.")
        audio.seek(first)
        sf.write(destination / "recording.wav", audio.read(last - first, dtype="int16"), rate, subtype="PCM_16")

    labels = csv.DictReader(io.StringIO(files["11_notes_VN.csv"].decode()))
    notes = [(float(row["onset"]), float(row["offset"]), int(row["midi"])) for row in labels
             if start <= float(row["onset"]) < stop]

    def clip_note(onset, offset, pitch):
        return [f"{max(0, onset - start):.8f}", f"{min(stop, offset) - start:.8f}", pitch]

    theme = [clip_note(*note) for note in notes]
    columns = ["start_s", "end_s", "midi"]
    with (destination / "reference.csv").open("w", newline="") as table:
        writer = csv.writer(table)
        writer.writerow(columns)
        writer.writerows(theme)
    piano = pretty_midi.PrettyMIDI(io.BytesIO(files["11_PF.mid"])).instruments[0].notes
    with (destination / "parts.csv").open("w", newline="") as table:
        writer = csv.writer(table)
        writer.writerow(columns + ["instrument"])
        writer.writerows(row + ["41"] for row in theme)
        writer.writerows(clip_note(n.start, n.end, n.pitch) + ["1"] for n in piano if n.end > start and n.start < stop)
    violin, stem_rate = sf.read(io.BytesIO(files["11_VN_dry.wav"]))
    if stem_rate != rate or violin.ndim != 1:
        raise ValueError("Expected the matching isolated violin microphone.")
    result = verify_stem(violin, notes, rate)
    (destination / "origin" / "independent-stem-pitches.json").write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Published KRAISLER.zip or its extracted directory")
    parser.add_argument("output", type=Path, help="Local parent directory for the reconstructed fixture")
    parser.add_argument("--trace", type=Path, help="An actual saved worker melody trace in the source clock")
    args = parser.parse_args()
    source = partial(read_sources, args.dataset)
    return evaluate(prepare_fixture(NAME, COMMITMENT, args.output, source, write_fixture), args.trace)


if __name__ == "__main__":
    raise SystemExit(main())
