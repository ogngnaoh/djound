#!/usr/bin/env python3
"""Capacity policy against a real isolated app, PostgreSQL, and media store."""
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
    raise SystemExit('Use an isolated loopback FIXTURE installation with DJOUND_TEST_ALLOW_WRITE=1.')
ROOT = Path(__file__).resolve().parents[1]
AUDIO = (ROOT / 'fixtures/audio/source-study.wav').read_bytes()


def request(path, method='GET', body=None, key=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    req = urllib.request.Request(BASE + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = json.loads(response.read())
        assert response.status == expected, (path, response.status, data)
        return data


def key():
    return 'availability-' + uuid.uuid4().hex


def workspace():
    return request('/api/workspace')


def scenario(value):
    return request('/api/fixture/controls/availability', 'PUT', {'scenario': value})


def outcome(value):
    return request('/api/fixture/controls/next-attempt', 'PUT', {'outcome': value})


def eventually(check, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.15)
    raise AssertionError('Capacity check timed out; use an explicit short fixture cooldown for this isolated run.')


def terminal(attempt_id, state):
    def check():
        value = request('/api/attempts/' + attempt_id)
        if value['state'] not in ('SUCCEEDED', 'FAILED'):
            return None
        assert value['state'] == state, value
        return value
    return eventually(check)


def wait_available(field):
    def check():
        value = workspace()
        return value if value[field] else None
    return eventually(check)


initial = workspace()
assert initial['mode'] == 'FIXTURE' and initial['fixtureControlsEnabled']
assert initial['activeAttemptId'] is None and initial['unknownOutcome'] is None
blocked = scenario('BLOCKED_NO_STATUS')
assert blocked['eligibility'] == 'BLOCKED' and not blocked['canSubmit']
assert not blocked['canSubmitWithAcknowledgement'] and not blocked['supportsStatusCheck']
assert not blocked['canRecheck'] and blocked['providerResetAt'] is None and blocked['nextProbeAt']

# Capacity gates generation, not local source upload or playback.
boundary = 'availability-' + uuid.uuid4().hex
multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="initialDraft"\r\n\r\nSaved capacity notes.\r\n'
             f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="availability-study.wav"\r\n'
             'Content-Type: audio/wav\r\n\r\n').encode() + AUDIO + f'\r\n--{boundary}--\r\n'.encode()
req = urllib.request.Request(BASE + '/api/explorations', data=multipart,
                             headers={'Content-Type': 'multipart/form-data; boundary=' + boundary,
                                      'Idempotency-Key': key()}, method='POST')
with urllib.request.urlopen(req, timeout=20) as response:
    assert response.status == 201
    exploration_id = json.load(response)['explorationId']
path = '/api/explorations/' + exploration_id
detail = request(path)
original = detail['original']
command = {'text': 'A deliberate instrumental interpretation.', 'basisRecordingId': original['id'],
           'expectedSourceRevision': detail['sourceRevision'], 'expectedDraftRevision': detail['draft']['revision']}
create_key = key()
assert request(path + '/requests', 'POST', command, create_key, 503)['code'] == 'CAPACITY_UNAVAILABLE'
assert request(path)['draft'] == detail['draft']
unsupported = request('/api/workspace/availability/recheck', 'POST')
assert unsupported['eligibility'] == 'BLOCKED' and unsupported['nextProbeAt'] == blocked['nextProbeAt']
assert request(path)['latestAttempt'] is None
expired = wait_available('canSubmit')
assert expired['eligibility'] == 'UNKNOWN' and expired['observedAt'] == blocked['observedAt']
assert not expired['supportsStatusCheck'] and expired['providerResetAt'] is None
assert request(path)['latestAttempt'] is None, 'Expiry must not enqueue generation'

# A real accepted sample attempt reports capacity loss; the previously rejected key is reusable.
outcome('CAPACITY_UNAVAILABLE')
receipt = request(path + '/requests', 'POST', command, create_key, 202)
next_draft = request(path + '/draft', 'PUT', {
    'text': 'Independent next notes survive unavailable capacity.', 'basisRecordingId': original['id'],
    'expectedDraftRevision': receipt['nextDraftRevision'],
})
failed = terminal(receipt['attemptId'], 'FAILED')
assert failed['outcome']['code'] == 'CAPACITY_UNAVAILABLE' and failed['canRetry']
assert workspace()['eligibility'] == 'BLOCKED' and not workspace()['canSubmit']
assert request(path + '/requests', 'POST', command, create_key, 202)['replayed']
retry_path = '/api/requests/' + receipt['requestId'] + '/retries'
retry_body = {'expectedPreviousAttemptId': failed['id']}
retry_key = key()
assert request(retry_path, 'POST', retry_body, retry_key, 503)['code'] == 'CAPACITY_UNAVAILABLE'
assert request(path)['draft'] == next_draft
expired = wait_available('canSubmit')
assert expired['eligibility'] == 'UNKNOWN'
assert request(path)['latestAttempt']['id'] == failed['id']
retried = request(retry_path, 'POST', retry_body, retry_key, 202)
done = terminal(retried['attemptId'], 'SUCCEEDED')
assert done['request'] == failed['request'] and request(path)['draft'] == next_draft
stable_attempt = done['id']

# Supported checks never fall through to a speculative generation probe.
for control, reason in [('STATUS_MALFORMED', 'STATUS_MALFORMED'), ('STATUS_TIMEOUT', 'STATUS_TIMEOUT')]:
    observed = scenario(control)
    assert observed['eligibility'] == 'BLOCKED' and observed['supportsStatusCheck']
    assert not observed['canSubmit']
    request('/api/workspace/availability/recheck', 'POST', expected=409)
    expired = wait_available('canRecheck')
    assert expired['eligibility'] == 'UNKNOWN' and not expired['canSubmit']
    checked = request('/api/workspace/availability/recheck', 'POST')
    assert checked['eligibility'] == 'UNKNOWN' and checked['reasonCode'] == reason
    assert not checked['canSubmit'] and not checked['canSubmitWithAcknowledgement']
    assert checked['providerResetAt'] is None and checked['nextProbeAt']
    assert request('/api/workspace/availability/recheck', 'POST', expected=409)['code'] == 'RECHECK_TOO_SOON'
    assert request(path)['latestAttempt']['id'] == stable_attempt
    assert request(path)['draft'] == next_draft
    assert request(retry_path, 'POST', retry_body, retry_key, 202)['replayed']

scenario('STATUS_SUCCESS')
wait_available('canRecheck')
checked = request('/api/workspace/availability/recheck', 'POST')
assert checked['eligibility'] == 'ELIGIBLE' and checked['canSubmit']
assert request(path)['latestAttempt']['id'] == stable_attempt, 'A status check must not create an attempt'

# Capacity rejection cannot consume acknowledgement of an unresolved earlier operation.
outcome('UNKNOWN')
current = request(path)
unknown_command = {'text': 'A separate uncertain sample request.', 'basisRecordingId': original['id'],
                   'expectedSourceRevision': current['sourceRevision'], 'expectedDraftRevision': current['draft']['revision']}
unknown_receipt = request(path + '/requests', 'POST', unknown_command, key(), 202)
unknown = terminal(unknown_receipt['attemptId'], 'FAILED')
assert unknown['outcome']['code'] == 'OUTCOME_UNKNOWN'
preserved = request(path + '/draft', 'PUT', {
    'text': 'Keep these notes through acknowledged capacity recovery.', 'basisRecordingId': original['id'],
    'expectedDraftRevision': unknown_receipt['nextDraftRevision'],
})
scenario('BLOCKED_NO_STATUS')
accepted_path = '/api/requests/' + unknown['requestId'] + '/retries'
accepted_body = {'expectedPreviousAttemptId': unknown['id'], 'acknowledgedUnknownAttemptId': unknown['id']}
accepted_key = key()
assert request(accepted_path, 'POST', accepted_body, accepted_key, 503)['code'] == 'CAPACITY_UNAVAILABLE'
assert workspace()['unknownOutcome']['attemptId'] == unknown['id']
assert workspace()['unknownOutcome']['provenance'] == 'FIXTURE'
assert request(path)['draft'] == preserved
scenario('READY')
assert workspace()['canSubmitWithAcknowledgement'] and not workspace()['canSubmit']
accepted = request(accepted_path, 'POST', accepted_body, accepted_key, 202)
assert workspace()['unknownOutcome'] is None
terminal(accepted['attemptId'], 'SUCCEEDED')
final = request(path)
assert final['draft'] == preserved and final['original'] == original
assert len(final['renditions']) == 2
assert request('/api/attempts/' + unknown['id'])['outcome']['code'] == 'OUTCOME_UNKNOWN'
for recording in [original, *final['renditions']]:
    with urllib.request.urlopen(BASE + recording['mediaUrl'], timeout=20) as response:
        audio = response.read()
        assert hashlib.sha256(audio).hexdigest() == recording['sha256']
assert request(accepted_path, 'POST', accepted_body, accepted_key, 202)['replayed']
print(json.dumps({'explorationId': exploration_id, 'attemptId': accepted['attemptId'],
                  'recordingId': final['renditions'][-1]['id'],
                  'acceptedPath': accepted_path, 'acceptedKey': accepted_key, 'acceptedCommand': accepted_body,
                  'checks': ['durable block', 'upload without generation bypass', 'read-only expiry',
                             'deliberate no-status retry', 'malformed/timeout status remains unknown',
                             'explicit successful status recovery', 'receipt replay before current block',
                             'capacity rejection preserves acknowledgement and notes', 'exact media retention']}))
