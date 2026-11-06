import type { Chord, ChordSuggestion, Harmony, Recipe } from "./types";
import { normalizeMix } from "./mixState.ts";

export const CHORDS = [
  "C",
  "Cm",
  "Db",
  "Dbm",
  "D",
  "Dm",
  "Eb",
  "Ebm",
  "E",
  "Em",
  "F",
  "Fm",
  "F#",
  "F#m",
  "G",
  "Gm",
  "Ab",
  "Abm",
  "A",
  "Am",
  "Bb",
  "Bbm",
  "B",
  "Bm",
] as const satisfies readonly Chord[];

export function isChord(value: unknown): value is Chord {
  return CHORDS.some((chord) => chord === value);
}

export function changeBar(
  parent: Recipe,
  index: number,
  value: Chord | null,
): Recipe {
  const chart = parent.harmony;
  if (
    !chart ||
    !Number.isInteger(index) ||
    index < 0 ||
    index >= chart.chords.length ||
    chart.chords[index] === value
  )
    return parent;
  const chords = chart.chords.map((chord, bar) =>
    bar === index ? value : chord,
  );
  return normalizeMix({ ...parent, harmony: { ...chart, chords } });
}

export function removeChart(parent: Recipe): Recipe {
  if (!parent.harmony) return parent;
  const { harmony, accompanimentStyle, ...arrangement } = normalizeMix(parent);
  return normalizeMix(arrangement);
}

export function chartBassNotice(recipe: Recipe, before?: Recipe) {
  if (!recipe.bass) return;
  if (recipe.harmony?.chords.every((chord) => chord === null))
    return "Bass stays silent until a bar has a chord.";
  if (!recipe.harmony && before?.harmony && before.bass)
    return before.harmony.chords.every((chord) => chord === null)
      ? "Bass resumes the melody pulse."
      : "Bass returns to the melody pulse.";
}

export function barStart(chart: Harmony, index: number) {
  return chart.downbeatMs / 1000 + (index * 240) / chart.bpm;
}

/** Keep evidence separate from editable fields; ignore unknown or unmatched metadata from old servers. */
export function chordSuggestions(
  chart: Harmony | undefined,
  value: unknown,
): ChordSuggestion[] | undefined {
  if (!chart || !Array.isArray(value) || value.length !== chart.chords.length)
    return;
  const result: ChordSuggestion[] = [];
  for (const [index, raw] of value.entries()) {
    if (
      !raw ||
      typeof raw !== "object" ||
      typeof raw.start !== "number" ||
      !Number.isFinite(raw.start) ||
      Math.abs(raw.start - barStart(chart, index)) > 0.002 ||
      (raw.chord !== null && !isChord(raw.chord)) ||
      typeof raw.ambiguous !== "boolean" ||
      !Array.isArray(raw.alternatives) ||
      !raw.alternatives.every(isChord)
    )
      return;
    result.push({
      chord: raw.chord,
      ambiguous: raw.ambiguous,
      alternatives: raw.alternatives,
    });
  }
  return result;
}
