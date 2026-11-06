#!/usr/bin/env python3
"""Black-box checks against a real isolated Djound/PostgreSQL installation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import struct
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get('DJOUND_TEST_BASE_URL', '').rstrip('/')
if not BASE or os.environ.get('DJOUND_TEST_ALLOW_WRITE') != '1':
    raise SystemExit('Set DJOUND_TEST_BASE_URL and DJOUND_TEST_ALLOW_WRITE=1 for an isolated test installation.')
if not BASE.startswith(('http://127.0.0.1:', 'http://localhost:')):
    raise SystemExit('Acceptance tests only target a loopback server.')
ROOT = Path(__file__).resolve().parents[1]
AUDIO = (ROOT / 'fixtures/audio/source-study.wav').read_bytes()


def request(path, method='GET', data=None, headers=None):
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def expect(status, actual, message):
    assert actual == status, f'{message}: expected {status}, received {actual}'


def multipart(audio, draft='Gentle keys and a warm bass.', name='evening.wav'):
    boundary = 'djound-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="initialDraft"\r\n\r\n{draft}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
    return body, {'Content-Type': 'multipart/form-data; boundary=' + boundary}


def upload(audio, key, draft='Gentle keys and a warm bass.', extra_headers=None):
    body, headers = multipart(audio, draft)
    headers['Idempotency-Key'] = key
    headers.update(extra_headers or {})
    return request('/api/explorations', 'POST', body, headers)


def save_draft(exploration_id, text, basis, revision):
    return request('/api/explorations/' + exploration_id + '/draft', 'PUT',
                   json.dumps({'text': text, 'basisRecordingId': basis, 'expectedDraftRevision': revision}).encode(),
                   {'Content-Type': 'application/json'})


def replace_source(exploration_id, audio, revision, key):
    boundary = 'djound-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="expectedSourceRevision"\r\n\r\n{revision}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="replacement.wav"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
    return request('/api/explorations/' + exploration_id + '/source', 'PUT', body,
                   {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Idempotency-Key': key})


def problem(status, body, allowed):
    assert status in allowed, (status, body[:500])
    result = json.loads(body)
    assert all(field in result for field in ('type', 'title', 'status', 'detail', 'code', 'permittedActions')), result
    assert result['status'] == status
    return result


# A browser refresh asks the packaged server for the editor URL directly.
status, _, index_html = request('/')
expect(200, status, 'studio entry point')
for view in ('melody', 'notes'):
    status, headers, body = request(f'/remix/deep-link-check/{view}')
    expect(200, status, f'{view} deep link')
    assert headers.get_content_type() == 'text/html' and body == index_html


status, _, body = request('/api/workspace')
expect(200, status, 'workspace')
workspace = json.loads(body)
assert workspace['configuration'] == 'UNCONFIGURED' and not workspace['canSubmit']
assert workspace['canSubmitWithAcknowledgement'] is False and workspace['unknownOutcome'] is None
assert workspace['eligibility'] == 'UNKNOWN' and not workspace['supportsStatusCheck'] and not workspace['canRecheck']
assert workspace['providerResetAt'] is None and workspace['nextProbeAt'] is None
assert workspace['submissionAllowance'] is None
assert workspace['inputPolicy']['providerMaxDurationMs'] is None
assert workspace['inputPolicy']['maxAssembledCodePoints'] == 16000
assert workspace['activeAttemptId'] is None and workspace['activeExplorationId'] is None
assert workspace['inputPolicy']['instructionOverheadCodePoints'] > 0
assert workspace['fixtureControlsEnabled'] is False
status, _, body = request('/api/fixture/controls')
problem(status, body, {404})
status, _, body = request('/api/fixture/controls/next-attempt', 'PUT', json.dumps({'outcome': 'FAILURE'}).encode(),
                         {'Content-Type': 'application/json'})
problem(status, body, {404})
status, _, body = request('/api/fixture/controls/availability', 'PUT', json.dumps({'scenario': 'READY'}).encode(),
                         {'Content-Type': 'application/json'})
problem(status, body, {404})
status, _, body = request('/api/workspace/availability/recheck', 'POST')
assert problem(status, body, {503})['code'] == 'PROVIDER_NOT_CONFIGURED'
assert workspace['inputPolicy']['maxBytes'] == 64 * 1024 * 1024
status, _, body = request('/api/explorations')
expect(200, status, 'library')
before = len(json.loads(body)['items'])

key = 'acceptance-' + uuid.uuid4().hex
status, _, body = upload(AUDIO, key)
expect(201, status, 'upload')
receipt = json.loads(body)
assert receipt['replayed'] is False
exploration_id = receipt['explorationId']
media_id = receipt['originalRecordingId']
status, _, body = upload(AUDIO, key)
expect(201, status, 'receipt replay')
replay = json.loads(body)
assert replay['replayed'] is True
assert replay['explorationId'] == exploration_id and replay['originalRecordingId'] == media_id
status, _, body = upload(AUDIO, key, 'Changed words under the same key')
assert problem(status, body, {409})['code'] == 'IDEMPOTENCY_CONFLICT'

race_key = 'concurrent-' + uuid.uuid4().hex
with ThreadPoolExecutor(max_workers=2) as executor:
    results = list(executor.map(lambda _: upload(AUDIO, race_key), range(2)))
assert all(result[0] == 201 for result in results), [(r[0], r[2][:400]) for r in results]
race_receipts = [json.loads(result[2]) for result in results]
assert race_receipts[0]['explorationId'] == race_receipts[1]['explorationId']
assert sorted(r['replayed'] for r in race_receipts) == [False, True]

status, _, body = request('/api/explorations/' + exploration_id)
expect(200, status, 'detail')
detail = json.loads(body)
assert detail['draft']['text'] == 'Gentle keys and a warm bass.'
assert detail['original']['sha256'] == hashlib.sha256(AUDIO).hexdigest()
assert detail['original']['durationMs'] == 12000
assert detail['original']['provenance'] == 'ORIGINAL' and not detail['renditions']
media = detail['original']['mediaUrl']
status, headers, body = request(media)
expect(200, status, 'media')
assert body == AUDIO and int(headers['Content-Length']) == len(AUDIO)
status, headers, body = request(media, 'HEAD')
expect(200, status, 'media HEAD')
assert body == b'' and int(headers['Content-Length']) == len(AUDIO)
status, headers, body = request(media, headers={'Range': 'bytes=100-199'})
expect(206, status, 'media range')
assert body == AUDIO[100:200] and headers['Content-Range'] == f'bytes 100-199/{len(AUDIO)}'
status, _, body = request(media, headers={'Range': f'bytes={len(AUDIO)+99}-'})
expect(416, status, 'out of bounds range')

for audio, label in [(b'This is not audio.', 'corrupt'), (b'', 'empty')]:
    status, _, body = upload(audio, 'invalid-' + uuid.uuid4().hex)
    problem(status, body, {400, 415, 422})
# Header claims a disallowed rate and byte rate; sample content remains PCM.
invalid_rate = bytearray(AUDIO)
struct.pack_into('<I', invalid_rate, 24, 22050)
struct.pack_into('<I', invalid_rate, 28, 44100)
status, _, body = upload(bytes(invalid_rate), 'rate-' + uuid.uuid4().hex)
problem(status, body, {415, 422})
status, _, body = upload(AUDIO, 'text-' + uuid.uuid4().hex, '\U0001f3b5' * 4001)
problem(status, body, {400, 422})
status, _, body = upload(AUDIO, 'origin-' + uuid.uuid4().hex, extra_headers={'Origin': 'https://untrusted.example'})
problem(status, body, {403})
status, _, body = upload(AUDIO, 'origin-' + uuid.uuid4().hex, extra_headers={'Origin': 'null'})
problem(status, body, {403})

# Compare-and-set editing and immutable idempotent source replacement.
assert detail['canReplaceSource'] is True
saved_text = 'Keep these words when the source changes.'
status, _, body = save_draft(exploration_id, saved_text, media_id, detail['draft']['revision'])
expect(200, status, 'save draft')
saved_draft = json.loads(body)
assert saved_draft['text'] == saved_text and saved_draft['revision'] == detail['draft']['revision'] + 1
status, _, body = save_draft(exploration_id, 'Stale tab words', media_id, detail['draft']['revision'])
assert problem(status, body, {409})['code'] == 'STALE_DRAFT'
status, _, body = replace_source(exploration_id, b'not audio', 1, 'replace-invalid-' + uuid.uuid4().hex)
problem(status, body, {415, 422})
status, _, body = request('/api/explorations/' + exploration_id)
unchanged = json.loads(body)
assert unchanged['draft']['text'] == saved_text and unchanged['original']['id'] == media_id
replacement = (ROOT / 'fixtures/audio/sample-warm.wav').read_bytes()
replacement_key = 'replace-' + uuid.uuid4().hex
status, _, body = replace_source(exploration_id, replacement, 1, replacement_key)
expect(200, status, 'replace source')
replacement_receipt = json.loads(body)
assert replacement_receipt['sourceRevision'] == 2 and replacement_receipt['draftRevision'] == saved_draft['revision'] + 1
assert not replacement_receipt['replayed']
status, _, body = request('/api/explorations/' + exploration_id)
replaced = json.loads(body)
assert replaced['draft']['text'] == saved_text
assert replaced['draft']['basisRecordingId'] == replaced['original']['id']
assert replaced['original']['sha256'] == hashlib.sha256(replacement).hexdigest()
status, _, body = request(media)
expect(200, status, 'retained old original')
assert body == AUDIO
newer_text = 'Newer words must survive an old replacement receipt.'
status, _, body = save_draft(exploration_id, newer_text, replaced['original']['id'], replaced['draft']['revision'])
expect(200, status, 'save after replacement')
newer_draft = json.loads(body)
status, _, body = replace_source(exploration_id, replacement, 1, replacement_key)
expect(200, status, 'replace replay after newer draft')
replaced_replay = json.loads(body)
assert replaced_replay['replayed'] and replaced_replay['draftRevision'] == replacement_receipt['draftRevision']
status, _, body = request('/api/explorations/' + exploration_id)
current = json.loads(body)
assert current['draft'] == newer_draft and current['draft']['text'] == newer_text
status, _, body = replace_source(exploration_id, replacement, 2, replacement_key)
assert problem(status, body, {409})['code'] == 'IDEMPOTENCY_CONFLICT'
status, _, body = replace_source(exploration_id, replacement, 1, 'stale-replace-' + uuid.uuid4().hex)
assert problem(status, body, {409})['code'] == 'SOURCE_CHANGED'

# Normal mode must remain unavailable, with no accepted request or draft consumption.
status, _, body = request('/api/explorations/' + exploration_id + '/requests', 'POST',
                         json.dumps({'text': 'Do not run generation in unconfigured mode.',
                                     'basisRecordingId': current['original']['id'],
                                     'expectedSourceRevision': current['sourceRevision'],
                                     'expectedDraftRevision': current['draft']['revision']}).encode(),
                         {'Content-Type': 'application/json', 'Idempotency-Key': 'unconfigured-' + uuid.uuid4().hex})
assert problem(status, body, {503})['code'] == 'PROVIDER_NOT_CONFIGURED'
status, _, body = request('/api/explorations/' + exploration_id)
unconfigured = json.loads(body)
assert unconfigured['latestAttempt'] is None and unconfigured['renditions'] == []
assert unconfigured['draft'] == current['draft']

status, _, body = request('/api/explorations')
expect(200, status, 'library after failed uploads')
assert len(json.loads(body)['items']) == before + 2, 'Rejected uploads created library entries or replay duplicated one'
status, headers, body = request('/remix/' + exploration_id)
expect(200, status, 'packaged deep route')
assert 'text/html' in headers['Content-Type'] and b'id="root"' in body
status, headers, body = request('/api/not-a-route')
expect(404, status, 'unknown API route')
assert 'application/problem+json' in headers['Content-Type']
problem(status, body, {404})
print(json.dumps({'result': 'PASS', 'explorationId': exploration_id, 'recordingId': media_id,
                  'checks': ['real upload', 'receipt replay', 'concurrent replay', 'conflict', 'metadata/hash identity', 'HEAD', '206/416',
                             'corrupt/empty/rate/text rejection', 'origin policy', 'no failed library entries',
                             'packaged deep route', 'typed API 404', 'draft CAS conflict', 'source replacement/rebind',
                             'old original retained', 'replacement replay preserves newer words', 'stale source rejection', 'unconfigured request rejects without consuming draft', 'review controls unavailable in normal mode']}, indent=2))
