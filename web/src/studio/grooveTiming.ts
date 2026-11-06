/** A short rolling median smooths normal variation without averaging across pauses. */
export function measureTap(taps: number[], now: number) {
  const previous = taps.at(-1);
  if (previous === undefined || now - previous > 2000) {
    return { taps: [now], hint: "Tap two more beats." };
  }
  const interval = now - previous;
  if (interval < 300 || interval > 1500) {
    return { taps: [now], hint: "Tap a steady beat between 40 and 200 BPM." };
  }
  const next = [...taps.slice(-5), now];
  if (next.length < 3) return { taps: next, hint: "Tap one more beat." };
  const periods = next
    .slice(1)
    .map((t, index) => t - next[index])
    .sort((a, b) => a - b);
  const middle = Math.floor(periods.length / 2);
  const period =
    periods.length % 2
      ? periods[middle]
      : (periods[middle - 1] + periods[middle]) / 2;
  const bpm = Math.round(60000 / period);
  return { taps: next, bpm, hint: `Tapped tempo: ${bpm} BPM.` };
}

export function downbeatAt(seconds: number | null, duration: number) {
  if (
    seconds === null ||
    !Number.isFinite(seconds) ||
    seconds < 0 ||
    !Number.isFinite(duration)
  )
    return null;
  const milliseconds = Math.round(seconds * 1000);
  return milliseconds + 10 <= duration * 1000 + 1e-7 ? milliseconds : null;
}
