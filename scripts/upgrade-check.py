"""Real old/new application upgrade probe; invoked only by verify-upgrade.sh."""
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid

BASE = os.environ['DJOUND_TEST_BASE_URL']
assert BASE.startswith('http://127.0.0.1:')
ROOT = Path(__file__).resolve().parents[1]


def request(path, method='GET', data=None, headers=None):
    with urllib.request.urlopen(urllib.request.Request(
            BASE + path, method=method, data=data, headers=headers or {}), timeout=20) as response:
        return response.read()


def multipart(path, method, filename, field, value, key):
    boundary = 'upgrade-' + uuid.uuid4().hex
    audio = (ROOT / 'fixtures/audio' / filename).read_bytes()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            'Content-Type: audio/wav\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
    return json.loads(request(path, method, body, {
        'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Idempotency-Key': key}))


def matches_old(old, new):
    # Additive response fields are compatible; existing values must survive exactly.
    if isinstance(old, dict):
        assert isinstance(new, dict)
        for key, value in old.items():
            matches_old(value, new[key])
    elif isinstance(old, list):
        assert len(old) == len(new)
        for before, after in zip(old, new):
            matches_old(before, after)
    else:
        assert old == new, (old, new)


phase, snapshot_path = sys.argv[1:]
if phase == 'prepare':
    upload_key, replace_key = str(uuid.uuid4()), str(uuid.uuid4())
    uploaded = multipart('/api/explorations', 'POST', 'source-study.wav',
                         'initialDraft', 'Preserve my first words.', upload_key)
    path = '/api/explorations/' + uploaded['explorationId']
    original = json.loads(request(path))['original']
    replaced = multipart(path + '/source', 'PUT', 'sample-warm.wav',
                         'expectedSourceRevision', 1, replace_key)
    detail = json.loads(request(path))
    request(path + '/draft', 'PUT', json.dumps({
        'text': 'Preserve these newer words through an upgrade 🎵',
        'basisRecordingId': detail['original']['id'],
        'expectedDraftRevision': detail['draft']['revision'],
    }).encode(), {'Content-Type': 'application/json'})
    snapshot = dict(path=path, detail=json.loads(request(path)), original=original,
                    uploaded=uploaded, replaced=replaced, uploadKey=upload_key, replaceKey=replace_key)
    Path(snapshot_path).write_text(json.dumps(snapshot))
elif phase == 'verify':
    saved = json.loads(Path(snapshot_path).read_text())
    path = saved['path']
    matches_old(saved['detail'], json.loads(request(path)))
    for recording in (saved['original'], saved['detail']['original']):
        assert hashlib.sha256(request(recording['mediaUrl'])).hexdigest() == recording['sha256']
    upload = multipart('/api/explorations', 'POST', 'source-study.wav',
                       'initialDraft', 'Preserve my first words.', saved['uploadKey'])
    replacement = multipart(path + '/source', 'PUT', 'sample-warm.wav',
                            'expectedSourceRevision', 1, saved['replaceKey'])
    matches_old({**saved['uploaded'], 'replayed': True}, upload)
    matches_old({**saved['replaced'], 'replayed': True}, replacement)
    matches_old(saved['detail'], json.loads(request(path)))
else:
    raise SystemExit('Expected prepare or verify')
