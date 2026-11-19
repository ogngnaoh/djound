"""Publish an observed worker stage to the local studio when a job is attached."""
import os


def report_stage(stage: str) -> None:
    path = os.environ.get("DJOUND_RENDITION_PROGRESS")
    if not path or not stage.isascii() or not 0 < len(stage) <= 16:
        return
    try:
        # The server creates and removes this file. One fixed-width write replaces
        # the previous stage; a worker surviving shutdown cannot recreate it.
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb", buffering=0) as output:
            output.write(stage.encode("ascii").ljust(16))
    except OSError:
        pass
