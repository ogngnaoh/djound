# Third-party notices

Original Djound code is distributed under the [MIT license](LICENSE). The components and assets below retain their respective upstream terms. This notice records provenance; it does not replace those licenses or grant additional rights to third-party material. Dependency versions are pinned in the application and engine lockfiles.

## Assets included in the interface and repository

| Material | Source and terms |
| --- | --- |
| Inter font | `@fontsource-variable/inter` 5.3.0, used by the application. Copyright 2016 The Inter Project Authors. [SIL Open Font License 1.1](web/public/licenses/inter-OFL.txt), retained from the installed package and included in web builds. [Upstream Inter](https://github.com/rsms/inter). |
| Prototype sample audio | `warm.wav` and `pulse.wav` derive from the authored fixtures listed in [fixtures/manifest.json](fixtures/manifest.json), dedicated under CC0 1.0 in [fixtures/README.md](fixtures/README.md). [Prototype provenance](web/public/audio/PROVENANCE.txt) records the mapping. |
| Local demo source | `fixtures/audio/local-demo-source.wav` and its byte-identical `web/public/audio/original.wav` copy are a separately authored synthetic melody. [Source metadata](fixtures/audio/local-demo-source.json) identifies the composition and preparation. This original project fixture is provided under the [project MIT license](LICENSE), separately from the three CC0 fixtures above. |
| Djound artwork | The application includes its published [brand marks](web/public/brand/). |

## Featured film

The [featured Djound film](showcase/release/zanarkand.mp4) contains an excerpt of *To Zanarkand* from *Final Fantasy X*, composed by Nobuo Uematsu. The uploaded piano phrase derives from [Nitayahisa’s MIDI sequence](https://www.vgmusic.com/file/49364e75d5e976e0c5e41e6283486541.html). Djound renders the electric-piano arrangement locally. The composition and source arrangement are excluded from the project’s MIT license.

## Downloaded during local engine setup

These model weights and rendering resources are not committed to the source repository. [assets.json](local-engine/assets.json) specifies exact source URLs, revisions, lengths, and integrity hashes; [requirements.lock](local-engine/requirements.lock) pins Python dependencies. Setup retains available upstream notices alongside the installed assets.

| Component | Pinned identity and upstream terms |
| --- | --- |
| Qwen3.5-0.8B, MLX 4-bit conversion | `mlx-community/Qwen3.5-0.8B-4bit` revision `da28692b5f139cb0ec58a356b437486b7dac7462`; Apache-2.0 model metadata. [Official model](https://huggingface.co/Qwen/Qwen3.5-0.8B) · [Pinned community conversion](https://huggingface.co/mlx-community/Qwen3.5-0.8B-4bit/tree/da28692b5f139cb0ec58a356b437486b7dac7462). |
| Qwen3-4B-Instruct-2507, MLX 4-bit conversion | The arrangement director uses this Apache-2.0 model. Djound pins and verifies the conversion through [its asset manifest](local-engine/score-director-assets.json). [Official model](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) · [Pinned community conversion](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit/tree/50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b). |
| Spotify Basic Pitch 0.4.0 | Copyright 2022 Spotify AB. [Apache-2.0](https://github.com/spotify/basic-pitch/blob/main/LICENSE). Setup extracts the pinned wheel, retaining its `dist-info` license. |
| MLX LM | Copyright © 2023 Apple Inc. [MIT](https://github.com/ml-explore/mlx-lm/blob/main/LICENSE); version pinned in the engine lockfile. |
| TinySoundFont | Commit `853a0a171759f1ddba0de1442133a75912bbeffa`, [MIT](https://github.com/schellingb/TinySoundFont/blob/853a0a171759f1ddba0de1442133a75912bbeffa/LICENSE). Copyright 2017–2023 Bernhard Schelling, based on SFZero by Steve Folta (2012). Setup retains `TinySoundFont-LICENSE`. |
| FluidR3 GM 3.1 | [Sound-bank release](https://github.com/pianobooster/fluid-soundfont/releases/tag/v3.1), [MIT copyright and license](https://github.com/pianobooster/fluid-soundfont/blob/main/COPYING). Copyright 2000–2002, 2008 Frank Wen. Setup retains the hash-verified `FluidR3-COPYING`. |

## Song rendition engines

ACE-Step 1.5 is developed by the ACE-Step authors. The optional Python HTTP service uses the MIT-licensed reference implementation, copyright (c) 2026 ACEStep. The authors identify their published checkpoint bundle as MIT. The bundle includes Qwen3-Embedding-0.6B, whose upstream model card identifies Apache-2.0. [ACE-Step source license](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE) · [ACE-Step checkpoint card](https://huggingface.co/ACE-Step/Ace-Step1.5/blob/19671f406d603126926c1b7e2adc169acbcade22/README.md) · [Qwen embedding model](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)

The native Metal backend builds the ACE-Step C++ implementation, copyright (c) 2023–2026 The acestep.cpp authors, and its GGML submodule, copyright (c) 2023–2026 The ggml authors. Both retain their MIT license files in the installed source trees. The GGUF weights come from Serveurperso’s MIT-labeled conversion. Djound verifies the Q4_K_M Turbo model for covers and repaints, the Q4_K_M Base model for accompanying instrumental parts, the shared Q8_0 text encoder and the BF16 audio codec by SHA-256. The authors identify the Base checkpoint as MIT. [Native source license](https://github.com/ServeurpersoCom/acestep.cpp/blob/973517be024ff8b6bbd50e0adc9e308ab2ed71b3/LICENSE) · [GGML license](https://github.com/ServeurpersoCom/ggml/blob/ef4555b7aede4467a5de803b103615e0a5f30dd8/LICENSE) · [Base checkpoint](https://huggingface.co/ACE-Step/acestep-v15-base) · [Conversion snapshot](https://huggingface.co/Serveurperso/ACE-Step-1.5-GGUF/tree/666ac70204440867d8c01ba4b119cc79c95b370a)


## Other software dependencies

JavaScript, Java and Python dependencies retain their upstream licenses. Versions are recorded in the [web manifest](web/package.json), [Java manifest](server/pom.xml) and [engine requirements](local-engine/requirements.lock). The featured film was rendered with Remotion 4.0.541. [Remotion license](https://github.com/remotion-dev/remotion/blob/v4.0.541/LICENSE.md)

## Finished mix separation

SCNet small by Weinan Tong and coauthors estimates drums, bass, other instruments and vocals. Its [author's license clarification](https://github.com/starrytong/SCNet/issues/35) grants MIT terms for the released checkpoints and redistribution of ONNX conversions with attribution. Copyright (c) 2024 starrytong. The original model was trained on MUSDB18-HQ. The converted [audiojs model card](https://huggingface.co/audiojs/scnet/tree/e71bcdb42bce1d62b7fae25ba90fcbcf90f73f46) identifies the upstream checkpoint and conversion procedure. The installer verifies the model and license against the [finished mix manifest](local-engine/finished-mix-assets.json) and stores the MIT license beside the weights.

The SCNet processing in Djound follows the format and overlap-add behavior published in [@audio/neural-separate 0.7.1](https://www.npmjs.com/package/@audio/neural-separate/v/0.7.1), copyright 2026 Dmitry Iv. and audiojs contributors, and [Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training), copyright (c) 2024 Roman Solovyev (ZFTurbo). Their MIT terms are reproduced in the [SCNet processing notice](local-engine/licenses/scnet-processing.txt).
