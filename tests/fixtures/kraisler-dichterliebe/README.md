# Can the violin melody survive the piano accompaniment?

This 23.05-second excerpt contains the complete first verse of a violin and piano arrangement of Schumann's *Dichterliebe*, Op. 48, No. 1. A professional violinist and pianist played together in adjacent isolated rooms, each hearing the other. The researchers published the mixed performance, the separate violin microphone, directly recorded piano MIDI and score-aligned violin notes checked by a trained annotator. Independent pitch estimates from the violin microphone agreed exactly on 30 of 31 notes. Offset estimates and piano pedal resonance carry more uncertainty. [KRAISLER release and recording methods](https://zenodo.org/records/21082251)

Hyemi Kim, Jiyun Park, Sein Lee, Taegyun Kwon, Sunjae Won and Juhan Nam published the research recordings in July 2026. The release specifies CC BY 4.0 and also requests noncommercial research use. Obtain the released material directly for local assessment. These new duet sessions belong to an external collection absent from the 2024 transcription model's training data. The installed ONNX model uses the published weights. [KRAISLER release](https://zenodo.org/records/21082251) · [YourMT3+ model and training data](https://arxiv.org/html/2407.04822v2) · [ONNX conversion notice](https://huggingface.co/ONLY4DarkKnight/YourMT3-plus-onnx/blob/7f0d7c26a90ce1bcff74d3938f84db570a74abab/NOTICE.md)

## Repeat the check

Download `KRAISLER.zip` from the research release. The archive is approximately 1.6 GB. The runner accepts the ZIP or its extracted directory. It verifies six source files, reconstructs frames 624,015 through 1,640,519 of `11_mix_dry.wav` as the original PCM16 and compares the violin pitches with the isolated recording. Run from the repository root. [Download the dataset](https://zenodo.org/records/21082251)

```sh
kraisler_work=$(mktemp -d)
.runtime/local-engine/venv/bin/python tests/kraisler_melody_acceptance.py /path/to/KRAISLER.zip "$kraisler_work"
```

Upload the WAV at the printed path and ask “Make this a calm instrumental for a late-night study session, with a steady pulse and room to hear the tune.” Score the first take's saved `source-notes.json` against the four lines in the recorded verse.

```sh
.runtime/local-engine/venv/bin/python tests/kraisler_melody_acceptance.py /path/to/KRAISLER.zip "$kraisler_work" --trace /path/to/first-source-notes.json
```

The command prints JSON and exits with status 1 when a threshold is missed. Chronological one-to-one matching requires the correct octave within one semitone, at least 80% of distinct source attacks overall and at least 70% in every line. Exact pitches, voiced time, wrong octaves and competition from the piano are reported separately.
