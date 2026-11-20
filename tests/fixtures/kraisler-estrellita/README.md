# Can the violin still sing over a new beat?

This 25.05-second performance comes from Manuel Ponce's *Estrellita*, arranged for violin and piano by Jascha Heifetz. The violin plays an answering phrase, returns to the theme and works through its ornaments to a cadence. The piano continues underneath. The test counts every published violin attack in those two phrases, including the grace notes.

Hyemi Kim, Jiyun Park, Sein Lee, Taegyun Kwon, Sunjae Won and Juhan Nam published the KRAISLER recordings in July 2026. A professional violinist and pianist played simultaneously in isolated rooms. The reference comes from the musician-reviewed violin alignment and the directly recorded Disklavier part. An independent check against the violin microphone confirmed 38 of 40 note centres at their exact annotated pitch. Some ornament timings and bowed note endings carry additional uncertainty. The same collection supplied the earlier Schumann example. This is a different composition and performance. The dataset specifies CC BY 4.0 and also requests noncommercial research use. [Original recording, annotation procedure and release terms](https://zenodo.org/records/21082251)

## Repeat the check

From the extension worktree, use the published KRAISLER ZIP or its extracted directory. The fixture runner verifies six original member hashes and extracts the unmodified PCM16 mixture from 13.12 to 38.17 seconds, on its original 44.1 kHz clock. [KRAISLER archive](https://zenodo.org/records/21082251)

```sh
kraisler_work=$(mktemp -d)
.runtime/local-engine/venv/bin/python tests/kraisler_melody_acceptance.py "/path/to/KRAISLER.zip" "$kraisler_work" --piece estrellita
.runtime/local-engine/venv/bin/python tests/kraisler_melody_acceptance.py "/path/to/KRAISLER.zip" "$kraisler_work" --piece estrellita --trace /path/to/first-source-notes.json
```

The first command prints the upload path. The listening request was “Make this into music for my morning walk. Give it a steady beat with a kick drum and snare, and keep the tune easy to hear.” The follow-up was “Keep the same pulse and accompaniment exactly as they are. Give the melody to a cello.” To generate and grade a fresh local transcription directly, use `--transcribe` in place of `--trace /path/to/first-source-notes.json`.

The second command prints the source-clock report. Passing requires 80% of distinct original note attacks within one semitone in the concert octave, and 70% in each of the two phrases. The pitch reference, original files and scorer were fixed before Djound processed this recording. The pre-run SHA-256 of `SHA256SUMS` is `739132ed5bda2cae5e646087ab5f7d0e7c88fa3fdcb865a4f7def737d90e460a`. The source excerpt digest is `8e3b86a598902614c3a26f449296691343428765f391f0f7177b656989b8bbef`.

## First blind result

At freeze `6a09366030202fdc6d46e72c33471b5b44deb421`, take `90dd73f7-1047-45ed-aa97-c8c42b38f706` selected the instrument-and-waveform route (`separated_player`). **34/40 distinct source attacks** matched in the concert octave within one semitone, including 32 exact pitches. The phrases scored 11/12 and 23/28, with 38 events in the output. The source trace followed 82.03% of the annotated voiced time and left 56.94% of rest time silent. Three repeated-note pairs merged. For 0.20 seconds the line followed a pitch in the piano accompaniment.

All 34 matched attacks contributed measurable sound in the exported piano take and the cello revision `481484e3-a989-46be-a7fd-545752289c3d`. The cello moved the melody down two octaves. The 88 BPM pulse, lead timing and dynamics stayed the same, and the 243 backing events rendered to identical samples. Independently muting one drum at a time confirmed 25 kick and 24 snare hits in each WAV. Both drums were audible in every two-bar window across the theme. Listeners can judge whether this pace suits their walk.

The original report in `first-result.json` has SHA-256 `c09a77ee07d964059f84bbedbf512eeed537ad30ec89645652aa4f6380699ba2`. The independent evaluator has SHA-256 `5fa390eb85feb92f92f0af53704723ddf33bf1f5f3f454d405c69636cf064477`.
