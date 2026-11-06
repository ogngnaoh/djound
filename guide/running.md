# Running Djound

Follow the [quick start](../README.md#run-it-locally) on an Apple Silicon Mac. Bootstrap installs the pinned Java and Node toolchains inside the checkout, installs web dependencies, prepares Maven and pulls the PostgreSQL image. The audio setup verifies the downloaded assets and compiles the sampler. It can take several minutes on the first run.

## Check an installation

```sh
DJOUND_MODE=LOCAL bash scripts/run.sh --check
bash scripts/run.sh --status
```

The first command checks dependencies, core model assets, media tools, the container engine and the local data directory. The second reads the running app. The default port is 8080. If it is in use, set `DJOUND_PORT` to another port for both launch and status. After rebuilding, restart the app to load the new package.

The audio-only installation supports note detection, rendering, named arrangements, specific text edits and direct controls. For the optional freeform planner, rerun `bash local-engine/setup_localengine.sh` without flags. To inspect the worker separately:

```sh
.runtime/local-engine/venv/bin/python local-engine/worker.py --check
```

If the worker reports memory pressure, close other large workloads before trying again. If the transcription misses a note, correct it in melody review. Choose a synthesized lead to hear those note corrections. The uploaded lead plays the original recording.

## Local data

The default media directory is `.runtime/data`. PostgreSQL runs on loopback port 55432 and uses the `djound_postgres` Compose volume. Back up the database and media directory together with the app stopped. Keep the installation identity with its media. Starting the app with an unrelated database and media directory will fail the pairing check.

To use a separate installation, set `DJOUND_DB_URL`, `DJOUND_DATA_DIR` and `DJOUND_PORT` before launching. Stop the server with Control C. Use `docker compose stop db` to stop PostgreSQL while retaining the volume.
