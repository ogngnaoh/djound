# Can the listener still follow the cello's song?

In the slow movement of Dvořák's *American* Quartet, the cello takes over a melody previously played by the first violin. This 39.594-second excerpt contains the cello's first two complete statements with the higher strings playing their own figures. Every repeated attack and ornament in the aligned cello part counts. [Chamber Music Society of Lincoln Center programme notes](https://chambermusicsociety.org/about-the-music/compositions/dvorak-american-quartet)

MusicNet credits the performance to Musopen and the score MIDI to Masahiro Ishii. John Thickstun, Zaid Harchaoui and Sham M. Kakade provide the aligned labels under CC BY 4.0. The release includes licensed and public-domain recordings. Download the source locally. Recording 1931 was held out of Djound development until this result. It occurs in the published MusicNet training splits used by YourMT3+, so it measures the complete engine on a new local input with prior model exposure. [MusicNet release and credits](https://zenodo.org/records/5120004) · [YourMT3+ preprocessing](https://huggingface.co/spaces/mimbres/YourMT3/blob/main/amt/src/utils/preprocess/preprocess_musicnet.py)

## Repeat the check

Use the local engine's Python environment from the repository root. Obtain `1931.wav` and `1931.csv` from MusicNet's `train_data` and `train_labels` directories or from its byte-identical mirror. The runner verifies the two original files, crops frames 2,084,161 through 3,830,237 at 44.1 kHz and reconstructs program 43's score beats [66, 114). It prints the studio upload path. [Individual files in the mirror](https://huggingface.co/datasets/DreamyWanderer/MusicNet/tree/main/data/train)

```sh
musicnet_work=$(mktemp -d)
curl -fL https://huggingface.co/datasets/DreamyWanderer/MusicNet/resolve/main/data/train/1931.wav -o "$musicnet_work/1931.wav"
curl -fL https://huggingface.co/datasets/DreamyWanderer/MusicNet/resolve/main/data/train/1931.csv -o "$musicnet_work/1931.csv"
.runtime/local-engine/venv/bin/python tests/musicnet_melody_acceptance.py "$musicnet_work/1931.wav" "$musicnet_work/1931.csv" "$musicnet_work" --piece american
```

Upload the printed WAV and ask “Turn this into music for a brisk morning walk. Give it a steady, energetic beat and let me hear the tune.” Score the first take's saved `source-notes.json` against the original performance times.

```sh
.runtime/local-engine/venv/bin/python tests/musicnet_melody_acceptance.py "$musicnet_work/1931.wav" "$musicnet_work/1931.csv" "$musicnet_work" --piece american --trace /path/to/first-source-notes.json
```

The command prints a JSON report and exits with status 1 when a threshold is missed. Each pitch must match in the correct octave within one semitone. Each output attack can match one reference attack. Required recall is 80% overall and 70% in each phrase. MusicNet omits some pizzicato parts, so only a subset of errors can be attributed to competing voices.
