# Sample recordings

These three short original compositions are deterministic audio fixtures authored for Djound. They use no recording, model, soundfont, or external music service. The composition, score, synthesis code, and generated audio are dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

`audio/source-study.wav` is an example upload. The other two files are predefined results for the explicit fixture demo. They do **not** reinterpret arbitrary uploads or respond to directions.

Reproduce with `python3 fixtures/generate.py`. `manifest.json` records the exact hashes, duration, format, and provenance. All clips are 12-second, mono, 44.1 kHz, PCM16 WAV files. Production media ingestion and playback use these bytes through the same paths as user uploads.

The Studio example, `audio/local-demo-source.wav`, is a separate authored fixture distributed under the [project license](../LICENSE). Its [note and source metadata](audio/local-demo-source.json) also drive regression tests.

The small files in `provider/melodyflow/` are synthetic HTTP and event responses for testing recovery of older installations. They contain example identifiers and no user recordings.

## Listening room example

The home player calls the 12-second, project-authored C-minor melody in `audio/local-demo-source.wav` *Evening Study*. The note sequence and source provenance are in `audio/local-demo-source.json`. Djound's local rendition worker produced `audio/evening-study-jazz.wav` from that recording with the request “Jazz trio”. The packaged example preserves the worker's arrangement plan and musical timing landmarks in `server/src/main/resources/demo/evening-study.json`, so listeners can compare and refine the same performance they heard on the homepage.
