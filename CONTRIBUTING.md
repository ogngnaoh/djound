# Contributing to Djound

Start with the [local setup](README.md#run-it-locally). The [architecture guide](guide/architecture.md) describes how a request becomes a saved recording. The project pins its Java, Node, Python and model dependencies.

## Run the checks

```sh
bash scripts/verify.sh
```

The check uses the Python environment installed by rendition setup. For a checkout without local models, use a Python 3.11 virtual environment and run `python -m pip install -r local-engine/requirements-audio.lock` first. Set `DJOUND_LOCAL_PYTHON` to its Python executable if needed.

This runs the unit tests, builds the app, checks its API against PostgreSQL, and exercises the packaged interface with Playwright. Docker must be running. On macOS, install Google Chrome or the Playwright Chromium browser. CI runs the same model-free suite on Linux.

The packaged browser check opens the listening room, library and Studio. With a running local-engine installation and a disposable workspace, run the full browser rehearsal to import the bundled melody, arrange it, refine it and verify the saved WAV. The rehearsal creates a recording in that workspace.

```sh
DJOUND_TEST_BASE_URL=http://127.0.0.1:8080 DJOUND_TEST_RENDER=1 node scripts/web-entry-smoke.mjs
```

For local inference work, use the planner integration check after packaging and installing the local engine.

```sh
bash scripts/verify-local.sh planner
```

## Working on the interface or API

Use `source scripts/env.sh` to select the project toolchain, then `pnpm --dir web dev` to serve the listening room, library and Studio. Start the backend on port 8080 so Vite can proxy API requests. The three entry pages and their controllers live in [web/app](web/app).

Edit [the OpenAPI contract](contracts/openapi.yaml) before changing a public API shape, then run `pnpm --dir web generate`. In a pull request, describe the user-visible change and which checks you ran. Include a screenshot for interface changes or a small attributable fixture for an audio regression. Keep recordings from your own library, model downloads and local credentials on your machine.
