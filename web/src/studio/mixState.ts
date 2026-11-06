import type { MixStrip, Recipe } from "./types";

const STEMS = ["lead", "bass", "drums", "chords"] as const;
export const UNITY: MixStrip = { gainDb: 0, pan: 0, muted: false };

export function activeStems(recipe: Recipe) {
  const soundingChords =
    recipe.harmony?.chords.some((chord) => chord !== null) ?? false;
  const available = {
    lead: true,
    bass: recipe.bass && (!recipe.harmony || soundingChords),
    drums: recipe.drums,
    chords: soundingChords,
  };
  return STEMS.filter((stem) => available[stem]);
}
export function isUnity(s: MixStrip) {
  return (
    s.gainDb === 0 &&
    s.pan === 0 &&
    !s.muted &&
    (s.tone ?? "neutral") === "neutral"
  );
}

export function normalizeMix(recipe: Recipe): Recipe {
  if (!recipe.mix) return recipe;
  const mix = { ...recipe.mix };
  const active = activeStems(recipe);
  for (const stem of STEMS) {
    const strip = mix[stem];
    if (!strip || !active.includes(stem) || isUnity(strip)) delete mix[stem];
    else if (strip.tone === "neutral") {
      const { tone, ...flat } = strip;
      mix[stem] = flat;
    }
  }
  const result: Recipe = { ...recipe, mix };
  if (!Object.keys(mix).length) delete result.mix;
  return result;
}

export function allTracksMuted(recipe: Recipe) {
  return activeStems(recipe).every((stem) => recipe.mix?.[stem]?.muted);
}
