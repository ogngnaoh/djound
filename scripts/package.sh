#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$DJOUND_ROOT"
pnpm --dir web run build
mkdir -p server/src/main/resources/static
# This directory contains only generated frontend output.
find server/src/main/resources/static -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -R web/dist/. server/src/main/resources/static/
server/mvnw -o -f server/pom.xml -DskipTests package
