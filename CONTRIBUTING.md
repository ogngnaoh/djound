# Contributing to Djound

Start with the [local setup](README.md#run-it-locally). The [architecture guide](guide/architecture.md) describes how a request becomes a saved recording. The project pins its Java, Node, Python and model dependencies.

## Run the checks

```sh
bash scripts/verify.sh
```

This runs the unit tests, builds the app, checks its API against PostgreSQL, and exercises the packaged interface with Playwright. Docker must be running. On macOS, install Google Chrome or the Playwright Chromium browser. CI runs the same model-free suite on Linux.

For recording-led changes, open **New project** on the packaged app, import a recording, make a take and play the downloaded WAV. [Rendition setup](guide/running.md#generate-song-renditions)

For changes to the short-melody editor, exercise its real audio flow on an Apple Silicon Mac after packaging and installing the melody engine.

```sh
bash scripts/verify-local.sh jazz
```

The check uploads the included melody, creates and plays two versions, compares the served audio with the source and checks both versions after a restart. With the full model installation, `bash scripts/verify-local.sh studio` also covers the longer refinement and recovery flow.

## Working on the interface or API

Use `source scripts/env.sh` to select the project toolchain, then `pnpm --dir web dev` to start Vite. Open `/renditions?mode=backend` for the recording-led studio or `/?mode=prototype` for the melody editor’s sample library.

Edit [the OpenAPI contract](contracts/openapi.yaml) before changing a public API shape, then run `pnpm --dir web generate`. In a pull request, describe the user-visible change and which checks you ran. Include a screenshot for interface changes or a small attributable fixture for an audio regression. Keep recordings from your own library, model downloads and local credentials on your machine.
