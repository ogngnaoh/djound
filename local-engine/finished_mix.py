"""Render non-destructive deltas against an immutable, sample-aligned stereo passage."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import wave
import zipfile

from common import EngineError, canonical_hash, data_root, digest, inside

RATE = 44100
MAX_FRAMES = RATE * 300
MAX_PASSAGE = RATE * 30
RAMP = 441
CONTEXT = RATE * 2
DECODE = 'ffmpeg-44100-stereo-pcm16-bitexact-v1'
OPERATIONS = ('mix-decode', 'mix-prepare', 'mix-preview')
CONTROLS = ('vocals', 'drums')


def invalid(message):
    raise EngineError('MIX_INVALID_REQUEST', message)


def validate_request(request):
    operation = request['operation']
    fields = {'schemaVersion', 'operation', 'sourceAudio', 'sourceSha256'}
    if operation != 'mix-decode':
        fields.update({'startFrame', 'endFrame'})
    if operation == 'mix-preview':
        fields.update({'preparationPath', 'preparationSha256', 'gains'})
    if (type(request.get('schemaVersion')) is not int or request['schemaVersion'] != 1
            or set(request) - ({'changedControl', 'passageTrimDb'} if operation == 'mix-preview' else set()) != fields):
        invalid('The finished mix request is incomplete.')
    for key in ('sourceSha256', 'preparationSha256'):
        if key in request and (not isinstance(request[key], str) or not re.fullmatch('[a-f0-9]{64}', request[key])):
            invalid('An audio or preparation identity is missing.')
    for key in ('sourceAudio', 'preparationPath'):
        if key in request:
            if not isinstance(request[key], str):
                invalid('Choose an existing local recording.')
            inside(request[key], data_root())
    if operation != 'mix-decode':
        start, end = request['startFrame'], request['endFrame']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= MAX_FRAMES or end - start > MAX_PASSAGE:
            invalid('Select a passage of up to 30 seconds.')
    if operation == 'mix-preview':
        trim = request.get('passageTrimDb', 0)
        if type(trim) not in (float, int) or not -12 <= trim <= 0:
            invalid('Choose a passage trim between −12 and 0 dB.')
        gains = request['gains']
        if not isinstance(gains, dict) or set(gains) != set(CONTROLS) or not all(
                type(gain) in (int, float) and -12 <= gain <= 12 for gain in gains.values()):
            invalid('Choose vocal and drum gains between −12 and +12 dB.')
        if request.get('changedControl') is not None and request['changedControl'] not in CONTROLS:
            invalid('Choose vocals or drums.')


def verified(path, expected, *, preparation=False):
    source = inside(path, data_root())
    try:
        matches = source.is_file() and source.stat().st_size <= 64 * 1024 ** 2 and digest(source) == expected
    except OSError:
        matches = False
    if not matches:
        if preparation:
            raise EngineError('MIX_PREPARATION_INVALID', 'The prepared passage changed or is missing. Prepare it again.')
        raise EngineError('MIX_SOURCE_CHANGED', 'The selected recording changed or is missing. Reopen the original.')
    return source


def pcm_info(path):
    try:
        with wave.open(str(path), 'rb') as handle:
            frames = handle.getnframes()
            if handle.getparams()[:3] != (2, 2, RATE) or not 1 <= frames <= MAX_FRAMES:
                invalid('Choose a stereo recording of up to five minutes.')
    except (wave.Error, EOFError) as error:
        raise EngineError('MIX_INVALID_AUDIO', 'Choose a supported stereo recording.') from error
    return frames


def audio_result(path, frames):
    return {'audioPath': str(path), 'audioSha256': digest(path), 'frames': frames,
            'sampleRate': RATE, 'channels': 2}


def decode(source, output):
    target = output / 'audio.wav'
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                        '-i', str(source), '-map', '0:a:0', '-t', '301', '-ar', str(RATE),
                        '-c:a', 'pcm_s16le', '-map_metadata', '-1', '-fflags', '+bitexact',
                        '-flags:a', '+bitexact', '-f', 'wav', str(target)], check=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as error:
        raise EngineError('MIX_DECODE_FAILED', 'The local decoder could not open this stereo recording. Check ffmpeg and the file.') from error
    return audio_result(target, pcm_info(target))


def samples(path, start, stop):
    import numpy as np
    with wave.open(str(path), 'rb') as handle:
        handle.setpos(start)
        raw = handle.readframes(stop - start)
    if len(raw) != (stop - start) * 4:
        invalid('The decoded recording is incomplete.')
    return np.frombuffer(raw, dtype='<i2').reshape(-1, 2)


def preparation_metadata(request):
    import scnet
    provider = os.environ.get('DJOUND_MIX_PROVIDER', 'CPUExecutionProvider')
    if provider not in ('CPUExecutionProvider', 'CoreMLExecutionProvider'):
        invalid('Choose a supported local inference backend.')
    return {'sourceSha256': request['sourceSha256'], 'startFrame': request['startFrame'], 'endFrame': request['endFrame'],
            'decode': DECODE, 'modelSha256': scnet.MODEL_HASH, 'revision': scnet.MANIFEST['revision'],
            'processing': scnet.MANIFEST['preprocessing'], 'provider': provider}


def read_preparation(path, expected):
    import numpy as np
    try:
        with path.open('rb') as handle, np.load(handle, allow_pickle=False) as archive:
            if json.loads(archive['metadata'].item()) != expected:
                raise ValueError('different model, source, or passage')
            estimates = {name: archive[name] for name in CONTROLS}
        frames = expected['endFrame'] - expected['startFrame']
        if any(signal.shape != (frames, 2) or signal.dtype != '<f4' or not np.isfinite(signal).all()
               for signal in estimates.values()):
            raise ValueError('invalid stereo estimate')
        return estimates
    except (EOFError, KeyError, OSError, TypeError, ValueError, zipfile.BadZipFile) as error:
        raise EngineError('MIX_PREPARATION_INVALID', 'The prepared audio does not match this passage and model. Prepare it again.') from error


def prepare(source, request, frames, output):
    import numpy as np
    import scnet
    start, end = request['startFrame'], request['endFrame']
    meta = preparation_metadata(request)
    key = canonical_hash(meta)
    directory = data_root() / 'finished-mix-cache'
    directory.mkdir(exist_ok=True)
    cached = directory / (key + '.npz')
    receipt = directory / (key + '.sha256')
    try:
        hit = cached.is_file() and receipt.is_file() and digest(cached) == receipt.read_text().strip()
        if hit:
            read_preparation(cached, meta)
    except (OSError, ValueError, EngineError):
        hit = False
    if not hit:
        left, right = max(0, start - CONTEXT), min(frames, end + CONTEXT)
        signal = samples(source, left, right).astype(np.float32) / 32768
        estimates = scnet.estimate(signal, meta['provider'])
        selected = {}
        for name in CONTROLS:
            values = estimates[name][start - left:end - left]
            if values.shape != (end - start, 2) or not np.isfinite(values).all():
                raise EngineError('MIX_INVALID_ESTIMATE', 'The separator produced invalid passage audio.')
            selected[name] = values
        # The supervised inference lock serializes writers. Atomic renames survive cancellation.
        with tempfile.TemporaryDirectory(dir=directory) as pending:
            scratch = Path(pending) / 'prepared.npz'
            np.savez(scratch, metadata=np.array(json.dumps(meta)), **selected)
            stamp = Path(pending) / 'hash'
            stamp.write_text(digest(scratch))
            os.replace(scratch, cached)
            os.replace(stamp, receipt)
    target = output / 'preparation.npz'
    os.link(cached, target)
    return {'preparationPath': str(target), 'preparationSha256': receipt.read_text().strip(),
            'cacheKey': key, 'cacheHit': hit, 'provenance': meta}


def data_offset(path):
    """Find the existing RIFF sample payload without rewriting metadata or sample format."""
    with path.open('rb') as handle:
        header = handle.read(12)
        if header[:4] != b'RIFF' or header[8:] != b'WAVE':
            invalid('The decoded recording has an unexpected format.')
        while chunk := handle.read(8):
            if len(chunk) != 8:
                break
            kind, size = struct.unpack('<4sI', chunk)
            if kind == b'data':
                return handle.tell()
            handle.seek(size + (size & 1), os.SEEK_CUR)
    invalid('The decoded recording has no audio samples.')


def ramp(size):
    import numpy as np
    count = min(RAMP, size // 2)
    envelope = np.ones((size, 1))
    if count:
        fade = np.linspace(0, 1, count)
        envelope[:count, 0], envelope[-count:, 0] = fade, fade[::-1]
    if size == 1:
        envelope.fill(0)
    return envelope


def fits(values):
    # Rounding to PCM16 is monotonic. Check the two extrema before converting to integers.
    return round(float(values.min())) >= -32768 and round(float(values.max())) <= 32767


def clipped(raw, changes, gains, control, trim_db, envelope, wet):
    """Find settings that pass the very same export check with all other controls held fixed."""
    trim = 10 ** (trim_db / 20)
    suggested_gain = None
    if control in CONTROLS and gains[control]:
        fixed = wet - changes[control]
        divisor = math.expm1(gains[control] * math.log(10) / 20)
        for tenth in range(math.ceil(abs(gains[control]) * 10) - 1, -1, -1):
            candidate = math.copysign(tenth / 10, gains[control]) if tenth else 0.
            scale = math.expm1(candidate * math.log(10) / 20) / divisor
            if fits(raw + envelope * ((fixed + changes[control] * scale) * trim - raw)):
                suggested_gain = candidate
                break
    suggested_trim = None
    for lower in range(math.ceil(trim_db) - 1, -13, -1):
        if fits(raw + envelope * (wet * (10 ** (lower / 20)) - raw)):
            suggested_trim = lower
            break
    message = 'That change would clip.'
    if suggested_gain:
        message += f' Try {control} at {suggested_gain:+g} dB.'
    if suggested_trim is not None:
        message += f' For this edit, set passage trim to {suggested_trim} dB.'
    if not suggested_gain and suggested_trim is None:
        message += ' Reduce the vocal or drum adjustment.'
    return EngineError('MIX_WOULD_CLIP', message, details={
        'suggestedGainDb': suggested_gain, 'suggestedPassageTrimDb': suggested_trim, 'changedControl': control})


def preview(source, request, frames, output):
    import numpy as np
    start, end = request['startFrame'], request['endFrame']
    prepared = verified(request['preparationPath'], request['preparationSha256'], preparation=True)
    estimates = read_preparation(prepared, preparation_metadata(request))
    raw = samples(source, start, end)
    gains = request['gains']
    trim_db = request.get('passageTrimDb', 0)
    changes = {name: estimates[name].astype(np.float64) * (32768 * math.expm1(gains[name] * math.log(10) / 20))
               for name in CONTROLS}
    envelope = ramp(len(raw))
    wet = raw.astype(np.float64) + changes['vocals'] + changes['drums']
    adjusted = raw + envelope * (wet * (10 ** (trim_db / 20)) - raw)
    if not fits(adjusted):
        raise clipped(raw, changes, gains, request.get('changedControl'), trim_db, envelope, wet)
    target = output / 'audio.wav'
    shutil.copyfile(source, target)
    with target.open('r+b') as destination:
        destination.seek(data_offset(source) + 4 * start)
        destination.write(np.rint(adjusted).astype('<i2').tobytes())
    return {**audio_result(target, frames), 'passageTrimDb': trim_db,
            'gains': gains, 'startFrame': start, 'endFrame': end}


def execute(request, output):
    validate_request(request)
    source = verified(request['sourceAudio'], request['sourceSha256'])
    if request['operation'] == 'mix-decode':
        return decode(source, output)
    frames = pcm_info(source)
    if frames < request['endFrame']:
        invalid('The selected passage extends past the end of this recording.')
    if request['operation'] == 'mix-prepare':
        return prepare(source, request, frames, output)
    return preview(source, request, frames, output)
