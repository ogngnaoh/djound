#!/usr/bin/env python3
"""Fixture acceptance checks. This runs real app/SQL/media, with explicit sample output."""
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
assert workspace['activeExplorationId'] is None
assert workspace['canSubmitWithAcknowledgement'] is False and workspace['unknownOutcome'] is None
assert workspace['submissionAllowance'] is None
assert workspace['inputPolicy']['providerMaxDurationMs'] is None
assert workspace['inputPolicy']['maxAssembledCodePoints'] == 16000
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
assert workspace['activeExplorationId'] == source['id']
assert not workspace['canSubmit'] and not workspace['canCreateExploration']
assert workspace['canSubmitWithAcknowledgement'] is False
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
assert workspace['activeExplorationId'] is None

# A temporary rejection must not be cached as an accepted receipt.
status, later_receipt = submit(loser)
expect(202, status, 'deliberate resubmission after temporary active-slot rejection')
assert not later_receipt['replayed']
await_terminal(later_receipt['attemptId'])
assert len(detail(sources[loser]['id'])['renditions']) == 1
# Keep acts on exact retained bytes and refinements always anchor the original.
keep_path = '/api/recordings/' + result['id']
status, kept = json_request(keep_path, 'PATCH', {'kept': True})
expect(200, status, 'Keep rendition')
assert kept['kept'] is True and kept['sha256'] == result['sha256']
status, kept_again = json_request(keep_path, 'PATCH', {'kept': True})
expect(200, status, 'idempotent Keep')
assert kept_again == kept
status, library = json_request('/api/explorations')
summary = next(item for item in library['items'] if item['id'] == source['id'])
assert [recording['id'] for recording in summary['keptRecordings']] == [result['id']]
assert summary['renditionCount'] == 1
status, original_problem = json_request('/api/recordings/' + source['original']['id'], 'PATCH', {'kept': True})
expect(422, status, 'original cannot be kept')
assert original_problem['code'] == 'ORIGINAL_NOT_KEEPABLE'
for invalid_body in ({}, {'kept': None}):
    status, _ = json_request(keep_path, 'PATCH', invalid_body)
    expect(400, status, 'kept flag must be explicit')
status, _, played_kept = request(kept['mediaUrl'])
assert played_kept == output
assert detail(source['id'])['latestAttempt']['id'] == receipt['attemptId']

before_refine = detail(source['id'])
refinement_text = 'Less busy keys, warmer bass, and softer accents.'
refinement_command = {
    'text': refinement_text,
    'basisRecordingId': result['id'],
    'expectedSourceRevision': before_refine['sourceRevision'],
    'expectedDraftRevision': before_refine['draft']['revision'],
}
status, refinement_receipt = json_request(path, 'POST', refinement_command, 'refine-' + uuid.uuid4().hex)
expect(202, status, 'refinement acceptance')
status, refinement_attempt = json_request('/api/attempts/' + refinement_receipt['attemptId'])
assert refinement_attempt['request']['basisRecordingId'] == result['id']
assert refinement_attempt['request']['sourceRecordingId'] == source['original']['id']
assert refinement_attempt['request']['inheritedDirection'] == result['direction']
assert refinement_attempt['request']['text'] == refinement_text
assert refinement_text in refinement_attempt['request']['direction']
pinned_words = 'Keep this separate idea pinned to the earlier version.'
status, pinned_draft = json_request('/api/explorations/' + source['id'] + '/draft', 'PUT', {
    'text': pinned_words,
    'basisRecordingId': result['id'],
    'expectedDraftRevision': refinement_receipt['nextDraftRevision'],
})
expect(200, status, 'next refinement draft')
refinement_done = await_terminal(refinement_receipt['attemptId'])
after_refine = detail(source['id'])
assert len(after_refine['renditions']) == 2
assert after_refine['draft'] == pinned_draft
assert after_refine['draft']['basisRecordingId'] == result['id']
assert after_refine['original']['id'] == source['original']['id']
assert not after_refine['canReplaceSource']
new_result = next(recording for recording in after_refine['renditions'] if recording['id'] == refinement_done['resultRecordingId'])
assert new_result['id'] != result['id'] and new_result['provenance'] == 'FIXTURE'
assert new_result['sha256'] in OUTPUT_HASHES
assert new_result['direction'] == refinement_done['request']['direction']
status, _, still_exact = request(result['mediaUrl'])
assert still_exact == output
status, unkept = json_request(keep_path, 'PATCH', {'kept': False})
expect(200, status, 'unkeep rendition')
assert unkept['kept'] is False
status, _, retained = request(result['mediaUrl'])
assert retained == output, 'Unkeep must not delete or regenerate the recording'
status, kept = json_request(keep_path, 'PATCH', {'kept': True})
expect(200, status, 'keep exact version again')
status, library = json_request('/api/explorations')
summary = next(item for item in library['items'] if item['id'] == source['id'])
assert summary['renditionCount'] == 2
assert [recording['id'] for recording in summary['keptRecordings']] == [result['id']]
assert detail(source['id'])['latestAttempt']['id'] == refinement_receipt['attemptId']

print(json.dumps({
    'result': 'PASS', 'explorationId': source['id'], 'attemptId': receipt['attemptId'],
    'recordingId': result['id'], 'acceptedKey': keys[winner], 'acceptedCommand': commands[winner],
    'checks': ['real concurrent admission', 'immutable literal request', 'one active across explorations',
               'next draft remains separate', 'receipt replay preserves later words', 'changed payload conflict',
               'active upload/source guards', 'normal publication of predefined sample', 'durable fixture provenance',
               'original bytes unchanged', 'one output after replay', 'temporary rejection not cached',
               'explicit idempotent Keep', 'exact Library kept references', 'original Keep rejection',
               'refinement ancestry with original source', 'next draft stays pinned',
               'unkeep retains exact bytes without generation'],
}, indent=2))
