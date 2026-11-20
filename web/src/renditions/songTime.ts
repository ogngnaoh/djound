type Landmarks = ReadonlyArray<ReadonlyArray<number>> | undefined;
const rate = 44100;

/** A missing range leaves playback at the corresponding musical position. */
export function passageAuditionStart(start: number | null | undefined) {
  return start == null ? undefined : Math.max(0, start - 2);
}

/** Look up a file frame on the song clock, or the song frame on another file's clock. */
function interpolate(
  map: Landmarks,
  frame: number,
  axis: 0 | 1,
  preferred?: number,
): number {
  if (!map?.length) return frame;
  const other = 1 - axis;
  // Lower bound finds the first landmark at or beyond the requested position.
  let low = 0;
  let high = map.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (map[middle][axis] < frame) low = middle + 1;
    else high = middle;
  }
  if (low === map.length) return map[low - 1][other];
  const next = map[low];
  if (frame === next[axis]) {
    // A held intro or coda has several file positions for the same song frame.
    // Returning to the same take resumes inside the hold where the listener left.
    if (axis === 1 && preferred !== undefined) {
      let last = low;
      while (last + 1 < map.length && map[last + 1][axis] === frame) last++;
      return Math.max(next[other], Math.min(preferred, map[last][other]));
    }
    return next[other];
  }
  if (low === 0) return next[other];
  const before = map[low - 1];
  const fraction = (frame - before[axis]) / (next[axis] - before[axis]);
  return before[other] + fraction * (next[other] - before[other]);
}

export function correspondingPosition(
  seconds: number,
  from: Landmarks,
  to: Landmarks,
  previousSeconds?: number,
): number {
  if (
    from === to ||
    (from &&
      to &&
      from.length === to.length &&
      from.every(([x, y], i) => to[i][0] === x && to[i][1] === y))
  )
    return seconds;
  const songFrame = interpolate(from, Math.round(seconds * rate), 0);
  const previous =
    previousSeconds === undefined ? undefined : previousSeconds * rate;
  return interpolate(to, songFrame, 1, previous) / rate;
}
