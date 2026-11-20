"""Rebuild and score a released MusicNet melody check. See fixtures/musicnet-trout/README.md."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import soundfile as sf


CASE = Path(__file__).parent / "fixtures" / "musicnet-trout"
COMMITMENT = "12ff6b3653171c65cae7f70a653cf60a0d34ff29e36d09fb0db8e1de1733ac3a"
EVALUATOR = "abf555185accdfb54c2092c848efc680fec723cd10d74b86bb3ff18944654da7"


def verify(path, expected):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"Unexpected SHA-256 for {path}.")


def write_labels(source, destination, frames, rate):
    start, end = frames
    keys = ["start_s", "end_s", "midi"]
    with (destination / "parts.csv").open("w", newline="") as parts, \
         (destination / "reference.csv").open("w", newline="") as theme, source.open(newline="") as labels:
        all_parts, lead = csv.writer(parts), csv.writer(theme)
        all_parts.writerow(keys + ["instrument", "score_beat"])
        lead.writerow(keys)
        for row in csv.DictReader(labels):
            onset, release = int(row["start_time"]), int(row["end_time"])
            if release <= start or onset >= end:
                continue
            note = [f"{max(0, onset - start) / rate:.8f}", f"{(min(end, release) - start) / rate:.8f}", row["note"]]
            all_parts.writerow(note + [row["instrument"], row["start_beat"]])
            if row["instrument"] == "42" and 118.5 <= float(row["start_beat"]) < 134.5:
                lead.writerow(note)


def prepare(recording, labels, output):
    verify(CASE / "SHA256SUMS", COMMITMENT)
    verify(CASE / "frozen_evaluator.py", EVALUATOR)
    manifest = json.loads((CASE / "manifest.json").read_text())
    verify(recording, manifest["source_hashes"]["performance.wav"])
    verify(labels, manifest["source_hashes"]["aligned.csv"])
    expected = {filename: sha for sha, filename in (line.split("  ", 1)
                for line in (CASE / "SHA256SUMS").read_text().splitlines())}

    # An interrupted preparation or a different recording cannot silently replace
    # an existing reference. A verified directory can score multiple saved takes.
    destination = output / "musicnet-trout"
    output.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with tempfile.TemporaryDirectory(dir=output) as temp:
            staged = Path(temp) / destination.name
            (staged / "origin").mkdir(parents=True)
            rate = manifest["sample_rate"]
            start, end = manifest["clip_frames_half_open"]
            with sf.SoundFile(recording) as wav:
                if wav.samplerate != rate or wav.channels != 1 or len(wav) < end:
                    raise ValueError("Expected the published mono performance at its original sample rate.")
                wav.seek(start)
                sf.write(staged / "recording.wav", wav.read(end - start), rate, subtype="PCM_16")
            write_labels(labels, staged, (start, end), rate)
            shutil.copyfile(CASE / "manifest.json", staged / "manifest.json")
            shutil.copyfile(CASE / "frozen_evaluator.py", staged / "origin" / "assess.py")
            for name, sha in expected.items():
                verify(staged / name, sha)
            try:
                staged.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
    for name, sha in expected.items():
        verify(destination / name, sha)
    verify(destination / "origin" / "assess.py", EVALUATOR)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, help="Original MusicNet train_data/1729.wav")
    parser.add_argument("labels", type=Path, help="Original MusicNet train_labels/1729.csv")
    parser.add_argument("output", type=Path, help="Local parent directory for the reconstructed fixture")
    parser.add_argument("--trace", type=Path, help="A saved source-notes.json from this recording")
    args = parser.parse_args()
    fixture = prepare(args.recording, args.labels, args.output)
    if args.trace is None:
        print(fixture / "recording.wav")
        return 0
    result = subprocess.run([sys.executable, fixture / "origin" / "assess.py", args.trace],
                            text=True, stdout=subprocess.PIPE, check=True)
    sys.stdout.write(result.stdout)
    return 0 if json.loads(result.stdout)["passed_note_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
