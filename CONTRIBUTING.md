# Contributing to Djound

Start with the [local setup](README.md#run-it-locally). The [architecture guide](guide/architecture.md) describes how a request becomes a saved recording. The project pins its Java, Node, Python and model dependencies.

## Run the checks

```sh
bash scripts/verify.sh
```

This runs the unit tests, builds the app, checks its API against PostgreSQL, and exercises the packaged interface with Playwright. Docker must be running. On macOS, install Google Chrome or the Playwright Chromium browser. CI runs the same model-free suite on Linux.

The frontend entry currently serves an empty document while the next workspace is in design. The packaged browser check verifies this on home and former studio routes. The backend checks still exercise the API, recovery and stored audio.

For local inference work, use the planner integration check after packaging and installing the local engine.

```sh
bash scripts/verify-local.sh planner
```

## Working on the interface or API

Use `source scripts/env.sh` to select the project toolchain, then `pnpm --dir web dev` to start the empty Vite entry.

Edit [the OpenAPI contract](contracts/openapi.yaml) before changing a public API shape, then run `pnpm --dir web generate`. In a pull request, describe the user-visible change and which checks you ran. Include a screenshot for interface changes or a small attributable fixture for an audio regression. Keep recordings from your own library, model downloads and local credentials on your machine.
