#!/usr/bin/env bash
# Explicit setup downloads weights; worker inference never downloads anything.
set -euo pipefail
case "${1:-}" in
  ""|--audio-only|--finished-mix|--finished-mix-only) ;;
  *) echo 'usage: setup_localengine.sh [--audio-only | --finished-mix]' >&2; exit 2 ;;
esac
if (( $# > 1 )); then echo 'usage: setup_localengine.sh [--audio-only | --finished-mix]' >&2; exit 2; fi
ENGINE_CODE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$ENGINE_CODE/.." && pwd)"
ENGINE_HOME="${DJOUND_LOCAL_ENGINE_HOME:-$REPO_ROOT/.runtime/local-engine}"
export DJOUND_LOCAL_ENGINE_HOME="$ENGINE_HOME"
export UV_CACHE_DIR="$ENGINE_HOME/uv-cache"
export UV_PYTHON_DOWNLOADS=never
command -v uv >/dev/null || { echo 'Install uv and Python 3.11 before local-engine setup.' >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo 'Install ffmpeg before local-engine setup.' >&2; exit 1; }
if [[ "${1:-}" != --finished-mix && "${1:-}" != --finished-mix-only ]]; then
  command -v cc >/dev/null || { echo 'Install Apple Command Line Tools before local-engine setup.' >&2; exit 1; }
fi
mkdir -p "$ENGINE_HOME"
if [[ ! -x "$ENGINE_HOME/venv/bin/python" ]]; then
  uv venv --python "${DJOUND_PYTHON:-python3.11}" "$ENGINE_HOME/venv"
fi
"$ENGINE_HOME/venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11), "Use Python 3.11 for this pinned environment"'
if [[ "${1:-}" == --finished-mix || "${1:-}" == --finished-mix-only ]]; then
  uv pip install --python "$ENGINE_HOME/venv/bin/python" -r "$ENGINE_CODE/requirements-finished-mix.lock"
  "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/setup_assets.py" --finished-mix
  "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/worker.py" --check-mix
elif [[ "${1:-}" == --audio-only ]]; then
  # Installing the audio subset in an existing full setup preserves its planner.
  uv pip install --python "$ENGINE_HOME/venv/bin/python" -r "$ENGINE_CODE/requirements-audio.lock"
  "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/setup_assets.py" --audio-only
  "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/worker.py" --check
else
  uv pip sync --python "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/requirements.lock"
  "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/setup_assets.py"
  "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/worker.py" --check --require-freeform
fi
