# Two layered melody checks

These excerpts test whether a reimagining keeps the tune in its original register when another player sounds above it, and when the tune moves to a different instrument. The reference comes from manually aligned instrument MIDI. The audio was recorded one player at a time. Both references and the pass criteria were fixed before the first Djound transcription of these excerpts.

## Recordings

| Work | Source seconds | Listening cue |
| --- | --- | --- |
| Johannes Brahms, Horn Trio in E-flat major, Op. 40, I. Andante | 3.55–26.55 | The violin starts with E-flat 5 and D5 against piano. The horn takes over with B-flat 4 and A-flat 4, then moves beneath the returning high violin. The excerpt is a sample-exact crop of the published mono mix. |
| Franz Schubert, Piano Trio in E-flat major, D.929, II. Andante con moto | 41.96–51.85 | The cello plays the same arch twice, E-flat 4, F4, G4, A-flat 4, G4, F4, E-flat 4. The foreground balance uses the recorded cello unchanged and the recorded violin and piano at one third amplitude each. This makes the embedded cello phrase an explicit listening target. The command also writes the published balance for comparison. |

In the Brahms, the horn is silent during the opening violin phrase. It enters as the violin resolves and then rests. When the violin returns higher, the two parts carry independent lines before the cadence. With the published balance the horn has approximately 5 dB more RMS level than the violin from source 15.76 to 21.9 seconds and 13 dB more from 21.9 to 27.6 seconds. The selected horn line is a defensible melody in that duet. In the Schubert, the cello-forward balance brings it 9.54 dB forward relative to both other players. The original score gives the piano its own melodic material.

The published score and the reference audio come from Joachim Fritsch, *High Quality Musical Audio Source Separation*, UPMC / IRCAM / Telecom ParisTech, 2012. The archive contains the instrumental recordings, published mixes, printed scores and per-part aligned MIDI. Its release MD5 is `0922b785187d655bd6f2d83265af2098`. The listening examples derived from the recordings retain CC BY-NC-SA 2.0 UK. Keep downloaded media and generated excerpts outside the repository. [TRIOS dataset](https://zenodo.org/records/6797837) · [Author's methodology, appendix B](https://www.atiam.ircam.fr/Archives/Stages1112/FRITSCH.pdf) · [License](https://creativecommons.org/licenses/by-nc-sa/2.0/uk/)

## Run

Use the local engine's Python environment. Supply the published archive or its extracted directory. Omitting both optional flags prepares the listening file and reference. A saved trace must use the upload's timeline and contain a `melody` array of `[onset_seconds, release_seconds, MIDI, ...]` events.

```sh
.runtime/local-engine/venv/bin/python tests/layered_melody_acceptance.py /path/to/TRIOS.zip brahms /tmp/djound-layered --transcribe
.runtime/local-engine/venv/bin/python tests/layered_melody_acceptance.py /path/to/TRIOS.zip schubert /tmp/djound-layered --trace /path/to/schubert.trace.json
```

The evaluator verifies the release audio and MIDI fingerprints as well as the resulting clip's PCM hash. It scores the opening violin, first horn phrase and closing horn phrase separately (11, 11 and 10 notes), as well as both cello arches (7 notes each). A direct autocorrelation check of each published lead stem at these 46 note centers independently confirmed the aligned pitch to the nearest semitone. At 18 of 21 horn centers and 13 of 14 cello centers another instrument plays a higher pitch.

Every phrase must recover at least 70% of the reference pitches to within one semitone, including register, and at least 60% exactly. Both thresholds apply to two counts. The midpoint count checks the pitch sounding at each reference note's center. The distinct count also requires a separate ordered played event covering at least half of that reference note. An extended note spanning several repetitions earns a single distinct match. The exit status is nonzero if any phrase misses any threshold. These counts describe the extracted line. Listener judgment and rendered arrangements are evaluated separately.
