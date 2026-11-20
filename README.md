# Djound

Djound is an open-source music project for reimagining a recording and refining it in your own words. The local engine and recording API are available in this checkout. The workspace is being redesigned and the web entry is intentionally empty. [Product vision](guide/product.md) · [API contract](contracts/openapi.yaml)

## run it locally

for the local rendition studio, use an Apple Silicon Mac with Git, Apple Command Line Tools, CMake, Python 3.11, uv, FFmpeg, pnpm 11.5.0 and Docker with Compose. start Docker, then run these commands from the repo root.

```sh
bash scripts/bootstrap.sh
bash local-engine/renditions.sh setup
bash local-engine/renditions.sh check
bash scripts/package.sh
DJOUND_MODE=LOCAL bash scripts/run.sh
```

Setup downloads the tools and models. Use the [running guide](guide/running.md) for model choices, the optional mix and melody engines and installation checks.

## how it works

in the rendition engine, the same local Qwen3 4B model interprets your request and directs the arrangement. older installations can use the smaller Qwen3.5 0.8B model. when the worker can follow a tune in the recording, the director chooses how fast to play it, who carries the melody and how the other players accompany it. a new take can have its own tempo and ending. its saved arrangement gives the next request something concrete to change. the player performs the score locally. ACE-Step handles generated sound treatments, passage changes and direct covers of recordings with little reliable pitch information. each ready take keeps its own audio and the request that led to it.

in the melody engine, Basic Pitch detects the notes. you can correct them, keep the uploaded performance as the lead, or play them on a different instrument. the renderer builds the backing from the arrangement you approve. the melody setup includes built-in text edits and direct controls. running `bash local-engine/setup_localengine.sh` without flags installs the melody and finished mix engines plus the optional Qwen arrangement planner.

spring boot manages the api, postgres keeps project history, and a python worker handles transcription and rendering. [architecture](guide/architecture.md) · [setup and troubleshooting](guide/running.md) · [contributing](CONTRIBUTING.md) · [license](LICENSE)
