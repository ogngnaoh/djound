#!/usr/bin/env bash
# Each run owns a separate database and data directory; never uses the review library.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
check_mode="${1:-all}"
case "$check_mode" in all|api|fixture|listening|recovery|unknown|capacity|interaction|live-policy|upload|upload-local) ;; *) echo 'Usage: verify-installation.sh [all|api|fixture|listening|recovery|unknown|capacity|interaction|live-policy|upload|upload-local]' >&2; exit 2 ;; esac
mkdir -p .runtime/checks
run_dir="$(mktemp -d "$DJOUND_ROOT/.runtime/checks/run.XXXXXX")"
test_db="djound_check_$$_${RANDOM}"
test_port="${DJOUND_CHECK_PORT:-18087}"
python3 - "$test_port" <<'PY'
import socket, sys
with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(('127.0.0.1', int(sys.argv[1])))
PY
app_pid=''
database_created=0
cleanup() {
  if [[ -n "$app_pid" ]]; then kill "$app_pid" 2>/dev/null || true; wait "$app_pid" 2>/dev/null || true; fi
  if [[ "$database_created" == 1 ]]; then docker compose exec -T db dropdb -U djound --force "$test_db" >/dev/null; fi
}
trap cleanup EXIT
docker compose up -d --pull never --wait db
docker compose exec -T db createdb -U djound "$test_db"
database_created=1
export DJOUND_DB_URL="jdbc:postgresql://127.0.0.1:55432/$test_db"
export DJOUND_DATA_DIR="$run_dir/data"
export DJOUND_PORT="$test_port"
export DJOUND_ALLOWED_ORIGINS="http://127.0.0.1:$test_port"
export DJOUND_TEST_BASE_URL="http://127.0.0.1:$test_port"
export DJOUND_TEST_ALLOW_WRITE=1
export DJOUND_MODE=UNCONFIGURED
java_options=(-Xmx768m -Djdk.httpclient.redirects.retrylimit=1 -Djdk.httpclient.disableRetryConnect=true -Djdk.httpclient.enableAllMethodRetry=false)
if [[ "$check_mode" == live-policy ]]; then
  export DJOUND_MODE=LIVE
fi
export DJOUND_FIXTURE_REVIEW_CONTROLS=false
unset DJOUND_FIXTURE_COOLDOWN_MS
if [[ "$check_mode" == fixture || "$check_mode" == listening || "$check_mode" == recovery || "$check_mode" == unknown || "$check_mode" == capacity || "$check_mode" == interaction || "$check_mode" == upload ]]; then
  export DJOUND_MODE=FIXTURE
  export DJOUND_FIXTURE_DELAY_MS=8000
  export DJOUND_FIXTURE_COOLDOWN_MS=6000
  export DJOUND_FIXTURE_REVIEW_CONTROLS=true
fi
export DJOUND_SCREENSHOT_DIR="$(mktemp -d /tmp/djound-qa.XXXXXX)"
shopt -s nullglob
jars=(server/target/djound-*.jar)
[[ ${#jars[@]} -eq 1 ]] || { echo 'Package the app before verification.' >&2; exit 1; }
cp "${jars[0]}" "$run_dir/application.jar"
test_jar="$run_dir/application.jar"
start_app() {
  java "${java_options[@]}" -jar "$test_jar" >> "$run_dir/app.log" 2>&1 &
  app_pid=$!
  for ((i=0;i<60;i++)); do
    kill -0 "$app_pid" 2>/dev/null || { tail -60 "$run_dir/app.log"; exit 1; }
    if curl --silent --fail "$DJOUND_TEST_BASE_URL/api/workspace" > "$run_dir/workspace.json"; then return; fi
    sleep 0.5
  done
  echo "App did not start; inspect $run_dir/app.log" >&2
  exit 1
}
start_app
if [[ "$check_mode" == upload || "$check_mode" == upload-local ]]; then
  node scripts/upload-smoke.mjs | tee "$run_dir/api-result.json"
elif [[ "$check_mode" == live-policy ]]; then
  node scripts/live-policy-smoke.mjs | tee "$run_dir/api-result.json"
elif [[ "$check_mode" == interaction ]]; then
  node scripts/interaction-smoke.mjs | tee "$run_dir/api-result.json"
elif [[ "$check_mode" == capacity ]]; then
  python3 tests/availability_acceptance.py | tee "$run_dir/api-result.json"
  node scripts/capacity-smoke.mjs | tee "$run_dir/capacity-result.json"
elif [[ "$check_mode" == unknown ]]; then
  node scripts/unknown-smoke.mjs | tee "$run_dir/api-result.json"
elif [[ "$check_mode" == recovery ]]; then
  python3 tests/recovery_acceptance.py | tee "$run_dir/api-result.json"
  node scripts/recovery-smoke.mjs | tee "$run_dir/recovery-result.json"
elif [[ "$check_mode" == listening ]]; then
  node scripts/listening-smoke.mjs | tee "$run_dir/api-result.json"
elif [[ "$check_mode" == fixture ]]; then
  python3 tests/fixture_acceptance.py | tee "$run_dir/api-result.json"
  node scripts/creation-smoke.mjs | tee "$run_dir/creation-result.json"
  node scripts/listening-smoke.mjs | tee "$run_dir/listening-result.json"
  python3 tests/recovery_acceptance.py | tee "$run_dir/recovery-api-result.json"
  node scripts/recovery-smoke.mjs | tee "$run_dir/recovery-result.json"
  node scripts/unknown-smoke.mjs | tee "$run_dir/unknown-result.json"
  python3 tests/availability_acceptance.py | tee "$run_dir/availability-api-result.json"
  node scripts/capacity-smoke.mjs | tee "$run_dir/capacity-result.json"
else
  if [[ "$check_mode" == all ]]; then
    node scripts/browser-smoke.mjs | tee "$run_dir/browser-result.json"
    node scripts/editor-smoke.mjs | tee "$run_dir/editor-result.json"
  fi
  python3 tests/api_acceptance.py | tee "$run_dir/api-result.json"
fi
expect_startup_failure() {
  python3 - "$test_jar" "$1" "$2" "$3" "$((test_port+1))" <<'PY'
import os, pathlib, subprocess, sys
jar, log, directory, expected, port = sys.argv[1:]
env = dict(os.environ, DJOUND_DATA_DIR=directory, DJOUND_PORT=port)
with open(log, 'w') as output:
    try:
        options = ['-Xmx768m', '-Djdk.httpclient.redirects.retrylimit=1', '-Djdk.httpclient.disableRetryConnect=true', '-Djdk.httpclient.enableAllMethodRetry=false']
        result = subprocess.run(['java', *options, '-jar', jar], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=30)
    except subprocess.TimeoutExpired:
        raise SystemExit('Guard failed to refuse startup within 30 seconds: ' + log)
assert result.returncode != 0 and expected in pathlib.Path(log).read_text(), log
PY
}
expect_startup_failure "$run_dir/second-jvm.log" "$DJOUND_DATA_DIR" 'already in use'
python3 - "$run_dir/api-result.json" "$run_dir/before-restart.json" <<'PY'
import json, os, sys, urllib.request
result = json.load(open(sys.argv[1]))
with urllib.request.urlopen(os.environ['DJOUND_TEST_BASE_URL'] + '/api/explorations/' + result['explorationId']) as response:
    snapshot = json.load(response)
with open(sys.argv[2], 'w') as output:
    json.dump(snapshot, output)
PY
kill "$app_pid"
wait "$app_pid" || true
app_pid=''
export DJOUND_MODE=UNCONFIGURED
unset DJOUND_FIXTURE_COOLDOWN_MS
start_app
python3 - "$run_dir/api-result.json" "$run_dir/before-restart.json" <<'PY'
import hashlib, json, os, sys, urllib.request
result = json.load(open(sys.argv[1]))
before = json.load(open(sys.argv[2]))
base = os.environ['DJOUND_TEST_BASE_URL']
with urllib.request.urlopen(base + '/api/explorations/' + result['explorationId']) as response:
    saved = json.load(response)
assert saved == before, 'Restart changed stored exploration, draft, request, or recording metadata'
for recording in [saved['original'], *saved['renditions']]:
    with urllib.request.urlopen(base + recording['mediaUrl']) as response:
        audio = response.read()
    assert hashlib.sha256(audio).hexdigest() == recording['sha256']
if 'acceptedCommand' in result:
    command = urllib.request.Request(
        base + result.get('acceptedPath', '/api/explorations/' + result['explorationId'] + '/requests'),
        method='POST', data=json.dumps(result['acceptedCommand']).encode(),
        headers={'Content-Type': 'application/json', 'Idempotency-Key': result['acceptedKey']})
    with urllib.request.urlopen(command) as response:
        assert response.status == 202
        receipt = json.load(response)
    assert receipt['replayed'] and receipt['attemptId'] == result['attemptId']
    print('PASS: accepted command receipt replays with generation disabled, without new work')
print('PASS: restart with generation disabled retains all metadata, provenance, drafts, and exact audio bytes')
PY
expect_startup_failure "$run_dir/wrong-directory.log" "$run_dir/wrong-directory" 'bound to another'
echo "PASS: isolated $check_mode checks, restart, singleton, and database pairing. Evidence: $run_dir"
