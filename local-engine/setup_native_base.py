"""Install the fixed native accompaniment DiT alongside Turbo without duplicating verified local bytes."""

import argparse
import errno
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

NAME = "acestep-v15-base-Q4_K_M.gguf"
SIZE = 1_445_710_208
SHA256 = "942ec6a7bc11a40809af5f337063f2af2acc63e4fcd4a92b0cb42dc2bc7c41e8"
REPOSITORY = "Serveurperso/ACE-Step-1.5-GGUF"
REVISION = "666ac70204440867d8c01ba4b119cc79c95b370a"


def verify(path):
    if not path.is_file() or path.stat().st_size != SIZE:
        raise ValueError(f"The native Base checkpoint is missing or incomplete at {path}.")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
            raise ValueError(f"The native Base checkpoint at {path} failed SHA-256 verification.")


def link_verified(source, target):
    verify(source)
    try:
        os.link(source.resolve(strict=True), target)
    except FileExistsError:
        # Another concurrent setup has already published the checkpoint. Verify its own bytes.
        verify(target)
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise ValueError(
                "Use a verified checkpoint on the same volume to reuse it without copying."
            ) from error
        raise


def install(models, hf, reuse=None):
    models.mkdir(parents=True, exist_ok=True)
    target = models / NAME
    if target.exists() or target.is_symlink():
        verify(target)
    elif reuse is not None:
        link_verified(reuse, target)
    else:
        # Stage on the installation volume. A partial download never becomes a discoverable model.
        with tempfile.TemporaryDirectory(prefix=".base-setup-", dir=models) as work:
            subprocess.run(
                [hf, "download", REPOSITORY, NAME, "--revision", REVISION, "--local-dir", work],
                check=True,
                timeout=3600,
            )
            link_verified(Path(work) / NAME, target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--hf", default="hf")
    parser.add_argument("--reuse", type=Path, help="Existing matching file on this volume to hard-link")
    args = parser.parse_args()
    print("Verified native accompaniment checkpoint:", install(args.models, args.hf, args.reuse))
