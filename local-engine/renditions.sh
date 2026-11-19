#!/usr/bin/env bash
# The setup subcommand downloads both engines. Serve works from local files.
set -euo pipefail

usage() { echo 'Usage: bash local-engine/renditions.sh {setup|check|setup-http|serve}'; }
fail() { echo "$*" >&2; exit 1; }
if [[ "$#" -ne 1 ]]; then usage; exit 2; fi
case "$1" in
  setup|setup-http|serve|check) ;;
  -h|--help) usage; exit 0 ;;
  *) usage; exit 2 ;;
esac

code="$(cd "$(dirname "$0")" && pwd -P)"
root="$(cd "$code/.." && pwd -P)"
runtime="$root/.runtime"
ace="$runtime/ace-step"
planner="${DJOUND_LOCAL_ENGINE_HOME:-$runtime/local-engine}"
export DJOUND_LOCAL_ENGINE_HOME="$planner"
ckpt="$ace/checkpoints"

if [[ "$1" == check ]]; then
  python="${DJOUND_LOCAL_PYTHON:-$planner/venv/bin/python}"
  [[ -x "$python" ]] || fail 'Set up the rendition worker with bash local-engine/renditions.sh setup.'
  "$python" "${DJOUND_RENDITION_WORKER:-$code/rendition_worker.py}" --check |
    "$python" -c 'import json, sys; result = json.load(sys.stdin); print(json.dumps(result)); sys.exit(0 if result.get("ready") is True else 1)'
  exit 0
fi

[[ "$(uname -s)/$(uname -m)" == Darwin/arm64 ]] || fail 'This local MLX installation requires an Apple Silicon Mac.'
command -v git >/dev/null || fail 'Install Git before setting up song renditions.'

checkout() (
  set -e
  local target="$1" remote="$2" revision="$3" install="$4"
  if [[ "$install" == yes && ! -e "$target" && ! -L "$target" ]]; then
    mkdir -p "$(dirname "$target")"
    pending="$(mktemp -d "$(dirname "$target")/.ace-setup-XXXXXXXX")"
    trap 'if [[ -d "$pending" ]]; then rm -rf -- "$pending"; fi' EXIT
    git init --quiet "$pending"
    git -C "$pending" remote add origin "$remote"
    git -C "$pending" fetch --depth 1 origin "$revision"
    git -C "$pending" checkout --quiet --detach FETCH_HEAD
    # Refuse to replace a checkout another setup completed concurrently.
    python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$pending" "$target"
  fi
  [[ -d "$target/.git" && ! -L "$target" ]] || fail "The pinned ACE checkout is required at $target. Run setup."
  [[ "$(git -C "$target" rev-parse --show-toplevel)" == "$target" &&
     "$(git -C "$target" rev-parse HEAD)" == "$revision" &&
     "$(git -C "$target" remote get-url origin)" == "$remote" ]] ||
    fail "Existing $target is a different checkout. Choose a fresh Djound worktree or move that checkout before setup."
  git -C "$target" diff --quiet HEAD -- || fail "Existing $target contains tracked edits. Review them before using this installer."
)

if [[ "$1" == setup || "$1" == setup-http ]]; then
  command -v uv >/dev/null || fail 'Install uv before setting up song renditions.'
  command -v ffmpeg >/dev/null || fail 'Install ffmpeg before setting up song renditions.'
  export UV_CACHE_DIR="${UV_CACHE_DIR:-$runtime/uv-cache}"
  export UV_PYTHON_DOWNLOADS=never
  python="${DJOUND_PYTHON:-python3.11}"
  mkdir -p "$planner"
  if [[ ! -x "$planner/venv/bin/python" ]]; then
    uv venv --python "$python" "$planner/venv"
  fi
  "$planner/venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11), "The pinned planner needs Python 3.11."'
  # Share version pins with the other editors while installing the planner and model download CLI.
  uv pip install --python "$planner/venv/bin/python" -c "$code/requirements.lock" mlx-lm huggingface-hub
  "$planner/venv/bin/python" "$code/setup_assets.py" --planner-only
fi

if [[ "$1" == setup ]]; then
  native="${DJOUND_ACE_NATIVE_HOME:-$runtime/ace-native}"
  command -v cmake >/dev/null || fail 'Install CMake and Apple Command Line Tools for Metal synthesis.'
  checkout "$native" https://github.com/ServeurpersoCom/acestep.cpp.git 973517be024ff8b6bbd50e0adc9e308ab2ed71b3 yes
  if [[ ! -e "$native/ggml/.git" ]]; then git -C "$native" submodule update --init --depth 1 ggml; fi
  [[ "$(git -C "$native/ggml" rev-parse HEAD)" == ef4555b7aede4467a5de803b103615e0a5f30dd8 ]] ||
    fail 'The existing GGML submodule differs from the pinned source.'
  git -C "$native/ggml" diff --quiet HEAD -- || fail 'The existing GGML submodule contains tracked edits.'
  cmake -S "$native" -B "$native/build" -DCMAKE_BUILD_TYPE=Release \
    -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_BLAS=ON -DGGML_CCACHE=OFF \
    -DGGML_BUILD_TESTS=OFF -DGGML_BUILD_EXAMPLES=OFF
  cmake --build "$native/build" --target ace-synth --config Release --parallel 2
  "$planner/venv/bin/hf" download Serveurperso/ACE-Step-1.5-GGUF \
    acestep-v15-turbo-Q4_K_M.gguf Qwen3-Embedding-0.6B-Q8_0.gguf vae-BF16.gguf \
    --revision 666ac70204440867d8c01ba4b119cc79c95b370a --local-dir "$native/models"
  (cd "$native/models" && shasum -a 256 -c <<'HASHES'
55b4d8514850f3d0f82536f37e99673aaf48df802b5ae5b153eea32a2e2daa5e  acestep-v15-turbo-Q4_K_M.gguf
972f23255e46adfe744a0eb9a0039f3c63988f65753b0968d776e8b27168c321  Qwen3-Embedding-0.6B-Q8_0.gguf
0599862ac5d15cd308e1d2e368373aea6c02e25ebd1737ad4a4562a0901b0ef8  vae-BF16.gguf
HASHES
  )
  echo 'The native Metal audio engine and Djound language planner are ready. Run bash local-engine/renditions.sh check.'
  exit 0
fi

install=no
if [[ "$1" == setup-http ]]; then install=yes; fi
checkout "$ace" https://github.com/ace-step/ACE-Step-1.5.git ca1e85fe9430179831e6bc6be790c332190a3866 "$install"
if [[ "$1" == setup-http ]]; then
  # Pin the project environment even if the caller has another virtualenv selected.
  ace_python="$python"
  if [[ -x "$ace/.venv/bin/python" ]]; then ace_python="$ace/.venv/bin/python"; fi
  (cd "$ace" && UV_PROJECT_ENVIRONMENT="$ace/.venv" uv sync --python "$ace_python" --frozen --no-dev)
  "$ace/.venv/bin/hf" download ACE-Step/Ace-Step1.5 --revision 19671f406d603126926c1b7e2adc169acbcade22 --local-dir "$ckpt"
fi

[[ -x "$ace/.venv/bin/acestep-api" ]] || fail 'The ACE-Step API runtime is missing. Run renditions.sh setup-http.'
# Verify large weights against the published snapshot in setup. Fast local checks
# at launch also catch incomplete downloads before ACE's automatic downloader runs.
"$ace/.venv/bin/python" - "$ckpt" "$1" <<'PY'
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
weights = {
    "acestep-v15-turbo/model.safetensors": (4787825604, "3f6e0797fad420a39bd33979eb6e840e30989e34a3794e843d23b60ec6e422d7"),
    "vae/diffusion_pytorch_model.safetensors": (337431388, "da17edb604c40deaf09e9b24974e590d1ca83a374070e5d0884cfa4bed9a99b0"),
    "Qwen3-Embedding-0.6B/model.safetensors": (1191586416, "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"),
    "acestep-5Hz-lm-1.7B/model.safetensors": (3708521528, "f161689da73e5ecefa28ff780d51c2d92a00f056d021d7933c779ed5c6cd7db8"),
}
for name, (size, expected) in weights.items():
    file = root / name
    if not file.is_file() or file.stat().st_size != size:
        sys.exit(f"Missing or incomplete ACE-Step weight {name}. Run renditions.sh setup-http.")
    if sys.argv[2] == "setup-http":
        with file.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                sys.exit(f"ACE-Step weight {name} failed SHA-256 verification.")
for name in ("acestep-v15-turbo/config.json", "acestep-v15-turbo/silence_latent.pt",
             "acestep-v15-turbo/configuration_acestep_v15.py", "acestep-v15-turbo/modeling_acestep_v15_turbo.py",
             "vae/config.json", "Qwen3-Embedding-0.6B/config.json", "Qwen3-Embedding-0.6B/tokenizer.json"):
    if not (root / name).is_file():
        sys.exit(f"Missing ACE-Step file {name}. Run renditions.sh setup-http.")
PY

if [[ "$1" == setup-http ]]; then
  echo 'The rendition engines are installed. Start the music service with bash local-engine/renditions.sh serve.'
  exit 0
fi

port="${DJOUND_ACE_PORT:-8001}"
[[ "$port" =~ ^[1-9][0-9]{0,4}$ && "$port" -le 65535 ]] || fail 'DJOUND_ACE_PORT must be an integer from 1 to 65535.'
export ACESTEP_PROJECT_ROOT="$ace" ACESTEP_CHECKPOINTS_DIR="$ckpt" ACESTEP_CONFIG_PATH=acestep-v15-turbo
export ACESTEP_INIT_LLM=false ACESTEP_NO_INIT=false ACESTEP_OFFLOAD_TO_CPU=false ACESTEP_OFFLOAD_DIT_TO_CPU=false
export ACESTEP_CONFIG_PATH2="" ACESTEP_CONFIG_PATH3="" ACESTEP_ON_DEMAND_MODEL_LOAD=false
export ACESTEP_DOWNLOAD_SOURCE=huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export GRADIO_ANALYTICS_ENABLED=false TOKENIZERS_PARALLELISM=false
if [[ -n "${DJOUND_ACE_API_KEY:-}" ]]; then export ACESTEP_API_KEY="$DJOUND_ACE_API_KEY"; fi
cd "$ace"
echo "Starting ACE-Step on http://127.0.0.1:$port. Keep this terminal open while generating takes."
# Upstream can fall back to another model host when a checkpoint is missing.
# Enforce offline operation at the OS boundary while allowing loopback clients.
exec /usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network-outbound (remote ip)) (allow network-outbound (remote ip "localhost:*"))' \
  "$ace/.venv/bin/acestep-api" --host 127.0.0.1 --port "$port"
