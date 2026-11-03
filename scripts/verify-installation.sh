#!/usr/bin/env bash
# Each run owns a separate database and data directory; never uses the review library.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
mkdir -p .runtime/checks
run_dir="$(mktemp -d "$DJOUND_ROOT/.runtime/checks/run.XXXXXX")"
test_db="djound_check_$$_${RANDOM}"
test_port="${DJOUND_CHECK_PORT:-18087}"
python3 - "$test_port" <<'PY'
import socket, sys
with socket.socket() as probe:
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
export DJOUND_SCREENSHOT_DIR="$(mktemp -d /tmp/djound-qa.XXXXXX)"
shopt -s nullglob
jars=(server/target/djound-*.jar)
[[ ${#jars[@]} -eq 1 ]] || { echo 'Package the app before verification.' >&2; exit 1; }
cp "${jars[0]}" "$run_dir/application.jar"
test_jar="$run_dir/application.jar"
start_app() {
  java -jar "$test_jar" >> "$run_dir/app.log" 2>&1 &
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
if [[ "${1:-all}" != api ]]; then
  node scripts/browser-smoke.mjs | tee "$run_dir/browser-result.json"
fi
python3 tests/api_acceptance.py | tee "$run_dir/api-result.json"
expect_startup_failure() {
  python3 - "$test_jar" "$1" "$2" "$3" "$((test_port+1))" <<'PY'
import os, pathlib, subprocess, sys
jar, log, directory, expected, port = sys.argv[1:]
env = dict(os.environ, DJOUND_DATA_DIR=directory, DJOUND_PORT=port)
with open(log, 'w') as output:
    try:
        result = subprocess.run(['java', '-jar', jar], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=30)
    except subprocess.TimeoutExpired:
        raise SystemExit('Guard failed to refuse startup within 30 seconds: ' + log)
assert result.returncode != 0 and expected in pathlib.Path(log).read_text(), log
PY
}
expect_startup_failure "$run_dir/second-jvm.log" "$DJOUND_DATA_DIR" 'already in use'
kill "$app_pid"
wait "$app_pid" || true
app_pid=''
start_app
python3 - "$run_dir/api-result.json" <<'PY'
import hashlib, json, os, sys, urllib.request
result = json.load(open(sys.argv[1]))
base = os.environ['DJOUND_TEST_BASE_URL']
with urllib.request.urlopen(base + '/api/explorations/' + result['explorationId']) as response:
    saved = json.load(response)
with urllib.request.urlopen(base + saved['original']['mediaUrl']) as response:
    audio = response.read()
assert hashlib.sha256(audio).hexdigest() == saved['original']['sha256']
print('PASS: restart retains original metadata and exact audio bytes')
PY
expect_startup_failure "$run_dir/wrong-directory.log" "$run_dir/wrong-directory" 'bound to another'
echo "PASS: isolated ${1:-all} checks, restart, singleton, and database pairing. Evidence: $run_dir"
