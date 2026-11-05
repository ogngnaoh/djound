#!/usr/bin/env python3
"""Give a running JVM immutable package bytes while development rebuilds continue."""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def snapshot(source, directory):
    expected = digest(source)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (expected + '.jar')
    if not destination.exists():
        with tempfile.NamedTemporaryFile(prefix='.launch-', dir=directory, delete=False) as temporary:
            staged = Path(temporary.name)
        try:
            shutil.copyfile(source, staged)
            if digest(staged) != expected:
                raise ValueError('The package changed during launch. Wait for packaging to finish and try again.')
            try:
                destination.hardlink_to(staged)
            except FileExistsError:
                pass  # Another launch may have prepared these same bytes.
        finally:
            staged.unlink(missing_ok=True)
    if digest(destination) != expected:
        raise ValueError('A cached launch package changed. Preserve it for diagnosis and rebuild the app.')
    return destination.resolve()


if __name__ == '__main__':
    print(snapshot(Path(sys.argv[1]), Path(sys.argv[2])))
