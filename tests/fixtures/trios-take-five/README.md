# Can the saxophone hook carry over to a new band?

Paul Desmond's *Take Five* begins with a familiar piano and drum pattern. In this TRIOS recording the alto saxophone then plays the hook twice, changing the second ending. The test covers these two complete statements, including the short ornaments. It uses 13.90 seconds from the published ensemble mix. Joachim Fritsch recorded the parts and aligned their MIDI scores by hand. The independently measured pitch at the centre of each note agreed with 45 of the 47 scored pitches in the isolated saxophone microphone. The source does not identify the individual performers. [TRIOS dataset](https://zenodo.org/records/6797837) · [Fritsch's thesis and recording procedure](https://www.atiam.ircam.fr/Archives/Stages1112/FRITSCH.pdf)

## Rebuild and score

Download the 92.8 MB TRIOS release (MD5 `0922b785187d655bd6f2d83265af2098`). The research recording and aligned scores are licensed CC BY-NC-SA 2.0 UK, England and Wales. Obtain the archive directly for local research. The other TRIOS works used in development came from the same collection. This recording, its source notes and both phrases were held independently until after the version 7 engine was frozen. [Source and attribution](https://zenodo.org/records/6797837) · [License](https://creativecommons.org/licenses/by-nc-sa/2.0/uk/)

Run from the repository root. The runner verifies the original files, crops PCM frames 582,120 through 1,195,109 at 44.1 kHz and prints the WAV path. It reconstructs the reference from the recorded saxophone MIDI, competing pitches from the piano MIDI and the pitch checks from the isolated saxophone microphone.

```sh
trios_work=$(mktemp -d)
.runtime/local-engine/venv/bin/python tests/trios_melody_acceptance.py "/path/to/TRIOS Dataset.zip" "$trios_work"
```

Upload the printed WAV and ask “Turn this into music for a brisk morning walk. Give it a steady, energetic beat and let me hear the tune.” Save the worker's first source trace and pass it to the same runner.

```sh
.runtime/local-engine/venv/bin/python tests/trios_melody_acceptance.py "/path/to/TRIOS Dataset.zip" "$trios_work" --trace /path/to/first-source-notes.json
```

The program prints a JSON report. The registered gates require distinct correct-octave attacks within one semitone on at least 80% of the full theme and 70% of each phrase. The command exits with status 1 when a threshold is missed. Only the piano supplies pitched competitor labels because the drum MIDI keys identify kit pieces.
