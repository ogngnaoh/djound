# Recording Library and Studio

**Final result: passed**

Compared the selected dark studio and companion Library mockups with the rendered app at 1536 × 1024, using one browser pixel per design pixel. The studio comparison used Tifa’s saved saxophone take during playback with a draft in the composer. The Library comparison put Tifa first for the screenshot using the same real project rows. A separate pass exercised the live sort order. The [app map](guide/app-map.md) records the Mobbin flows that informed the navigation.

## Findings and fixes

| Priority | What we found | What changed and how we checked it |
| --- | --- | --- |
| P1 | Selecting the performance already playing in comparison could restart the wrong audio. | Kept the existing media element on this transition. The browser selected the reference from history, continued playback, and submitted the next edit against the explicitly chosen take. |
| P1 | An old shell rule limited the seek target to a thin strip. Terra’s quiet recording was also hard to distinguish from silence. | The whole waveform is a seek target. Its bars scale to the highest real peak in each recording. A lower-edge tap at 320 px, keyboard seeking and a failed peak request were checked against the media clock. Terra’s original, which peaks at about 7% of full scale, now has a readable contour. |
| P2 | In the first desktop implementation, playback occupied too little height at the reference size. The expanded transport also clipped the primary action at 1440 × 900. | At the reference size, the finished waveform and transport run from y=322 to 606, compared with y=321 to 610 in the mock. At heights of 800, 900 and 1024 px the default take’s entire Make take button fits on screen. |
| P2 | The Library’s navigation, type and artwork initially read too small. Import errors and keyboard focus were easy to lose on a phone. | Adjusted row spacing, titles, action sizes and the recording cover. Checked the popovers, import, confirmation, restore and return-to-list focus at desktop and phone widths. The import error text has a 10.67:1 contrast ratio. |

## Visual comparison

The 68 px desktop header leads into a 473 px history column in the studio and a 320 px navigation column in the Library at the reference size. The same recording carries its artwork from Library into Studio. Inter distinguishes the song title, take name, submitted words and supporting information. Aligned times and a fixed play control keep the waveform easy to scan. The blue wordmark retains its original shape. Pale lime marks the active choice, played audio and primary action.

The mock shows a shorter request and fewer takes. The implementation shows the saved request, the named edit target and the four actual generated versions. Full instructions are available in take details. One waveform controls seeking, with a separate volume control alongside the transport. The mobile layout puts history below the composer. Reviewed the first-recording, selected-favorite and populated-library states at 1440, 390 and 320 px wide. All nine rendered without document overflow or browser errors.

## Release checks

- Imported a recording, recovered from a dropped response, and reopened the same project. Checked an empty search, filtered navigation, focus after Escape, and moving a disposable project through Trash, Undo and Restore.
- Compared a working take to Original and its parent during playback and while paused. The draft and its target survived the switch. Checked passage editing, clarification, missing take links and failed media.
- Imported Terra’s Theme into the isolated library, made a lo-fi hip-hop arrangement, and requested a warm clarinet lead. The revision retained the preceding take’s 30 note events, tempo, backing piano and brushed drums. A/B playback returned to the corresponding phrase within one audio frame. Reloading Favorites reopened the clarinet take, and the downloaded WAV matched its playable audio byte for byte.
- The initial transcription matched 29 of 33 written melody attacks in this fixture. That bounds the claim about preserving the source melody. The exact 30-note comparison applies to the subsequent edit of the generated arrangement.
- Packaged the browser assets with the local server and checked the existing Zanarkand library separately from the disposable edit sessions. The personal recording kept its original audio and empty take history.

The [import](scripts/rendition-import-smoke.mjs), [comparison and export](scripts/rendition-comparison-smoke.mjs) and [saved-link](scripts/rendition-link-smoke.mjs) browser regressions exercise the live service. Use a disposable library for runs that create or move recordings.
