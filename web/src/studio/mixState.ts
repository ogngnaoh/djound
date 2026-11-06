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

export function backingTiming(recipe: Recipe) {
  const stems = activeStems(recipe);
  const parts: string[] = [];
  if (recipe.harmony) {
    const label = recipe.bass ? "Chords and bass" : "Chords";
    parts.push(
      stems.includes("chords")
        ? `${label} follow the ${recipe.harmony.bpm} BPM chart.`
        : `${label} rest in every bar.`,
    );
    if (recipe.drums) parts.push(`Drums play at ${recipe.bpm} BPM.`);
  } else if (recipe.bass || recipe.drums) {
    const label =
      recipe.bass && recipe.drums
        ? "Bass and drums play"
        : recipe.bass
          ? "Bass plays"
          : "Drums play";
    parts.push(`${label} at ${recipe.bpm} BPM.`);
  } else {
    parts.push("No backing parts are active.");
  }
  return parts.join(" ");
}

export function hasActiveBacking(recipe: Recipe) {
  return activeStems(recipe).some((stem) => stem !== "lead");
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
