#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
command -v python3 >/dev/null || { echo 'Python 3 is required for local startup checks.' >&2; exit 1; }
case "${1:-}" in
  --status) exec python3 scripts/runtime-check.py --status ;;
  --check) exec python3 scripts/runtime-check.py ;;
  '') ;;
  *) echo 'Usage: bash scripts/run.sh [--check|--status]' >&2; exit 2 ;;
esac
python3 scripts/runtime-check.py
export DJOUND_PORT="$(python3 -c 'import os; print(int(os.environ.get("DJOUND_PORT", "8080")))')"
if ! docker compose up -d --pull never --wait --wait-timeout 45 db; then
  echo 'Local PostgreSQL did not become ready. Inspect `docker compose logs db`; no app was started.' >&2
  exit 1
fi
shopt -s nullglob
jars=(server/target/djound-*.jar)
if [[ ${#jars[@]} -ne 1 ]]; then
  echo 'Build the application first with scripts/package.sh.' >&2
  exit 1
fi
if [[ -z "${DJOUND_ALLOWED_ORIGINS:-}" ]]; then
  export DJOUND_ALLOWED_ORIGINS="http://127.0.0.1:$DJOUND_PORT,http://localhost:$DJOUND_PORT"
  if [[ "$DJOUND_PORT" != 5173 ]]; then
    export DJOUND_ALLOWED_ORIGINS="$DJOUND_ALLOWED_ORIGINS,http://127.0.0.1:5173,http://localhost:5173"
  fi
fi
# Also required when normal mode recovers a saved live result without resubmission.
java_options=(
  -Xmx768m
  -Djdk.httpclient.redirects.retrylimit=1
  -Djdk.httpclient.disableRetryConnect=true
  -Djdk.httpclient.enableAllMethodRetry=false
)
echo "Starting Djound. Open http://127.0.0.1:${DJOUND_PORT:-8080} after startup completes; Ctrl+C stops the app."
# Spring loads some classes lazily. Repackaging server/target must never replace
# the bytes beneath a running JVM's classloader.
runtime_jar="$(python3 scripts/prepare-launch.py "${jars[0]}" "$DJOUND_ROOT/.runtime/packages")"
exec java "${java_options[@]}" -jar "$runtime_jar"
