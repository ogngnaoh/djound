#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
mode="${1:-all}"
case "$mode" in
  fast)
    docker compose up -d --pull never --wait db
    pnpm --dir web run format:check
    pnpm --dir web test
    pnpm --dir web run build
    server/mvnw -o -f server/pom.xml test
    ;;
  api)
    python3 tests/api_acceptance.py
    ;;
  browser)
    node scripts/browser-smoke.mjs
    ;;
  all)
    bash scripts/verify.sh fast
    bash scripts/package.sh
    bash scripts/verify-installation.sh
    ;;
  *) echo 'Usage: scripts/verify.sh [all|fast|api|browser]' >&2; exit 2 ;;
esac
