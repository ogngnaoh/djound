"""Rebuild and score released MusicNet performance checks from their original WAV and labels."""

import argparse
import csv
from functools import partial
from pathlib import Path

import soundfile as sf

from melody_acceptance import evaluate, prepare_fixture, verify


MUSICNET = {
    "trout": ("musicnet-trout", "12ff6b3653171c65cae7f70a653cf60a0d34ff29e36d09fb0db8e1de1733ac3a", "42", 118.5, 134.5),
    "american": ("musicnet-american", "1cdcfb61f02c85c20bcd8a09b89a9bdb3c0128ae1a36249cf2290ed8a34aaa56", "43", 66., 114.),
}


def read_sources(recording, labels, hashes):
    verify(recording, hashes["performance.wav"])
    verify(labels, hashes["aligned.csv"])
    return recording, labels


def write_musicnet(sources, destination, manifest, *, instrument, first_beat, last_beat):
    recording, labels = sources
    rate = manifest["sample_rate"]
    start, end = manifest["clip_frames_half_open"]
    with sf.SoundFile(recording) as wav:
        if wav.samplerate != rate or wav.channels != 1 or len(wav) < end:
            raise ValueError("Expected the published mono performance at its original sample rate.")
        wav.seek(start)
        sf.write(destination / "recording.wav", wav.read(end - start), rate, subtype="PCM_16")
    keys = ["start_s", "end_s", "midi"]
    with (destination / "parts.csv").open("w", newline="") as parts, \
         (destination / "reference.csv").open("w", newline="") as theme, labels.open(newline="") as table:
        all_parts, lead = csv.writer(parts), csv.writer(theme)
        all_parts.writerow(keys + ["instrument", "score_beat"])
        lead.writerow(keys)
        for row in csv.DictReader(table):
            onset, release = int(row["start_time"]), int(row["end_time"])
            if release <= start or onset >= end:
                continue
            note = [f"{max(0, onset - start) / rate:.8f}", f"{(min(end, release) - start) / rate:.8f}", row["note"]]
            all_parts.writerow(note + [row["instrument"], row["start_beat"]])
            if row["instrument"] == instrument and first_beat <= float(row["start_beat"]) < last_beat:
                lead.writerow(note)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, help="Original MusicNet WAV")
    parser.add_argument("labels", type=Path, help="Original performance-aligned MusicNet CSV")
    parser.add_argument("output", type=Path, help="Local parent directory for the reconstructed fixture")
    parser.add_argument("--piece", choices=MUSICNET, default="trout")
    parser.add_argument("--trace", type=Path, help="A saved source-notes.json from this recording")
    args = parser.parse_args()
    name, commitment, instrument, first, last = MUSICNET[args.piece]
    source = partial(read_sources, args.recording, args.labels)
    build = partial(write_musicnet, instrument=instrument, first_beat=first, last_beat=last)
    return evaluate(prepare_fixture(name, commitment, args.output, source, build), args.trace)


if __name__ == "__main__":
    raise SystemExit(main())
