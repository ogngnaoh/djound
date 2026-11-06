import test from "node:test";
import assert from "node:assert/strict";
import { downbeatAt, measureTap } from "./grooveTiming.ts";

test("tap tempo requires a series, ignores an outlier using a rolling median, and resets after a pause", () => {
  let taps: number[] = [];
  for (const [time, expected] of [
    [100, undefined],
    [600, undefined],
    [1100, 120],
    [2300, 120],
    [2800, 120],
    [5300, undefined],
  ] as const) {
    const result = measureTap(taps, time);
    assert.equal(result.bpm, expected);
    taps = result.taps;
  }
  assert.deepEqual(taps, [5300]);
  for (const interval of [299, 1501, 0]) {
    assert.equal(measureTap([0, 500], 500 + interval).bpm, undefined);
  }
  assert.equal(measureTap([0, 1500], 3000).bpm, 40);
  assert.equal(measureTap([0, 300], 600).bpm, 200);
});

test("playhead uses integer milliseconds within the clip's remaining ten millisecond window", () => {
  assert.equal(downbeatAt(0.7104, 12), 710);
  assert.equal(downbeatAt(11.99, 12), 11990);
  for (const position of [null, NaN, Infinity, -0.0001, 11.991, 12.5]) {
    assert.equal(downbeatAt(position, 12), null);
  }
});
