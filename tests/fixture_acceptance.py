#!/usr/bin/env python3
"""S03 black-box checks. This runs real app/SQL/media, with explicit sample output."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get('DJOUND_TEST_BASE_URL', '').rstrip('/')
if not BASE.startswith(('http://127.0.0.1:', 'http://localhost:')) or os.environ.get('DJOUND_TEST_ALLOW_WRITE') != '1':
    raise SystemExit('Set loopback DJOUND_TEST_BASE_URL and DJOUND_TEST_ALLOW_WRITE=1 for an isolated FIXTURE installation.')
ROOT = Path(__file__).resolve().parents[1]
AUDIO = (ROOT / 'fixtures/audio/source-study.wav').read_bytes()
MANIFEST = json.loads((ROOT / 'fixtures/manifest.json').read_text())
OUTPUT_HASHES = {r['sha256'] for r in MANIFEST['recordings'] if r['role'] == 'predefined-output'}


def request(path, method='GET', body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def json_request(path, method='GET', body=None, key=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    status, _, data = request(path, method, None if body is None else json.dumps(body).encode(), headers)
    return status, json.loads(data)


def expect(status, actual, message):
    assert actual == status, f'{message}: expected {status}, received {actual}'


def upload(key):
    boundary = 'fixture-' + uuid.uuid4().hex
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="initialDraft"\r\n\r\nEarlier saved words.\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture-source.wav"\r\n'
            'Content-Type: audio/wav\r\n\r\n').encode() + AUDIO + f'\r\n--{boundary}--\r\n'.encode()
    status, _, body = request('/api/explorations', 'POST', data,
                              {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Idempotency-Key': key})
    return status, json.loads(body)


def detail(exploration_id):
    status, body = json_request('/api/explorations/' + exploration_id)
    expect(200, status, 'exploration')
    return body


def await_terminal(attempt_id):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        status, attempt = json_request('/api/attempts/' + attempt_id)
        expect(200, status, 'attempt polling')
        if attempt['state'] in {'SUCCEEDED', 'FAILED'}:
            assert attempt['state'] == 'SUCCEEDED', attempt
            return attempt
        time.sleep(.15)
    raise AssertionError('Fixture attempt did not reach a bounded terminal state')


status, workspace = json_request('/api/workspace')
expect(200, status, 'workspace')
assert workspace['mode'] == 'FIXTURE' and workspace['configuration'] == 'CONFIGURED'
assert workspace['canSubmit'] and workspace['activeAttemptId'] is None
sources = []
for _ in range(2):
    status, receipt = upload('fixture-upload-' + uuid.uuid4().hex)
    expect(201, status, 'fixture source upload')
    sources.append(detail(receipt['explorationId']))
commands = [{
    'text': f'Exactly frozen direction {i}: warm piano 🎶',
    'basisRecordingId': source['original']['id'],
    'expectedSourceRevision': source['sourceRevision'],
    'expectedDraftRevision': source['draft']['revision'],
} for i, source in enumerate(sources)]
keys = ['fixture-command-' + uuid.uuid4().hex for _ in sources]


def submit(index):
    return json_request('/api/explorations/' + sources[index]['id'] + '/requests', 'POST', commands[index], keys[index])


with ThreadPoolExecutor(max_workers=2) as executor:
    responses = list(executor.map(submit, range(2)))
assert sorted(status for status, _ in responses) == [202, 409], responses
winner = next(i for i, response in enumerate(responses) if response[0] == 202)
loser = 1 - winner
assert responses[loser][1]['code'] == 'CREATION_ACTIVE', responses[loser]
receipt = responses[winner][1]
source = sources[winner]
path = '/api/explorations/' + source['id'] + '/requests'
assert receipt['consumedDraftRevision'] == commands[winner]['expectedDraftRevision']
assert receipt['nextDraftRevision'] == receipt['consumedDraftRevision'] + 1
assert receipt['replayed'] is False
status, active = json_request('/api/attempts/' + receipt['attemptId'])
expect(200, status, 'accepted attempt')
assert active['request']['text'] == commands[winner]['text']
assert active['request']['sourceRecordingId'] == source['original']['id']
assert active['request']['provenance'] == 'FIXTURE'
assert commands[winner]['text'] in active['request']['assembledInstructions']
assert active['request']['assemblerVersion']
status, workspace = json_request('/api/workspace')
assert workspace['activeAttemptId'] == receipt['attemptId']
assert not workspace['canSubmit'] and not workspace['canCreateExploration']
assert not detail(sources[loser]['id'])['canReplaceSource']
assert detail(sources[loser]['id'])['latestAttempt'] is None

next_words = 'These later notes are independent of the accepted request.'
status, draft = json_request('/api/explorations/' + source['id'] + '/draft', 'PUT', {
    'text': next_words,
    'basisRecordingId': source['original']['id'],
    'expectedDraftRevision': receipt['nextDraftRevision'],
})
expect(200, status, 'next draft during creation')
status, replay = submit(winner)
expect(202, status, 'accepted replay while active')
assert replay['replayed'] and replay['attemptId'] == receipt['attemptId']
assert replay['nextDraftRevision'] == receipt['nextDraftRevision']
assert detail(source['id'])['draft'] == draft
status, conflict = json_request(path, 'POST', {**commands[winner], 'text': 'Changed command'}, keys[winner])
expect(409, status, 'changed command under accepted key')
assert conflict['code'] == 'IDEMPOTENCY_CONFLICT'
status, rejected_upload = upload('blocked-upload-' + uuid.uuid4().hex)
expect(409, status, 'new exploration while active')
assert rejected_upload['code'] == 'CREATION_ACTIVE'

completed = await_terminal(receipt['attemptId'])
finished = detail(source['id'])
assert finished['latestAttempt']['id'] == receipt['attemptId']
assert finished['draft']['text'] == next_words
assert len(finished['renditions']) == 1
result = finished['renditions'][0]
assert result['id'] == completed['resultRecordingId']
assert result['provenance'] == 'FIXTURE'
assert result['requestId'] == receipt['requestId'] and result['attemptId'] == receipt['attemptId']
assert result['direction'] == completed['request']['direction']
assert result['sha256'] in OUTPUT_HASHES
status, _, output = request(result['mediaUrl'])
expect(200, status, 'published sample audio')
assert hashlib.sha256(output).hexdigest() == result['sha256']
status, _, original = request(finished['original']['mediaUrl'])
assert original == AUDIO
status, replay = submit(winner)
expect(202, status, 'accepted replay after completion')
assert replay['attemptId'] == receipt['attemptId'] and replay['replayed']
assert len(detail(source['id'])['renditions']) == 1
status, workspace = json_request('/api/workspace')
assert workspace['activeAttemptId'] is None and workspace['canSubmit']

# A temporary rejection must not be cached as an accepted receipt.
status, later_receipt = submit(loser)
expect(202, status, 'deliberate resubmission after temporary active-slot rejection')
assert not later_receipt['replayed']
await_terminal(later_receipt['attemptId'])
assert len(detail(sources[loser]['id'])['renditions']) == 1
print(json.dumps({
    'result': 'PASS', 'explorationId': source['id'], 'attemptId': receipt['attemptId'],
    'recordingId': result['id'], 'acceptedKey': keys[winner], 'acceptedCommand': commands[winner],
    'checks': ['real concurrent admission', 'immutable literal request', 'one active across explorations',
               'next draft remains separate', 'receipt replay preserves later words', 'changed payload conflict',
               'active upload/source guards', 'normal publication of predefined sample', 'durable fixture provenance',
               'original bytes unchanged', 'one output after replay', 'temporary rejection not cached'],
}, indent=2))
