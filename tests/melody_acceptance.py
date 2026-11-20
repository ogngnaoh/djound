"""Reconstruct a sealed fixture and run its published source-clock note evaluator."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE_EVALUATOR = FIXTURES / "musicnet-trout" / "frozen_evaluator.py"
BASE_SHA = "abf555185accdfb54c2092c848efc680fec723cd10d74b86bb3ff18944654da7"


def verify(path, expected):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"Unexpected SHA-256 for {path}.")


def prepare_fixture(name, commitment, output, read_sources, build):
    """Stage audio, external references and scorer, then verify the sealed contents on every use."""
    case = FIXTURES / name
    verify(case / "SHA256SUMS", commitment)
    verify(BASE_EVALUATOR, BASE_SHA)
    expected = {}
    for line in (case / "SHA256SUMS").read_text().splitlines():
        sha, filename = line.split("  ", 1)
        expected[filename] = sha
    expected.setdefault("origin/assess.py", BASE_SHA)

    verify(case / "manifest.json", expected["manifest.json"])
    manifest = json.loads((case / "manifest.json").read_text())
    sources = read_sources(manifest["source_hashes"])

    destination = output / name
    output.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with tempfile.TemporaryDirectory(dir=output) as temp:
            staged = Path(temp) / name
            (staged / "origin").mkdir(parents=True)
            build(sources, staged, manifest)
            shutil.copyfile(case / "manifest.json", staged / "manifest.json")
            scorer = staged / "origin" / "assess.py"
            shutil.copyfile(BASE_EVALUATOR, scorer)
            patch = case / "evaluator.patch"
            if patch.is_file():
                subprocess.run(["patch", "-s", str(scorer.resolve())], input=patch.read_bytes(), check=True)
            for filename, sha in expected.items():
                verify(staged / filename, sha)
            try:
                staged.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
    for filename, sha in expected.items():
        verify(destination / filename, sha)
    return destination


def trace_options(parser):
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--trace", type=Path, help="Score an existing worker trace on the upload's clock")
    options.add_argument("--transcribe", action="store_true", help="Run the installed local transcriber and score its output")


def score_trace(fixture, trace):
    result = subprocess.run([sys.executable, fixture / "origin" / "assess.py", trace],
                            text=True, stdout=subprocess.PIPE, check=True)
    sys.stdout.write(result.stdout)
    return 0 if json.loads(result.stdout)["passed_note_gate"] else 1


def evaluate(fixture, trace=None, *, transcribe=False):
    if transcribe:
        sys.path.insert(0, str(FIXTURES.parents[1] / "local-engine"))
        from rendition_melody import transcribe_recording

        with tempfile.TemporaryDirectory() as scratch:
            audio, result = Path(scratch) / "upload.wav", Path(scratch) / "notes.json"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
                            "-i", fixture / "recording.wav", "-map", "0:a:0", "-vn", "-ar", "44100", "-ac", "2",
                            "-c:a", "pcm_s16le", audio], check=True)
            if not transcribe_recording(audio, result, deadline=time.monotonic() + 240):
                raise SystemExit("The transcriber could not recover a reliable tune.")
            return score_trace(fixture, result)
    if trace is not None:
        return score_trace(fixture, trace)
    print(fixture / "recording.wav")
    return 0
