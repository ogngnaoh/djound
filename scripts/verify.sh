#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
mode="${1:-all}"
case "$mode" in
  fast)
    export DJOUND_MODE=UNCONFIGURED
    python3 tests/runtime_check_test.py
    python3 -m unittest discover -s local-engine -p 'test_*.py'
    python3 tests/test_free_provider.py
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
    node scripts/studio-smoke.mjs
    ;;
  legacy-browser)
    node scripts/browser-smoke.mjs
    node scripts/editor-smoke.mjs
    ;;
  fixture)
    bash scripts/verify-installation.sh fixture-api
    ;;
  legacy-fixture)
    bash scripts/verify-installation.sh fixture
    ;;
  all)
    bash scripts/verify.sh fast
    bash scripts/package.sh
    bash scripts/verify-installation.sh studio
    bash scripts/verify-installation.sh fixture-api
    ;;
  *) echo 'Usage: scripts/verify.sh [all|fast|api|browser|fixture|legacy-browser|legacy-fixture]' >&2; exit 2 ;;
esac
