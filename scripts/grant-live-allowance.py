#!/usr/bin/env python3
"""Preview or explicitly append an operator authorization; never start an app or provider call."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write the explicit grant; default is preview only.')
    parser.add_argument('--installation-id', required=True)
    parser.add_argument('--grant-id', required=True, help='Stable unique ID; reuse it only for the exact same authorization.')
    parser.add_argument('--submissions', type=int, required=True)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        uuid.UUID(args.installation_id)
    except ValueError:
        parser.error('installation-id must be a UUID')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', args.grant_id):
        parser.error('grant-id must contain 8–128 letters, digits, dots, underscores, colons or hyphens')
    if not 1 <= args.submissions <= 2147483647:
        parser.error('submissions must be a positive PostgreSQL integer')
    if not args.reason.strip() or len(args.reason) > 1000:
        parser.error('reason must contain 1–1000 characters')

    root = Path(__file__).resolve().parent.parent
    data = Path(os.environ.get('DJOUND_DATA_DIR', root / '.runtime/data')).resolve(strict=True)
    if (data / 'installation.id').read_text().strip() != args.installation_id:
        parser.error('installation-id does not match the data directory')
    url = os.environ.get('DJOUND_DB_URL', 'jdbc:postgresql://127.0.0.1:55432/djound')
    match = re.fullmatch(r'jdbc:postgresql://(?:127\.0\.0\.1|localhost):55432/([A-Za-z_][A-Za-z0-9_]*)', url)
    if not match:
        parser.error('Only the local Djound Compose PostgreSQL on port 55432 is supported')
    if os.environ.get('DOCKER_HOST') and not os.environ['DOCKER_HOST'].startswith('unix://'):
        parser.error('A local Unix-socket Docker engine is required')
    endpoint = subprocess.run(
        ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if not endpoint.startswith('unix://'):
        parser.error('A local Unix-socket Docker context is required')

    # psql quoted variables escape SQL values; no shell interpolation or app startup occurs.
    command = ['docker', 'compose', '--project-directory', str(root), '-f', str(root / 'compose.yaml'),
               'exec', '-T', 'db', 'psql', '-X', '-qAt', '-U', 'djound', '-d', match[1],
               '-v', 'ON_ERROR_STOP=1']
    values = {'installation': args.installation_id, 'directory': str(data), 'grant': args.grant_id,
              'submissions': str(args.submissions), 'reason': args.reason,
              'apply': 'true' if args.apply else 'false'}
    for key, value in values.items():
        command.extend(['-v', key + '=' + value])
    result = subprocess.run(command, input="BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='10s';\n"
                            "SELECT grant_live_submissions(:'installation', :'directory', "
                            ":'grant', :'submissions'::integer, :'reason', :'apply'::boolean);\nCOMMIT;\n",
                            check=True, capture_output=True, text=True, timeout=30)
    print(json.dumps(json.loads(result.stdout), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        print('Allowance grant failed: ' + detail, file=sys.stderr)
        sys.exit(1)
