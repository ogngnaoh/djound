#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
mode="${1:-all}"
case "$mode" in
  fast)
    export DJOUND_MODE=UNCONFIGURED
    export DJOUND_TEST_DB_URL="${DJOUND_TEST_DB_URL:-jdbc:postgresql://127.0.0.1:$DJOUND_DB_PORT/djound}"
    python3 tests/runtime_check_test.py
    test_python="${DJOUND_LOCAL_PYTHON:-$DJOUND_ROOT/.runtime/local-engine/venv/bin/python}"
    if [[ ! -x "$test_python" && -z "${DJOUND_LOCAL_PYTHON:-}" ]]; then test_python=python3; fi
    "$test_python" -m unittest discover -s local-engine -p 'test_*.py'
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
    node scripts/web-entry-smoke.mjs
    ;;
  fixture)
    bash scripts/verify-installation.sh fixture-api
    ;;
  all)
    bash scripts/verify.sh fast
    bash scripts/package.sh
    bash scripts/verify-installation.sh studio
    bash scripts/verify-installation.sh fixture-api
    ;;
  *) echo 'Usage: scripts/verify.sh [all|fast|api|browser|fixture]' >&2; exit 2 ;;
esac
