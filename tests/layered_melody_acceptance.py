"""Prepare two TRIOS listening examples or check their melody traces. See fixtures/layered-melodies.md."""

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time
from zipfile import ZipFile

import numpy as np
import pretty_midi
import soundfile as sf

CASES = {
    "brahms": {
        "crop": (3.55, 26.55),
        "parts": ("violin", "horn", "piano"),
        "audio": ("mix",),
        "phrases": (
            ("opening-violin", "violin", 3.55, 12.75),
            ("horn-answer", "horn", 12.75, 21.95),
            ("horn-cadence", "horn", 21.95, 26.55),
        ),
        "members_sha256": "105af2462f99056034a8a34af8b101287a3d8aad8b827d25e5cac28dc1c34953",
        "pcm_sha256": "0e787e740a307199e2c18b4bde8941ddf473ca2eddeb55c61f8f6149b3f38053",
    },
    "schubert": {
        "crop": (41.96, 51.85),
        "parts": ("cello", "piano", "violin"),
        "audio": ("cello", "mix", "piano", "violin"),
        "phrases": (
            ("cello-first-arch", "cello", 41.96, 47.05),
            ("cello-second-arch", "cello", 47.05, 51.85),
        ),
        "members_sha256": "08563d4bb07835755ed1f02a938965402457d0729f94fd5c20ece88867f8f36b",
        "pcm_sha256": "2eeb5c0b2a7c1e55bc9fdcc7a99136f0403a0dee5e5ce5033e90012e89b24ac1",
    },
}
RATE = 44100


def source_files(dataset, piece, case):
    names = sorted([part + ".mid" for part in case["parts"]] + [part + ".wav" for part in case["audio"]])
    if dataset.is_file():
        with ZipFile(dataset) as archive:
            files = {name: archive.read(f"TRIOS Dataset/{piece}/{name}") for name in names}
    else:
        base = dataset / "TRIOS Dataset" if (dataset / "TRIOS Dataset").is_dir() else dataset
        files = {name: (base / piece / name).read_bytes() for name in names}
    digests = b"".join(hashlib.sha256(files[name]).digest() for name in names)
    if hashlib.sha256(digests).hexdigest() != case["members_sha256"]:
        raise ValueError(f"The published {piece} audio or MIDI files differ from the reference release.")
    return files


def prepare(dataset, piece, output):
    case = CASES[piece]
    files = source_files(dataset, piece, case)
    start, end = case["crop"]
    output.mkdir(exist_ok=True, parents=True)
    stems = {}
    for part in case["audio"]:
        samples, rate = sf.read(io.BytesIO(files[part + ".wav"]), dtype="int16",
                                start=round(start * RATE), stop=round(end * RATE))
        if rate != RATE or samples.ndim != 1:
            raise ValueError("Expected the original mono 44.1 kHz recordings.")
        stems[part] = samples
    audio = stems["mix"]
    if piece == "schubert":
        sf.write(output / "schubert-published-balance.wav", audio, RATE, subtype="PCM_16")
        audio = np.rint((3 * stems["cello"].astype(np.int32) + stems["piano"] + stems["violin"]) / 3)
        if np.max(np.abs(audio)) > 32767:
            raise ValueError("The controlled Schubert balance clips.")
        audio = audio.astype(np.int16)
    if hashlib.sha256(audio.astype("<i2", copy=False).tobytes()).hexdigest() != case["pcm_sha256"]:
        raise ValueError(f"The prepared {piece} waveform differs from the locked example.")
    sf.write(output / f"{piece}.wav", audio, RATE, subtype="PCM_16")

    notes = {}
    for part in case["parts"]:
        score = pretty_midi.PrettyMIDI(io.BytesIO(files[part + ".mid"]))
        notes[part] = sorted((n for player in score.instruments for n in player.notes), key=lambda n: n.start)
    phrases = []
    for label, part, begin, finish in case["phrases"]:
        expected = [[round(n.start - start, 4), round(n.end - start, 4), n.pitch]
                    for n in notes[part] if begin <= n.start < finish]
        phrases.append({"id": label, "part": part, "notes": expected})
    reference = {"piece": piece, "pcm_sha256": case["pcm_sha256"], "phrases": phrases,
                 "notes": [n for phrase in phrases for n in phrase["notes"]]}
    (output / f"{piece}.aligned-notes.json").write_text(json.dumps(reference, indent=2) + "\n")
    return reference


def match_by_time(expected, observed, tolerance):
    """Use chronological, separate played events. Repeated notes cannot claim one long event twice."""
    distinct, midpoint = set(), set()
    next_event = 0
    for i, (start, end, key) in enumerate(expected):
        center = (start + end) / 2
        sounding = [(j, event) for j, event in enumerate(observed) if event[0] <= center < event[1]]
        if not sounding:
            continue
        # A monophonic lead may overlap its preceding release. Its newest attack is the sounding pitch.
        j, (attack, release, pitch) = sounding[-1]
        if abs(pitch - key) <= tolerance:
            midpoint.add(i)
            if j >= next_event and min(release, end) - max(attack, start) >= (end - start) / 2:
                distinct.add(i)
                next_event = j + 1
    return distinct, midpoint


def evaluate(reference, trace):
    observed = [note[:3] for note in trace["melody"]]
    if any(len(note) != 3 or not all(map(math.isfinite, note)) or
           not (0 <= note[0] < note[1] and int(note[2]) == note[2] and 0 <= note[2] <= 127)
           for note in observed):
        raise ValueError("Invalid melody event. Expected [start seconds, end seconds, MIDI, ...].")
    if observed != sorted(observed, key=lambda note: note[0]):
        raise ValueError("Melody events must be in time order.")
    metrics = {}
    for tolerance, label in ((0, "exact"), (1, "within_one")):
        distinct, midpoint = match_by_time(reference["notes"], observed, tolerance)
        metrics["distinct_" + label] = distinct
        metrics["midpoint_" + label] = midpoint
    reports, start = [], 0
    for phrase in reference["phrases"]:
        count = len(phrase["notes"])
        indices = set(range(start, start + count))
        scores = {key: len(indices & hits) for key, hits in metrics.items()}
        passed = all(hits >= count * (0.6 if key.endswith("exact") else 0.7) for key, hits in scores.items())
        reports.append({"phrase": phrase["id"], "expected": count, **scores, "pass": passed})
        start += count
    return {"piece": reference["piece"], "phrases": reports, "pass": all(p["pass"] for p in reports)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Published TRIOS archive or extracted directory")
    parser.add_argument("piece", choices=CASES)
    parser.add_argument("output", type=Path)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--trace", type=Path, help="Existing Djound melody JSON in original upload time")
    choice.add_argument("--transcribe", action="store_true", help="Run the current production transcriber")
    args = parser.parse_args()
    ref = prepare(args.dataset, args.piece, args.output)
    trace = args.trace
    if args.transcribe:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
        from rendition_melody import transcribe_recording
        trace = args.output / f"{args.piece}.trace.json"
        if not transcribe_recording(args.output / f"{args.piece}.wav", trace, deadline=time.monotonic() + 240):
            raise SystemExit("The transcriber returned no reliable lead.")
    if trace is None:
        print(args.output / f"{args.piece}.wav")
        return 0
    result = evaluate(ref, json.loads(trace.read_text()))
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
