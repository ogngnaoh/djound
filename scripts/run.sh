#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
docker compose up -d --pull never --wait db
shopt -s nullglob
jars=(server/target/djound-*.jar)
if [[ ${#jars[@]} -ne 1 ]]; then
  echo 'Build the application first with scripts/package.sh.' >&2
  exit 1
fi
# Also required when normal mode recovers a saved live result without resubmission.
java_options=(
  -Djdk.httpclient.redirects.retrylimit=1
  -Djdk.httpclient.disableRetryConnect=true
  -Djdk.httpclient.enableAllMethodRetry=false
)
exec java "${java_options[@]}" -jar "${jars[0]}"
