# Architecture

Djound serves the React studio and its API from a local Spring Boot process. PostgreSQL records projects, take history and preferences. Audio lives on disk. Three workflows share this installation. Reimagine performs a new version of a recording, Mix editor adjusts estimated instruments in a stereo passage, and the melody editor builds an arrangement from confirmed notes. [Rendition API client](../web/src/renditions/api.ts) · [Finished mix client](../web/src/mixes/api.ts) · [OpenAPI contract](../contracts/openapi.yaml)

```mermaid
flowchart LR
    Studio[React Studio] --> API[Spring Boot]
    API --> DB[(PostgreSQL)]
    API --> Rendition[Rendition worker]
    Rendition --> Caption[Named style or Qwen musical direction]
    Caption --> Director[Local score director]
    Rendition --> Tune[Transcribed tune and phrase timings]
    Tune --> Director
    Director --> Score[Perform an editable instrumental score]
    Score --> Media[Local audio files]
    Score --> ACE[Optional ACE-Step production]
    Rendition --> ACE
    ACE --> Media
    API --> Worker[Local Python worker]
    Worker --> SCNet[SCNet passage separation]
    SCNet --> Estimates[Cached vocals and drums]
    Estimates --> Delta[Gain delta over decoded stereo]
    Delta --> Media
    Worker --> Notes[Basic Pitch transcription]
    Worker --> Plan[Arrangement planner]
    Worker --> Render[Soundfont renderer and stereo mixer]
    Render --> Media
    Media --> API
```

## From a recording to a rendition

### Why the studio keeps an arrangement

A first request such as “make this synth-pop” gives the performers considerable freedom. The next request, “keep the pulse and give the tune to a cello,” commits the studio to decisions the listener has already heard. Djound uses one musical director across styles. The language model interprets the request and can revise the saved arrangement. A local player realizes the notes. The application owns playback, parent selection, saving, and the sample boundaries of passage edits. Each stage runs in turn on the local machine.

ACE-Step offers source-conditioned covers and passage repainting. Those operations are useful when a request concerns the sound of a performance. A new generative render can vary details the listener liked. The score route gives Djound control over which players and written parts change. YuE2 provides a related example of modifying harmony in a machine-readable score before rendering. Its authors also distinguish equality of the written notes from equality of the resulting audio. Djound keeps the audio of every completed take for that reason. [ACE-Step inference operations](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INFERENCE.md) · [YuE2 score editing](https://github.com/multimodal-art-projection/YuE/blob/main/docs/editing.md)

Choose the next change by listening to where the performance misses. A lost tune points to source interpretation. A clear tune with the wrong groove points to arrangement. The intended instruments playing the right parts with an unconvincing sound point to the player or audio model. These failures call for different work, even when the listener describes each as “that doesn't sound right.”

### From request to playback

1. An import starts a project at `/renditions/{id}`. FFmpeg decodes the recording into a 44.1 kHz stereo PCM16 WAV. The project keeps that file as its original and the database records its digest. Import and take requests have stable retry keys so a lost response can be recovered without creating a second project or take.
2. The listener chooses the original or a saved take as the source. The request names that parent explicitly. Jazz Trio, Solo Piano, Lo-fi Hip-hop and Cinematic Strings each supply a musical description. For free-form requests, the worker uses the same local Qwen3 4B Instruct model that directs the score. It reads the parent’s description, recent requests from that branch, and whether the listener selected a passage. It returns a description and a scope, or asks for clarification. A clarification creates a history entry the listener can revise. Older installations use Qwen3.5 0.8B when the verified 4B weights are unavailable.
3. A whole-recording request uses the performance workflow described below. An ordinary local edit uses ACE-Step's repaint operation. An explicit range is measured on the parent recording. An explicit “last 8 seconds” request uses those exact times. For an opening or ending without a length, the worker uses the first or last quarter, capped at 16 seconds. Named sections such as a second chorus need a listener selection. “Closer to the original melody” uses the imported audio as the musical reference. A selected passage follows the selected take’s song-time map to find the corresponding passage in the upload and retains the selected take everywhere outside that interval. Without a time scope, it makes a complete new version. The Apple Silicon path runs the `ace-synth` Metal executable with GGUF weights. An alternative adapter sends the request to a configured ACE-Step HTTP service. [Rendition worker](../local-engine/rendition_worker.py) · [Audio adapters](../local-engine/rendition_audio.py) · [Setup](running.md#generate-song-renditions)
4. The worker returns a full-length stereo WAV. For a short passage on a long track, the native path sends the selection with 12 seconds of context on each side, borrowing context from the other side at a song boundary. It keeps the whole recording if the window spans at least three quarters of the take. It pads the model input to its 80 ms grid and crops the generated audio to the real sample count. For a passage the worker blends the generated samples inside the boundaries and copies the rest from the parent. Before publication, Spring checks the WAV format and length, verifies that samples changed, and checks that every sample outside a requested passage still matches its parent. How closely the result carries the source melody, style, and instrumentation depends on the model and recording. [Audio checks](../server/src/main/java/com/djound/rendition/RenditionAudio.java)

Jobs run in a queue. After a restart, queued jobs can proceed and an interrupted running job is marked failed for deliberate retry. Each ready take has its own immutable file, request, parent ID and SHA-256 digest. Playback and export serve that file. Keeping a favorite changes which take the project opens on, and the listener can still play or develop any other saved take. [Project and job storage](../server/src/main/java/com/djound/rendition/RenditionProjects.java)

A local edit in a scored take introduces audio the original score cannot reproduce. On a later whole-take revision, the server can recover the score input, the performed take and the edited frame ranges from the selected branch. The worker uses that source for the score, so a saved intro or coda is played once. The listener can keep the earlier passage exactly or have the new direction performed there too. Keeping it copies the selected samples and blends at their outer edges. Developing it uses the selected passage as the audio reference while playing the score elsewhere. The revised take retains the passage history for the following request. Keeping passages on their exact timeline requires the new score to fit that timeline. A fresh interpretation can have a different form.

By default a change of lead instrument reaches the whole take, including previously edited passages. For a lead swap in a passage with a traceable tune, the worker follows that passage's melody and reuses its estimated accompaniment. It matches the level across short intervals to preserve accents and fades. A quiet rest retains its recorded sound. The listener can explicitly protect a passage or start a fresh interpretation. When instructions about a passage conflict, the studio can save a question. Answering from history sends its ID and the listener's choice. The server recovers the original words and parent from that question. [Rendition requests and answers](../server/src/main/java/com/djound/rendition/RenditionProjects.java) · [Performing a revised lead](../local-engine/rendition_performance.py)

### How the tune reaches the new performance

Basic Pitch proposes notes from the whole recording. For a single clear line, pYIN follows its pitch directly. With several parts playing, the worker can ask SCNet for an estimate of the singer. It follows that line when the estimated voice is audible, has sustained pitch and agrees with note candidates in the mix. The mix still supplies the harmony. During a longer vocal break a distinct instrumental phrase can take over the tune. When the vocal evidence is weak or the separator is unavailable, the worker follows the local upper register and checks note entrances against the recording. Lower or crossing instrumental leads can still be missed. [Basic Pitch](https://github.com/spotify/basic-pitch#readme) · [SCNet](https://github.com/starrytong/SCNet) · [Melody extraction](../local-engine/rendition_melody.py)

The worker estimates the pulse and finds phrase boundaries from attacks and rests. Repeated pitch intervals help locate returning phrases. New scored takes record both the transcription version and which signal supplied the tune. A revision that uses the same recording reuses that choice. A saved vocal take needs the separator to recover its tune. Takes created with earlier transcribers retain their original behavior. If a saved passage follows a different recording and the listener asks for the upload's melody, the studio explains how to start a fresh arrangement from the upload. [Phrase analysis and arrangement](../local-engine/rendition_score.py) · [Recording selection on revision](../local-engine/rendition_performance.py)

For a whole take with a usable tune, the score director reads the listener's words, a summary of the source phrases and the selected take's saved arrangement when available. It chooses a tempo, meter, feel, lead instrument, backing parts, phrasing and ending. The suggested styles supply ordinary written directions. A request like “make it jazz” leaves the ensemble open. A follow-up such as “give the melody to a cello” can change the lead program while inheriting the bass and drum parts. The director runs in a separate process and exits before rendering. [Director](../local-engine/rendition_score_plan.py) · [Performance workflow](../local-engine/rendition_performance.py)

The player places the source tune on the chosen pulse, voices chords from the recording and writes the supporting players' notes. The director can keep a steady backing, have players answer in spaces the lead leaves, or build up a returning motif. The player places those changes using detected phrases and scored note entrances. The director assigns the response to individual instruments. A piano can answer the lead while the bass keeps walking and the drummer plays the written ride figure. Short breaths can hold a single answer. A lead-instrument revision inherits these decisions from its parent. TinySoundFont plays the score through a local instrument bank. ACE-Step can treat that performance when a direction calls for studio effects or timbres beyond the player. This adds variation across renders. Available gestures and the instrument bank limit what a written direction can become. A whole take can have its own tempo, intro and coda. The worker returns a map from performed phrases to points in the source along with the private arrangement. Spring composes this map with the parent's map and saves it with the take. [Score renderer](../local-engine/rendition_score_render.py) · [Song-time storage](../server/src/main/java/com/djound/rendition/RenditionTimeline.java)

Passage revisions use ACE-Step on the selected audio. The worker preserves the samples outside the edit. When the upload supplies the requested melody, the worker aligns its guide with the passage in the selected take. The older guided-cover path remains available to installs without the score player and for requests to recover a theme using the selected ensemble. It can combine the guide with a generated backing, or use ACE-Step Base for one supporting part. When transcription cannot find a dependable line, the worker can use an audio cover directly. [Audio workflow](../local-engine/rendition_performance.py) · [Guide alignment](../local-engine/rendition_timeline.py)

For “I like that beat. Bring back the wistful feeling,” the director can keep the ensemble and write a new delivery for the lead. It specifies how strongly the first, middle and last notes of a phrase are played, how long to hold them and how early or late to land. The player applies these marks to the source notes. Earlier scores keep their original delivery until it is revised. This approach draws on experiments with timing and dynamics in expressive performance. Whether the result conveys the listener's intended feeling still requires listening. [KTH expressive performance research](https://www.speech.kth.se/music/performance/performance_emotion.html) · [Magenta performance examples](https://magenta.tensorflow.org/performance-rnn)

A lead swap can affect the loudness of the accompaniment even when the accompanists play identical notes. A different instrument changes the peak of the summed mix. For a scored revision with identical backing, timing and room, the player reuses the saved master gain. It reduces the changed lead if that leaves enough headroom, and can attenuate the whole mix when necessary to prevent clipping. For an older take without a stored gain, it first checks a reconstruction against the audio the listener heard. [Score player](../local-engine/rendition_score_render.py)

When a direct revision of a saved score produces the same tracks, duration and room, the director gets one more opportunity to arrange the source. It interprets the listener’s words and the recorded phrases before it sees the previous band. A listening purpose such as music for studying gives it room to choose a different pace and texture. For a specific edit it brings back the selected arrangement and checks which parts to keep. If the players would still perform the same score, the studio asks what the listener would like to change. Each completed WAV also undergoes the sample comparison before publication. [Revision logic](../local-engine/rendition_performance.py)

On a revision, the arranger can propose a new tempo even when the rest of the plan changes only the melody. If the two disagree, the director uses the listener’s words to decide whether the band can change pace. A request for music for a bike ride gives the arranger that choice. “Keep that beat and make the piano more energetic” keeps the current pace. A tempo the listener explicitly requests takes precedence over the chart. The director also distinguishes a desired tempo from a reference to an earlier take or a speed the listener dislikes. [Planning tests](../local-engine/test_rendition_score_revision.py)

### How to judge an engine change

Agents choose representative recordings and write the directions a casual listener might give. They run the studio headlessly from import through export, one local inference at a time. For each revision, compare the same musical phrase across the source, the selected take and the result. Check both what was asked to change and what was asked to stay. When a check fails, repair the responsible stage and replay the same request. Agents own that decision to proceed. Voluntary listening feedback can supply a new case or describe a preference the audio measurements cannot establish.

| Listener's expectation | What the engine can check | What listening feedback can add |
| --- | --- | --- |
| “I can hum my tune in the new style.” | Compare the performed melody with known source phrases. Include rests and returning motifs. | Can someone recognize the tune, and does the playing evoke the requested style? |
| “I love that beat. Change how the tune feels.” | Compare the rhythm section's note onsets, releases, pitches and velocities on the selected take. Check that the lead's delivery actually changes in the audio. | Does the new delivery express the requested feeling? |
| “Use that instrument through the ending I liked.” | Inspect a sounding passage and a fade to silence. Check the instrument assignment, passage timing and level over time. An explicit request to keep the exact recording can be checked sample for sample. | Does the new player sound convincing alongside the same accompaniment? |
| “Play that version again.” | Verify playback and export against the saved WAV. Describe the parts the score actually sent to the player. | Is this a finished take someone wants to keep? |

For a change in genre, the relevant comparison is an audio cover and a scored interpretation of the same recording and direction. For a follow-up that must preserve a part, compare with the selected parent as well. The score offers control over the written parts. ACE-Step can produce sounds and transitions beyond the local instrument bank. Their musical merits can differ by recording and request. [ACE-Step cover and repaint operations](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INFERENCE.md#task-types)

Local runs have exercised this distinction on a piano recording recast as synth-pop and on a sung tune arranged for flute and harp. Changing the synth lead's expression preserved every written bass and drum event. After comparing the two synth-pop takes, the listener confirmed that the new performance delivered the requested wistful feeling while keeping the beat. Changing how the flute played preserved the harp's notes, including their timing and velocity. A later clarinet revision developed a previously edited, sounding passage. Its overall dynamics followed that passage, with some variation in individual attacks. An accompaniment inferred from a stereo recording can only approximate the original players.

Three short recordings checked which tune the engine carries into an arrangement. The two vocal comparisons allow a one-semitone pitch tolerance. Each row describes an individual excerpt.

| Recording | How the reference was obtained | Earlier trace | Current trace |
| --- | --- | --- | --- |
| A singer with added guitar, keys, bass and drums | 28 known notes from the first four lines of *Twinkle, Twinkle, Little Star*, timed against the unaccompanied voice. [Dcoetzee recording](https://commons.wikimedia.org/wiki/File:Twinkle_Twinkle_Little_Star_-_sung_with_full_lyrics.ogg) | 0 of 28 | 28 of 28 |
| Clara Berry And Wooldog, *Waltz For My Victims* | 14 notes estimated from the released isolated vocal in a 6.8-second excerpt. [MUSDB18 stems](https://sigsep.github.io/datasets/musdb.html) | 9 of 14 | 14 of 14 |
| Soni Ventorum and guests, Mozart K.375, fourth movement | 36 first-clarinet score notes aligned to the wind ensemble recording. Other winds can double the tune. [Recording](https://commons.wikimedia.org/wiki/File:Wolfgang_Amadeus_Mozart_-_Serenade_K.375_-_4._Menuetto.ogg) · [MusicNet annotations](https://zenodo.org/records/5120004) | 31 of 36 | 31 of 36, with the same pitches and timings |

Imported through the studio, the first recording produced a 28.7-second piano take. Asking for the tune on clarinet kept all 42 piano accompaniment notes with their original pitches, timing and velocities. The saved WAVs matched their respective playback and export responses. The controlled vocal mix and its transcription check are available for offline regression testing. [Fixture and instructions](../tests/fixtures/controlled-vocal-and-band.md)

## From a stereo mix to a kept take

The worker decodes the MP3 to a fixed 44.1 kHz stereo PCM16 WAV. A selection freezes the source take and start and end sample indices. SCNet estimates the vocal and drum signals with up to two seconds of surrounding audio on each side. The preparation cache includes the source WAV digest, sample range, decoder configuration, model, preprocessing revision and inference backend.

The interpreter reads the requested instrument, action and quantity against the preview that played. “Set that to +1 dB” changes the final level. “Halve that change” scales the previous move from its starting level. Copying a setting requires a named take or a listener's choice when earlier takes have different settings. A clarification leaves the audio untouched until the musician submits a new request or confirms an exact level.

The worker adds the gain difference for each estimated source to the original selected samples. It blends the wet mix back to the dry baseline over 10 ms at both ends. A boost that would clip returns suggested alternatives. Applying a passage trim requires an explicit choice. PCM samples outside the passage keep their exact values.

Each preview has a durable ID and full WAV hash. Keep associates the auditioned artifact with its parent and request receipt. A/B, reopen, undo and export select immutable artifacts. A newly selected passage uses the kept version as its base. [Model and weight provenance](../THIRD_PARTY_NOTICES.md#finished-mix-separation)

## From a melody recording to an arrangement

1. Spring validates and saves the upload. Basic Pitch proposes notes. The listener compares them with the original and confirms or corrects them.
2. The planner starts from the chosen version. Built in text edits, named arrangements and the optional Qwen planner produce a recipe for review. Direct controls edit that same recipe. A swing combo can use the confirmed notes to suggest chords and a pulse.
3. Creating a version freezes its source, note revision and recipe. The worker decodes the uploaded performance or plays the lead on the selected instrument. It renders separate backing tracks, applies the approved gain, pan and tone, and checks the stereo output.
4. Spring publishes the recording after validation. A new version can inherit its settings. Earlier versions retain their audio, including when the listener later revises the melody.

## Playback and recovery

In the rendition studio, one browser player switches between the original and saved takes at the corresponding musical position using its saved phrase map. Equal-time takes use the existing playback position. In the melody editor, a tone audition remixes the verified dry tracks of a saved version and returns a temporary WAV. Only an explicit creation adds that arrangement to the library.

The melody editor uses draft and note revisions to protect against stale edits. Job records and request receipts distinguish a retry of an accepted request from the creation of a new version. The local workers use deadlines, and the rendition queue processes one take at a time.

[Worker assets and checksums](../local-engine/assets.json) · [Local setup and storage](running.md)
