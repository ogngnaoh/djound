#!/usr/bin/env python3
"""S01 black-box checks against a real isolated Djound/PostgreSQL installation."""
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


def problem(status, body, allowed):
    assert status in allowed, (status, body[:500])
    result = json.loads(body)
    assert all(field in result for field in ('type', 'title', 'status', 'detail', 'code', 'permittedActions')), result
    assert result['status'] == status
    return result


status, _, body = request('/api/workspace')
expect(200, status, 'workspace')
workspace = json.loads(body)
assert workspace['configuration'] == 'UNCONFIGURED' and not workspace['canSubmit']
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
                             'packaged deep route', 'typed API 404']}, indent=2))
