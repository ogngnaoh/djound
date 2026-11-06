#!/usr/bin/env python3
"""Recovery checks against real app/SQL/media and explicitly enabled sample controls."""
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


def wait_state(attempt_id, expected):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        status, attempt = json_request('/api/attempts/' + attempt_id)
        expect(200, status, 'attempt polling')
        if attempt['state'] in {'SUCCEEDED', 'FAILED'}:
            assert attempt['state'] == expected, attempt
            return attempt
        time.sleep(.15)
    raise AssertionError('Sample attempt did not reach its expected terminal state')


def control(outcome):
    status, body = json_request('/api/fixture/controls/next-attempt', 'PUT', {'outcome': outcome})
    expect(200, status, 'set explicit next sample outcome')
    assert body['nextOutcome'] == outcome


def create(exploration, text, basis=None):
    body = {'text': text, 'basisRecordingId': basis or exploration['original']['id'],
            'expectedSourceRevision': exploration['sourceRevision'],
            'expectedDraftRevision': exploration['draft']['revision']}
    status, receipt = json_request('/api/explorations/' + exploration['id'] + '/requests', 'POST', body,
                                   'recovery-create-' + uuid.uuid4().hex)
    expect(202, status, 'creation acceptance')
    return receipt


def save(exploration_id, text, basis, revision):
    status, draft = json_request('/api/explorations/' + exploration_id + '/draft', 'PUT', {
        'text': text, 'basisRecordingId': basis, 'expectedDraftRevision': revision,
    })
    expect(200, status, 'save next draft')
    return draft


status, workspace = json_request('/api/workspace')
expect(200, status, 'workspace')
assert workspace['mode'] == 'FIXTURE' and workspace['fixtureControlsEnabled'] is True
assert workspace['canSubmitWithAcknowledgement'] is False and workspace['unknownOutcome'] is None
status, controls = json_request('/api/fixture/controls')
expect(200, status, 'explicit sample controls')
assert workspace['activeAttemptId'] is None
status, uploaded = upload('recovery-upload-' + uuid.uuid4().hex)
expect(201, status, 'source upload')
source = detail(uploaded['explorationId'])
control('FAILURE')
initial = create(source, 'The exact first interpretation, preserved for Retry.')
# Updating the next scenario cannot change the already admitted one.
control('SUCCESS')
next_draft = save(source['id'], 'Later notes must survive every Retry.', source['original']['id'], initial['nextDraftRevision'])
failed = wait_state(initial['attemptId'], 'FAILED')
assert failed['outcome']['code'] == 'GENERATION_FAILED' and failed['resultRecordingId'] is None
assert failed['canRetry'] is True
assert detail(source['id'])['renditions'] == []
assert detail(source['id'])['draft'] == next_draft
retry_path = '/api/requests/' + initial['requestId'] + '/retries'
retry_body = {'expectedPreviousAttemptId': failed['id']}
retry_keys = ['retry-race-' + uuid.uuid4().hex for _ in range(2)]
with ThreadPoolExecutor(max_workers=2) as executor:
    retry_results = list(executor.map(lambda key: json_request(retry_path, 'POST', retry_body, key), retry_keys))
assert sorted(status for status, _ in retry_results) == [202, 409], retry_results
winner = next(i for i, response in enumerate(retry_results) if response[0] == 202)
retry_receipt = retry_results[winner][1]
assert retry_results[1 - winner][1]['code'] in {'STALE_ATTEMPT', 'CREATION_ACTIVE'}
assert retry_receipt['requestId'] == initial['requestId']
assert retry_receipt['attemptId'] != failed['id']
assert 'consumedDraftRevision' not in retry_receipt and 'nextDraftRevision' not in retry_receipt
status, retried = json_request('/api/attempts/' + retry_receipt['attemptId'])
assert retried['previousAttemptId'] == failed['id'] and retried['request'] == failed['request']
assert detail(source['id'])['draft'] == next_draft
newer_draft = save(source['id'], 'Even newer words typed during Retry.', source['original']['id'], next_draft['revision'])
status, retry_replay = json_request(retry_path, 'POST', retry_body, retry_keys[winner])
expect(202, status, 'lost Retry response replay')
assert retry_replay['replayed'] and retry_replay['attemptId'] == retry_receipt['attemptId']
assert detail(source['id'])['draft'] == newer_draft
successful_retry = wait_state(retry_receipt['attemptId'], 'SUCCEEDED')
first = detail(source['id'])['renditions'][0]
assert first['id'] == successful_retry['resultRecordingId']
assert detail(source['id'])['draft'] == newer_draft
status, stale = json_request(retry_path, 'POST', retry_body, 'stale-retry-' + uuid.uuid4().hex)
expect(409, status, 'old failure cannot retry after success')
assert stale['code'] == 'STALE_ATTEMPT'
status, replay = json_request(retry_path, 'POST', retry_body, retry_keys[winner])
expect(202, status, 'accepted Retry receipt precedes stale authority')
assert replay['replayed'] and replay['attemptId'] == retry_receipt['attemptId']
assert len(detail(source['id'])['renditions']) == 1

control('FAILURE')
refinement = create(detail(source['id']), 'The frozen refinement to retry unchanged.', first['id'])
refinement_notes = save(source['id'], 'Separate notes remain pinned to the earlier version.', first['id'], refinement['nextDraftRevision'])
refinement_failure = wait_state(refinement['attemptId'], 'FAILED')
assert refinement_failure['request']['sourceRecordingId'] == source['original']['id']
assert detail(source['id'])['draft'] == refinement_notes
assert len(detail(source['id'])['renditions']) == 1
status, refined_retry = json_request('/api/requests/' + refinement['requestId'] + '/retries', 'POST',
                                    {'expectedPreviousAttemptId': refinement_failure['id']}, 'refined-retry-' + uuid.uuid4().hex)
expect(202, status, 'refinement Retry')
wait_state(refined_retry['attemptId'], 'SUCCEEDED')
assert detail(source['id'])['draft'] == refinement_notes
assert len(detail(source['id'])['renditions']) == 2
status, _, before_fault = request(first['mediaUrl'])
expect(200, status, 'sample before playback fault')
status, controls = json_request('/api/fixture/controls/playback', 'PUT', {'recordingId': first['id'], 'unavailable': True})
expect(200, status, 'non-destructive sample playback fault')
status, headers, body = request(first['mediaUrl'])
expect(503, status, 'unavailable sample media')
assert json.loads(body)['code'] == 'MEDIA_UNAVAILABLE'
status, _, original_bytes = request(source['original']['mediaUrl'])
expect(200, status, 'original remains available')
assert original_bytes == AUDIO
status, _ = json_request('/api/fixture/controls/playback', 'PUT', {'recordingId': first['id'], 'unavailable': False})
expect(200, status, 'restore sample playback')
status, headers, after_fault = request(first['mediaUrl'])
expect(200, status, 'restored sample media')
assert after_fault == before_fault and 'no-store' in headers.get('Cache-Control', '')
assert detail(source['id'])['latestAttempt']['id'] == refined_retry['attemptId']
assert len(detail(source['id'])['renditions']) == 2

# Replacing an original after its first failed request retires that old Retry.
status, another_upload = upload('retired-upload-' + uuid.uuid4().hex)
expect(201, status, 'another source upload')
another = detail(another_upload['explorationId'])
control('FAILURE')
retired_request = create(another, 'This request must remain tied to the old source.')
retired_failure = wait_state(retired_request['attemptId'], 'FAILED')
boundary = 'replace-' + uuid.uuid4().hex
replacement = (ROOT / 'fixtures/audio/sample-pulse.wav').read_bytes()
replacement_body = (f'--{boundary}\r\nContent-Disposition: form-data; name="expectedSourceRevision"\r\n\r\n1\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="new-source.wav"\r\n'
                    'Content-Type: audio/wav\r\n\r\n').encode() + replacement + f'\r\n--{boundary}--\r\n'.encode()
status, _, body = request('/api/explorations/' + another['id'] + '/source', 'PUT', replacement_body,
                          {'Content-Type': 'multipart/form-data; boundary=' + boundary,
                           'Idempotency-Key': 'replace-retired-' + uuid.uuid4().hex})
expect(200, status, 'replace source after first failure')
status, retired_status = json_request('/api/attempts/' + retired_failure['id'])
assert retired_status['canRetry'] is False
status, stale_source = json_request('/api/requests/' + retired_request['requestId'] + '/retries', 'POST',
                                   {'expectedPreviousAttemptId': retired_failure['id']}, 'retired-retry-' + uuid.uuid4().hex)
expect(409, status, 'retired source request cannot retry')
assert stale_source['code'] in {'SOURCE_CHANGED', 'STALE_ATTEMPT'}
assert detail(another['id'])['renditions'] == []
print(json.dumps({
    'result': 'PASS', 'explorationId': source['id'], 'attemptId': retry_receipt['attemptId'],
    'recordingId': first['id'], 'acceptedPath': retry_path,
    'acceptedKey': retry_keys[winner], 'acceptedCommand': retry_body,
    'checks': ['captured one-shot failure', 'first failure has no phantom recording',
               'one concurrent Retry accepted', 'Retry preserves immutable request and current draft',
               'lost/stale Retry receipt replay', 'refinement failure preserves earlier audio and notes',
               'non-destructive playback failure/restore', 'source replacement retires old Retry'],
}, indent=2))
