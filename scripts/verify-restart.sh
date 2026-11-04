#!/usr/bin/env bash
# Own disposable databases, JVMs, and a loopback result server for crash recovery checks.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
docker compose up -d --pull never --wait db
exec python3 scripts/restart-check.py
