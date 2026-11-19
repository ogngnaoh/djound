import assert from "node:assert/strict";
import test from "node:test";
import { correspondingPosition } from "./songTime.ts";

const r = 44100;
const trio = [
  [0, 0],
  [4 * r, 2 * r],
  [10 * r, 10 * r],
];
const slower = [
  [0, 0],
  [20 * r, 10 * r],
];

test("compares the same phrase across the upload and different arrangements", () => {
  assert.equal(correspondingPosition(7, trio, undefined), 6);
  assert.equal(correspondingPosition(7, trio, slower), 12);
  assert.equal(correspondingPosition(12, slower, trio), 7);
  assert.equal(correspondingPosition(1.5, undefined, undefined), 1.5);
});

test("retains the listener's place in a held introduction across comparisons and scoped edits", () => {
  const intro = [
    [0, 0],
    [2 * r, 0],
    [6 * r, 4 * r],
  ];
  assert.equal(correspondingPosition(1.25, intro, undefined), 0);
  assert.equal(correspondingPosition(0, undefined, intro, 1.25), 1.25);
  assert.equal(correspondingPosition(0, undefined, intro), 0);
  assert.equal(
    correspondingPosition(1.25, intro, structuredClone(intro)),
    1.25,
  );
});
