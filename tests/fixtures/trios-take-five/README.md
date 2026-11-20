# Can the saxophone hook carry over to a new band?

Paul Desmond's *Take Five* begins with a familiar piano and drum pattern. In this TRIOS recording the alto saxophone then plays the hook twice, changing the second ending. The test covers these two complete statements, including the short ornaments. It uses 13.90 seconds from the published ensemble mix. Joachim Fritsch recorded the parts and aligned their MIDI scores by hand. The independently measured pitch at the centre of each note agreed with 45 of the 47 scored pitches in the isolated saxophone microphone. The source does not identify the individual performers. [TRIOS dataset](https://zenodo.org/records/6797837) · [Fritsch's thesis and recording procedure](https://www.atiam.ircam.fr/Archives/Stages1112/FRITSCH.pdf)

## Rebuild and score

Download the 92.8 MB TRIOS release (MD5 `0922b785187d655bd6f2d83265af2098`). The research recording and aligned scores are licensed CC BY-NC-SA 2.0 UK, England and Wales. Obtain the archive directly for local research. The other TRIOS works used in development came from the same collection. This recording, its source notes and both phrases were held independently until after the version 7 engine was frozen. [Source and attribution](https://zenodo.org/records/6797837) · [License](https://creativecommons.org/licenses/by-nc-sa/2.0/uk/)

Run from the extension worktree. The runner verifies the original files, crops PCM frames 582,120 through 1,195,109 at 44.1 kHz and prints the WAV path. It reconstructs the reference from the recorded saxophone MIDI, competing pitches from the piano MIDI and the pitch checks from the isolated saxophone microphone.

```sh
trios_work=$(mktemp -d)
.runtime/local-engine/venv/bin/python tests/trios_melody_acceptance.py "/path/to/TRIOS Dataset.zip" "$trios_work"
```

Upload the printed WAV and ask “Turn this into music for a brisk morning walk. Give it a steady, energetic beat and let me hear the tune.” Save the worker's first source trace and pass it to the same runner.

```sh
.runtime/local-engine/venv/bin/python tests/trios_melody_acceptance.py "/path/to/TRIOS Dataset.zip" "$trios_work" --trace /path/to/first-source-notes.json
```

The program prints a JSON report. The registered gates require distinct correct-octave attacks within one semitone on at least 80% of the full theme and 70% of each phrase. The command exits with status 1 when a threshold is missed. Only the piano supplies pitched competitor labels because the drum MIDI keys identify kit pieces.

## First blind result

At commit `b453eb6f6abc73dff1ccb261f9aece53096c4ff7`, first take `8ffbea32-87be-4c2d-8698-7a7688240e50` recovered **39/47 attacks**, all at their exact concert pitches. The first statement scored 20/23 and the second 19/24. Of 43 output attacks, 39 matched. The selected line followed the saxophone for 89.66% of voiced time and preserved 50% of annotated rest time. No sustained switch to the piano or a different octave was detected. The engine selected its mixed-audio melody route.

All 39 matched attacks contributed audible sound in the exported 120 BPM trumpet performance. The requested cello revision `00c43b7b-0338-4166-928e-dccec35c7747` played those same attacks an octave lower with identical timing and dynamics. All 33 backing events rendered to the same samples. Both exported WAVs matched independent score renders, and both source traces reproduced the frozen report.

The pre-run commitment to `SHA256SUMS` was `cb2ec6a070a704a84ae590ecd559ce2d35233cebfeccfde52752a9b0fcf4aeb3`. The excerpt digest is `fb0eba88d5f980cb3b1c270d3ede231a1adee489d9dfd7ed38f3349ea3a31121`. The evaluator digest is `ac630bc15e67e4a46a7b44da47beda5254ea61b821ae3fa915e6ba1928dbecc4`. The original first result has SHA-256 `f743fe6c37d010abc2458d9d46bb6e882442b368266116c52b0236418f4b8574`.
