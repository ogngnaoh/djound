#!/usr/bin/env python3
"""One bounded local job; structured files in/out, no daemon or automatic retries."""
import argparse
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

from common import HERE, HOME, ROOT, EngineError, data_root, digest, inside, notes, recipe, write_json
from edit_commands import PREFIX
from setup_assets import is_planner_asset, verify

OPERATIONS = ['transcribe', 'preview', 'plan', 'render']
OWNER_PID = os.getppid()


def verify_dependencies(lockfile):
    for line in (HERE / lockfile).read_text().splitlines():
        if '==' in line:
            name, expected = line.split('==', 1)
            if importlib.metadata.version(name) != expected:
                raise EngineError('LOCAL_SETUP_REQUIRED', f'The local engine dependency {name} needs setup.')


def readiness(*, check_freeform=True):
    manifest = json.loads((HERE / 'assets.json').read_text())
    result = {'schemaVersion': 1, 'ready': False, 'model': manifest['model'], 'revision': manifest['revision'],
              'operations': OPERATIONS, 'maxDurationSeconds': 12, 'freeformAvailable': False}
    try:
        if sys.platform != 'darwin' or os.uname().machine != 'arm64':
            raise EngineError('LOCAL_PLATFORM', 'This local engine is qualified for Apple Silicon macOS.')
        verify_dependencies('requirements-audio.lock')
        setup = json.loads((HOME / 'setup.json').read_text())
        for item in manifest['assets']:
            if not is_planner_asset(manifest, item) and not verify(HOME / item['path'], item):
                raise EngineError('LOCAL_SETUP_REQUIRED', 'A pinned transcription or renderer asset is missing or changed.')
        if setup['rendererSha256'] != digest(HOME / 'renderer') or setup['rendererSourceSha256'] != digest(HERE / 'renderer.c'):
            raise EngineError('LOCAL_SETUP_REQUIRED', 'The local renderer needs rebuilding with setup_localengine.sh.')
        with zipfile.ZipFile(HOME / 'downloads/basic_pitch-0.4.0-py2.py3-none-any.whl') as package:
            for item in package.infolist():
                extracted = HOME / 'basic-pitch' / item.filename
                if not item.is_dir() and (not extracted.is_file() or extracted.read_bytes() != package.read(item)):
                    raise EngineError('LOCAL_SETUP_REQUIRED', 'The extracted transcription runtime differs from its pinned package.')
        if not shutil.which('ffmpeg'):
            raise EngineError('LOCAL_SETUP_REQUIRED', 'Install ffmpeg to decode local audio.')
        result['ready'] = True
    except EngineError as error:
        result['error'] = {'code': error.code, 'message': error.message}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, importlib.metadata.PackageNotFoundError):
        result['error'] = {'code': 'LOCAL_SETUP_REQUIRED', 'message': 'Run local-engine/setup_localengine.sh to prepare the local engine.'}
    if not result['ready']:
        result['freeformUnavailableReason'] = result['error']['message']
    elif check_freeform:
        try:
            verify_dependencies('requirements.lock')
            result['freeformAvailable'] = all(verify(HOME / item['path'], item) for item in manifest['assets']
                                              if is_planner_asset(manifest, item))
        except (EngineError, OSError, importlib.metadata.PackageNotFoundError):
            pass
        result['freeformUnavailableReason'] = (None if result['freeformAvailable'] else
            'The freeform language planner needs setup. Use a guided edit or the direct controls.')
    return result


def needs_freeform(request):
    return request['operation'] == 'plan' and not PREFIX.match(request['prompt'].lstrip())


def request_values(path):
    if path.stat().st_size > 2 * 1024 ** 2:
        raise EngineError('LOCAL_INVALID_REQUEST', 'The local request is too large.')
    request = json.loads(path.read_text())
    allowed = {'schemaVersion', 'operation', 'sourceAudio', 'sourceSha256', 'notes', 'duration', 'prompt', 'parentRecipe', 'recipe'}
    if not isinstance(request, dict) or set(request) - allowed or type(request.get('schemaVersion')) is not int or request.get('schemaVersion') != 1 or request.get('operation') not in OPERATIONS:
        raise EngineError('LOCAL_INVALID_REQUEST', 'The local operation contract is invalid.')
    operation = request['operation']
    if operation == 'transcribe':
        source = inside(request.get('sourceAudio', ''), data_root(), True)
        if source.stat().st_size > 64 * 1024 ** 2:
            raise EngineError('LOCAL_AUDIO_LIMIT', 'The audio file exceeds the local upload size limit.')
    elif operation == 'plan':
        if not isinstance(request.get('prompt'), str) or not 1 <= len(request['prompt'].strip()) <= 1000:
            raise EngineError('LOCAL_INVALID_REQUEST', 'Describe a concrete edit in 1000 characters or fewer.')
        if request.get('parentRecipe') is not None:
            recipe(request['parentRecipe'])
    else:
        notes(request.get('notes'), request.get('duration'))
        if operation == 'render':
            validated_recipe = recipe(request.get('recipe'))
            if validated_recipe['instrument'] == 'recorded':
                source = inside(request.get('sourceAudio', ''), data_root(), True)
                if source.stat().st_size > 64 * 1024 ** 2:
                    raise EngineError('LOCAL_AUDIO_LIMIT', 'The audio file exceeds the local upload size limit.')
                if not re.fullmatch(r'[0-9a-f]{64}', str(request.get('sourceSha256', ''))):
                    raise EngineError('LOCAL_INVALID_REQUEST', 'A recorded lead requires the original audio digest.')
    return request


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def supervise(request_path, output, operation):
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
               OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', VECLIB_MAXIMUM_THREADS='2',
               NUMBA_NUM_THREADS='2', NUMBA_CACHE_DIR=str(HOME / 'numba-cache'),
               PYTHONUNBUFFERED='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    deadline = 120 if operation == 'transcribe' else 60 if operation == 'plan' else 30
    memory_limit = (4 if operation == 'plan' else 2) * 1024 ** 3
    started = time.monotonic()
    process = None
    sampled_rss = 0
    resource_samples = []
    try:
        with (output / 'worker.log').open('w') as log:
            process = subprocess.Popen([sys.executable, str(HERE / 'stage.py'), str(request_path), str(output)],
                                       env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            write_json(output / 'process.json', {'workerPid': os.getpid(), 'childPid': process.pid, 'childProcessGroup': process.pid})
            while process.poll() is None:
                if os.getppid() != OWNER_PID:
                    raise EngineError('LOCAL_CANCELLED', 'The owning application exited; the local operation was stopped.')
                elapsed = time.monotonic() - started
                if elapsed > deadline:
                    raise EngineError('LOCAL_TIMEOUT', 'The local operation exceeded its time limit. No retry was made.')
                try:
                    ps = subprocess.run(['ps', '-axo', 'pgid=,rss='], capture_output=True, text=True, check=True, timeout=3)
                    pressure = subprocess.run(['memory_pressure', '-Q'], capture_output=True, text=True, timeout=3)
                except (OSError, subprocess.SubprocessError) as error:
                    raise EngineError('LOCAL_RESOURCE_MONITOR_FAILED', 'Resource availability could not be checked; the local operation was stopped.') from error
                rss = sum(int(parts[1]) * 1024 for line in ps.stdout.splitlines()
                          if len(parts := line.split()) == 2 and int(parts[0]) == process.pid)
                sampled_rss = max(sampled_rss, rss)
                if rss > memory_limit:
                    raise EngineError('LOCAL_MEMORY_LIMIT', 'The local operation reached its memory limit and was stopped.')
                match = re.search(r'System-wide memory free percentage:\s*(\d+)%', pressure.stdout)
                resource_samples.append({'seconds': round(elapsed, 3), 'groupRssBytes': rss,
                                         'freePercent': int(match.group(1)) if match else None})
                if pressure.returncode != 0 or not match:
                    raise EngineError('LOCAL_RESOURCE_MONITOR_FAILED', 'Memory availability could not be checked; the local operation was stopped.')
                if int(match.group(1)) < 30:
                    raise EngineError('LOCAL_MEMORY_PRESSURE', 'Available memory is low. Close other heavy applications before trying again.')
                time.sleep(.2)
            stage_result = output / 'stage-result.json'
            if not stage_result.is_file():
                raise EngineError('LOCAL_ENGINE_FAILED', 'The local process ended before producing a result.')
            result = json.loads(stage_result.read_text())
            if process.returncode != 0 and result.get('status') == 'succeeded':
                raise EngineError('LOCAL_ENGINE_FAILED', 'The local process failed after writing its result.')
            result.setdefault('provenance', {})['supervisor'] = {
                'seconds': time.monotonic() - started, 'peakSampledGroupRssBytes': sampled_rss,
                'deadlineSeconds': deadline, 'rssLimitBytes': memory_limit, 'minimumFreePercent': 30,
                'attempts': 1, 'network': 'HF offline plus blocked Python socket connections'}
            write_json(output / 'resource-samples.json', resource_samples)
            return result
    except BaseException:
        stop(process)
        write_json(output / 'resource-samples.json', resource_samples)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--require-freeform', action='store_true', help='Also require the optional language planner during --check')
    parser.add_argument('--request', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.check:
        value = readiness()
        print(json.dumps(value))
        return 0 if value['ready'] and (not args.require_freeform or value['freeformAvailable']) else 1
    def cancel(signum, frame):
        raise EngineError('LOCAL_CANCELLED', 'The local operation was cancelled.')
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    output, operation, created = None, None, False
    try:
        root = data_root()
        if args.request is None or args.output is None:
            raise EngineError('LOCAL_INVALID_REQUEST', 'A request file and new output directory are required.')
        path = inside(args.request, root, True)
        output = inside(args.output, root)
        if output.exists():
            raise EngineError('LOCAL_OUTPUT_EXISTS', 'The local output directory already exists; earlier results were preserved.')
        output.mkdir(parents=True, exist_ok=False)
        created = True
        request = request_values(path)
        operation = request['operation']
        # Persist the exact validated request so another writer cannot change the
        # file between validation and child execution.
        frozen = output / 'request.json'
        write_json(frozen, request)
        HOME.mkdir(parents=True, exist_ok=True)
        shared = ROOT / '.runtime/local-inference'
        shared.mkdir(parents=True, exist_ok=True)
        with (shared / 'inference.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise EngineError('LOCAL_BUSY', 'Another local engine job is running. Wait for it to finish.') from error
            freeform = needs_freeform(request)
            checked = readiness(check_freeform=freeform)
            if not checked['ready']:
                failure = checked['error']
                raise EngineError(failure['code'], failure['message'])
            if freeform and not checked['freeformAvailable']:
                raise EngineError('LOCAL_PLANNER_UNAVAILABLE', checked['freeformUnavailableReason'])
            result = supervise(frozen, output, operation)
    except EngineError as error:
        result = {'schemaVersion': 1, 'status': 'failed', 'operation': operation,
                  'error': {'code': error.code, 'message': error.message}}
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = {'schemaVersion': 1, 'status': 'failed', 'operation': operation,
                  'error': {'code': 'LOCAL_ENGINE_FAILED', 'message': 'The local operation could not complete. Earlier artifacts were preserved.'}}
    if created and output is not None and output.is_dir() and not (output / 'result.json').exists():
        write_json(output / 'result.json', result)
    print(json.dumps(result, allow_nan=False))
    return 0 if result['status'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
