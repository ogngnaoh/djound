# Running Djound

Follow the [quick start](../README.md#run-it-locally) on an Apple Silicon Mac. Bootstrap installs the pinned Java and Node toolchains inside the checkout, installs web dependencies, prepares Maven and pulls the PostgreSQL image. The finished mix setup installs a CPU inference runtime and verifies the SCNet model. The melody setup also compiles its sampler. It can take several minutes on the first run.

## Check an installation

```sh
DJOUND_MODE=LOCAL bash scripts/run.sh --check
bash scripts/run.sh --status
```

The first command reports which local editors have their required assets and checks the media tools, container engine and data directory. Either editor can run on its own. The second reads the running app. The default port is 8080. If it is in use, set `DJOUND_PORT` to another port for both launch and status. After rebuilding, restart the app to load the new package.

Run `bash local-engine/setup_localengine.sh --finished-mix` for the finished mix editor. In Studio, choose **Edit a finished mix**, import a stereo MP3 and select a passage. The original is available while the separator prepares its estimates. Confirming a passage trim changes the selected audio level for that take. Switching A/B keeps the playback gain fixed. After a clarification, **Choose an exact level** lets you pick vocals or drums and confirm a target in dB against the take you heard. If vocals or drums are already mixed very loud, listen to the proposed trim in context before keeping it.

The audio-only installation supports note detection, rendering, named arrangements, specific text edits and direct controls. To add both editors and the optional freeform arrangement planner, run `bash local-engine/setup_localengine.sh` without flags. To inspect the worker separately:

```sh
# Finished mix
.runtime/local-engine/venv/bin/python local-engine/worker.py --check-mix
# Melody arrangement
.runtime/local-engine/venv/bin/python local-engine/worker.py --check
```

If the worker reports memory pressure, close other large workloads before trying again. If the transcription misses a note, correct it in melody review. Choose a synthesized lead to hear those note corrections. The uploaded lead plays the original recording.

## Local data

The default media directory is `.runtime/data`. PostgreSQL runs on loopback port 55432 and uses the `djound_postgres` Compose volume. Back up the database and media directory together with the app stopped. Keep the installation identity with its media. Starting the app with an unrelated database and media directory will fail the pairing check.

To use a separate installation, set `DJOUND_DB_URL`, `DJOUND_DATA_DIR` and `DJOUND_PORT` before launching. Stop the server with Control C. Use `docker compose stop db` to stop PostgreSQL while retaining the volume.
