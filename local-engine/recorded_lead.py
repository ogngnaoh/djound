"""Decode an immutable uploaded performance on the arrangement's stereo timeline."""
import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf

from common import EngineError


def verified_copy(source, expected_hash, copy):
    if source is None or not expected_hash:
        raise EngineError('LOCAL_SOURCE_CHANGED', 'The original recording is missing.')
    checksum = hashlib.sha256()
    size = 0
    try:
        with source.open('rb') as uploaded, copy.open('xb') as saved:
            for block in iter(lambda: uploaded.read(1024 * 1024), b''):
                size += len(block)
                if size > 64 * 1024 ** 2:
                    raise EngineError('LOCAL_AUDIO_LIMIT', 'The uploaded audio exceeds the local size limit.')
                checksum.update(block)
                saved.write(block)
    except OSError as error:
        raise EngineError('LOCAL_SOURCE_CHANGED', 'Could not read the original recording.') from error
    if checksum.hexdigest() != expected_hash:
        raise EngineError('LOCAL_SOURCE_CHANGED', 'The original recording changed. Check the source before creating a version.')


def decode_recording(source, expected_hash, frames, output):
    # Verify the bytes FFmpeg will open, including if the original path is replaced.
    with TemporaryDirectory(dir=output) as scratch:
        snapshot = Path(scratch) / 'performance'
        decoded = Path(scratch) / 'decoded.wav'
        verified_copy(source, expected_hash, snapshot)
        try:
            # Preserve mono levels; the default mono-to-stereo matrix attenuates both channels.
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                            '-i', str(snapshot), '-map', '0:a:0', '-map_metadata', '-1',
                            '-t', '13', '-ar', '44100', '-c:a', 'pcm_f32le', str(decoded)],
                           capture_output=True, check=True, timeout=20)
            samples, rate = sf.read(decoded, dtype='float32', always_2d=True)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            raise EngineError('LOCAL_SOURCE_INVALID', 'Could not decode the uploaded recording.') from error
    if rate != 44100 or samples.shape[0] != frames or samples.shape[1] not in (1, 2) or not np.isfinite(samples).all():
        raise EngineError('LOCAL_SOURCE_INVALID', 'The decoded recording does not match the confirmed melody timeline.')
    return np.repeat(samples, 2, axis=1) if samples.shape[1] == 1 else samples
