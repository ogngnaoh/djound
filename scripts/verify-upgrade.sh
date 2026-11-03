#!/usr/bin/env bash
# Verify a real retained installation across a packaged application upgrade.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
old_jar="${1:?Usage: verify-upgrade.sh previous-application.jar}"
[[ -f "$old_jar" ]] || { echo 'Previous application jar is missing.' >&2; exit 1; }
mkdir -p .runtime/checks
run_dir="$(mktemp -d "$DJOUND_ROOT/.runtime/checks/upgrade.XXXXXX")"
test_db="djound_upgrade_$$_${RANDOM}"
export DJOUND_PORT="${DJOUND_CHECK_PORT:-18087}"
export DJOUND_DB_URL="jdbc:postgresql://127.0.0.1:55432/$test_db"
export DJOUND_DATA_DIR="$run_dir/data"
export DJOUND_MODE="${DJOUND_UPGRADE_MODE:-UNCONFIGURED}"
export DJOUND_FIXTURE_REVIEW_CONTROLS=false
export DJOUND_FIXTURE_DELAY_MS=100
export DJOUND_ALLOWED_ORIGINS="http://127.0.0.1:$DJOUND_PORT"
export DJOUND_TEST_BASE_URL="$DJOUND_ALLOWED_ORIGINS"
app_pid=''
database_created=0
cleanup() {
  if [[ -n "$app_pid" ]]; then kill "$app_pid" 2>/dev/null || true; wait "$app_pid" 2>/dev/null || true; fi
  if [[ "$database_created" == 1 ]]; then docker compose exec -T db dropdb -U djound --force "$test_db" >/dev/null; fi
}
trap cleanup EXIT
python3 - "$DJOUND_PORT" <<'PY'
import socket, sys
with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(('127.0.0.1', int(sys.argv[1])))
PY
docker compose up -d --pull never --wait db
docker compose exec -T db createdb -U djound "$test_db"
database_created=1
cp "$old_jar" "$run_dir/previous.jar"
shopt -s nullglob
jars=(server/target/djound-*.jar)
[[ ${#jars[@]} -eq 1 ]] || { echo 'Package the app before verification.' >&2; exit 1; }
cp "${jars[0]}" "$run_dir/current.jar"
start_app() {
  java -jar "$run_dir/$1.jar" > "$run_dir/$1.log" 2>&1 &
  app_pid=$!
  for ((i=0;i<60;i++)); do
    kill -0 "$app_pid" 2>/dev/null || { tail -60 "$run_dir/$1.log"; exit 1; }
    if curl --silent --fail "$DJOUND_TEST_BASE_URL/api/workspace" >/dev/null; then return; fi
    sleep 0.5
  done
  echo "App did not start: $run_dir/$1.log" >&2
  exit 1
}
start_app previous
python3 scripts/upgrade-check.py prepare "$run_dir/snapshot.json"
kill "$app_pid"
wait "$app_pid" || true
app_pid=''
start_app current
python3 scripts/upgrade-check.py verify "$run_dir/snapshot.json"
echo "PASS: packaged application upgrade preserves recordings, drafts, and receipts. Evidence: $run_dir"
