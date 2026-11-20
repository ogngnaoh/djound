# How Djound edits a recording

An edit belongs to the performance the listener selected. A request like “keep the rhythm and give the tune to a cello” needs both the audio they heard and the musical decisions that produced it. Djound saves them together.

## From the Studio to the worker

1. The browser uploads the recording. Spring Boot calls FFmpeg to decode it into a stereo WAV, stores the file locally and records its digest in PostgreSQL. The original gets its own playback address.
2. Studio submits the listener’s words, the selected parent and any passage boundaries. The API records the request and queues a Python worker. Each request has a retry key, so reconnecting can recover the existing job. The worker reports its progress back to Studio.
3. The worker reads the parent’s arrangement and the request. It can perform a new score, edit the selected audio or return a question when it needs more direction. Once the server validates the output, it publishes the new edit with its own WAV and parent.

The browser, API and worker meet at the [request contract](../contracts/openapi.yaml), [project service](../server/src/main/java/com/djound/rendition/RenditionProjects.java) and [worker entry point](../local-engine/rendition_worker.py).

## Performing an arrangement

For a recording with a traceable tune, the worker estimates notes, pulse and phrases. Basic Pitch supplies candidate notes. The transcriber can consult a separated vocal or YourMT3+ instrument estimates when several parts compete for the melody. The local Qwen3 4B model interprets freeform requests and directs the arrangement. It can change the tempo, lead, backing, phrasing or ending. A local player performs the parts with TinySoundFont and FluidR3.

A revision can reuse the parent’s musical input, arrangement and performance settings. That lets the worker change the lead while retaining the backing, or remove a written outro while keeping the preceding performance. The route depends on the recording and the request. Dense recordings can still make it hard to identify the intended melody. [Transcription](../local-engine/rendition_melody.py) · [Score direction](../local-engine/rendition_score_plan.py) · [Performance](../local-engine/rendition_performance.py)

For sound treatments, passage edits and covers without a reliable score, the worker can use ACE-Step. A passage edit is measured on the selected parent. The worker blends the revised passage into the saved performance and the server checks the audio outside the requested range. Native Apple Silicon setup runs the audio model through Metal. [Audio adapter](../local-engine/rendition_audio.py) · [Validation](../server/src/main/java/com/djound/rendition/RenditionAudio.java)

## Playback and recovery

Studio keeps the edit you are hearing separate from the edit you are developing. Its song time map aligns corresponding phrases when two versions have different introductions or tempos. Playback and export read the same saved WAV. Renaming or choosing a favorite updates project metadata. [Player timing](../web/src/renditions/songTime.ts) · [API routes](../server/src/main/java/com/djound/rendition/RenditionController.java)

PostgreSQL holds project history, job state and request receipts. The local media directory holds recordings and render files. Queued work can resume after a restart. An interrupted render is marked failed so it can be retried deliberately. Back up the database and media together. [Running and storage](running.md#local-data)

The server also retains separate melody arrangement and finished mix APIs. Those workers support note review, instrument rendering and passage level adjustments on separated audio. Their setup is documented in the [running guide](running.md#check-an-installation).
