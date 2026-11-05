"""Check generated audio before it can become another refinement's source."""
import json
import sys

import numpy as np
import soundfile as sf


def validate(path, expected_seconds=12):
    audio, sample_rate = sf.read(path, always_2d=True)
    if not audio.size or not np.isfinite(audio).all():
        raise ValueError("Empty or nonfinite audio")
    duration = len(audio) / sample_rate
    if abs(duration - expected_seconds) > 0.1:
        raise ValueError(f"Unexpected duration: {duration}s")
    rms = float(np.sqrt(np.mean(audio * audio)))
    if rms < 1e-5:
        raise ValueError("Silent output")
    return {
        "seconds": duration,
        "sample_rate": sample_rate,
        "channels": audio.shape[1],
        "peak": float(np.max(np.abs(audio))),
        "rms": rms,
        "clipped_fraction": float(np.mean(np.abs(audio) >= 0.999)),
    }


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
