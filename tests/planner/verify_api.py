#!/usr/bin/env python3
"""Compare expected recipes and refusal codes to version-linked plans through the packaged LOCAL API."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

DEFAULT = {'instrument': 'piano', 'bass': False, 'drums': False,
           'density': 'steady', 'backingVolume': 'normal', 'bpm': 100}
PARENTS = {'default': DEFAULT, 'full': {**DEFAULT, 'instrument': 'electric_piano', 'bass': True, 'drums': True}}


def expected(case):
    parent = case['parent']
    parent = PARENTS[parent] if isinstance(parent, str) else parent
    if set(parent) - {'mix'} != set(DEFAULT) or ('mix' in parent and not isinstance(parent['mix'], dict)):
        raise ValueError(f"Incomplete parent for case {case['id']}")
    label = case['expected']
    if isinstance(label, str):
        if label not in {'noop', 'unsupported'}:
            raise ValueError(f"Unknown label for {case['id']}")
        return parent, label, None
    desired = {**parent, **label}
    if desired == parent:
        raise ValueError(f"Edit label for {case['id']} has no effect")
    return parent, 'edit', desired

HERE = Path(__file__).resolve().parent
BASE = os.environ['DJOUND_TEST_BASE_URL']
if (os.environ.get('DJOUND_TEST_ALLOW_WRITE') != '1' or os.environ.get('DJOUND_MODE') != 'LOCAL'
        or urlsplit(BASE).hostname != '127.0.0.1'
        or not os.environ.get('DJOUND_DB_URL', '').split('/')[-1].startswith('djound_local_check_')):
    raise SystemExit('Use bash scripts/verify-local.sh planner with an isolated test database')


def api(path, method='GET', body=None, key=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    request = Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                      method=method, headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def until(fetch, accepts, deadline=140):
    expires = time.monotonic() + deadline
    while time.monotonic() < expires:
        state = fetch()
        if accepts(state):
            return state
        time.sleep(0.2)
    raise RuntimeError('API verification exceeded the expected job deadline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=HERE / 'requests.json')
    parser.add_argument('--results', type=Path, default=HERE / 'expected.json')
    parser.add_argument('--arm', default='baseline')
    parser.add_argument('--all', action='store_true', help='Run every case; default to one per parent/label pair.')
    options = parser.parse_args()
    cases = json.loads(options.cases.read_text())
    rows_expected = json.loads(options.results.read_text())
    worker_rows = {row['id']: row for row in rows_expected if row['arm'] == options.arm}
    assert len(cases) == len({case['id'] for case in cases})
    assert all(case['id'] in worker_rows for case in cases)
    selected, seen = [], set()
    for case in cases:
        parent, label, _ = expected(case)
        group = (json.dumps(parent, sort_keys=True), label)
        if options.all or group not in seen:
            selected.append(case)
            seen.add(group)
    seed = 'Prepare isolated planner verification.'
    fixture = HERE.parents[1] / 'fixtures/audio/local-demo-source.wav'
    upload = subprocess.run(['curl', '--silent', '--show-error', '--fail-with-body', '-H',
                             'Idempotency-Key: ' + uuid4().hex, '-F', 'file=@' + str(fixture),
                             '-F', 'initialDraft=' + seed, BASE + '/api/explorations'],
                            check=True, capture_output=True)
    path = '/api/explorations/' + json.loads(upload.stdout)['explorationId']

    def local_job(operation, body):
        submitted = api(path + '/local/' + operation, 'POST', body)
        result = until(lambda: api('/api/local/jobs/' + submitted['id']),
                       lambda row: row['job']['state'] in {'SUCCEEDED', 'FAILED'})
        assert result['job']['id'] == submitted['id']
        return result

    result = local_job('transcriptions', {'expectedSourceRevision': 1})
    notes = api(path + '/local')['notes']
    assert result['job']['state'] == 'SUCCEEDED' and notes['notes']
    notes = api(path + '/local/notes', 'PUT', {'expectedSourceRevision': 1, 'expectedNoteRevision': notes['revision'],
                                               'notes': notes['notes'], 'confirm': True})
    original = api(path)['original']['id']

    def command(text, basis):
        draft = api(path)['draft']
        if draft['text'] != text or draft['basisRecordingId'] != basis:
            draft = api(path + '/draft', 'PUT', {'text': text, 'basisRecordingId': basis,
                                                 'expectedDraftRevision': draft['revision']})
        return {'expectedSourceRevision': 1, 'expectedNoteRevision': notes['revision'],
                'expectedDraftRevision': draft['revision'], 'basisRecordingId': basis, 'text': text}

    # Stage representative parent recipes once through normal direct controls.
    # This avoids conditioning fixture creation on a correct model answer.
    parents = {json.dumps(PARENTS['default'], sort_keys=True): original}

    def basis_for(parent):
        key = json.dumps(parent, sort_keys=True)
        if key in parents:
            return parents[key]
        controls = command(seed, original)
        plan = api(path + '/local/manual-plans', 'POST', controls)
        plan = api('/api/local/plans/' + plan['id'] + '/corrections',
                   'POST', {**controls, 'recipe': parent})
        receipt = api(path + '/requests', 'POST', {'localPlanId': plan['id'], 'text': seed,
                      'expectedSourceRevision': 1, 'expectedDraftRevision': plan['draftRevision'],
                      'basisRecordingId': original}, uuid4().hex)
        attempt = until(lambda: api('/api/attempts/' + receipt['attemptId']),
                        lambda row: row['state'] in {'SUCCEEDED', 'FAILED'})
        assert attempt['state'] == 'SUCCEEDED', 'Could not render parent'
        entry = next(row for row in api(path)['renditions'] if row['attemptId'] == attempt['id'])
        assert entry['localRecipe'] == parent, 'Saved parent has a different recipe'
        parents[key] = entry['id']
        return entry['id']

    rows = []
    for case in selected:
        parent, label, _ = expected(case)
        basis = basis_for(parent)
        submitted = command(case['text'], basis)
        outcome = local_job('plans', submitted)
        actual = outcome['job']['outcomeCode']
        provenance = None
        if outcome['job']['state'] == 'SUCCEEDED':
            plan = outcome['plan']
            assert plan['basisRecordingId'] == basis and plan['basisRecipe'] == parent
            assert plan['text'] == case['text'] and plan['draftRevision'] == submitted['expectedDraftRevision']
            assert plan['sourceRevision'] == 1 and plan['noteRevision'] == notes['revision'] and plan['canGenerate']
            assert plan['model'], 'Worker provenance missing from stored plan'
            provenance = {key: plan['model'][key] for key in ('origin', 'model', 'inferenceAttempts')
                          if key in plan['model']}
            if case['text'].lstrip().lower().startswith('edit:'):
                assert provenance.get('origin') == 'GUIDED_COMMANDS', 'Guided text was routed to the wrong planner'
            if provenance.get('origin') in {'EXPLICIT_CONTROLS', 'GUIDED_COMMANDS'}:
                assert provenance.get('inferenceAttempts') == 0 and 'model' not in provenance
            actual = plan['recipe']
        else:
            assert outcome['plan'] is None, 'Failed job surfaced a stale proposal'
        row = {'id': case['id'], 'parent': parent, 'label': label, 'actual': actual,
               'workerActual': worker_rows[case['id']]['actual'], 'provenance': provenance}
        rows.append(row)
        print(json.dumps(row), flush=True)
    evidence = Path(os.environ['DJOUND_SCREENSHOT_DIR'])
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'planner-api.json').write_text(json.dumps(rows, indent=2) + '\n')
    assert all(row['actual'] == row['workerActual'] for row in rows), 'Spring and worker disagree'


if __name__ == '__main__':
    main()
