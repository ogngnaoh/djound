# Can the arrangement carry the lower viola theme?

This check uses two complete phrases from Variation II of Schubert’s *Trout* Quintet, D.667, fourth movement. The viola plays the song while the violin plays a fast, higher line. Twenty-six of the 30 viola notes repeat the opening violin theme an octave lower at the same notated beats. A listener can follow the violin as a musical line of its own. This test specifically asks whether the arrangement carries the song. [Orchestra of the Age of Enlightenment performance notes](https://www.youtube.com/watch?v=SLpTZJXfUOk) · [Hong Kong Philharmonic programme notes](https://www.hkphil.org/watch-and-listen/all-performances/schubert-s-trout-quintet)

## Rebuild the recording and reference

Use the local engine’s Python 3.11 environment. MusicNet distributes licensed and public-domain performances and supplies its aligned note labels under CC BY 4.0. The project credits this recording to European Archive, the original score MIDI to the transcriber at tirolmusic.blogspot.com, and the alignments to John Thickstun, Zaid Harchaoui and Sham M. Kakade. The dataset authors describe their alignment and its limitations in the release. Obtain the audio and annotations from the release or a byte-identical mirror and keep them in a local working directory. [MusicNet data and license](https://zenodo.org/records/5120004) · [Aligned label license](https://creativecommons.org/licenses/by/4.0/)

From the extension worktree, fetch the two upstream files once. The WAV download is about 78 MB. The runner verifies both source hashes, extracts the original mono frames at 44.1 kHz as PCM16 and reconstructs the note times. Variation II’s viola is program 42 in the MusicNet CSV. The two phrases cover score beats 118.5 through 134.5, with the latter endpoint excluded. The excerpt starts 50 ms before the first note and lasts 15.166 seconds.

```sh
musicnet_work=$(mktemp -d)
curl -fL https://huggingface.co/datasets/DreamyWanderer/MusicNet/resolve/main/data/train/1729.wav -o "$musicnet_work/1729.wav"
curl -fL https://huggingface.co/datasets/DreamyWanderer/MusicNet/resolve/main/data/train/1729.csv -o "$musicnet_work/1729.csv"
.runtime/local-engine/venv/bin/python tests/musicnet_melody_acceptance.py "$musicnet_work/1729.wav" "$musicnet_work/1729.csv" "$musicnet_work"
```

The command prints the path to the verified WAV. Its SHA-256 is `136453c9d7f4dfc94a9fcdb141adbe7e9789c624027e7fc1de61682d8ac3d9c0`. The file contains 668,829 original performance frames. To repeat the studio test, upload this file and request “Make this a calm instrumental for a late-night study session, with a steady pulse and room to hear the tune.” Evaluate the worker’s saved source trace on the original time axis.

```sh
.runtime/local-engine/venv/bin/python tests/musicnet_melody_acceptance.py "$musicnet_work/1729.wav" "$musicnet_work/1729.csv" "$musicnet_work" --trace /path/to/first-source-notes.json
```

The command scores chronological one-to-one notes in their original octave. At least 80% of the references must match within one semitone overall, and at least 70% in each phrase. It prints the full report and exits with status 1 when a threshold is missed. It also reports exact pitches, precision, pitched and rest-frame coverage and sustained switches onto other scored parts. Supporting parts sometimes double the same pitch, so timing and pitch alone cannot identify which performer supplied an event. Listening preference is a separate judgment.

## First blind result

The source, labels and scoring rules were sealed before Djound processed the recording. The SHA-256 of SHA256SUMS is `12ff6b3653171c65cae7f70a653cf60a0d34ff29e36d09fb0db8e1de1733ac3a`. The byte-preserved evaluator’s SHA-256 is `abf555185accdfb54c2092c848efc680fec723cd10d74b86bb3ff18944654da7`.

At commit `4f8f20f72eb3db8484a961be7571606df6d80d71`, take `0f4e7735-20d9-45be-9b6e-bd5d9766b87e` recovered 11 of 30 distinct notes, all exact. The two phrases scored 6/14 and 5/16. Across voiced reference frames, the selected line followed the subject for 38.64% and a competing scored part for 52.24%. All 46 output events reached the rendered piano. The transcription’s candidate pool contained 27/30 target notes. The original report is preserved in first-result.json, SHA-256 `a6c6b10f8dcabb10a8a3193803251234b6246c4fb3c54e57da6054f13c3f7c04`. Subsequent results on this performance measure a known regression.
