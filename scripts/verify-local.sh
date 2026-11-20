#!/usr/bin/env bash
# Real local models, isolated database and media. No hosted inference or fixtures.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
if [[ "${1:-planner}" != planner ]]; then
  echo 'The previous studio walkthroughs were retired. Use verify-local.sh planner for the current local API check.' >&2
  exit 2
fi
mkdir -p .runtime/checks
run_dir="$(mktemp -d "$DJOUND_ROOT/.runtime/checks/local.XXXXXX")"
test_db="djound_local_check_$$_${RANDOM}"
export DJOUND_PORT="${DJOUND_CHECK_PORT:-18088}"
export DJOUND_MODE=LOCAL
export DJOUND_DATA_DIR="$run_dir/data"
export DJOUND_DB_URL="jdbc:postgresql://127.0.0.1:$DJOUND_DB_PORT/$test_db"
export DJOUND_ALLOWED_ORIGINS="http://127.0.0.1:$DJOUND_PORT"
export DJOUND_TEST_BASE_URL="$DJOUND_ALLOWED_ORIGINS"
export DJOUND_TEST_ALLOW_WRITE=1
export DJOUND_SCREENSHOT_DIR="$run_dir/evidence"
python3 scripts/runtime-check.py
app_pid=''
database_created=0
cleanup() {
  if [[ -n "$app_pid" ]]; then kill "$app_pid" 2>/dev/null || true; wait "$app_pid" 2>/dev/null || true; fi
  if [[ "$database_created" == 1 && "${DJOUND_KEEP_LOCAL_CHECK:-0}" != 1 ]]; then
    docker compose exec -T db dropdb -U djound --force "$test_db" >/dev/null
  fi
}
trap cleanup EXIT
docker compose up -d --pull never --wait db
docker compose exec -T db createdb -U djound "$test_db"
database_created=1
if [[ "${DJOUND_KEEP_LOCAL_CHECK:-0}" == 1 ]]; then
  printf 'export DJOUND_MODE=LOCAL\nexport DJOUND_DB_URL=%q\nexport DJOUND_DATA_DIR=%q\n' "$DJOUND_DB_URL" "$DJOUND_DATA_DIR" > "$run_dir/review.env"
fi
shopt -s nullglob
jars=(server/target/djound-*.jar)
cp "${jars[0]}" "$run_dir/application.jar"
start_app() {
  java -Xmx768m -Djdk.httpclient.redirects.retrylimit=1 -Djdk.httpclient.disableRetryConnect=true -Djdk.httpclient.enableAllMethodRetry=false -jar "$run_dir/application.jar" >> "$run_dir/app.log" 2>&1 &
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
python3 tests/planner/verify_api.py "${@:2}"
