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
