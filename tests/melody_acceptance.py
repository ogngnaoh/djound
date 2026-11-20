"""Reconstruct a sealed fixture and run its published source-clock note evaluator."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


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


def evaluate(fixture, trace):
    if trace is None:
        print(fixture / "recording.wav")
        return 0
    result = subprocess.run([sys.executable, fixture / "origin" / "assess.py", trace],
                            text=True, stdout=subprocess.PIPE, check=True)
    sys.stdout.write(result.stdout)
    return 0 if json.loads(result.stdout)["passed_note_gate"] else 1
