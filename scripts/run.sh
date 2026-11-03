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
exec java -jar "${jars[0]}"
