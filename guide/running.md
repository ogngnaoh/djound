# Running Djound

Follow the [quick start](../README.md#run-it-locally) on an Apple Silicon Mac. Bootstrap installs the pinned Java and Node toolchains inside the checkout, installs web dependencies, prepares Maven and pulls the PostgreSQL image.

## Generate song renditions

Install Git, Apple Command Line Tools, CMake, Python 3.11, uv and FFmpeg. From the Djound checkout, run setup once with an internet connection.

```sh
bash local-engine/renditions.sh setup
```

Setup installs Qwen3 4B Instruct for both interpreting rendition requests and directing the score. The shared setup also installs Qwen3.5 0.8B for the melody editor. Older rendition installations with only those compact weights use them for both jobs. The 4B weights occupy about 2.3 GB in 4-bit form. Set `DJOUND_SCORE_DIRECTOR_CACHE` to an existing directory containing the pinned conversion to reuse it. Setup checks the hashes and can hard-link the weights on the same volume. The local setup also installs Basic Pitch, SCNet and the FluidR3 instrument bank, and builds TinySoundFont players for guides and scored arrangements. The native ACE-Step backend installs four verified GGUF files totaling about 4.01 GB for Turbo, Base and their shared encoders. Allow space for the models, Python packages and build caches. The language and audio models run in stages on the local Mac. [Qwen3 4B weights](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit/tree/50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b) · [Native audio implementation](https://github.com/ServeurpersoCom/acestep.cpp/tree/973517be024ff8b6bbd50e0adc9e308ab2ed71b3)

Rendition setup also installs about 190 MB of verified YourMT3+ instrument transcription assets. They run on the CPU for passages where several players compete for the tune. To reuse a local copy, set `DJOUND_INSTRUMENT_TRANSCRIBER_CACHE` to the directory containing the pinned files. Older installations can still make arrangements with the mix transcriber. If an existing take needs the instrument transcriber for a revision, rerun rendition setup to restore it. [Files, revisions and checksums](../local-engine/instrument-transcriber-assets.json)

On native setup, `DJOUND_ACE_BASE_CACHE` may point to an existing `acestep-v15-base-Q4_K_M.gguf` on the same volume. Setup verifies its SHA-256 digest and hard-links it into the native model directory. The optional HTTP setup uses its own Turbo service.

Check the worker and start Djound.

```sh
bash local-engine/renditions.sh check
DJOUND_MODE=LOCAL bash scripts/run.sh --check
DJOUND_MODE=LOCAL bash scripts/run.sh
```

The default `DJOUND_RENDITION_BACKEND=auto` uses an explicit `DJOUND_ACE_URL` if configured, then the installed native backend if that URL is unset, and otherwise the local HTTP service. Set the backend to `native` or `http` to select it explicitly.

### Optional ACE-Step Python service

The full-precision upstream service needs substantially more memory than the native backend. On a 16 GB M2 Pro, system-wide swap reached about 16 GB while the Python service was initializing. We stopped that run before generation and completed the same workflow with the native backend. For a larger host, the following installs the pinned Python runtime and upstream 10.1 GB checkpoint snapshot, then starts the service in the foreground. Its 1.7B music language model is included in the snapshot and remains unloaded. [Upstream source](https://github.com/ace-step/ACE-Step-1.5/tree/ca1e85fe9430179831e6bc6be790c332190a3866) · [Checkpoint snapshot](https://huggingface.co/ACE-Step/Ace-Step1.5/tree/19671f406d603126926c1b7e2adc169acbcade22)

```sh
bash local-engine/renditions.sh setup-http
bash local-engine/renditions.sh serve
```

In the app terminal, set `DJOUND_RENDITION_BACKEND=http` for both checks and launch. Serve eagerly loads the audio model, binds to loopback and blocks outbound internet connections. If port 8001 is occupied, set `DJOUND_ACE_PORT=18761` for serve and `DJOUND_ACE_URL=http://127.0.0.1:18761` in the app terminal. You can set `DJOUND_ACE_API_KEY` in both terminals. HTTP generation waits up to 20 minutes by default. Set `DJOUND_ACE_TIMEOUT_SECONDS` for a different deadline and keep `DJOUND_RENDITION_TIMEOUT_SECONDS`, the app’s worker timeout, longer. Control C in the service terminal stops ACE-Step.

## Check an installation

```sh
DJOUND_MODE=LOCAL bash scripts/run.sh --check
bash scripts/run.sh --status
```

The first command reports which local workflows are available and checks the media tools, container engine and data directory. Each workflow can run with its own engine installed. The second reads the running app. The default port is 8080. If it is in use, set `DJOUND_PORT` to another port for both launch and status. After rebuilding, restart the app to load the new package.

Run `bash local-engine/setup_localengine.sh --finished-mix` to install the finished mix engine.

The audio-only installation supports note detection, rendering, named arrangements, specific text edits and direct controls. To install the melody and finished mix engines along with the optional freeform arrangement planner, run `bash local-engine/setup_localengine.sh` without flags. To inspect the worker separately:

```sh
# Finished mix
.runtime/local-engine/venv/bin/python local-engine/worker.py --check-mix
# Melody arrangement
.runtime/local-engine/venv/bin/python local-engine/worker.py --check
```

If the worker reports memory pressure, close other large workloads before trying again.

## Local data

The default media directory is `.runtime/data`. PostgreSQL runs on loopback port 55432 and uses the `djound_postgres` Compose volume. Back up the database and media directory together with the app stopped. Keep the installation identity with its media. Starting the app with an unrelated database and media directory will fail the pairing check.

To use a separate installation, set `DJOUND_DB_URL`, `DJOUND_DATA_DIR` and `DJOUND_PORT` before launching. Stop the server with Control C. Use `docker compose stop db` to stop PostgreSQL while retaining the volume.
