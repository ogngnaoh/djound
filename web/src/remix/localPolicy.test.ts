import assert from "node:assert/strict";
import test from "node:test";
import { noteIssue, pitchName, planMatches } from "./localPolicy.ts";

test("different pitches may overlap for legato; same-pitch overlap is checked at render sample boundaries", () => {
  const first = { start: 0, end: 1, pitch: 60, velocity: 80 };
  assert.equal(
    noteIssue([first, { ...first, start: 0.9, end: 2, pitch: 62 }], 12),
    "",
  );
  assert.ok(noteIssue([first, { ...first, start: 0.9, end: 2 }], 12));
  assert.equal(
    noteIssue([first, { ...first, start: 1 - 0.2 / 44100, end: 2 }], 12),
    "",
  );
  assert.ok(noteIssue([{ ...first, end: 0.1 / 44100 }], 12));
});

test("melody review rejects invalid, overlapping, and out-of-clip notes without changing them", () => {
  const notes = [{ start: 0, end: 1, pitch: 60, velocity: 80 }];
  assert.equal(noteIssue(notes, 12), "");
  assert.ok(noteIssue([{ ...notes[0], end: 13 }], 12));
  assert.ok(noteIssue([{ ...notes[0], pitch: 60.5 }], 12));
  assert.ok(noteIssue([...notes, { ...notes[0], start: 0.5, end: 2 }], 12));
  assert.equal(notes[0].end, 1);
  assert.equal(pitchName(60), "C4");
});

test("an interpreted plan cannot authorize changed text, basis, notes, source or draft", () => {
  const original = {
    sourceRevision: 1,
    noteRevision: 2,
    draftRevision: 3,
    text: "Softer drums",
    basisRecordingId: "version-1",
  };
  assert.equal(planMatches(original, original), true);
  for (const change of [
    { sourceRevision: 2 },
    { noteRevision: 3 },
    { draftRevision: 4 },
    { text: "Louder drums" },
    { basisRecordingId: "original" },
  ])
    assert.equal(planMatches(original, { ...original, ...change }), false);
  assert.equal(planMatches(null, original), false);
});
