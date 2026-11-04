#!/usr/bin/env python3
"""Bounded local launch checks and read-only application status; never contact a provider."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request


ROOT = Path(__file__).resolve().parent.parent
STATUS_DEADLINE_SECONDS = 5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def configuration(environment):
    raw_port = environment.get('DJOUND_PORT', '8080')
    if not re.fullmatch(r'[0-9]+', raw_port) or not 1 <= int(raw_port) <= 65535:
        raise ValueError('DJOUND_PORT must be an integer from 1 through 65535.')
    mode = environment.get('DJOUND_MODE', 'UNCONFIGURED')
    if mode not in {'UNCONFIGURED', 'FIXTURE', 'LIVE'}:
        raise ValueError('DJOUND_MODE must be UNCONFIGURED, FIXTURE, or LIVE.')
    return int(raw_port), mode


def command(arguments, timeout=10):
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=True)


def port_available(port):
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
    except OSError as error:
        raise ValueError(f'Port {port} is unavailable. Use `bash scripts/run.sh --status` to check an existing app, '
                         'or choose a free DJOUND_PORT; existing processes will not be stopped.') from error


def status(port):
    def expired(*_):
        raise TimeoutError('Local status deadline exceeded.')

    started = time.monotonic()
    previous_handler = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, STATUS_DEADLINE_SECONDS)
    try:
        read_status(port)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])


def read_status(port):
    url = f'http://127.0.0.1:{port}'
    # No inherited proxy, redirect, or slow-trickle extension of the total deadline.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url + '/api/workspace', timeout=3) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError('Unexpected workspace response size.')
        workspace = json.loads(data)
        if not isinstance(workspace, dict) or workspace.get('mode') not in {'UNCONFIGURED', 'FIXTURE', 'LIVE'}:
            raise ValueError('The service is not a recognized Djound workspace.')
        allowance = workspace.get('submissionAllowance')
        if allowance is not None and (not isinstance(allowance, dict) or any(
                type(allowance.get(key)) is not int or allowance[key] < 0 for key in ('remaining', 'used'))):
            raise ValueError('The service returned an invalid allowance status.')
    except (OSError, ValueError) as error:
        raise ValueError(f'Djound is not responding at {url}. Start it with `bash scripts/run.sh`; '
                         'check its startup log if it exits.') from error
    print(f'Djound is responding at {url} (mode: {workspace["mode"]}).')
    print('Creation is active.' if workspace.get('activeAttemptId') else 'No creation is active.')
    if allowance is not None:
        print(f'Local live allowance: {allowance["remaining"]} remaining, {allowance["used"]} spent. '
              'This does not confirm provider capacity.')


def check(port, mode):
    problems = []
    jars = list((ROOT / 'server/target').glob('djound-*.jar'))
    if len(jars) != 1:
        problems.append('One packaged application is required. Run `bash scripts/package.sh`.')
    try:
        result = command(['java', '-version'])
        version = re.search(r'(?:version\s+"|openjdk\s+)(\d+)', result.stdout + result.stderr)
        if not version or version[1] != '25':
            problems.append('Java 25 is required. Run `bash scripts/bootstrap.sh` or select Java 25.')
    except (OSError, subprocess.SubprocessError):
        problems.append('Java 25 could not be run. Bootstrap the local toolchain first.')
    ffprobe = os.environ.get('DJOUND_FFPROBE', 'ffprobe')
    try:
        command([ffprobe, '-version'])
    except (OSError, subprocess.SubprocessError):
        problems.append('ffprobe could not be run. Install FFmpeg or set DJOUND_FFPROBE to its executable.')
    try:
        port_available(port)
    except ValueError as error:
        problems.append(str(error))
    data = Path(os.environ.get('DJOUND_DATA_DIR', ROOT / '.runtime/data')).resolve()
    ancestor = data
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if (data.exists() and not data.is_dir()) or not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
        problems.append('DJOUND_DATA_DIR must be a directory in a writable location; no files were changed.')
    try:
        host = os.environ.get('DOCKER_HOST', '')
        endpoint = command(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}']).stdout.strip()
        if (host and not host.startswith('unix://')) or not endpoint.startswith('unix://'):
            problems.append('Use a local Unix-socket Docker context. Remote Docker engines are unsupported.')
        else:
            command(['docker', 'info', '--format', '{{.ServerVersion}}'])
            images = command(['docker', 'compose', '-f', str(ROOT / 'compose.yaml'), 'config', '--images']).stdout.splitlines()
            for image in images:
                try:
                    command(['docker', 'image', 'inspect', image])
                except (OSError, subprocess.SubprocessError):
                    problems.append('The pinned PostgreSQL image is not cached. Run `bash scripts/bootstrap.sh` once; '
                                    'normal launch never pulls images.')
    except (OSError, subprocess.SubprocessError):
        problems.append('Docker/Compose is unavailable. Start the local container engine (for example, `colima start`) '
                        'and check `docker info`.')
    if problems:
        for problem in problems:
            print('Not ready: ' + problem, file=sys.stderr)
        return 1
    print(f'Ready to launch Djound at http://127.0.0.1:{port} (mode: {mode}).')
    print(f'Data directory: {data}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true', help='Read the local app workspace without starting anything.')
    args = parser.parse_args()
    port, mode = configuration(os.environ)
    if args.status:
        status(port)
        return 0
    return check(port, mode)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
