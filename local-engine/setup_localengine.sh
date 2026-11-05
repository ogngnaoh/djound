#!/usr/bin/env bash
# Explicit setup downloads weights; worker inference never downloads anything.
set -euo pipefail
ENGINE_CODE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$ENGINE_CODE/.." && pwd)"
ENGINE_HOME="${DJOUND_LOCAL_ENGINE_HOME:-$REPO_ROOT/.runtime/local-engine}"
export DJOUND_LOCAL_ENGINE_HOME="$ENGINE_HOME"
export UV_CACHE_DIR="$ENGINE_HOME/uv-cache"
export UV_PYTHON_DOWNLOADS=never
command -v uv >/dev/null || { echo 'Install uv and Python 3.11 before local-engine setup.' >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo 'Install ffmpeg before local-engine setup.' >&2; exit 1; }
command -v cc >/dev/null || { echo 'Install Apple Command Line Tools before local-engine setup.' >&2; exit 1; }
mkdir -p "$ENGINE_HOME"
if [[ ! -x "$ENGINE_HOME/venv/bin/python" ]]; then
  uv venv --python "${DJOUND_PYTHON:-python3.11}" "$ENGINE_HOME/venv"
fi
"$ENGINE_HOME/venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11), "Use Python 3.11 for this pinned environment"'
uv pip sync --python "$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/requirements.lock"
"$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/setup_assets.py"
"$ENGINE_HOME/venv/bin/python" "$ENGINE_CODE/worker.py" --check
