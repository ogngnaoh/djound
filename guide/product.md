# Reimagine a recording

Djound is an open-source music studio for someone who can say what they want to hear, even if they have never arranged a song. Bring a recording you care about. Hear another group of instruments perform it. Keep the version you find yourself playing again.

## A listener's first session

| What I want to do | What I expect to hear or see |
| --- | --- |
| Bring a song, a friend's performance or a hummed voice memo. | I can play my original and return to it throughout the session. |
| Pick a sound when I have no idea what to type. | One suggestion gives me a complete instrumental take. I can also describe a sound that has no button. |
| Recognize a tune in another genre. | The phrase I would hum comes through. The players can change the groove, chords, tempo and phrasing. On a vocal recording an instrument carries the sung line. |
| Keep the pulse and give the tune to a cello. | The next take develops the one I chose. The bass and drums keep the rhythm I liked while I hear a different lead. |
| Change only the ending. | I can select it, describe the change and hear the transition in context. The rest of that performance stays intact. |
| Make one more change after I like the new ending. | I can keep the exact ending I heard or ask the players to apply the new direction there too. I can compare both versions with the take I chose. |
| Keep my favorite or send it to a friend. | Reopening the project brings me back to that take. Export gives me the same performance I heard. |

## Decisions that shape the studio

Every project starts with a recording. Text directs a performance of that music. A short memo can become a complete performance of a short phrase. The first release keeps all renditions instrumental.

Suggested styles help someone start listening. A description like “swinging piano trio” names the group. A broader request like “make it jazz” gives Djound room to choose one. A mood, an unusual group of instruments or a direction about a single phrase can also be enough to act on.

After a listener edits an ending, “give the melody to a cello” applies to the whole performance, including that ending. The new player follows the phrasing, groove and fade the listener liked. A rest can still be a rest. “Leave the ending exactly as it is” carries its recorded audio forward. If two instructions conflict about a saved passage, the studio can ask which one to follow. The answer belongs to the recording and request that prompted it, even if the listener has since played another take.

The listener always has a take to play while another is being made. A whole interpretation can find a new tempo or leave space for an ending. Playback follows the corresponding phrase when comparing scored takes. If a request names a chorus the studio cannot locate, the listener can select the passage themselves.

Suno is the reference for three actions in this journey. Covers use a song's melody as the starting point for a change of style. Studio Chat takes direction in the context of the project and selection. Its editor lets the listener hear a replacement in place. Djound applies those ideas to a first release that begins with an uploaded recording and ends with a playable instrumental file. Suno's public guides describe these listener actions. Djound's implementation choices are documented in its own architecture. [Suno Covers](https://help.suno.com/en/articles/2872257) · [Studio Chat](https://help.suno.com/en/articles/13670721) · [Song Editor](https://help.suno.com/en/articles/6141505)

## What still needs a listener's ear

Can someone recognize the piece with their eyes closed? Can they hear why the musicians belong in the style they asked for? After one more instruction, can they hear both the change and the part they wanted to keep? Those questions guide work on the musical engine and apply to every style.

The listener recognized *To Zanarkand* in an early rendition but missed the jazz feel. They preferred a short arranged trio sketch. After hearing a later full-length trio perform the tune, they said they would keep that version. That full performance is the reference for what this listener wanted from the trio.

For a synth-pop take, the same listener asked, “I like that beat. Bring back the wistful feeling.” They heard the change they wanted in the revision. The rhythm section played the same notes while the lead changed its timing, accents and note lengths. These two sessions capture different reasons to keep a take, both expressible in everyday words.

Refining a completed take and exporting it are available today. Extending a short audition into a longer performance while keeping the exact audio someone fell in love with is an open product capability. [How renditions work](architecture.md#from-a-recording-to-a-rendition)

## First sessions with three different recordings

Agents ran three first sessions from the recordings library. Each imported a recording, played it, made an instrumental, asked for one change, compared the result with the earlier take and kept a favorite. Each favorite reopened in a fresh browser and its downloaded WAV matched the audio served for playback.

| Recording | What the listener could ask for | What the studio played |
| --- | --- | --- |
| A 4.45-second solo hum recorded on a phone. [Mahakush’s recording](https://freesound.org/people/mahakush/sounds/368817/) | Choose Solo Piano, then keep the piano accompaniment and put the tune on cello. | The pianist played the two rising figures as part of an 18-second arrangement. On the first revision the accompanying piano became 3.75 dB louder. After the level fix the piano-only opening matched the parent sample for sample, and the cello entered on the tune. |
| A singer with a higher guitar line, keys, bass and drums. [Recording and credits](../tests/fixtures/controlled-vocal-and-band.md) | Ask for a folk duet with flute and fingerpicked guitar, then give the tune to clarinet while keeping the guitar part. | The transcriber found all 28 reference notes. The lead changed from flute to clarinet while all 36 guitar notes kept their pitch, timing and velocity. |
| The opening of Mozart’s K.375 Menuetto, played by Soni Ventorum and guests. [Recording](https://commons.wikimedia.org/wiki/File:Wolfgang_Amadeus_Mozart_-_Serenade_K.375_-_4._Menuetto.ogg) | Choose Cinematic Strings, then ask for a gentler melody over the same accompaniment. | The source transcription matched 31 of 36 reference clarinet notes. In the revision, the viola attacked all 38 arranged melody notes more softly and moved 33 entrances. All 66 supporting string and bass notes kept their pitch, timing and velocity. |

Reopening a favorite exposed a navigation problem once a library had more than five recordings. The main library now includes **View all recordings** when its recent list is truncated. Reopening an older project from that link selects its saved favorite.

In the unaided session, the listener chose the saved *To Zanarkand* trio and asked, “make a study soundtrack for me to focus.” The studio reported that it had produced the same audio. The director had changed its description while the band played precisely the previous take. A first repair asked for clarification. The second repair used the same words and saved trio to produce a complete 2:58 instrumental. The 120 BPM swung piano lead became an 88 BPM straight guitar lead. The longer notes and fewer entrances gave the tune more space, and the guitar followed all 378 transcribed notes in order, allowing for octave changes. The agents verified playback, export and the original favorite. The listener delegated the final check to the agents.

On the phone memo’s piano take, the agents asked, “Give me something energetic for the last hill of my bike ride.” The first version shortened the notes but stayed at 72 BPM despite a new chart calling for 120. After the fix, the same words produced a 120 BPM performance with the same seven melody notes. The earlier piano and cello takes remained available.

## When several instruments could be the tune

In a Brahms trio, the violin begins the tune and the horn takes it over while the violin climbs above it. The agents asked for music for a brisk morning walk. The studio played a 120 BPM trumpet arrangement. On the first revision a cello played the same line an octave lower. When the direction explicitly said “in the same octave as this trumpet,” the cello played all 46 arranged notes at the trumpet’s pitches, with the same entrances, lengths and accents. All 236 backing notes and the audio produced by those players stayed identical.

The agents also went back to the trumpet take and asked, “Keep the trumpet melody exactly as it sounds, including its phrasing, timing and volume. Add a softly strummed acoustic guitar to the accompaniment.” The result added 135 steel-guitar notes. Muting just that guitar reproduced the entire earlier four-part performance sample for sample. The saved cello favorite still opened after the guitar take was exported.

For a late-night study request, the agents used a Schubert excerpt with the recorded cello brought forward against piano and violin. The first take carried all 14 reference cello notes into a 100 BPM piano arrangement. Giving the tune to cello kept all 42 accompaniment notes and their rendered audio. The study prompt and walking prompt were chosen before generation. Tempo, sounding notes and preserved parts describe what the studio played. Whether either performance feels right for a particular walk or study session is a matter of taste. [Source recordings and repeatable checks](../tests/fixtures/layered-melodies.md)

An independent agent also set aside an excerpt of the “Trout” Quintet, in which the viola plays the theme beneath a faster violin. On the first blind run the study arrangement followed only 11 of the 30 reference notes. A cello revision then faithfully preserved the wrong line and all 66 backing events. Playback and export agreed on every sample. This case is kept in the regression suite because verifying that a revision preserves its parent answers a different question from verifying that the first take follows the song. [First blind result and reproduction](../tests/fixtures/musicnet-trout/README.md)

With local instrument analysis, the same study request recovered 24 of the 30 viola notes. The cello revision preserved all 183 backing events and their audio. Two subsequent blind runs on the frozen engine exposed another failure. It heard a plausible tune in its estimated vocal track even though both recordings were instrumental. The brisk walking take recovered 26 of 49 Dvořák cello notes. The late-night study take recovered 24 of 31 Schumann violin notes and played at 112 BPM, above the 55–100 BPM range the agents had set for this example. Both instrument swaps kept the backing samples intact. [Dvořák recording and checks](../tests/fixtures/musicnet-american/README.md) · [Schumann recording and checks](../tests/fixtures/kraisler-dichterliebe/README.md)

After the engine began checking for instrumental notes in the estimated vocal track, the same walking request recovered 41 of the 49 Dvořák notes at 120 BPM. The same study request recovered 29 of the 31 Schumann notes at 72 BPM. Every matched note sounded in both exported performances of its piece. The cello edits kept all 230 and 292 backing events respectively, including the sound those events produced. The arrangements still played through some of the lead's written rests. About 28% of the Dvořák rest time and 66% of the Schumann rest time were silent in the recovered lines.

The next independent recording was a performance of Paul Desmond’s *Take Five*. A brisk walking request produced a 120 BPM arrangement. The trumpet played 39 of 47 reference saxophone notes at their original pitches. All 39 attacks also sounded in the exported cello revision, one octave lower, while the 33 bass events and their rendered audio stayed identical. The engine kept the ordinary mix transcription on this recording. It missed eight attacks and left half the annotated saxophone rest time silent. [Recording, reference and blind result](../tests/fixtures/trios-take-five/README.md)

The written chart for that take called for a kick and snare, but the player only received trumpet and bass. After the planner began honoring that instruction, a fresh performance from the same chart played 17 kicks, 16 snares and 32 hi-hats. Each hit was audible in the exported WAV. The earlier cello favorite still played its original arrangement.

For a second independent check, an agent chose a violin-and-piano recording of *Estrellita* and asked for music for a morning walk, with kick and snare and an easy-to-hear tune. The engine used the repaired instrument tracker. The 88 BPM piano take recovered 34 of 40 violin attacks within one semitone in the original octave, including 32 exact pitches. All 34 reached both exported takes. The cello revision kept all 243 backing events and their sound, including 25 kicks and 24 snares. Three repeated attacks merged and some piano sounded during violin rests. This excerpt and the earlier Schumann excerpt come from the same research collection and different performances. [Recording, reference and blind result](../tests/fixtures/kraisler-estrellita/README.md)
