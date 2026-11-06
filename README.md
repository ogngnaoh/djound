# Djound

![djound wordmark beside the notes of the sample melody](showcase/release/header.png)

a local music studio where you can rearrange a melody in your own words.

https://github.com/user-attachments/assets/62703213-9001-49cb-a7bd-21509d79f341

in the demo, i start with a piano phrase from *to zanarkand*, check the notes, and try it on electric piano with bass and drums. then i take the drums out and soften the bass. you can hear the original at 0:15 and the final take at 0:27. the film uses audio from the app and shortens the processing waits. [watch or download the film](showcase/release/zanarkand.mp4) · [music credits](#credits)

## in the studio

![studio with the original, its versions and the selected arrangement](showcase/local-studio/studio.png)

start from the original or pick a version you like. try “make this soundtrack jazz like” or choose **try swing combo**. review the chords and tempo, then create a version with piano, walking bass and swing ride behind your recording. select that version and type “quieter drums” to lower just the drums by 3 dB.

you can also change a single chord, set a track’s gain or pan, and preview a warmer or brighter tone in the full mix. keep the takes you like in your library.

<details>
<summary>see the library</summary>

![library with a kept take ready to play](showcase/local-studio/library.png)

</details>

## run it locally

use an apple silicon mac with python 3.11, uv, apple command line tools, ffmpeg, pnpm 11.5.0 and docker with compose. start docker, then run these commands from the repo root.

```sh
bash scripts/bootstrap.sh
bash local-engine/setup_localengine.sh --audio-only
bash scripts/package.sh
DJOUND_MODE=LOCAL bash scripts/run.sh
```

open [the studio](http://127.0.0.1:8080/?mode=backend) and upload [the sample melody](fixtures/audio/local-demo-source.wav) or your own isolated phrase. use a 1 to 12 second pcm16 wav or mp3 at 44.1 or 48 khz. choose **detect melody**, listen, then select **use this melody**. the first setup downloads the tools and audio assets. after that, editing runs on your mac.

## how it works

basic pitch detects the notes. you can correct them, keep the uploaded performance as the lead, or play them on a different instrument. the local renderer builds the backing from the arrangement you approve. each version saves its notes, settings and audio, so you can come back to the same take.

the setup above includes the built in text edits and direct controls. for broader phrasing, run `bash local-engine/setup_localengine.sh` to add the optional qwen planner. it can miss unfamiliar wording. specific requests such as “quieter drums” and the named arrangement choices are the most dependable way to use the editor.

react and typescript run the studio. spring boot manages the api, postgres keeps project history, and a python worker handles transcription and rendering.

## running and testing

after setup, run `DJOUND_MODE=LOCAL bash scripts/run.sh --check` to check the installation and `bash scripts/run.sh --status` to check the running server. set `DJOUND_PORT` if port 8080 is busy. if the worker reports memory pressure, close other large apps before trying again.

back up postgres and the media folder together with the app stopped. the defaults are the `djound_postgres` docker volume and `.runtime/data`. keep the installation identity with its media.

run `bash scripts/verify.sh` for the build, api, database and browser checks. docker and chrome or playwright chromium must be available. after packaging and installing the audio engine, run `bash scripts/verify-local.sh jazz` for a real upload, two arrangements, playback and recovery after restart. the [api contract](contracts/openapi.yaml) defines the interface shared by the studio and server.

## credits

*to zanarkand* from *final fantasy x* was composed by Nobuo Uematsu. the piano phrase in the demo derives from Nitayahisa’s midi sequence. djound rendered the electric piano arrangement. the composition and source arrangement have separate rights from the project’s MIT license. the film was edited with Remotion. [source sequence](https://www.vgmusic.com/file/49364e75d5e976e0c5e41e6283486541.html) · [remotion license](https://github.com/remotion-dev/remotion/blob/v4.0.541/LICENSE.md)

the included studio sample and its browser copy are an original synthetic melody under the [project license](LICENSE). the three other authored fixtures are dedicated under [CC0](https://creativecommons.org/publicdomain/zero/1.0/). the browser’s warm and pulse clips use their first twelve seconds. [sample notes](fixtures/audio/local-demo-source.json) · [fixture credits and hashes](fixtures/manifest.json)

the interface uses Inter under the [SIL open font license](web/public/licenses/inter-OFL.txt). local setup downloads Basic Pitch and optional Qwen weights under Apache 2.0. MLX LM, TinySoundFont and the FluidR3 GM sound bank retain their upstream MIT terms. setup keeps upstream notices with the installed assets. [asset revisions and checksums](local-engine/assets.json) · [engine requirements](local-engine/requirements.lock) · [sound bank credits](https://github.com/pianobooster/fluid-soundfont/blob/main/COPYING)
