#!/usr/bin/env python3
"""Kill real owned JVMs at lifecycle boundaries; never submit to a music provider."""
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('DJOUND_CHECK_PORT', '18091'))
BASE = f'http://127.0.0.1:{PORT}'
OUT = Path(tempfile.mkdtemp(prefix='restart.', dir=ROOT / '.runtime/checks'))
JAR = OUT / 'application.jar'
shutil.copyfile(ROOT / 'server/target/djound-0.1.0.jar', JAR)
SOURCE = (ROOT / 'fixtures/audio/source-study.wav').read_bytes()
OUTPUT = (ROOT / 'fixtures/audio/sample-warm.wav').read_bytes()
OUTPUT_HASH = hashlib.sha256(OUTPUT).hexdigest()
with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(('127.0.0.1', PORT))


def docker(*args):
    return subprocess.run(['docker', 'compose', 'exec', '-T', 'db', *args],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def request(path, method='GET', body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def api(path, method='GET', body=None, key=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    status, data = request(path, method, None if body is None else json.dumps(body).encode(), headers)
    parsed = json.loads(data)
    assert status == expected, (path, status, parsed)
    return parsed


def upload():
    boundary = 'crash-' + uuid.uuid4().hex
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="initialDraft"\r\n\r\nOriginal saved direction.\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="restart-study.wav"\r\n'
            'Content-Type: audio/wav\r\n\r\n').encode() + SOURCE + f'\r\n--{boundary}--\r\n'.encode()
    status, body = request('/api/explorations', 'POST', data, {
        'Content-Type': 'multipart/form-data; boundary=' + boundary,
        'Idempotency-Key': 'source-' + uuid.uuid4().hex,
    })
    assert status == 201, body
    return api('/api/explorations/' + json.loads(body)['explorationId'])


def eventually(check, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('Timed out waiting for ' + str(check))


def terminal(attempt_id):
    def check():
        value = api('/api/attempts/' + attempt_id)
        return value if value['state'] in ('SUCCEEDED', 'FAILED') else None
    return eventually(check)


class Installation:
    def __init__(self, name):
        self.name = name
        self.db = 'djound_crash_' + uuid.uuid4().hex[:20]
        self.directory = OUT / name
        self.directory.mkdir()
        self.data = self.directory / 'data'
        self.process = None
        self.log = None
        self.starts = 0
        docker('createdb', '-U', 'djound', self.db)

    def sql(self, statement):
        return docker('psql', '-U', 'djound', '-d', self.db, '-At', '-v', 'ON_ERROR_STOP=1', '-c', statement)

    def start(self, checkpoint=None, result_url=None, mode="FIXTURE"):
        assert self.process is None
        self.starts += 1
        env = dict(os.environ, DJOUND_DB_URL=f'jdbc:postgresql://127.0.0.1:55432/{self.db}',
                   DJOUND_DATA_DIR=str(self.data), DJOUND_PORT=str(PORT), DJOUND_MODE=mode,
                   DJOUND_FIXTURE_DELAY_MS='800', DJOUND_FIXTURE_REVIEW_CONTROLS='true',
                   DJOUND_ALLOWED_ORIGINS=BASE)
        env.pop('DJOUND_FIXTURE_CHECKPOINT', None)
        env.pop('DJOUND_FIXTURE_RESULT_URL', None)
        if checkpoint:
            env['DJOUND_FIXTURE_CHECKPOINT'] = checkpoint
        if result_url:
            env['DJOUND_FIXTURE_RESULT_URL'] = result_url
        self.log = open(self.directory / f'app-{self.starts}.log', 'wb')
        self.process = subprocess.Popen(['java', '-Djdk.httpclient.redirects.retrylimit=1', '-Djdk.httpclient.disableRetryConnect=true', '-Djdk.httpclient.enableAllMethodRetry=false', '-jar', str(JAR)], cwd=ROOT, env=env,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            assert self.process.poll() is None, f'App stopped; inspect {self.directory}'
            try:
                if request('/api/workspace')[0] == 200:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.15)
        raise AssertionError(f'App did not start; inspect {self.directory}')

    def stop(self, crash=False):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.kill() if crash else self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None
        self.log.close()

    def checkpoint(self, name, attempt_id):
        marker = self.data / 'checkpoints/reached.json'
        pid = self.process.pid
        def check():
            if not marker.exists():
                return None
            value = json.loads(marker.read_text())
            if value.get('processId') != pid:
                return None
            assert value['schemaVersion'] == 1 and value['checkpoint'] == name, value
            assert value['attemptId'] == attempt_id and value['attemptVersion'] >= 1, value
            return value
        return eventually(check)

    def close(self):
        self.stop()
        docker('dropdb', '-U', 'djound', '--force', self.db)


@contextmanager
def installation(name):
    instance = Installation(name)
    try:
        yield instance
    finally:
        instance.close()


# Fully authored local HTTP fixtures. No remote network, model, or paid service.
requests_seen = Counter()
unsupported_buffer = io.BytesIO()
with wave.open(unsupported_buffer, 'wb') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(3)
    wav.setframerate(44100)
    wav.writeframes(b'\0' * (44100 * 3 * 2))
UNSUPPORTED = unsupported_buffer.getvalue()


class Results(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        requests_seen[self.path] += 1
        body = OUTPUT
        content_type = 'audio/wav'
        if self.path == '/html':
            body, content_type = b'<html>upstream failed</html>', 'text/html'
        elif self.path == '/unsupported':
            body = UNSUPPORTED
        elif self.path == '/truncated':
            body = OUTPUT[:len(OUTPUT)//2]
        elif self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/valid')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        length = len(body)
        if self.path == '/oversized':
            length = 64 * 1024 * 1024 + 1
        elif self.path == '/interrupted':
            length += 1024
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if self.path != '/oversized-stream':
            self.send_header('Content-Length', str(length))
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            if self.path == '/stalled':
                self.wfile.write(body[:1024])
                self.wfile.flush()
                time.sleep(15)  # Deliberately exceeds the app's ten-second body deadline.
                self.wfile.write(body[1024:])
            elif self.path == '/oversized-stream':
                block = b'\0' * (1024 * 1024)
                for _ in range(65):
                    self.wfile.write(block)
            else:
                self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


server = ThreadingHTTPServer(('127.0.0.1', 0), Results)
threading.Thread(target=server.serve_forever, daemon=True).start()
RESULT_BASE = f'http://127.0.0.1:{server.server_port}'


def prepare():
    detail = upload()
    command = {'text': 'Keep the recognizable melody with warm instrumental piano.',
               'basisRecordingId': detail['original']['id'],
               'expectedSourceRevision': detail['sourceRevision'],
               'expectedDraftRevision': detail['draft']['revision']}
    key = 'crash-create-' + uuid.uuid4().hex
    receipt = api('/api/explorations/' + detail['id'] + '/requests', 'POST', command, key, 202)
    draft = api('/api/explorations/' + detail['id'] + '/draft', 'PUT', {
        'text': 'Separate next notes survive the process ending.',
        'basisRecordingId': detail['original']['id'],
        'expectedDraftRevision': receipt['nextDraftRevision'],
    })
    return detail, command, key, receipt, draft


def retained(detail, draft):
    current = api('/api/explorations/' + detail['id'])
    assert current['draft'] == draft
    assert current['original'] == detail['original']
    status, audio = request(detail['original']['mediaUrl'])
    assert status == 200 and audio == SOURCE
    for recording in current['renditions']:
        status, audio = request(recording['mediaUrl'])
        assert status == 200 and hashlib.sha256(audio).hexdigest() == recording['sha256']
    return current


def operations(instance, attempt_id):
    assert attempt_id.startswith('attempt_') and "'" not in attempt_id
    return int(instance.sql(f"SELECT count(*) FROM fixture_operation WHERE attempt_id='{attempt_id}'"))


def crash_scenario(checkpoint):
    with installation(checkpoint.lower()) as instance:
        instance.start(checkpoint, RESULT_BASE + '/valid')
        detail, command, key, receipt, draft = prepare()
        marker = instance.checkpoint(checkpoint, receipt['attemptId'])
        # SIGKILL targets only this harness's own Popen child.
        instance.stop(crash=True)
        count_at_crash = operations(instance, receipt['attemptId'])
        assert count_at_crash == (0 if checkpoint == 'BEFORE_SUBMIT' else 1)
        (instance.directory / 'checkpoint.json').write_text(json.dumps(marker, indent=2))
        journal_text = instance.sql(f"SELECT row_to_json(j) FROM publication_journal j WHERE attempt_id='{receipt['attemptId']}'")
        journal = json.loads(journal_text) if journal_text else None
        if journal:
            (instance.directory / 'journal-at-crash.json').write_text(json.dumps(journal, indent=2))
        orphan_media = instance.data / 'media' / (str(uuid.uuid4()) + '.audio')
        orphan_temp = instance.data / 'temporary' / ('upload-orphan-' + uuid.uuid4().hex + '.tmp')
        untouched = instance.data / 'temporary' / 'unowned-note.txt'
        orphan_media.write_bytes(b'unpublished media')
        orphan_temp.write_bytes(b'interrupted upload')
        untouched.write_text('Cleanup must not delete arbitrary files.')
        instance.start(mode='UNCONFIGURED' if checkpoint == 'AFTER_HANDLE' else 'FIXTURE',
                       result_url=RESULT_BASE + '/valid' if checkpoint == 'BEFORE_SUBMIT' else None)
        outcome = terminal(receipt['attemptId'])
        current = retained(detail, draft)
        assert not orphan_media.exists() and not orphan_temp.exists()
        assert untouched.read_text() == 'Cleanup must not delete arbitrary files.'
        if journal and current['renditions']:
            assert current['renditions'][0]['id'] == journal['recording_id']
            assert (instance.data / 'media' / journal['storage_key']).exists()
        replay = api('/api/explorations/' + detail['id'] + '/requests', 'POST', command, key, 202)
        assert replay['replayed'] and replay['attemptId'] == receipt['attemptId']
        assert operations(instance, receipt['attemptId']) == 1
        assert api('/api/workspace')['activeAttemptId'] is None
        if checkpoint == 'AFTER_SUBMITTING':
            assert outcome['state'] == 'FAILED' and outcome['outcome']['code'] == 'OUTCOME_UNKNOWN', outcome
            assert not current['renditions']
            workspace = api('/api/workspace')
            assert workspace['unknownOutcome']['attemptId'] == receipt['attemptId']
            assert not workspace['canSubmit'] and workspace['canSubmitWithAcknowledgement']
            # A rejected deliberate command cannot retire uncertainty.
            rejected = dict(command, expectedDraftRevision=command['expectedDraftRevision'],
                            acknowledgedUnknownAttemptId=receipt['attemptId'])
            api('/api/explorations/' + detail['id'] + '/requests', 'POST', rejected,
                'rejected-' + uuid.uuid4().hex, 409)
            assert api('/api/workspace')['unknownOutcome']['attemptId'] == receipt['attemptId']
            retry_body = {'expectedPreviousAttemptId': receipt['attemptId'],
                          'acknowledgedUnknownAttemptId': receipt['attemptId']}
            retry_key = 'acknowledged-' + uuid.uuid4().hex
            retry_path = '/api/requests/' + receipt['requestId'] + '/retries'
            retried = api(retry_path, 'POST', retry_body, retry_key, 202)
            assert api('/api/workspace')['unknownOutcome'] is None
            assert terminal(retried['attemptId'])['state'] == 'SUCCEEDED'
            assert api('/api/attempts/' + receipt['attemptId'])['outcome']['code'] == 'OUTCOME_UNKNOWN'
            assert operations(instance, receipt['attemptId']) == 1
            assert operations(instance, retried['attemptId']) == 1
            assert retained(detail, draft)['renditions'][0]['sha256'] == OUTPUT_HASH
            assert api(retry_path, 'POST', retry_body, retry_key, 202)['replayed']
        else:
            assert outcome['state'] == 'SUCCEEDED', outcome
            assert len(current['renditions']) == 1
            assert current['renditions'][0]['sha256'] == OUTPUT_HASH
            assert current['renditions'][0]['attemptId'] == receipt['attemptId']
        # Another complete restart proves the terminal state is stable and no operation repeats.
        before = api('/api/explorations/' + detail['id'])
        instance.stop()
        instance.start()
        assert retained(detail, draft) == before
        assert operations(instance, receipt['attemptId']) == 1
        print('PASS: crash recovery ' + checkpoint, flush=True)


def invalid_result_scenario(name):
    with installation('download-' + name) as instance:
        instance.start(result_url=RESULT_BASE + '/' + name)
        detail, command, key, receipt, draft = prepare()
        outcome = terminal(receipt['attemptId'])
        assert outcome['state'] == 'FAILED', outcome
        assert outcome['outcome']['code'] in ('OUTPUT_INVALID', 'MEDIA_STORAGE_FAILED'), outcome
        assert retained(detail, draft)['renditions'] == []
        assert operations(instance, receipt['attemptId']) == 1
        assert api('/api/workspace')['activeAttemptId'] is None
        instance.stop()
        instance.start()
        assert retained(detail, draft)['renditions'] == []
        assert operations(instance, receipt['attemptId']) == 1
        print('PASS: rejected result ' + name, flush=True)


if __name__ == '__main__':
    checkpoints = ['BEFORE_SUBMIT', 'AFTER_SUBMITTING', 'AFTER_HANDLE', 'DURING_DOWNLOAD',
                   'AFTER_MEDIA_LINK', 'BEFORE_PUBLICATION_COMMIT', 'AFTER_PUBLICATION_COMMIT']
    try:
        for checkpoint in checkpoints:
            crash_scenario(checkpoint)
        for name in ['html', 'oversized', 'oversized-stream', 'unsupported', 'interrupted', 'stalled', 'truncated', 'redirect']:
            invalid_result_scenario(name)
        result = {'result': 'PASS', 'checkpoints': checkpoints, 'resultRequests': dict(requests_seen),
                  'evidence': str(OUT)}
        (OUT / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)
    finally:
        server.shutdown()
        server.server_close()
